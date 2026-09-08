# Autoposter — система автопостинга в соцсети (аналог hooppy.ru)

Веб-сервис для одновременной публикации контента в соцсети: автопостинг по расписанию,
кросспостинг из чужих каналов, AI-рерайт текстов и публичный API в формате hooppy.

## Возможности

- **Автопостинг** — посты с фото/видео/документами сразу во все подключённые сети;
  немедленная публикация, отложенная по дате, по расписанию (слоты по дням недели),
  повторы (день/неделя/месяц), автоудаление через N дней/часов, закрепление,
  отдельный текст под конкретную сеть.
- **Кросспостинг** — источники ВК / Telegram / YouTube: опрос по интервалу,
  скачивание медиа, дедупликация, фильтры по словам и «только с медиа»,
  опциональный AI-рерайт перед публикацией к себе.
- **Рерайт (AI)** — любой OpenAI-совместимый провайдер (OpenAI, YandexGPT, GigaChat-шлюз,
  vLLM, Ollama); base_url + ключ + модель + промпт задаются в Настройках.
- **Публичный API** — пути и схемы ответов как у hooppy (`GET /api/accounts`,
  `GET /api/accounts/pages`, `GET|POST /api/posts`, `DELETE /api/posts/{id}`,
  `POST /api/posts/batch/delete`, `POST /api/files/media/upload`,
  `POST /api/files/documents/upload`, `GET /api/posts/projects`,
  `GET /api/posts/schedules`), авторизация `Authorization: Bearer ap_…`,
  конверт `{list, total_rows, is_has_more, rows_limit}`. Swagger: `/docs`.
- **Проекты** для группировки, **календарь** публикаций, **журнал** событий.

## Поддерживаемые сети

| Сеть | source_id | Что умеет |
|---|---|---|
| ВКонтакте | 1 | посты, фото, видео/клипы, истории, закреп, автоудаление, источники кросспостинга |
| Одноклассники | 2 | посты с фото/видео, удаление |
| Instagram (Business) | 6 | фото, карусели, Reels, истории |
| Telegram | 9 | боты: каналы/группы, медиагруппы, документы; аккаунты (Telethon): то же + истории; публичные каналы как источник |
| TikTok | 14 | видео и фото (Content Posting API) |
| YouTube | 17 | видео и Shorts; RSS-источник канала |
| Дзен | 20 | экспериментально (нет публичного API) |
| Песочница | 99 | имитация публикации — тест всего конвейера без ключей |

Facebook/X/Pinterest/Threads/LinkedIn/WordPress/Viber/WhatsApp/Rutube/Яппи/Тамблер/Вайбс/Макс/
Мой Мир — заявлены в UI, адаптеры добавляются по тому же интерфейсу `SocialAdapter`.

## Приложение для Android (Capacitor)

Фронтенд оборачивается в нативное приложение тем же способом, что и SPA: веб-сборка
кладётся в WebView (Capacitor 6), все данные ходят по API на бэкенд.

### Что нужно
- JDK 17 и Android SDK (как у проектов english-app/serials-app: `.tools/jdk-17...`,
  `.tools/android-sdk`); пути прописываются в `frontend/android/local.properties`.
- Телефон/эмулятор в той же сети, что и бэкенд (для теста), либо задеплоенный HTTPS-бэкенд.

### Сборка APK
```bash
cd frontend

# 1. собрать веб-бандл с адресом бэкенда и синхронизировать в Android-проект
#    локальный тест (телефон в одной Wi-Fi сети с ПК):
set VITE_API_BASE=http://192.168.1.106:8000        # Windows cmd (ваш LAN IP)
npm run build:android

#    продакшен (задеплоенный бэкенд):
#    set VITE_API_BASE=https://autoposter-api.onrender.com  → npm run build:android

# 2. собрать APK
npm run android:apk
# → frontend/android/app/build/outputs/apk/debug/app-debug.apk
```

Проще управлять из Android Studio: `npm run android:open` (эмулятор, иконки, подпись).

### Важные детали
- `VITE_API_BASE` вшивается в бандл на шаге сборки — при смене адреса бэкенда
  пересоберите APK (`npm run build:android && npm run android:apk`).
- Для теста по HTTP в манифесте включён `usesCleartextTraffic="true"`.
  В релизе с HTTPS-бэкендом эту строку можно убрать из
  `android/app/src/main/AndroidManifest.xml`.
