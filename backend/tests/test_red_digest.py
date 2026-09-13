"""Тесты дайджеста: ранжирование (FR-4.5), сборка черновика (FR-4.8–4.9),
условия пропуска (FR-4.3), stale (FR-4.13), публикация через пайплайн."""
from datetime import timedelta

from sqlalchemy import select

from app.db import SessionFactory
from app.models import User
from app.redakciya import digest
from app.redakciya.models import RedDigest, RedDigestItem, RedGuideline, RedItem, RedScore, RedSource
from app.util import utcnow


async def _env(s, email="digest@test.io"):
    user = User(email=email, password_hash="x")
    s.add(user)
    await s.flush()
    src = RedSource(user_id=user.id, type="rss", identifier="http://f/rss", title="Фид")
    s.add(src)
    await s.flush()
    s.add(RedGuideline(user_id=user.id, version=1, content="Тематика: ИИ", is_active=True))
    return user, src


async def _scored_item(s, user, src, title, sig, digest_worthy=True,
                       age_hours=1.0, digest_flag=False):
    import hashlib

    c = hashlib.sha1(title.encode()).hexdigest()
    item = RedItem(
        user_id=user.id, source_id=src.id, cluster_key=c, url=f"https://e.com/{c[:8]}",
        url_hash=c, content_hash=c, title=title, body=f"Тело: {title}",
        status="digest" if digest_flag else "scored",
        digest_flag=digest_flag, published_at=utcnow() - timedelta(hours=age_hours),
        fetched_at=utcnow() - timedelta(hours=age_hours))
    s.add(item)
    await s.flush()
    s.add(RedScore(item_id=item.id, user_id=user.id, guideline_version=1,
                   significance=sig, digest_worthy=digest_worthy, category="новость",
                   headline_ru=f"Н: {title}", summary_ru=f"С: {title}",
                   model="test-model"))
    await s.flush()
    return item


def test_rank_formula():
    now = utcnow()

    class I:
        def __init__(self, hours):
            self.fetched_at = now - timedelta(hours=hours)

    class S:
        def __init__(self, sig):
            self.significance = sig

    ranked = digest._rank(
        [(I(0), S(5.0)), (I(72), S(5.0)), (I(0), S(3.0))], now)
    # score = 0.7×sig + 0.3×свежесть: свежий 5.0 → 3.8; старый 5.0 → 3.5; свежий 3.0 → 2.4
    assert [round(r[2], 1) for r in ranked] == [3.8, 3.5, 2.4]


async def test_build_digest_skips_when_few_items():
    async with SessionFactory() as s:
        user, src = await _env(s)
        await _scored_item(s, user, src, "Один-единственный материал", 4.0)
        out = await digest.build_digest(s, user, "daily19")
        assert out is None  # <2 материалов → пропуск (FR-4.3)


async def test_build_digest_creates_draft(monkeypatch):
    async def no_llm_intro(*a, **k):
        return "", ""
    monkeypatch.setattr(digest, "_llm_intro_outro", no_llm_intro)
    monkeypatch.setattr(digest.alerting, "send_digest_draft", _fake_send)

    async with SessionFactory() as s:
        user, src = await _env(s)
        for i in range(3):
            await _scored_item(s, user, src, f"Материал {i}", 4.0 + i * 0.5 if i else 4.0)
        dg = await digest.build_digest(s, user, "daily19")
        assert dg is not None and dg.status == "draft"
        assert "Дайджест" in dg.draft_text
        assert "Н: Материал" in dg.draft_text
        links = (await s.execute(select(RedDigestItem))).scalars().all()
        assert len(links) == 3


def _fake_send(*a, **k):
    async def _s(*a, **k):
        return 123
    return _s()


async def test_digest_does_not_repeat_items(monkeypatch):
    async def no_llm(*a, **k):
        return "", ""
    monkeypatch.setattr(digest, "_llm_intro_outro", no_llm)
    monkeypatch.setattr(digest.alerting, "send_digest_draft", _fake_send)

    async with SessionFactory() as s:
        user, src = await _env(s, "digest2@test.io")
        await _scored_item(s, user, src, "A", 4.0)
        await _scored_item(s, user, src, "B", 4.5)
        first = await digest.build_digest(s, user, "daily19")
        # материалы первого дайджеста не попадают во второй (FR-4.4)
        second = await digest.build_digest(s, user, "daily19")
        assert first is not None and second is None


async def test_mark_stale_after_24h():
    async with SessionFactory() as s:
        user, src = await _env(s, "stale@test.io")
        dg = RedDigest(user_id=user.id, dtype="daily19", draft_text="x", status="draft",
                       created_at=utcnow() - timedelta(hours=25))
        s.add(dg)
        await s.flush()
        out = await digest.mark_stale(s)
        assert dg.status == "stale" and dg.id in out


async def test_publish_digest_via_pipeline(mock_pages):
    """Approve → публикация через общий пайплайн autoposter (песочница).
    Используем пользователя из auth-фикстуры — у него подключены страницы."""
    from app.services import notify as notify_service

    async with SessionFactory() as s:
        user = (await s.execute(
            select(User).where(User.email == "user@test.io"))).scalars().first()
        assert user is not None
        src = RedSource(user_id=user.id, type="rss", identifier="http://f/rss", title="Фид")
        s.add(src)
        await s.flush()
        await _scored_item(s, user, src, "Публикуемый материал", 4.5)
        dg = RedDigest(user_id=user.id, dtype="daily19", draft_text="Текст дайджеста",
                       status="draft")
        s.add(dg)
        await s.flush()
        item = (await s.execute(select(RedItem))).scalars().first()
        s.add(RedDigestItem(digest_id=dg.id, item_id=item.id, position=0))
        cfg = await notify_service.get_settings(s, user.id)
        cfg = dict(cfg)
        cfg["red_channel_page_ids"] = list(mock_pages)
        await notify_service.save_settings(s, user.id, cfg)
        await digest.publish_digest(s, dg)
        assert dg.status == "published" and dg.post_id is not None
