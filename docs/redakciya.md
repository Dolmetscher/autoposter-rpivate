# «Автоматизированная редакция» в autoposter — связка с ТЗ v2.0

Подсистема реализует ТЗ («Автоматизированная редакция», версия 2.0 от 09.09.2026)
внутри существующего проекта autoposter с переиспользованием его пайплайна
публикации, ingestion-механик, TG-бота и планировщика.

Код: `backend/app/redakciya/` (модели, сервисы, API), фронт: `frontend/src/pages/red/`
+ `frontend/src/pages/Redakciya.tsx` (раздел меню «📰 Редакция»).

## Адаптация стека ТЗ → autoposter

| ТЗ (раздел 3) | Реализация здесь | Почему |
|---|---|---|
| n8n / Celery beat cron | Задачи в `scheduler.tick_once` (30–60 с), метки расписаний в `app_state` | работает и в inline-, и в celery-режиме без новой инфраструктуры |
| PostgreSQL-партиции + DROP PARTITION | Таблицы + `DELETE` по индексу `fetched_at` (retention) | объёмы MVP (500 мат./день) не требуют партиций |
| Redis-очереди/дедуп/кэш | Таблицы-очереди по `status`; дедуп/блок-лист/кэш в БД | единая БД, меньше движущихся частей |
| MinIO | `MediaFile` + `services/files` (local/S3) | уже есть |
| OpenRouter SDK | `redakciya/llm.py` — OpenAI-совместимый клиент + ротация free-моделей | тот же API; fallback Ollama (тоже OpenAI-совместимый) |
| Отдельный бот алертов | Тот же бот из настроек уведомлений (`notify.bot_token/chat_id`) | привязка через /start уже реализована |
| Постинг в TG/VK/Bluesky | Общий пайплайн `publish.create_post` → адаптеры 14 сетей | «связка» с autoposter: алерт «В канал» создаёт Post |

## Трассировка требований ТЗ → код

| Требование | Где реализовано |
|---|---|
| FR-1.1–1.3 (5 типов источников, теги) | `redakciya/fetchers.py`, `RedSource` |
| FR-1.4–1.7 (планировщик, джиттер, TZ) | `redakciya/ingest.py::poll_due/_next_poll` |
| FR-1.8 (нормализация: UTM, HTML, trim) | `ingest.py::normalize_url/strip_html/_split_text` |
| FR-1.9–1.10 (дедуп URL+content, кластеры) | `ingest.py::_create_item` (статус `clustered`) |
| FR-1.12–1.13 (медиа до 4 шт.) | `ingest.py::_create_item` → `files.download_from_url` |
| FR-1.15–1.16, FR-5.8 (UI источников, тестовый сбор) | `redakciya/router.py::/sources*`, `RedSources.tsx` |
| FR-2.1–2.6 (скоринг, промпт, JSON-валидация, retry) | `redakciya/llm.py`, `redakciya/scoring.py` |
| FR-2.4.1, FR-2.13 (бюджет запросов/день) | `llm.py::budget_left/_spend` (app_state) |
| FR-2.7–2.9 (гайдлайны, версии, перескор) | `router.py::/guidelines`, `RedGuidelines.tsx` |
| FR-2.11 (кэш по content_hash+version) | `scoring.py::score_item` |
| FR-2.12 (префильтр тегов) | `scoring.py::score_item` |
| FR-2.14 (модель в scores.model) | `scoring.py`, `llm.py::score_material` |
| FR-2.15 (кнопка «Оценка неверна») | `callbacks.py` → `RedFeedback` |
| §2.2.5 fallback-цепочка | `llm.py`: OpenRouter-ротация → Ollama → rule-based |
| FR-3.1–3.2 (порог 4.0, ≤30 с) | `scoring.py::_maybe_alert` (алерт сразу после скоринга) |
| FR-3.4–3.5 (формат, хештеги-транслит) | `alerting.py::_alert_html/hashtag/age_text` |
| FR-3.6–3.7 (кнопки, подтверждение, TTL 48 ч) | `alerting.py::_buttons`, `callbacks.py` |
| FR-3.8 (анти-флуд 5/10 мин + сводка) | `alerting.py::send_item_alert/_append_batch` |
| FR-3.10 (1 кластер = 1 алерт) | `scoring.py::_maybe_alert` |
| FR-3.3 (silence-watch 60 мин) | `jobs.py::check_silence` |
| FR-4.1–4.3 (19:00, условный 13:00, <2 → уведомление) | `digest.py::run_digests/build_digest` |
| FR-4.4–4.5 (выборка ≤72 ч, ранжирование) | `digest.py::_candidate_items/_rank` |
| FR-4.8–4.10 (шаблон, 1 LLM-вызов, тон) | `digest.py::_render/_llm_intro_outro` |
| FR-4.11–4.13 (approve-flow, редактор, stale 24 ч) | `digest.py`, `callbacks.py`, `router.py::/digests*`, `RedDigests.tsx` |
| FR-5.1–5.7 (health 6 ч, статус-машина, архивация) | `health.py`, `ingest.py::poll_source` (переходы по результатам сборов) |
| FR-5.5 (диагностика) | `router.py::/sources/{id}/diagnostic`, `RedSources.tsx` |
| FR-7.1–7.4 (retention 72 ч, каскад, агрегаты) | `retention.py` |
| FR-8.1–8.3 (постинг TG/VK, формат, хештеги) | `callbacks.py`, `digest.publish_digest` → общий пайплайн |
| FR-8.7–8.9 (очередь, retry, идемпотентность) | унаследовано от `Post/PostTarget` пайплайна autoposter |
| NFR-17–19 (ToS: официальные API) | Reddit `.json` c UA, TG web-preview, VK API по токену |

