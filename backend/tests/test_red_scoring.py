"""Тесты AI-скоринга: префильтр тегов (FR-2.12), кэш (FR-2.11), rule-based
fallback (§2.2.5), очередь ручной проверки (FR-2.5), алертный порог (FR-3.1)."""
from sqlalchemy import select

from app.db import SessionFactory
from app.models import User
from app.redakciya import alerting, llm, scoring
from app.redakciya.models import RedGuideline, RedItem, RedScore, RedSource
from app.util import utcnow


async def _env(s, email="score@test.io"):
    user = User(email=email, password_hash="x")
    s.add(user)
    await s.flush()
    src = RedSource(user_id=user.id, type="rss", identifier="http://f/rss",
                    tags=["ии"], title="Фид")
    s.add(src)
    await s.flush()
    g = RedGuideline(user_id=user.id, version=1, content="Тематика: ИИ",
                     tags=["ии"], is_active=True)
    s.add(g)
    await s.flush()
    return user, src, g


def _item(user, src, title="Релиз новой ИИ-модели", body="Компания представила модель", status="new"):
    import hashlib

    c = hashlib.sha1(f"{title}|{body[:500]}".encode()).hexdigest()
    return RedItem(user_id=user.id, source_id=src.id, cluster_key=c,
                   url="https://e.com/x", url_hash=hashlib.sha1(f"ext:{c}".encode()).hexdigest(),
                   content_hash=c, title=title, body=body, status=status,
                   published_at=utcnow(), fetched_at=utcnow())


async def test_prefilter_no_tags_hit():
    async with SessionFactory() as s:
        user, src, g = await _env(s)
        item = _item(user, src, title="Кулинарный рецепт борща", body="Свёкла и капуста")
        s.add(item)
        await s.flush()
        stats = await scoring.score_item(s, item)
        assert stats.get("filtered") == 1
        assert item.status == "filtered"  # без вызова LLM (FR-2.12)


async def test_review_after_three_failed_rounds(monkeypatch):
    """LLM недоступен: материал остаётся в очереди, после 3 раундов → review (FR-2.5)."""
    async with SessionFactory() as s:
        user, src, g = await _env(s)
        item = _item(user, src)
        s.add(item)
        await s.flush()

        async def no_llm(*a, **k):  # все модели недоступны
            return None, "", 0

        monkeypatch.setattr(llm, "score_material", no_llm)
        for _ in range(3):
            await scoring.score_item(s, item)
        assert item.status == "review" and item.red_attempts == 3


async def test_scoring_success_and_alert(monkeypatch):
    async with SessionFactory() as s:
        user, src, g = await _env(s)
        item = _item(user, src)
        s.add(item)
        await s.flush()

        async def fake_llm(*a, **k):
            return {"significance": 4.5, "matched_tags": ["ии"], "rationale": "ok",
                    "digest_worthy": True, "category": "новость",
                    "headline_ru": "Модель!", "summary_ru": "Кратко о главном"}, \
                   "deepseek/deepseek-chat-v3.1:free", 123

        sent = []
        async def fake_alert(s_, user_, item_):
            sent.append(item_.id)
            return 1

        monkeypatch.setattr(llm, "score_material", fake_llm)
        monkeypatch.setattr(alerting, "send_item_alert", fake_alert)
        stats = await scoring.score_item(s, item)
        assert stats.get("scored") == 1 and stats.get("alerts") == 1
        score = (await s.execute(select(RedScore))).scalars().first()
        assert score.significance == 4.5 and "deepseek" in score.model
        assert sent == [item.id]


async def test_rule_based_suppresses_alerts():
    """Rule-based режим не алертит (§2.2.5) — проверка через _maybe_alert."""
    async with SessionFactory() as s:
        user, src, g = await _env(s)
        item = _item(user, src)
        s.add(item)
        await s.flush()
        assert await scoring._maybe_alert(s, user, item, 5.0, "rule-based") == 0


def test_parse_score_validates():
    good = llm.parse_score('{"significance": 4.5, "matched_tags": ["ии"], '
                           '"rationale": "x", "digest_worthy": true, '
                           '"category": "новость", "headline_ru": "H", "summary_ru": "S"}')
    assert good and good["significance"] == 4.5
    assert llm.parse_score('мусор без json') is None
    assert llm.parse_score('{"significance": 9.0}') is None          # вне шкалы
    bad = llm.parse_score('{"significance": 3.7}')                    # шаг 0.5
    assert bad and bad["significance"] == 3.5
    weird = llm.parse_score('Верю! {"significance": 1.0} конец')
    assert weird and weird["significance"] == 1.0


def test_rule_based_scoring_formula():
    class FakeSrc:
        tags = ["ии", "стартапы"]

    class FakeItem:
        title = "ИИ-релиз"
        body = "новость про ии"

    class FakeG:
        tags = ["ии", "стартапы"]

    out = llm.rule_based(FakeSrc(), FakeItem(), FakeG())
    assert out["significance"] == 1.0 and out["matched_tags"] == ["ии"]
    assert out["digest_worthy"] is False