- Релиз-подпись: `android/app/build.gradle` → signingConfigs, либо `npm run android:apk:release`
  после настройки keystore.
- CORS включён на бэкенде (`allow_origins=["*"]`) — WebView-оригин `https://localhost`
  проходит без дополнительных настроек.

## Быстрый старт (локально, без docker)

```bash
# backend
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt       # Windows
# source .venv/bin/activate && pip install -r requirements.txt  # Linux/macOS
copy .env.example .env
.venv\Scripts\python -m uvicorn app.main:app --port 8000

# frontend (в другом терминале)
cd frontend
npm install
npm run dev            # http://localhost:5173 (прокси на :8000)
```

SQLite + встроенный планировщик (`SCHEDULER_MODE=inline`) — ключи соцсетей не нужны:
зарегистрируйтесь и подключите «Песочницу», чтобы проверить весь конвейер.

## Продакшен (docker)

```bash
cd frontend && npm run build && cd ..
docker compose up -d --build     # nginx:80, api:8000, postgres, redis, worker
```

Очередь публикаций — Celery+Redis (`SCHEDULER_MODE=celery`), БД — PostgreSQL.

## Настройка реальных соцсетей

Ключи задаются в `.env`/compose (OAuth-кнопки) или вводятся в UI при подключении аккаунта.

- **ВКонтакте** — создайте приложение на dev.vk.com; права: wall, photos, video, stories,
  groups, offline. Либо вставьте access token пользователя/сообщества в UI.
  Redirect URI: `https://ваш-домен/api/web/oauth/vk/callback`.
- **Telegram (бот)** — токен от @BotFather; добавьте бта админом в каналы/группы
  и перечислите их при подключении (`@channel` или `-100…`).
- **Telegram (аккаунт)** — api_id/api_hash с my.telegram.org + session-строка Telethon
  (нужны истории и личные диалоги).
- **Одноклассники** — приложение на apiok.ru; при OAuth нужны access_token,
  application_key, session_secret_key.
- **YouTube** — проект Google Cloud с включённым YouTube Data API v3, OAuth refresh token
  со scope `youtube.upload`.
- **Instagram** — Business-аккаунт, привязанный к странице Facebook; долгоживущий
  token + Instagram Business Account ID. `PUBLIC_BASE_URL` должен быть доступен
  из интернета (Meta качает медиа по ссылке).
- **TikTok** — приложение TikTok for Developers с продуктами Content Posting API
  (video/photo, DIRECT_POST); токен пользователя.
- **Дзен** — эксперимент: cookie сессии; публикация через API недоступна, адаптер
  вернёт понятную ошибку.

## Публичный API — примеры

```bash
TOKEN="ap_..."   # Настройки → API-токены

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/accounts

curl -X POST http://localhost:8000/api/posts \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{
    "publication_when_type": 2,
    "publication_date": "01.12.2026 10:00",
    "selected_pages_ids": [822454, 22543],
    "texts": [{"text": "Привет!", "source_id": 0}],
    "attachments": [{"type": "settings", "data": {"pin_post": true}}]
  }'
```

Формат дат: `дд.мм.гггг[ ЧЧ:ММ]`; `publication_when_type`: 1 сейчас, 2 по дате,
3 по расписанию (`schedules_ids`), 4 проект (`project_id`).

## Архитектура

```
backend/app
├── api/web/       REST для SPA (JWT)
├── api/public/    публичный API hooppy-формы (Bearer ap_…)
├── integrations/  адаптеры соцсетей — единый интерфейс SocialAdapter
│                  (fetch_pages / publish / delete / fetch_source_items)
├── services/      публикация, кросспостинг, рерайт, файлы, аккаунты
├── scheduler.py   inline-планировщик (тик каждые 30 с)
└── celery_app.py  celery для продакшена
frontend/src/pages  Dashboard, Posts, PostEditor, Calendar, Accounts,
                   Projects, Schedules, Crosspost, Settings
tests/              pytest: авторизация, конвейер публикации, расписание,
                   автоудаление, кросспостинг+дедупликация, рерайт, публичный API
```

## Тесты

```bash
cd backend && .venv\Scripts\python -m pytest -q
```

## Ограничения MVP

- Дзен — экспериментальный адаптер (публикация отключена).
- Instagram/TikTok требуют публично доступного `PUBLIC_BASE_URL`.
- Кросспостинг из ВК скачивает фото (видео ВК отдаёт только своим приложениями).
- Rate-limit сетей не учитывается ретраями Celery при inline-режиме.
