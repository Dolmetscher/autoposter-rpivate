"""Тесты на исправления по результатам ревью остальной части проекта:
утечка счётчика ошибок в дашборде, сломанный OAuth-флоу, MissingGreenlet в celery,
обход каталога хранилища, «сиротские» строки при удалении поста, фильтры по дате
в таймзоне пользователя, коллизия file_id, чужие страницы в кросспостинге."""
import pytest

from .conftest import PNG_BYTES, upload_photo


# ---------- дашборд: счётчик ошибок только по своим постам ----------

async def test_dashboard_failed_count_ignores_other_users(client, auth_headers, mock_pages):
    """Пост с ошибкой другого пользователя не попадает в счётчик «failed»."""
    from app.db import SessionFactory
    from app.models import Post, PostTarget, SocialPage

    # создаём пост с упавшей целью от имени второго пользователя
    await client.post("/api/web/auth/register",
                      json={"email": "other@test.io", "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": "other@test.io", "password": "secret123"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.post("/api/web/accounts/connect", headers=other,
                      json={"source_id": 99, "credentials": {}})

    async with SessionFactory() as s:
        page = (await s.execute(
            __import__("sqlalchemy").select(SocialPage).where(
                SocialPage.user_id != 1))).scalars().first()
        assert page is not None
        post = Post(user_id=page.user_id, status="failed")
        s.add(post)
        await s.flush()
        s.add(PostTarget(post_id=post.id, page_id=page.id, source_id=99, status="failed"))
        await s.commit()

    r = await client.get("/api/web/dashboard", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["counts"]["failed"] == 0


# ---------- OAuth ----------

async def test_oauth_start_accepts_token_in_query(client, auth_headers, monkeypatch):
    """Веб-интерфейс уходит на страницу провайдера без заголовков — токен
    передаётся в query и должен приниматься (раньше был 401)."""
    from app.integrations import ok as ok_adapter
    from app.integrations.registry import get_adapter

    adapter = get_adapter(2)
    monkeypatch.setattr(adapter, "oauth_authorize_url",
                        lambda state: f"https://connect.ok.ru/oauth?state={state}")

    token = auth_headers["Authorization"].split(" ", 1)[1]
    r = await client.get(f"/api/web/oauth/ok/start?token={token}",
                         follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    assert "connect.ok.ru" in r.headers["location"]
    assert ok_adapter is not None


async def test_oauth_start_requires_auth(client):
    r = await client.get("/api/web/oauth/ok/start", follow_redirects=False)
    assert r.status_code == 401


async def test_oauth_callback_is_public(client):
    """Колбэк приходит от провайдера без авторизации и должен отвечать редиректом,
    а не 401 (раньше роут попадал под общий guard ролей)."""
    r = await client.get("/api/web/oauth/ok/callback?error=denied",
                         follow_redirects=False)
    assert r.status_code in (302, 307), r.text
    assert "error=denied" in r.headers["location"]


async def test_oauth_callback_bad_state_redirects(client):
    r = await client.get("/api/web/oauth/ok/callback?code=x&state=broken",
                         follow_redirects=False)
    assert r.status_code in (302, 307)
    assert "error=" in r.headers["location"]


# ---------- хранилище: обход каталога ----------

def test_storage_rejects_path_traversal():
    from app.services import storage

    with pytest.raises(ValueError):
        storage.local_full("../secret.txt")
    with pytest.raises(ValueError):
        storage.local_full("posts/../../secret.txt")


# ---------- публичный API: удаление без «сирот» ----------

async def test_public_delete_removes_child_rows(client, auth_headers, mock_pages):
    """После удаления поста не остаётся текстов, вложений и целей публикации."""
    from sqlalchemy import func, select

    from app.db import SessionFactory
    from app.models import PostAttachment, PostTarget, PostText

    r = await client.post("/api/web/settings/tokens", headers=auth_headers,
                          json={"name": "t"})
    token = r.json()["token"]
    hdr = {"Authorization": f"Bearer {token}"}
    up = await upload_photo(client, auth_headers)

    r = await client.post("/api/posts", headers=hdr, json={
        "publication_when_type": 2,
        "publication_date": "01.01.2030 12:00",
        "selected_pages_ids": mock_pages[:1],
        "texts": [{"text": "Пост с вложением", "source_id": 0}],
        "attachments": [{"type": "photos", "data": [up]}],
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]

    async with SessionFactory() as s:
        assert (await s.execute(select(func.count(PostText.id))
                                .where(PostText.post_id == post_id))).scalar_one() == 1
        assert (await s.execute(select(func.count(PostAttachment.id))
                                .where(PostAttachment.post_id == post_id))).scalar_one() == 1
        assert (await s.execute(select(func.count(PostTarget.id))
                                .where(PostTarget.post_id == post_id))).scalar_one() == 1

    r = await client.delete(f"/api/posts/{post_id}", headers=hdr)
    assert r.status_code == 200

    async with SessionFactory() as s:
        assert (await s.execute(select(func.count(PostText.id))
                                .where(PostText.post_id == post_id))).scalar_one() == 0
        assert (await s.execute(select(func.count(PostAttachment.id))
                                .where(PostAttachment.post_id == post_id))).scalar_one() == 0
        assert (await s.execute(select(func.count(PostTarget.id))
                                .where(PostTarget.post_id == post_id))).scalar_one() == 0


# ---------- фильтр по дате в таймзоне пользователя ----------

async def test_posts_filter_by_date_uses_user_timezone(client, auth_headers, mock_pages):
    """Пост на 23:30 по Москве лежит в «московском» дне, а в UTC — в предыдущем."""
    r = await client.put("/api/web/settings", headers=auth_headers,
                         json={"timezone": "Europe/Moscow"})
    assert r.status_code == 200

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Вечерний пост", "source_id": 0}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 2,
        "publication_date": "2026-10-20T23:30:00",
        "status": "scheduled",
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]
    # в БД хранится 20:30 UTC
    assert r.json()["publication_date"].startswith("2026-10-20T20:30")

    r = await client.get("/api/web/posts", headers=auth_headers,
                         params={"date_from": "2026-10-20", "date_to": "2026-10-20"})
    assert r.status_code == 200
    assert any(p["id"] == post_id for p in r.json()["list"]), "пост должен попасть в свой день"

    r = await client.get("/api/web/posts", headers=auth_headers,
                         params={"date_from": "2026-10-19", "date_to": "2026-10-19"})
    assert not any(p["id"] == post_id for p in r.json()["list"])


def test_local_day_bounds_utc():
    from app.schemas import local_day_bounds_utc

    start, end = local_day_bounds_utc("2026-10-20", "Europe/Moscow")
    assert start.isoformat() == "2026-10-19T21:00:00"
    assert end.isoformat() == "2026-10-20T21:00:00"

    start, end = local_day_bounds_utc("2026-10-20", "UTC")
    assert start.isoformat() == "2026-10-20T00:00:00"
    assert end.isoformat() == "2026-10-21T00:00:00"


# ---------- загрузка файлов: занятый file_id ----------

async def test_upload_with_duplicate_file_id_conflicts(client, auth_headers):
    r = await client.post("/api/web/files/media/upload", headers=auth_headers,
                          files={"file": ("a.png", PNG_BYTES, "image/png")},
                          data={"file_id": "my_custom_id"})
    assert r.status_code == 200, r.text
    assert r.json()["id"] == "my_custom_id"

    r = await client.post("/api/web/files/media/upload", headers=auth_headers,
                          files={"file": ("b.png", PNG_BYTES, "image/png")},
                          data={"file_id": "my_custom_id"})
    assert r.status_code == 409, r.text


# ---------- кросспостинг: только свои страницы ----------

async def test_crosspost_rejects_foreign_pages(client, auth_headers, mock_pages):
    """Нельзя указать в целях страницу чужого пользователя."""
    await client.post("/api/web/auth/register",
                      json={"email": "owner@test.io", "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": "owner@test.io", "password": "secret123"})
    other = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post("/api/web/accounts/connect", headers=other,
                          json={"source_id": 99, "credentials": {}})
    foreign_page = r.json()["pages"][0]["id"]

    r = await client.post("/api/web/crosspost", headers=auth_headers, json={
        "source_id": 9, "source_ref": "@channel", "target_page_ids": [foreign_page],
    })
    assert r.status_code == 400, r.text
    assert "не найдены" in r.json()["detail"]

    r = await client.post("/api/web/crosspost", headers=auth_headers, json={
        "source_id": 9, "source_ref": "@channel", "target_page_ids": mock_pages[:1],
    })
    assert r.status_code == 201, r.text


# ---------- celery: публикация без MissingGreenlet ----------

async def test_celery_publish_task_loads_relations(client, auth_headers, mock_pages):
    """Пост, загруженный для публикации «как в celery» (обычный select),
    публикуется без MissingGreenlet — связи подгружены явно."""
    from app.db import SessionFactory
    from app.services import publish as publish_service

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост для celery", "source_id": 0}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 2,
        "publication_date": "2030-01-01T12:00:00",
        "status": "scheduled",
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]

    async with SessionFactory() as s:
        post = await publish_service.load_post_for_publish(s, post_id)
        assert post is not None
        await publish_service.publish_post_now(s, post)
        await s.commit()

    r = await client.get(f"/api/web/posts/{post_id}", headers=auth_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "published", r.text


# ---------- telegram-бот: offset сохраняется в БД ----------

async def test_tgbot_offset_persisted():
    from app.db import SessionFactory
    from app.services import tgbot

    async with SessionFactory() as s:
        assert await tgbot._load_offset(s, "123:ABC") == 0
        await tgbot._save_offset(s, "123:ABC", 42)
        await s.commit()
    async with SessionFactory() as s:
        assert await tgbot._load_offset(s, "123:ABC") == 42
        # другой бот — другое состояние
        assert await tgbot._load_offset(s, "456:XYZ") == 0
