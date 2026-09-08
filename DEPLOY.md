# Деплой Autoposter на бесплатный хостинг

## Что куда можноdeployить — краткая матрица

| Хостинг | Что умеет для этого проекта | Бесплатно? |
|---|---|---|
| **GitHub** | только код (репозиторий). Приложение НЕ запускает | да |
| **Netlify / Vercel / GitHub Pages** | только фронтенд (статика). Бэкенд Python не умеют | да |
| **Render.com** | ✅ бэкенд FastAPI + статика фронта; сон после 15 мин простоя | да (free) |
| **Neon / Supabase** | ✅ бесплатный PostgreSQL к бэкенду | да |
| **cron-job.org** | пинг `healthz`, чтобы бесплатный Render не засыпал | да |
| **Fly.io** | ✅ весь docker-стек с постоянным диском | ~$2-3/мес после триала |
| **Oracle Cloud Always Free** | ✅ полноценная ВМ 24/7, весь docker-compose | да, навсегда (нужна карта) |

**Рекомендация:** для быстрого публичного демо — вариант A (Render + Neon + Netlify).
Для постоянной работы 24/7 с реальными соцсетями — вариант B (Oracle VM) или любой VPS за ~150-300 ₽/мес.

---

## Шаг 0. Выложить код на GitHub

```bash
cd autoposter
git init
git add .
git commit -m "Autoposter: автопостинг в соцсети"
# создайте репозиторий на github.com (Private — код содержит ваши будущие ключи в .env,
# но .env в git не попадает, он в .gitignore)
git remote add origin https://github.com/ВАШ_ЛОГИН/autoposter.git
git branch -M main
git push -u origin main
```

---

## Вариант A (рекомендуемый для старта): Render + Neon + Netlify

### A1. База данных — Neon (бесплатный Postgres)

1. [neon.tech](https://neon.tech) → регистрация → New Project.
2. Скопируйте **Connection string** вида
   `postgresql://user:pass@host/dbname?sslmode=require`
3. Для приложения он записывается так:
   `postgresql+asyncpg://user:pass@host/dbname?sslmode=require`
   (префикс `postgresql+asyncpg`!)

### A2. Бэкенд — Render

1. [render.com](https://render.com) → New → **Blueprint** → выберите репозиторий
   (проект уже содержит `render.yaml`).
2. В дашборде у сервиса `autoposter-api` впишите Environment:
   - `DATABASE_URL` = строка Neon (см. A1) — замените заглушку из blueprint;
   - `PUBLIC_BASE_URL` = `https://имя-сервиса.onrender.com` (URL появится после первого деплоя — впишите и задеплойте ещё раз);
   - при желании ключи соцсетей (`VK_APP_ID` и т.п.).
3. Готово: API и Swagger доступны по `https://имя-сервиса.onrender.com/docs`.

⚠️ Особенности бесплатного Render:
- сервис засыпает после ~15 мин без запросов → планировщик останавливается,
  отложенные посты выходят с опозданием. Лечение — A4 (пинг).
- диск эфемерный: SQLite и загруженные файлы пропадают при каждом деплое,
  поэтому обязательно Postgres от Neon (сам код БД — в Postgres, уцелеет).
  Медиафайлы при пересборке теряются — для демо ок, для продакшена см. вариант B.

### A3. Фронтенд — Netlify (или static site на Render)

Netlify:
1. [netlify.com](https://netlify.com) → Add new site → Import from Git → репозиторий.
2. Файл `netlify.toml` уже настроен (base `frontend`, publish `dist`).
3. В `netlify.toml` замените `ВАШ-БЭКЕНД.onrender.com` на реальный URL из A2 — и запушьте.
4. Сайт получит адрес `https://что-то.netlify.app`. Свой домен: Domain settings → Add domain.

Если хотите всё в одном месте — вместо Netlify используйте static-сервис
`autoposter-front` из `render.yaml` (прокси `/api` уже прописан в routes).

### A4. Не давать бэкенду засыпать — cron-job.org

1. [cron-job.org](https://cron-job.org) → Create cronjob.
2. URL: `https://имя-сервиса.onrender.com/healthz`, каждые 10 минут.
3. Планировщик и отложенные посты теперь работают круглосуточно (лимит free-типа
   Render 750 ч/мес, один сервис 24/7 = ~720 ч — вписывается).

### A5. Соцсети

- Instagram/TikTok: в `PUBLIC_BASE_URL` должен быть публичный HTTPS-URL — теперь он есть.
- OAuth redirect'ы: `https://имя-сервиса.onrender.com/api/web/oauth/vk/callback` и т.д.

---

## Вариант B: полноценный продакшен — Oracle Cloud Always Free (или любой VPS)

Always Free: 2 ВМ ARM (4 ядра/24 ГБ суммарно) навсегда бесплатно. Нужна карта для регистрации.

1. Создайте ВМ Ubuntu (Ampere A1, shape 2.4), откройте порты 80/443 в Security List.
2. На ВМ:

```bash
sudo apt update && sudo apt install -y docker.io docker-compose-plugin git
git clone https://github.com/ВАШ_ЛОГИН/autoposter.git
cd autoposter
# сборка фронта
cd frontend && npm install && npm run build && cd ..
# переменные
cat > .env <<'EOF'
SECRET_KEY=длинная-случайная-строка
PUBLIC_BASE_URL=https://ваш-домен
VK_APP_ID=
VK_APP_SECRET=
EOF
sudo docker compose up -d --build   # nginx(:80) + api + worker + postgres + redis
```

3. Бесплатный домен: [duckdns.org](https://duckdns.org) (ваше-имя.duckdns.org → IP ВМ)
   или Cloudflare (бесплатные DNS + свой купленный домен).
4. HTTPS: `sudo apt install -y certbot python3-certbot-nginx && sudo certbot --nginx`
   (или добавьте Caddy в compose вместо nginx).

Здесь всё по-взрослому: PostgreSQL и файлы на диске ВМ, Celery+Redis, https,
собственный домен — и никаких снов/сбросов данных.

---

## Вариант C: Fly.io (быстрый компромисс)

Умеет docker + постоянный volume (файлы не теряются), ~$2-3/мес после триала:

```bash
fly launch --dockerfile backend/Dockerfile --name autoposter-api
fly volumes create data --size 3
fly secrets set SECRET_KEY=... DATABASE_URL=sqlite+aiosqlite:///app/data/storage/../autoposter.db PUBLIC_BASE_URL=https://autoposter-api.fly.dev
fly deploy
```

Фронт — туда же через Dockerfile с nginx (см. `nginx.conf`), либо Netlify как в A3.

---

## Чек-лист после деплоя

- [ ] открывается `https://.../` — форма входа, регистрация работает;
- [ ] `https://.../docs` — Swagger бэкенда;
- [ ] в Settings создан API-токен, `curl -H "Authorization: Bearer ap_..." .../api/accounts` отвечает;
- [ ] подключена «Песочница» и опубликован тестовый пост;
- [ ] `PUBLIC_BASE_URL` совпадает с реальным адресом (важно для Instagram/TikTok/OAuth);
- [ ] cron-job (вариант A) пингует `healthz`;
- [ ] `SECRET_KEY` — длинная случайная строка (не дефолтная!).