## Настройка (для боевого прогона)

1. **LLM**: в `.env` бэкенда — `RED_OPENROUTER_API_KEY=sk-or-...` (free-модели из
   `RED_MODELS`, по умолчанию DeepSeek → Qwen → Llama → openrouter/free), либо
   свой провайдер в настройках профиля (Личный токен LLM в autoposter).
   Fallback: `RED_OLLAMA_BASE_URL=http://localhost:11434/v1`. Без LLM — rule-based
   (система жива, алерты подавлены).
2. **TG-бот**: в настройках autoposter указать bot_token и привязать чат через
   `/start` боту — туда приходят алерты и черновики дайджестов.
3. **Куда публиковать**: «Редакция» → Дайджесты → отметить страницы (TG-канал
   из подключённых аккаунтов, VK-группа) — используются и кнопкой «В канал».
4. **Гайдлайны**: «Редакция» → Гайдлайны — выбрать шаблон, отредактировать,
   сохранить (создаётся версия v1). Теги — для префильтра.
5. **Источники**: «Редакция» → Источники → добавить (RSS/Reddit/Bluesky/TG/VK),
   кнопка «Тестовый сбор» показывает предпросмотр. Поллинг стартует сразу.

## Замена LLM-роутера (OpenRouter → другой провайдер)

Скоринг и дайджесты работают с **любым OpenAI-совместимым API** — вызов один и тот же:
`POST {base_url}/chat/completions`. Роутер — это просто `base_url` + `api_key` + список
`models`, поэтому замена делается конфигом, без правки кода.

**Порядок цепочки вызовов** (ротация на следующий элемент при 429/таймауте/5xx):

1. Личный LLM пользователя — настройки профиля autoposter (base_url/api_key/model);
2. Провайдеры из `RED_LLM_PROVIDERS` — по порядку, каждая модель по списку;
3. Ollama (`RED_OLLAMA_BASE_URL`) — локальный fallback;
4. Rule-based — без LLM, алерты подавляются (система продолжает работать).

**Варианты замены**

*Вариант А — один роутер вместо OpenRouter* (`.env` бэкенда):

```
RED_LLM_PROVIDERS='[{"name":"groq","base_url":"https://api.groq.com/openai/v1","api_key":"gsk_...","models":["llama-3.3-70b-versatile"]}]'
```

*Вариант Б — каскад из нескольких роутеров* (перегоревший сверху автоматически
заменяется нижним):

