"""LLM-скоринг через OpenAI-совместимые API (FR-2.2–2.6, §2.2.5).

Провайдер (роутер) = base_url + api_key + список моделей — OpenRouter,
Groq, DeepSeek, Gemini, LiteLLM-proxy и т.д. взаимозаменяемы через конфиг.
Цепочка вызовов (ротация при 429/таймауте/5xx): личный LLM пользователя →
провайдеры из RED_LLM_PROVIDERS (по порядку) → Ollama → rule-based.
Бюджет запросов/день учитывается в app_state (FR-2.4.1, FR-2.13).
Фактический «провайдер:модель» пишется в red_scores.model (FR-2.14).
"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import timedelta

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, User
from ..util import utcnow
from .models import RedGuideline, RedItem, RedSource

log = logging.getLogger("red.llm")

CATEGORIES = ("новость", "аналитика", "мнение", "инструменты", "инфоповод")

SCORE_SCHEMA = {
    "significance": "число 0.0-5.0 с шагом 0.5",
    "matched_tags": ["теги из гайдлайнов, которым соответствует материал"],
    "rationale": "1-2 предложения обоснования",
    "digest_worthy": "true/false",
    "category": "новость | аналитика | мнение | инструменты | инфоповод",
    "headline_ru": "короткий заголовок на русском для канала (до 100 символов)",
    "summary_ru": "резюме на русском 2-3 предложения, до 300 символов",
}

SCALE_TEXT = """Шкала significance:
5.0 — мастхэв: прямо про ядро тематики канала, срочно, публиковать сразу
4.0-4.5 — очень релевантно, достойно немедленного алерта
3.0-3.5 — релевантно, кандидат в дайджест
1.5-2.5 — слабая связь с тематикой
0-1.0 — не подходит"""

PROMPT_TEMPLATE = """Ты — дежурный редактор новостного канала. Твоя задача — оценить, насколько
материал подходит для публикации в канале согласно редакционным гайдлайнам.

ГАЙДЛАЙНЫ КАНАЛА (версия {version}):
{guidelines}

МАТЕРИАЛ:
Источник: {source_name} ({source_type})
Заголовок: {title}
Текст: {body}
Теги источника: {tags}

Верни СТРОГО JSON по схеме:
{schema}

