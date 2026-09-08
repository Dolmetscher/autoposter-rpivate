import pytest

from app.services import crosspost as crosspost_service
from app.services import rewrite as rewrite_service
from app.integrations.base import SourceItem
from app.integrations.mock import MockAdapter
from app.util import utcnow


@pytest.fixture
def fake_source_items(monkeypatch):
    items = [
        SourceItem(external_id="src_1", text="Первый пост из канала", media=[]),
        SourceItem(external_id="src_2", text="Второй пост с фото",
                   media=[{"url": "http://example.test/pic.jpg", "kind": "photo"}]),
    ]

    async def fake_fetch(self, source_ref, limit=10, account=None):
        return items

    monkeypatch.setattr(MockAdapter, "fetch_source_items", fake_fetch)
    return items


async def _make_source(client, auth_headers, mock_pages, **overrides):
    payload = {
        "source_id": 99,
        "name": "Тестовый канал",
        "source_ref": "mock_channel",
        "target_page_ids": mock_pages[:1],
        "poll_interval_min": 15,
    }
    payload.update(overrides)
    r = await client.post("/api/web/crosspost", headers=auth_headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_crosspost_copies_new_posts(client, auth_headers, mock_pages, fake_source_items):
    src_id = await _make_source(client, auth_headers, mock_pages)
    r = await client.post(f"/api/web/crosspost/{src_id}/run", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 2

    r = await client.get("/api/web/posts", headers=auth_headers)
    texts = [t["text"] for p in r.json()["list"] for t in p["texts"]]
    assert "Первый пост из канала" in texts
    assert "Второй пост с фото" in texts
    assert all(p["status"] == "published" for p in r.json()["list"])

    # повторный опрос — дубликатов нет
    r = await client.post(f"/api/web/crosspost/{src_id}/run", headers=auth_headers)
    assert r.json()["created"] == 0


async def test_crosspost_filters(client, auth_headers, mock_pages, fake_source_items):
    # only_with_media: пост без медиа отфильтрован
    src_id = await _make_source(client, auth_headers, mock_pages,
                                only_with_media=True)
    r = await client.post(f"/api/web/crosspost/{src_id}/run", headers=auth_headers)
    assert r.json()["created"] == 1  # только второй пост с фото

    # keyword_filter: только посты со словом "Первый"
    src_id = await _make_source(client, auth_headers, mock_pages,
                                keyword_filter="Первый")
    r = await client.post(f"/api/web/crosspost/{src_id}/run", headers=auth_headers)
    assert r.json()["created"] == 1


async def test_crosspost_poll_due_respects_interval(client, auth_headers, mock_pages,
                                                    fake_source_items):
    from sqlalchemy import select

    from app.db import SessionFactory
    from app.models import CrosspostSource

    await _make_source(client, auth_headers, mock_pages)
    async with SessionFactory() as s:
        src = (await s.execute(select(CrosspostSource))).scalar_one()
        src.last_polled_at = utcnow()
        await s.commit()
    from app.scheduler import tick_once
    from app.db import SessionFactory as SF
    res = await tick_once(SF)
    assert res.get("sources") == 0  # интервал ещё не истёк

    async with SessionFactory() as s:
        src = (await s.execute(select(CrosspostSource))).scalar_one()
        src.last_polled_at = utcnow() - __import__("datetime").timedelta(minutes=20)
        await s.commit()
    res = await tick_once(SF)
    assert res.get("sources") == 1


async def test_rewrite_with_llm(monkeypatch):
    async def fake_post_chat(llm_config, system_prompt, text):
        assert "уникальн" in system_prompt.lower() or len(system_prompt) > 10
        return {"choices": [{"message": {"content": f"УНИКАЛЬНО::{text}"}}]}

    monkeypatch.setattr(rewrite_service, "_post_chat", fake_post_chat)
    out, err = await rewrite_service.rewrite_text(
        {"base_url": "http://llm.test/v1", "api_key": "k", "model": "m", "prompt": "Перепиши уникально"},
        "исходный текст")
    assert err == ""
    assert out == "УНИКАЛЬНО::исходный текст"


async def test_rewrite_without_config():
    out, err = await rewrite_service.rewrite_text({}, "текст")
    assert out == "текст"
    assert "LLM не настроен" in err


async def test_rewrite_failure_falls_back(monkeypatch):
    async def broken(*a, **k):
        raise RuntimeError("llm down")

    monkeypatch.setattr(rewrite_service, "_post_chat", broken)
    out, err = await rewrite_service.rewrite_text(
        {"base_url": "http://llm.test/v1", "api_key": "k"}, "оригинал")
    assert out == "оригинал"
    assert "не удался" in err
