from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Autoposter"
    secret_key: str = "dev-secret-change-me-in-production"
    jwt_expire_days: int = 7

    # sqlite по умолчанию — работает локально без docker; в compose задаётся postgres
    database_url: str = f"sqlite+aiosqlite:///{BASE_DIR / 'data' / 'autoposter.db'}"
    storage_dir: str = str(BASE_DIR / "data" / "storage")

    # inline — планировщик в процессе API (без redis); celery — через celery+redis; off — только тесты
    scheduler_mode: str = "inline"
    tick_interval_seconds: int = 30

    # публичный адрес сервиса: используется адаптерами, которым нужны общедоступные URL медиа
    public_base_url: str = "http://localhost:8000"

    # хранилище медиа: local (диск) | s3 (любой S3-совместимый: MinIO, Yandex Object Storage...)
    media_storage: str = "local"
    s3_endpoint_url: str = ""
    s3_bucket: str = ""
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_region: str = "us-east-1"
    s3_public_base: str = ""  # если бакет публичный: https://cdn.example.com (без bucket в пути — как настроено)
    s3_public_style: str = "path"  # path → base/bucket/key; virtual → base/key

    # мастер-ключ шифрования токенов соцсетей; пусто = производный от secret_key
    credentials_secret: str = ""

    # опрос Telegram-бота-ассистента (сек)
    tg_bot_poll_seconds: int = 6

    # тарифы; price_month/price_year — суммы в рублях для биллинга (год = 10 месяцев)
    plans: dict = {
        "free": {"name": "Базовый", "accounts": 3, "posts_month": 100, "sources": 3,
                 "price": "0 ₽", "price_month": 0, "price_year": 0},
        "pro": {"name": "Профи", "accounts": 15, "posts_month": 1000, "sources": 20,
                "price": "999 ₽/мес", "price_month": 999, "price_year": 9990},
        "business": {"name": "Ультра", "accounts": 50, "posts_month": 10000, "sources": 100,
                     "price": "2499 ₽/мес", "price_month": 2499, "price_year": 24990},
    }

    # --- Биллинг: ЮKassa (https://yookassa.ru) ---
    # shop_id и секретный ключ из личного кабинета; без них оплата недоступна (501),
    # но ручная активация подписок работает
    yookassa_shop_id: str = ""
    yookassa_secret_key: str = ""
    yookassa_api_url: str = "https://api.yookassa.ru/v3"
    billing_return_path: str = "/settings"  # куда вернуть пользователя после оплаты

    rows_limit: int = 20
    max_media_size_mb: int = 100
    crosspost_max_items_per_poll: int = 5
    crosspost_max_media_mb: int = 50

    # OAuth-приложения (если пусто — в UI остаётся только подключение по токену)
    vk_app_id: str = ""
    vk_app_secret: str = ""
    ok_app_id: str = ""
    ok_app_secret: str = ""
    ok_app_public_key: str = ""
    ok_access_token_url: str = "https://api.ok.ru/oauth/token.do"
    google_client_id: str = ""
    google_client_secret: str = ""
    meta_app_id: str = ""
    meta_app_secret: str = ""
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""

    default_rewrite_prompt: str = (
        "Перепиши текст поста другими словами, сохранив смысл, тон, эмодзи, "
        "хэштеги и ссылки. Сделай текст уникальным, но естественным. "
        "Верни только переписанный текст без пояснений."
    )

    # --- «Автоматизированная редакция» (redakciya) ---
    # LLM: OpenRouter по умолчанию (OpenAI-совместимый), ротация free-моделей.
    red_openrouter_api_key: str = ""
    red_openrouter_base_url: str = "https://openrouter.ai/api/v1"
    red_models: list[str] = [
        "deepseek/deepseek-chat-v3.1:free",
        "qwen/qwen3-235b-a22b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "openrouter/free",
    ]
    # Альтернативные LLM-роутеры (замена/каскад вместо OpenRouter): список словарей
    # {"name": "...", "base_url": "https://.../v1", "api_key": "...",
    #  "models": ["..."], "extra_headers": {"заголовок": "значение"}}.
    # Подходит любой OpenAI-совместимый провайдер (Groq, DeepSeek, Gemini,
    # LiteLLM-proxy, Requesty...). Если список пуст — работает legacy-конфиг
    # red_openrouter_api_key + red_models (см. выше).
    red_llm_providers: list[dict] = []
    # Fallback: локальный Ollama (OpenAI-совместимый). Пусто = пропустить.
    red_ollama_base_url: str = ""
    red_ollama_model: str = "qwen2.5:7b"
    red_llm_timeout: int = 30
    red_llm_daily_budget: int = 45  # FR-2.4.1/2.13: без пополнения лимит 50/день
    red_llm_max_tokens: int = 500

    red_alert_threshold: float = 4.0            # FR-3.1
    red_max_alerts_per_10min: int = 5           # FR-3.8
    red_digest_time: str = "19:00"              # FR-4.1, TZ пользователя
    red_noon_digest: bool = True                # FR-4.2 (13:00 при >=3 digest-worthy)
    red_retention_hours: int = 72               # FR-7.2
    red_max_body_chars: int = 10000             # NFR-22
    red_max_media_per_item: int = 4             # FR-1.12
    red_silence_alert_min: int = 60             # FR-3.3, 0 = выключено
    red_degraded_poll_sec: int = 21600          # FR-5.6: degraded поллится раз в 6 ч


settings = Settings()