```
RED_LLM_PROVIDERS='[
  {"name":"openrouter","base_url":"https://openrouter.ai/api/v1","api_key":"sk-or-...",
   "models":["deepseek/deepseek-chat-v3.1:free","qwen/qwen3-235b-a22b:free"]},
  {"name":"groq","base_url":"https://api.groq.com/openai/v1","api_key":"gsk_...",
   "models":["llama-3.3-70b-versatile"]},
  {"name":"deepseek","base_url":"https://api.deepseek.com/v1","api_key":"sk-...",
   "models":["deepseek-chat"]}
]'
```

*Вариант В — legacy (как было изначально)*: `RED_OPENROUTER_API_KEY` + `RED_MODELS`.
Работает, только пока `RED_LLM_PROVIDERS` пуст; заданному списку провайдеров
приоритет отдаётся всегда.

*Вариант Г — без правки .env*: личный LLM в настройках профиля autoposter —
он всегда первый в цепочке (удобно для персонального теста нового провайдера).

**Нюансы**

- не-Bearer авторизация (Azure-стиль `api-key: ...`) — поле `extra_headers` провайдера;
- для legacy-OpenRouter атрибуционные заголовки (`HTTP-Referer`, `X-Title`) ставятся
  автоматически; для кастомных провайдеров — при необходимости через `extra_headers`;
- JSON в env-переменной пишется в одну строку в одинарных кавычках (формат `.env`);
  кривой JSON уронит старт бэкенда — это осознанно (fail fast);
- записи без `base_url` или `models` пропускаются с warning в лог;
- дневной бюджет запросов (`RED_LLM_DAILY_BUDGET`, по умолчанию 45) общий на все
  провайдеры — при исчерпании система уходит в rule-based до конца дня.

**Проверка после замены**: `GET /api/web/red/health` → `llm_providers` (имена и
модели, без ключей) и `llm_personal`; фактический исполнитель пишется в
`red_scores.model` как «имя:модель» (напр. `groq:llama-3.3-70b-versatile`,
`ollama:qwen2.5:7b`, `rule-based`) и виден в ленте материалов.

**Примеры OpenAI-совместимых эндпоинтов**: Groq `https://api.groq.com/openai/v1`,
DeepSeek `https://api.deepseek.com/v1`, Gemini
`https://generativelanguage.googleapis.com/v1beta/openai`,
LiteLLM-proxy `http://localhost:4000/v1`, Requesty `https://router.requesty.ai/v1`.

## Операционный распорядок (как в Приложении А ТЗ)

| Когда | Что | Метка в `app_state` |
|---|---|---|
| каждый тик | поллинг «зрелых» источников, скоринг очереди `new` | `next_poll_at` в red_sources |
| 19:00 (TZ профиля) | дайджест дня | `red_digest19_{uid}` |
| 13:00 (при ≥3 digest-worthy с 07:00) | условный дайджест | `red_digest13_{uid}` |
| слоты 00/06/12/18 | health-check источников | `red_health_{uid}` |
| 02:00 | retention 72 ч + агрегаты | `red_retention_{uid}` |
| каждые 60 мин тишины | «пайплайн молчит» (если включено) | `red_silence_notified` |

## Отложено (приоритет S ТЗ — TODO в коде)

- FR-1.11 рекламный фильтр (is_ad);
- FR-2.4 батчинг ×5 материалов в одном запросе;
- FR-3.9 тихие часы (настройка `RED_*` может быть добавлена без схемы);
- FR-4.14 auto_publish реализован как настройка, но без расписания слотов;
- FR-8.10 отложенный постинг по слотам, FR-8.11 «Отменить пост», VK +2 мин;
- FR-8.14 карточки-заглушки Pillow;
- FR-2.16–2.17 еженедельная сводка фидбека и выгрузка JSONL;
- модуль 6 (еженедельная рубрика) — этап 4 ТЗ.

## Тесты

`backend/tests/test_red_ingest.py`, `test_red_scoring.py`, `test_red_digest.py`,
`test_red_alerts_health.py` — 27 сценариев: нормализация/дедуп/кластеры, префильтр,
валидация JSON скоринга, rule-based, очередь review, ранжирование дайджеста,
анти-флуд, статус-машина источников, retention с каскадом и агрегатами,
публикация дайджеста через общий пайплайн (песочница).
