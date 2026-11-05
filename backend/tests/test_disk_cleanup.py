"""Агрессивная экономия диска (10 ГБ на сервере).

1. Полностью опубликованный пост: физические файлы вложений стираются
   сразу (запись о посте и тексты остаются в базе).
2. MEDIA_RETENTION_DAYS: старые неприкреплённые файлы и файлы
   черновиков/ошибок удаляет фоновая очистка в тике планировщика.
3. Файлы, нужные другим постам/шаблонам/повторам, не трогаются.
"""
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.db import SessionFactory
from app.models import MediaFile, Post, PostAttachment
from app.scheduler import tick_once
from app.services import cleanup
from app.util import utcnow

from .conftest import upload_photo
from .test_dangling_refs import _raw_sql


def _storage_file(rel: str) -> Path:
    return Path(settings.storage_dir) / rel


async def _file_path(media_id: str) -> str:
    async with SessionFactory() as s:
        mf = await s.get(MediaFile, media_id)
        return mf.file_path if mf else ""


def _moscow_dt(delta: timedelta) -> str:
    """Локальное московское время со сдвигом — как его ждёт API."""
    return (utcnow().replace(tzinfo=ZoneInfo("UTC"))
            .astimezone(ZoneInfo("Europe/Moscow")) + delta
            ).strftime("%Y-%m-%dT%H:%M:%S")


