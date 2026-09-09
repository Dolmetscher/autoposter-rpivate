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

    # тарифы (MVP: без биллинга; смена плана вручную в настройках)
    plans: dict = {
        "free": {"name": "Базовый", "accounts": 3, "posts_month": 100, "sources": 3,
                 "price": "0 ₽"},
        "pro": {"name": "Профи", "accounts": 15, "posts_month": 1000, "sources": 20,
                "price": "999 ₽/мес"},
        "business": {"name": "Ультра", "accounts": 50, "posts_month": 10000, "sources": 100,
                     "price": "2499 ₽/мес"},
    }

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


settings = Settings()
