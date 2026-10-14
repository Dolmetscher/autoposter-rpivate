# Autoposter — система автопостинга в соцсети (аналог hooppy.ru)

Веб-сервис для одновременной публикации контента в соцсети: автопостинг по расписанию,
кросспостинг из чужих каналов, AI-рерайт текстов и публичный API в формате hooppy.

## Возможности

- **Автопостинг** — посты с фото/видео/документами сразу во все подключённые сети;
  немедленная публикация, отложенная по дате, по расписанию (слоты по дням недели),
  повторы (день/неделя/месяц), автоудаление через N дней/часов, закрепление,
  отдельный текст под конкретную сеть.
- **Кросспостинг** — источники ВК / Telegram / YouTube / **RSS-фиды**: опрос по интервалу,
  скачивание медиа, дедупликация, фильтры по словам и «только с медиа»,
  опциональный AI-рерайт перед публикацией к себе.
- **Telegram-бот-ассистент** — вставьте токен бота в Настройках и отправьте ему `/start`;
  дальше пересылайте боту текст, фото, видео или чужой пост — он создаст черновик
  или опубликует сразу в выбранные «входящие» группы. Туда же приходят уведомления
  об ошибках публикаций (и, по желанию, об успехах).
- **Рерайт (AI)** — любой OpenAI-совместимый провайдер (OpenAI, YandexGPT, GigaChat-шлюз,
  vLLM, Ollama); base_url + ключ + модель + промпт задаются в Настройках.
- **Хранилище медиа** — локальный диск или любой S3-совместимый (MinIO, Yandex Object
  Storage): `MEDIA_STORAGE=s3` + ключи бакета в `.env`, файлы доступны и адаптерам,
  и сетям, качающим медиа по ссылке (Instagram/TikTok).
- **Шифрование секретов** — токены соцсетей, LLM-ключи и конфиг бота шифруются
  в БД (Fernet); мастер-ключ — `CREDENTIALS_SECRET` (по умолчанию производный от `SECRET_KEY`).
- **Публичный API** — пути и схемы ответов как у hooppy (`GET /api/accounts`,
  `GET /api/accounts/pages`, `GET|POST /api/posts`, `DELETE /api/posts/{id}`,
  `POST /api/posts/batch/delete`, `POST /api/files/media/upload`,
  `POST /api/files/documents/upload`, `GET /api/posts/projects`,
  `GET /api/posts/schedules`), авторизация `Authorization: Bearer ap_…`,
  конверт `{list, total_rows, is_has_more, rows_limit}`. Swagger: `/docs`.
- **Аналитика** — метрики опубликованных постов (просмотры/лайки/репосты/комментарии)
  собираются ежечасно для ВК, YouTube, Instagram и песочницы; страница «Аналитика»
  с итогами за 7/30/90 дней и разбивкой по соцсетям.
- **Транскодинг видео** — прямо в редакторе поста кнопки «9:16» (вертикальная копия
  под Shorts/Reels/Клипы/TikTok), «1:1» и «Сжать» (720p); оригинал не заменяется.
  Требуется ffmpeg на сервере (без него функция возвращает понятную ошибку).
- **AI-помощник** — «✨ Написать» (готовый пост по теме) и «# Хэштеги» прямо в редакторе;
  использует настроенный LLM. Подсказка «лучшее время» в планировщике — часы с самой
  высокой средней статистикой просмотров ваших публикаций.
- **Команды и роли** — владелец приглашает пользователей по email (админ / редактор /
  наблюдатель); участники работают с данными команды в рамках роли, наблюдатель —
  только просмотр, редактору недоступны настройки и подключение аккаунтов.
- **Тарифы и биллинг** — Базовый / Профи / Ультра: лимиты аккаунтов, постов в месяц и
  источников кросспостинга. Оплата через **ЮKassa**: checkout с редиректом + публичный
  вебхук `/api/yookassa/webhook` (статус платежа перепроверяется запросом к API,
  идемпотентно), годовая подписка −17 %. Без ключей ЮKassa доступна **ручная
  активация** (оплата переводом) и переключение тарифов владельцем. История подписок —
  в Настройках; истёкшие подписки ежечасно понижаются до Базового планировщиком.
