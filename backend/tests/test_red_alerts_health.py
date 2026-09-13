"""Тесты алертов и health/retention: хештеги (FR-3.5), анти-флуд (FR-3.8),
блок-лист, статус-машина источников (FR-5.2), retention 72 ч (FR-7.2–7.4)."""
from datetime import timedelta

from sqlalchemy import select

from app.config import settings
from app.db import SessionFactory
from app.models import MediaFile, User
from app.redakciya import alerting, health, retention
from app.redakciya.models import (RedAlert, RedBlocklist, RedDailyStat, RedItem,
                                  RedScore, RedSource, RedSourceCheck)
from app.util import utcnow


def test_hashtag_transliteration():
    assert alerting.hashtag("Искусственный интеллект") == "#iskusstvennyy_intellekt"
    assert alerting.hashtag("стартапы!") == "#startapy"
    assert alerting.hashtag("  ") == ""


def test_age_text():
    assert alerting.age_text(None) == "только что"
    dt = utcnow() - timedelta(minutes=12)
    assert "12 минут" in alerting.age_text(dt)
    dt2 = utcnow() - timedelta(hours=2)
    assert "2 часа" in alerting.age_text(dt2)


def test_post_text_for_item():
    item = RedItem(id=1, url="https://e.com/a", title="Т",
                   media_urls=[], media_ids=[])
    score = RedScore(item_id=1, headline_ru="Заголовок", summary_ru="Резюме",
                     matched_tags=["ии"])
    text = alerting.post_text_for_item(item, score)
    # FR-3.5: теги транслитерируются в валидные хештеги
    assert "Заголовок" in text and "https://e.com/a" in text and "#ii" in text


async def _env(s, email="alert@test.io"):
    user = User(email=email, password_hash="x")
    s.add(user)
    await s.flush()
    src = RedSource(user_id=user.id, type="rss", identifier="http://f/rss", title="Фид")
    s.add(src)
    await s.flush()
    return user, src


def _item(user, src, title):
    import hashlib

    c = hashlib.sha1(title.encode()).hexdigest()
    return RedItem(user_id=user.id, source_id=src.id, cluster_key=c, url="https://e.com/x",
                   url_hash=c, content_hash=c, title=title, body="тело",
                   status="scored", published_at=utcnow(), fetched_at=utcnow())


async def test_send_alert_without_bot_config():
    """Бот не настроен — алерт не отправляется, но не падает."""
    async with SessionFactory() as s:
        user, src = await _env(s)
        item = _item(user, src, "Горячая новость")
        s.add(item)
        await s.flush()
        assert await alerting.send_item_alert(s, user, item) == 0


async def test_anti_flood_batch(monkeypatch):
    """>5 алертов за 10 минут → сводное сообщение (FR-3.8)."""
    calls = []

    async def fake_tg(token, method, payload=None, files=None):
        calls.append(method)
        return {"message_id": len(calls)}

    async def fake_cfg(s, user_id):
        return {"bot_token": "T", "chat_id": 42}

    monkeypatch.setattr(alerting, "_tg", fake_tg)
    monkeypatch.setattr(alerting, "_cfg", fake_cfg)

    async with SessionFactory() as s:
        user, src = await _env(s, "flood@test.io")
        for i in range(7):
            item = _item(user, src, f"Новость {i}")
            s.add(item)
        await s.flush()
        items = (await s.execute(select(RedItem))).scalars().all()
        sent = sum([await alerting.send_item_alert(s, user, it) for it in items])
        assert sent == 7
        # первые 5 — персональные, дальше — сводные (sendMessage/editMessageText)
        assert calls.count("sendMessage") >= 6
        alerts = (await s.execute(select(RedAlert))).scalars().all()
        assert len(alerts) == 7


async def test_health_transitions():
    async with SessionFactory() as s:
        user, src = await _env(s, "health@test.io")
        src.status = "active"
        src.fail_count = 5  # ≥3 ошибок → degraded (FR-5.2)
        s.add(src)
        await s.flush()
        changed = await health.check_all_sources(s, user.id)
        assert changed == 1 and src.status == "degraded"

        src.last_ok_at = utcnow() - timedelta(days=8)  # 7 дней в degraded → archived
        changed = await health.check_all_sources(s, user.id)
        assert changed == 1 and src.status == "archived"


async def test_health_reactivates_on_streak():
    async with SessionFactory() as s:
        user, src = await _env(s, "heal@test.io")
        src.status = "degraded"
        src.ok_streak = 5  # 5 успешных подряд → active (FR-5.2)
        await s.flush()
        await health.check_all_sources(s, user.id)
        assert src.status == "active"


async def test_retention_deletes_and_aggregates(mock_pages):
    async with SessionFactory() as s:
        user, src = await _env(s, "retain@test.io")
        now = utcnow()
        old = _item(user, src, "Старый материал")
        old.fetched_at = now - timedelta(hours=80)
        old.published_at = now - timedelta(hours=80)
        keep_posted = _item(user, src, "Опубликованный")
        keep_posted.fetched_at = now - timedelta(hours=80)
        keep_posted.status = "posted"
        keep_flag = _item(user, src, "Сохранённый в дайджест")
        keep_flag.fetched_at = now - timedelta(hours=80)
        keep_flag.digest_flag = True
        fresh = _item(user, src, "Свежий")
        s.add_all([old, keep_posted, keep_flag, fresh])
        await s.flush()
        s.add(RedScore(item_id=old.id, user_id=user.id, guideline_version=1,
                       significance=3.0, model="m"))
        s.add(RedAlert(user_id=user.id, item_id=old.id, cluster_key=old.cluster_key))
        mf = MediaFile(id="m1", user_id=user.id, type="photo", file_path="x/y.webp")
        s.add(mf)
        old.media_ids = ["m1"]
        await s.flush()

        stats = await retention.cleanup_user(s, user.id, now)
        assert stats["deleted_items"] == 1  # только «старый» (FR-7.2)
        items = (await s.execute(select(RedItem))).scalars().all()
        titles = {i.title for i in items}
        assert "Старый материал" not in titles
        assert {"Опубликованный", "Сохранённый в дайджест", "Свежий"} <= titles
        assert (await s.execute(select(RedScore))).scalars().first() is None  # каскад
        assert (await s.execute(select(RedAlert))).scalars().first() is None