{scale}"""


def budget_state_key(user_id: int) -> str:
    return f"red_llm_budget_{user_id}"


async def budget_left(s: AsyncSession, user_id: int) -> int:
    """Остаток дневного бюджета LLM-запросов (сброс по дате UTC, FR-2.13)."""
    today = utcnow().strftime("%Y-%m-%d")
    row = await s.get(AppState, budget_state_key(user_id))
    if row is None or row.value.get("date") != today:
        return settings.red_llm_daily_budget
    return max(0, settings.red_llm_daily_budget - int(row.value.get("count", 0)))


async def _spend(s: AsyncSession, user_id: int) -> None:
    today = utcnow().strftime("%Y-%m-%d")
    row = await s.get(AppState, budget_state_key(user_id))
    # текущий вызов сам расходует запрос: новый день всегда начинается с 1
    value = {"date": today, "count": 1}
    if row is not None and row.value.get("date") == today:
        value["count"] = int(row.value.get("count", 0)) + 1
    if row is not None:
        row.value = value
        row.updated_at = utcnow()
    else:
        s.add(AppState(key=budget_state_key(user_id), value=value, updated_at=utcnow()))


@dataclass(frozen=True)
class LLMProvider:
    """Один LLM-роутер: любой OpenAI-совместимый /chat/completions-эндпоинт."""
    name: str
    base_url: str
    api_key: str
    models: tuple[str, ...] = ()
    extra_headers: dict = field(default_factory=dict)

    def links(self) -> list[dict]:
        return [{"provider": self.name, "base_url": self.base_url.rstrip("/"),
                 "api_key": self.api_key, "model": m,
                 "extra_headers": dict(self.extra_headers)}
                for m in self.models]


def providers_from_settings() -> list[LLMProvider]:
    """Провайдеры из RED_LLM_PROVIDERS; если не заданы — legacy OpenRouter-конфиг."""
    out: list[LLMProvider] = []
    for raw in settings.red_llm_providers or []:
        base_url = str(raw.get("base_url") or "").rstrip("/")
        models = tuple(str(m) for m in (raw.get("models") or []) if str(m).strip())
        if not base_url or not models:
            log.warning("RED_LLM_PROVIDERS: запись пропущена (нужны base_url и models): %s",
                        list(raw.keys()))
            continue
        out.append(LLMProvider(
            name=str(raw.get("name") or base_url.split("//")[-1].split("/")[0]),
            base_url=base_url, api_key=str(raw.get("api_key") or ""),
            models=models, extra_headers=dict(raw.get("extra_headers") or {})))
    if not out and settings.red_openrouter_api_key:
        out.append(LLMProvider(
            name="openrouter", base_url=settings.red_openrouter_base_url,
            api_key=settings.red_openrouter_api_key, models=tuple(settings.red_models),
            extra_headers={"HTTP-Referer": "https://autoposter.local",
                           "X-Title": "Autoposter Redakciya"}))
    return out


def providers_summary() -> list[dict]:
    """Имена и модели настроенных провайдеров — для /red/health (без ключей)."""
    return [{"name": p.name, "models": list(p.models)} for p in providers_from_settings()]


def _provider_chain(user: User) -> list[dict]:
    """Приоритетная цепочка вызовов (FR-2.2):
    личный LLM пользователя → провайдеры из настроек (по порядку)."""
    chain: list[dict] = []
    cfg = user.llm_config or {}
    if cfg.get("base_url") and cfg.get("api_key"):
        chain.append({"provider": "personal", "base_url": cfg["base_url"].rstrip("/"),
                      "api_key": cfg["api_key"],
                      "model": cfg.get("model") or settings.red_models[0],
                      "extra_headers": {}})
    for p in providers_from_settings():
        chain.extend(p.links())
    return chain


# совместимость со старым именем (использовался в digest.py до рефакторинга)
_effective_chain = _provider_chain


def _ollama_link() -> dict | None:
    if settings.red_ollama_base_url:
        return {"provider": "ollama",
                "base_url": settings.red_ollama_base_url.rstrip("/"), "api_key": "ollama",
                "model": settings.red_ollama_model, "extra_headers": {}}
    return None


def _model_label(link: dict) -> str:
    """«провайдер:модель» — записывается в scores.model для оценки качества (FR-2.14)."""
    name, model = str(link.get("provider") or ""), str(link["model"])
    if name and name not in ("personal",) and not model.startswith(f"{name}:"):
        return f"{name}:{model}"
    return model


def _headers(link: dict) -> dict:
    """Bearer-авторизация + доп. заголовки провайдера (напр. api-key для Azure-стиля)."""
    out = {"Authorization": f"Bearer {link['api_key']}"}
    out.update(link.get("extra_headers") or {})
    return out


async def _chat(link: dict, system_prompt: str, user_prompt: str) -> str:
    """Один вызов chat/completions; ошибки — исключения (timeout/429/5xx/сеть)."""
    async with httpx.AsyncClient(timeout=settings.red_llm_timeout) as client:
        resp = await client.post(
            f"{link['base_url'].rstrip('/')}/chat/completions",
            headers=_headers(link),
            json={
                "model": link["model"],
                "temperature": 0,
                "max_tokens": settings.red_llm_max_tokens,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            },
        )
        if resp.status_code in (429, 500, 502, 503):
            raise RuntimeError(f"HTTP {resp.status_code} (ротация модели)")
        resp.raise_for_status()
        data = resp.json()
    return (data["choices"][0]["message"].get("content") or "").strip()


def parse_score(content: str) -> dict | None:
    """Достать и валидировать JSON-ответ скоринга (FR-2.5). None = не валидно."""
    m = re.search(r"\{.*\}", content, re.S)
    if not m:
        return None
    try:
        raw = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(raw, dict):
        return None
    try:
        sig = float(raw["significance"])
    except (KeyError, TypeError, ValueError):
        return None
    if not 0 <= sig <= 5:
        return None
    category = str(raw.get("category") or "новость")
    if category not in CATEGORIES:
        category = "новость"
    tags = [str(t)[:64] for t in (raw.get("matched_tags") or []) if str(t).strip()][:10]
    return {
        "significance": round(sig * 2) / 2,  # шаг 0.5
        "matched_tags": tags,
        "rationale": str(raw.get("rationale") or "")[:1000],
        "digest_worthy": bool(raw.get("digest_worthy")),
        "category": category,
        "headline_ru": str(raw.get("headline_ru") or "")[:300],
        "summary_ru": str(raw.get("summary_ru") or "")[:1500],
    }


def build_prompts(guideline: RedGuideline, source: RedSource, item: RedItem) -> tuple[str, str]:
    system_prompt = PROMPT_TEMPLATE.format(
        version=guideline.version,
        guidelines=(guideline.content or "Гайдлайны не заданы")[:5000],
        source_name=source.title or source.identifier,
        source_type=source.type,
        title=item.title or "(без заголовка)",
        body=(item.body or item.title or "")[:4000],
        tags=", ".join(source.tags or []) or "—",
        schema=json.dumps(SCORE_SCHEMA, ensure_ascii=False, indent=1),
        scale=SCALE_TEXT,
    )
    return system_prompt, f"Материал:\nЗаголовок: {item.title}\nТекст: {item.body[:4000]}"


def rule_based(source: RedSource, item: RedItem, guideline: RedGuideline | None) -> dict:
    """Финальный fallback (§2.2.5): significance = min(3.0, совпавшие теги × 1.0)."""
    tags = (guideline.tags if guideline and guideline.tags else None) or source.tags or []
    text = f"{item.title}\n{item.body}".lower()
    matched = [t for t in tags if str(t).strip().lower() in text]
    sig = min(3.0, float(len(matched)))
    return {
        "significance": sig,
        "matched_tags": matched,
        "rationale": "Rule-based режим: LLM недоступен или исчерпан дневной лимит.",
        "digest_worthy": sig >= 3.0,
        "category": "новость",
        "headline_ru": item.title[:300],
        "summary_ru": (item.body or "")[:300],
    }


async def score_material(s: AsyncSession, user: User, source: RedSource,
                         item: RedItem, guideline: RedGuideline | None,
                         ) -> tuple[dict | None, str, int]:
    """Скоринг одного материала. Возвращает (score|None, model, latency_ms).
    score=None — материал остаётся в очереди (retry следующим тиком)."""
    started = time.monotonic()

    # Бюджет исчерпан → сразу rule-based до конца дня (FR-2.13)
    if await budget_left(s, user.id) <= 0:
        return rule_based(source, item, guideline), "rule-based", 0

    system_prompt, user_prompt = build_prompts(guideline, source, item)
    chain = _provider_chain(user)
    ollama = _ollama_link()

    if not chain and not ollama:
        # LLM не настроен вовсе — работаем rule-based, чтобы система оставалась живой
        return rule_based(source, item, guideline), "rule-based", 0

    for link in chain:  # ротация между провайдерами/моделями при 429/таймауте/5xx (FR-2.3)
        try:
            content = await _chat(link, system_prompt, user_prompt)
        except Exception as e:  # noqa: BLE001
            log.info("scoring %s failed: %s", _model_label(link), e)
            continue
        score = parse_score(content)
        if score is None:
            # один retry с уточнением (FR-2.5)
            try:
                content = await _chat(
                    link, system_prompt,
                    user_prompt + "\n\nПредыдущий ответ не был валидным JSON. "
                                  "Верни ТОЛЬКО валидный JSON по схеме, без пояснений.")
            except Exception:  # noqa: BLE001
                continue
            score = parse_score(content)
        if score is not None:
            await _spend(s, user.id)
            latency = int((time.monotonic() - started) * 1000)
            return score, _model_label(link), latency

    if ollama:  # fallback на локальную модель (§2.2.5)
        try:
            content = await _chat(ollama, system_prompt, user_prompt)
            score = parse_score(content)
        except Exception as e:  # noqa: BLE001
            log.info("ollama fallback failed: %s", e)
            score = None
        if score is not None:
            return score, _model_label(ollama), int((time.monotonic() - started) * 1000)

    return None, "", int((time.monotonic() - started) * 1000)
