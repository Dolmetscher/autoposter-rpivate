"""Тесты механизма замены LLM-роутера (FR-2.2/2.14): цепочка провайдеров из
конфига, ротация между роутерами при 429, legacy OpenRouter-конфиг, заголовки,
метка фактического «провайдер:модель» без утечки ключей."""
from sqlalchemy import select

from app.config import settings
from app.db import SessionFactory
from app.models import User
from app.redakciya import llm
from app.redakciya.models import RedGuideline, RedItem, RedSource
from app.util import utcnow

PROVIDERS = [
    {"name": "router-a", "base_url": "https://a.example/v1", "api_key": "ka",
     "models": ["model-a1", "model-a2"]},
    {"name": "router-b", "base_url": "https://b.example/v1", "api_key": "kb",
     "models": ["model-b1"], "extra_headers": {"api-key": "kb"}},
]

GOOD_JSON = ('{"significance": 4.0, "matched_tags": ["ии"], "rationale": "ok", '
             '"digest_worthy": true, "category": "новость", "headline_ru": "H", '
             '"summary_ru": "S"}')


def test_providers_from_settings_custom_wins(monkeypatch):
    monkeypatch.setattr(settings, "red_llm_providers", PROVIDERS)
    monkeypatch.setattr(settings, "red_openrouter_api_key", "legacy-key")
    provs = llm.providers_from_settings()
    assert [p.name for p in provs] == ["router-a", "router-b"]  # legacy игнорируется
    assert provs[1].extra_headers == {"api-key": "kb"}


def test_providers_legacy_openrouter(monkeypatch):
    monkeypatch.setattr(settings, "red_llm_providers", [])
    monkeypatch.setattr(settings, "red_openrouter_api_key", "legacy-key")
    provs = llm.providers_from_settings()
    assert len(provs) == 1 and provs[0].name == "openrouter"
    assert "HTTP-Referer" in provs[0].extra_headers  # атрибуция OpenRouter на месте
    assert tuple(provs[0].models) == tuple(settings.red_models)


def test_providers_skip_invalid_entries(monkeypatch):
    monkeypatch.setattr(settings, "red_llm_providers", [
        {"name": "broken"},  # нет base_url/models
        {"name": "ok", "base_url": "https://ok.example/v1", "api_key": "k",
         "models": ["m1"]},
    ])
    provs = llm.providers_from_settings()
    assert [p.name for p in provs] == ["ok"]


def test_chain_personal_first_then_providers(monkeypatch):
    monkeypatch.setattr(settings, "red_llm_providers", PROVIDERS)
    user = User(email="chain@test.io", password_hash="x",
                llm_config={"base_url": "https://mine.example/v1", "api_key": "km",
                            "model": "my-model"})
    chain = llm._provider_chain(user)
    assert [(c["provider"], c["model"]) for c in chain] == [
        ("personal", "my-model"),
        ("router-a", "model-a1"), ("router-a", "model-a2"),
        ("router-b", "model-b1")]


def test_headers_merge_and_auth():
    link = {"provider": "router-b", "base_url": "https://b.example/v1", "api_key": "kb",
            "model": "m", "extra_headers": {"api-key": "kb"}}
    h = llm._headers(link)
    assert h["Authorization"] == "Bearer kb" and h["api-key"] == "kb"
    plain = llm._headers({"provider": "x", "api_key": "k", "extra_headers": {}})
    assert plain == {"Authorization": "Bearer k"}


def test_model_label():
    assert llm._model_label({"provider": "groq", "model": "llama-3.3"}) == "groq:llama-3.3"
    assert llm._model_label({"provider": "personal", "model": "my-model"}) == "my-model"
    assert llm._model_label({"provider": "ollama", "model": "qwen2.5:7b"}) == "ollama:qwen2.5:7b"


def test_summary_no_secrets(monkeypatch):
    monkeypatch.setattr(settings, "red_llm_providers", PROVIDERS)
    summ = llm.providers_summary()
    text = str(summ)
    assert "ka" not in text and "kb" not in text and "api_key" not in text
    assert summ[0] == {"name": "router-a", "models": ["model-a1", "model-a2"]}


async def test_rotation_across_providers_on_429(monkeypatch):
    """Первый роутер отдаёт 429 → система сама уходит на второй (FR-2.3)."""
    monkeypatch.setattr(settings, "red_llm_providers", PROVIDERS)

    async def fake_chat(link, system_prompt, user_prompt):
        if link["provider"] == "router-a":
            raise RuntimeError("HTTP 429 (ротация модели)")
        assert link["provider"] == "router-b" and link["api_key"] == "kb"
        return GOOD_JSON

    monkeypatch.setattr(llm, "_chat", fake_chat)

    async with SessionFactory() as s:
        user = User(email="rotate@test.io", password_hash="x")
        s.add(user)
        await s.flush()
        src = RedSource(user_id=user.id, type="rss", identifier="http://f/rss", title="Фид")
        s.add(src)
        await s.flush()
        g = RedGuideline(user_id=user.id, version=1, content="Тематика: ИИ", is_active=True)
        s.add(g)
        import hashlib
        c = hashlib.sha1(b"t")
        item = RedItem(user_id=user.id, source_id=src.id, cluster_key=c.hexdigest(),
                       url="https://e.com/x", url_hash=c.hexdigest(),
                       content_hash=c.hexdigest(), title="Т", body="Б",
                       published_at=utcnow(), fetched_at=utcnow())
        s.add(item)
        await s.flush()

        score, model, _latency = await llm.score_material(s, user, src, item, g)
        assert score["significance"] == 4.0
        assert model == "router-b:model-b1"  # фактический провайдер зафиксирован
        await s.flush()  # запись бюджета — в той же сессии
        assert await llm.budget_left(s, user.id) == settings.red_llm_daily_budget - 1
