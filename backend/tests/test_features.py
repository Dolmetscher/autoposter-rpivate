"""Тесты новых функций: RSS-кросспостинг, шифрование секретов, Telegram-уведомления."""
import pytest

from app.integrations.mock import MockAdapter
from app.integrations.rss import parse_feed
from app.services import crypto
from app.services import notify as notify_service

SAMPLE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel><title>Блог</title>
<item>
  <title><![CDATA[Первый пост]]></title>
  <link>https://blog.test/1</link>
  <description><![CDATA[<p>Текст с <b>html</b> и &amp; сущностями</p>]]></description>
  <enclosure url="https://blog.test/img.jpg" type="image/jpeg" length="123"/>
</item>
<item>
  <title>Второй</title>
  <link>https://blog.test/2</link>
  <media:content url="https://blog.test/clip.mp4" medium="video"/>
</item>
</channel></rss>"""


def test_rss_parse_feed():
    items = parse_feed(SAMPLE_FEED, limit=10)
    assert len(items) == 2
    first, second = items
    assert first.external_id == "https://blog.test/1"
    assert "Первый пост" in first.text
    assert "html" in first.text and "<p>" not in first.text
    assert "&amp;" not in first.text
    assert first.media == [{"url": "https://blog.test/img.jpg", "kind": "photo"}]
    assert second.media == [{"url": "https://blog.test/clip.mp4", "kind": "video"}]


async def test_rss_crosspost_end_to_end(client, auth_headers, mock_pages, monkeypatch):
    from app.integrations.base import SourceItem

    async def fake_fetch(feed_url, limit=10):
        assert feed_url == "https://blog.test/feed.xml"
        return [SourceItem(external_id="https://blog.test/1",
                           text="Пост из RSS", media=[])]

    monkeypatch.setattr("app.services.crosspost.fetch_rss_items", fake_fetch)

    r = await client.post("/api/web/crosspost", headers=auth_headers, json={
        "source_id": 30,  # RSS
        "name": "Блог",
        "source_ref": "https://blog.test/feed.xml",
        "target_page_ids": mock_pages[:1],
    })
    assert r.status_code == 201, r.text
    src_id = r.json()["id"]

    r = await client.post(f"/api/web/crosspost/{src_id}/run", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1

    r = await client.get("/api/web/posts", headers=auth_headers)
    texts = [t["text"] for p in r.json()["list"] for t in p["texts"]]
    assert "Пост из RSS" in texts


async def test_credentials_encrypted_at_rest(client, auth_headers):
    from sqlalchemy import text as sql_text

    from app.db import SessionFactory

    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 99,
                                "credentials": {"access_token": "TOPSECRET-TOKEN"}})
    assert r.status_code == 200

    async with SessionFactory() as s:
        raw = (await s.execute(sql_text(
            "SELECT credentials FROM social_accounts LIMIT 1"))).scalar()
        assert "TOPSECRET-TOKEN" not in (raw or "")
        assert "__enc" in (raw or "")
        acc = (await s.execute(sql_text(
            "SELECT id FROM social_accounts LIMIT 1"))).scalar_one()

    # чтение через ORM расшифровывает
    from app.models import SocialAccount
    async with SessionFactory() as s:
        account = await s.get(SocialAccount, acc)
        assert account.credentials["access_token"] == "TOPSECRET-TOKEN"


def test_crypto_roundtrip_and_legacy():
    data = {"bot_token": "123:ABC", "chat_id": "42"}
    enc = crypto.encrypt_value(data)
    assert "123:ABC" not in str(enc)
    assert crypto.decrypt_value(enc) == data
    # legacy-запись в открытом виде читается как есть
    assert crypto.decrypt_value({"bot_token": "plain"}) == {"bot_token": "plain"}


async def test_notify_on_publish_error(client, auth_headers, mock_pages, monkeypatch):
    captured = []

    async def fake_send(bot_token, chat_id, text):
        captured.append((bot_token, chat_id, text))
        return True, ""

    monkeypatch.setattr("app.services.notify.send", fake_send)

    from app.db import SessionFactory
    from app.services import notify as ns
    async with SessionFactory() as s:
        await ns.save_settings(s, 1, {"bot_token": "111:T", "chat_id": "777",
                                      "on_error": True, "on_success": False})
        await s.commit()

    async def fail_publish(self, page, content):  # noqa: ANN001
        from app.integrations.base import PublishOutcome
        return PublishOutcome(ok=False, error="Сеть недоступна")

    monkeypatch.setattr(MockAdapter, "publish", fail_publish)

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Сломанный пост"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert r.status_code == 200
    assert r.json()["status"] == "failed"
    await client.get("/api/web/dashboard", headers=auth_headers)  # прогон

    assert len(captured) == 1
    token, chat_id, text = captured[0]
    assert token == "111:T" and chat_id == "777"
    assert "ошибки публикации" in text and "Сеть недоступна" in text


async def test_notify_skipped_on_success_by_default(client, auth_headers, mock_pages,
                                                    monkeypatch):
    captured = []

    async def fake_send(bot_token, chat_id, text):
        captured.append(text)
        return True, ""

    monkeypatch.setattr("app.services.notify.send", fake_send)

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Успешный пост"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert r.json()["status"] == "published"
    assert captured == []  # on_success выключен по умолчанию


async def test_settings_notify_api_masking(client, auth_headers):
    from app.db import SessionFactory
    from app.services import notify as ns
    async with SessionFactory() as s:
        await ns.save_settings(s, 1, {"bot_token": "111:T", "chat_id": "777",
                                      "on_error": True, "on_success": False,
                                      "inbox_page_ids": [1]})
        await s.commit()

    r = await client.get("/api/web/settings", headers=auth_headers)
    assert r.status_code == 200
    notify = r.json()["notify"]
    assert notify["bot_token"] == "***"  # не отдаём токен
    assert notify["chat_id"] == "777"

    # PUT с "***" сохраняет прежний токен; chat_id из payload игнорируется
    r = await client.put("/api/web/settings", headers=auth_headers, json={
        "notify": {"bot_token": "***", "chat_id": "HACKED", "on_error": False,
                   "on_success": True, "inbox_page_ids": [2, 3]}})
    assert r.status_code == 200
    from app.db import SessionFactory as SF
    async with SF() as s:
        cfg = await ns.get_settings(s, 1)
        assert cfg["bot_token"] == "111:T"
        assert cfg["chat_id"] == "777"
        assert cfg["on_error"] is False
        assert cfg["on_success"] is True
        assert cfg["inbox_page_ids"] == [2, 3]


async def test_llm_config_encrypted(client, auth_headers):
    from sqlalchemy import text as sql_text

    from app.db import SessionFactory

    r = await client.put("/api/web/settings", headers=auth_headers, json={
        "llm_config": {"base_url": "https://llm.test/v1", "api_key": "LLM-SECRET",
                       "model": "m", "prompt": "p"}})
    assert r.status_code == 200
    async with SessionFactory() as s:
        raw = (await s.execute(sql_text("SELECT llm_config FROM users LIMIT 1"))).scalar()
        assert "LLM-SECRET" not in (raw or "")
    r = await client.get("/api/web/settings", headers=auth_headers)
    assert r.json()["llm_config"]["api_key"] == "***"
