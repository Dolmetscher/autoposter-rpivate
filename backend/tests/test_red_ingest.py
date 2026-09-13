"""Тесты ingestion «Редакции»: нормализация URL/текста, дедуп и кластеры,
блок-лист, статусы источников при ошибках сбора (FR-1.8–1.10, FR-5.2)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.integrations.base import SourceItem
from app.db import SessionFactory
from app.models import User
from app.redakciya import fetchers, ingest
from app.redakciya.models import RedBlocklist, RedItem, RedSource, RedSourceCheck
from app.util import utcnow


async def _make_user_and_source(s, email: str) -> tuple[User, RedSource]:
    user = User(email=email, password_hash="x")
    s.add(user)
    await s.flush()
    src = RedSource(user_id=user.id, type="rss", identifier="http://feed.example/rss",
                    title="Тестовый фид", tags=["ии", "стартапы"], next_poll_at=utcnow())
    s.add(src)
    await s.flush()
    return user, src


def test_normalize_url_strips_utm():
    url = "https://example.com/news/a?utm_source=x&id=7&fbclid=abc&gclid=z"
    assert ingest.normalize_url(url) == "https://example.com/news/a?id=7"
    assert ingest.normalize_url("https://example.com/x/") == "https://example.com/x"


def test_content_hash_stable():
    a = ingest.content_hash("Заголовок", "текст")
    b = ingest.content_hash("  заголовок ", "текст ")
    assert a == b and len(a) == 40


def test_split_text():
    title, body = ingest._split_text("Первый заголовок\n\nПервый абзац\nВторой")
    assert title == "Первый заголовок"
    assert "Первый абзац" in body
    t2, b2 = ingest._split_text("Только заголовок")
    assert t2 == "Только заголовок" and b2 == ""


def test_url_hash_ignores_utm():
    assert (ingest.url_hash("https://e.com/a?utm_source=x") ==
            ingest.url_hash("https://e.com/a"))


async def test_create_item_dedup_and_cluster():
    async with SessionFactory() as s:
        user, src = await _make_user_and_source(s, "dedup@test.io")
        now = utcnow()
        item1 = SourceItem(external_id="1", text="Релиз модели ИИ\n\nПодробности",
                           url="https://e.com/a?utm_source=x")
        item2 = SourceItem(external_id="2", text="Релиз модели ИИ\n\nПодробности",
                           url="https://other.com/copy")
        assert await ingest._create_item(s, src, item1, now)
        assert not await ingest._create_item(s, src, item1, now)  # тот же URL — дедуп
        assert await ingest._create_item(s, src, item2, now)      # другой URL, тот же контент
        items = (await s.execute(select(RedItem))).scalars().all()
        assert len(items) == 2
        assert items[0].cluster_key == items[1].cluster_key
        assert items[0].status == "new"
        assert items[1].status == "clustered"  # FR-1.9: без отдельного скоринга


async def test_blocklist_marks_ignored():
    async with SessionFactory() as s:
        user, src = await _make_user_and_source(s, "block@test.io")
        now = utcnow()
        c_hash = ingest.content_hash("Т", "Б")
        s.add(RedBlocklist(user_id=user.id, content_hash=c_hash,
                           until=now + timedelta(days=30)))
        raw = SourceItem(external_id="9", text="Т\n\nБ", url="https://e.com/blocked")
        assert await ingest._create_item(s, src, raw, now)
        item = (await s.execute(select(RedItem))).scalars().first()
        assert item.status == "ignored"


async def test_poll_source_records_check_and_degrades(monkeypatch):
    async def failing(src_type, identifier, limit=20, account=None):
        raise ValueError("HTTP 404")

    monkeypatch.setattr(fetchers, "fetch_items", failing)

    async with SessionFactory() as s:
        user, src = await _make_user_and_source(s, "fail@test.io")
        await s.commit()

        for _ in range(3):  # 3 неудачи подряд → degraded (FR-5.2)
            await ingest.poll_source(s, src)
        assert src.status == "degraded"
        assert src.fail_count == 3
        checks = (await s.execute(select(RedSourceCheck))).scalars().all()
        assert len(checks) == 3 and all(not c.ok for c in checks)