async def test_published_post_files_purged(client, auth_headers, mock_pages):
    photo = await upload_photo(client, auth_headers)
    rel = await _file_path(photo["id"])
    assert rel and _storage_file(rel).exists()

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Публикую с фото"}],
        "media_ids": [photo["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    # файл стёрт с диска, запись MediaFile и вложение удалены,
    # сам пост с текстом остался
    assert not _storage_file(rel).exists()
    async with SessionFactory() as s:
        assert await s.get(MediaFile, photo["id"]) is None
        post = (await s.execute(
            select(Post).options(selectinload(Post.texts))
            .where(Post.id == r.json()["id"]))).scalar_one()
        assert post.status == "published"
        assert (await s.execute(select(PostAttachment).where(
            PostAttachment.post_id == post.id))).scalars().all() == []
        assert post.texts and post.texts[0].text == "Публикую с фото"

    # в ответе вложения уже нет — и в журнале запись об экономии
    assert r.json()["media_ids"] == []
    dash = (await client.get("/api/web/dashboard", headers=auth_headers)).json()
    assert any("удалены с диска" in l["message"] for l in dash["logs"])


async def test_repeat_post_keeps_files(client, auth_headers, mock_pages):
    """Повторный пост публикуется снова — его медиа сохраняется."""
    photo = await upload_photo(client, auth_headers)
    rel = await _file_path(photo["id"])

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Повторяющийся пост"}],
        "media_ids": [photo["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 2,
        "publication_date": _moscow_dt(timedelta(seconds=-5)),
        "settings": {"repeat_type": 1},
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]

    await tick_once(SessionFactory)

    async with SessionFactory() as s:
        post = await s.get(Post, post_id)
        assert post.status == "scheduled"  # перезапланирован на завтра
        assert await s.get(MediaFile, photo["id"]) is not None
    assert _storage_file(rel).exists()


async def test_file_shared_with_scheduled_post_is_kept(client, auth_headers, mock_pages):
    """Тот же файл у отложенного поста — после публикации первого поста файл жив."""
    photo = await upload_photo(client, auth_headers)
    rel = await _file_path(photo["id"])

    rb = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Отложенный пост Б"}],
        "media_ids": [photo["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 2,
        "publication_date": _moscow_dt(timedelta(minutes=30)),
    })
    assert rb.status_code == 200, rb.text
    assert rb.json()["status"] == "scheduled"

    ra = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост А"}],
        "media_ids": [photo["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert ra.status_code == 200, ra.text
    assert ra.json()["status"] == "published"
    # вложение осталось: файл нужен посту Б
    assert ra.json()["media_ids"] == [photo["id"]]
    assert _storage_file(rel).exists()
    async with SessionFactory() as s:
        assert await s.get(MediaFile, photo["id"]) is not None


async def test_file_in_template_is_kept(client, auth_headers, mock_pages):
    photo = await upload_photo(client, auth_headers)
    rel = await _file_path(photo["id"])
    rt = await client.post("/api/web/templates", headers=auth_headers, json={
        "name": "Шаблон с фото",
        "texts": [{"text": "шаблон"}],
        "media_ids": [photo["id"]],
        "settings": {},
    })
    assert rt.status_code == 201, rt.text

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост из шаблона"}],
        "media_ids": [photo["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert r.json()["status"] == "published"
    assert _storage_file(rel).exists()
    async with SessionFactory() as s:
        assert await s.get(MediaFile, photo["id"]) is not None


async def test_partial_post_keeps_files(client, auth_headers, mock_pages):
    """Частичная публикация: неудавшуюся цель можно повторить — файл нужен."""
    photo = await upload_photo(client, auth_headers)
    rel = await _file_path(photo["id"])

    # отложенный пост на две страницы; одну страницу удаляем в обход FK
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Частичный пост"}],
        "media_ids": [photo["id"]],
        "page_ids": mock_pages[:2],
        "publication_when_type": 2,
        "publication_date": _moscow_dt(timedelta(minutes=30)),
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]
    _raw_sql(("DELETE FROM social_pages WHERE id = ?", (mock_pages[0],)))

    async with SessionFactory() as s:
        post = await s.get(Post, post_id)
        post.publication_date = utcnow() - timedelta(seconds=1)
        await s.commit()
    await tick_once(SessionFactory)

    async with SessionFactory() as s:
        post = await s.get(Post, post_id)
        assert post.status == "partial"
    assert _storage_file(rel).exists()


async def test_retention_deletes_draft_and_error_files(client, auth_headers, mock_pages,
                                                       monkeypatch):
    monkeypatch.setattr(settings, "media_retention_days", 7)

    old_draft = await upload_photo(client, auth_headers, name="old-draft.png")
    err_post = await upload_photo(client, auth_headers, name="err.png")
    unattached = await upload_photo(client, auth_headers, name="unattached.png")
    fresh_draft = await upload_photo(client, auth_headers, name="fresh.png")
    shared = await upload_photo(client, auth_headers, name="shared.png")

    rel = {name: await _file_path(mf["id"])
           for name, mf in [("old_draft", old_draft), ("err", err_post),
                            ("unattached", unattached), ("fresh", fresh_draft),
                            ("shared", shared)]}

    # старый черновик
    rd = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Старый черновик"}],
        "media_ids": [old_draft["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
        "status": "draft",
    })
    assert rd.json()["status"] == "draft"
    # свежий черновик
    rf = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Свежий черновик"}],
        "media_ids": [fresh_draft["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
        "status": "draft",
    })
    assert rf.json()["status"] == "draft"
    # «ошибочный» пост со старым файлом
    re_ = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Ошибочный пост"}],
        "media_ids": [err_post["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
        "status": "draft",
    })
    # файл одновременно у старого черновика и у запланированного поста — не трогаем
    rs = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Запланирован с общим файлом"}],
        "media_ids": [shared["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 2,
        "publication_date": _moscow_dt(timedelta(minutes=30)),
    })
    assert rs.json()["status"] == "scheduled"
    rd2 = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Черновик с общим файлом"}],
        "media_ids": [shared["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
        "status": "draft",
    })
    assert rd2.json()["status"] == "draft"

    async with SessionFactory() as s:
        d = await s.get(Post, rd.json()["id"])
        d.created_at = utcnow() - timedelta(days=10)
        e = await s.get(Post, re_.json()["id"])
        e.status = "failed"
        e.created_at = utcnow() - timedelta(days=10)
        d2 = await s.get(Post, rd2.json()["id"])
        d2.created_at = utcnow() - timedelta(days=10)
        mf = await s.get(MediaFile, unattached["id"])
        mf.updated_date = str(int((utcnow() - timedelta(days=10)).timestamp()))
        await s.commit()

    async with SessionFactory() as s:
        deleted = await cleanup.cleanup_if_due(s)
        await s.commit()
    assert deleted >= 3

    # стёрты: старый черновик, ошибочный пост, старый неприкреплённый
    for name in ("old_draft", "err", "unattached"):
        assert not _storage_file(rel[name]).exists(), name
    # живы: свежий черновик и файл, нужный запланированному посту
    for name in ("fresh", "shared"):
        assert _storage_file(rel[name]).exists(), name
    async with SessionFactory() as s:
        assert await s.get(MediaFile, old_draft["id"]) is None
        assert await s.get(MediaFile, err_post["id"]) is None
        assert await s.get(MediaFile, unattached["id"]) is None
        assert await s.get(MediaFile, fresh_draft["id"]) is not None
        assert await s.get(MediaFile, shared["id"]) is not None


async def test_retention_disabled_by_default(client, auth_headers):
    """media_retention_days=0 — фоновая очистка ничего не делает."""
    photo = await upload_photo(client, auth_headers)
    async with SessionFactory() as s:
        mf = await s.get(MediaFile, photo["id"])
        mf.updated_date = str(int((utcnow() - timedelta(days=100)).timestamp()))
        await s.commit()
    assert settings.media_retention_days == 0
    async with SessionFactory() as s:
        assert await cleanup.cleanup_if_due(s) == 0
        assert await s.get(MediaFile, photo["id"]) is not None