- **Шаблоны постов** — сохраните текст+медиа+настройки как шаблон и применяйте
  в один клик из редактора («Шаблон…» / «💾 В шаблон»).
- **Согласование постов** — включите «Требовать согласование»: редактор отправляет пост,
  админ/владелец одобряет (✓) или отклоняет (✗) прямо в списке постов; публиковать
  ждущий согласования пост может только админ/владелец после одобрения.
- **Проекты** для группировки, **календарь** публикаций, **журнал** событий.

## Поддерживаемые сети

| Сеть | source_id | Что умеет |
|---|---|---|
| ВКонтакте | 1 | посты, фото, видео/клипы, истории, закреп, автоудаление, источники кросспостинга, метрики |
| Одноклассники | 2 | посты с фото/видео, удаление |
| Facebook | 3 | посты на страницы (текст/фото/видео), удаление |
| Твиттер/X | 4 | посты с фото (до 4) и видео через API v2 + OAuth 1.0a, удаление |
| Пинтерест | 8 | пины на доски (фото/видео по ссылке), удаление |
| Instagram (Business) | 6 | фото, карусели, Reels, истории, лайки/комментарии |
| Telegram | 9 | боты: каналы/группы, медиагруппы, документы; аккаунты (Telethon): то же + истории; публичные каналы как источник |
| TikTok | 14 | видео и фото (Content Posting API) |
| ЛинкедИн | 13 | текстовые посты в ленту (Posts API), удаление |
| Макс | 22 | боты: чаты и каналы (Bot API platform-api2.max.ru), медиа, удаление сообщений |
| Рутуб | 15 | видео по ссылке — экспериментально (API выдаёт поддержка Rutube) |
| YouTube | 17 | видео и Shorts; RSS-источник канала; метрики |
| Тредс | 12 | текст, фото, видео, карусели (Threads API, нужен approved app) |
| Тамблер | 19 | текст и фото в блог (API v2, OAuth 1.0a) |
| Вордпресс | 10 | записи с медиатекой через REST API + Application Passwords |
| Дзен | 20 | экспериментально (нет публичного API) |
| Песочница | 99 | имитация публикации — тест всего конвейера без ключей |

Без публичного API публикации (заглушки с понятной ошибкой): Мой Мир (5),
Вайбер (7), Вотсап (16), Яппи (18), Вайбс (21). Всего реализовано 20 сетей.

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

**Слабый сервер (1 vCPU / 1 ГБ RAM / 10 ГБ)?** Используйте
`docker compose -f docker-compose.lean.yml up -d --build` — 2 контейнера
(api + nginx) на SQLite и встроенном планировщике, без PostgreSQL/Redis/Celery.
Подробно — в DEPLOY.md, раздел «Слабый сервер».

## Настройка реальных соцсетей

Ключи задаются в `.env`/compose (OAuth-кнопки) или вводятся в UI при подключении аккаунта.

- **ВКонтакте** — подключение токеном сообщества, без OAuth-приложения:
  в сообществе: Управление → Работа с API → Ключи доступа → создать ключ с правами
  «Управление сообществом», «Фотографии», «Видеозаписи», «Документы», «Истории».
  Вставьте ключ в форму подключения «ВКонтакте»; ссылку на группу или её ID можно
  не указывать — сообщество определится по токену автоматически.
- **Твиттер/X** — Developer Portal (developer.x.com) → Project → Keys and tokens:
  скопируйте API Key, API Key Secret и Access Token/Secret пользователя
  (право tweet.write). Вставьте 4 ключа в форму подключения.
- **Тредс** — приложение Meta с продуктом Threads API (права threads_basic,
  threads_content_publish); токен и Threads User ID — через Graph API Explorer.
- **Тамблер** — api.tumblr.com/console → скопируйте Consumer Key/Secret,
  затем OAuth Token/Secret. В списке подключаются все блоги аккаунта.
- **Telegram (бот)** — токен от @BotFather; добавьте бота админом в каналы/группы
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
