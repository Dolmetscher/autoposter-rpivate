"""Регрессия: «висящие» ссылки на удалённые файлы/страницы.

В SQLite внешние ключи по умолчанию не проверялись, поэтому удаление медиафайла
или страницы оставляло post_attachments / post_targets со ссылкой в пустоту.
Сериализация постов падала с AttributeError (500 на /api/web/posts и /calendar),
а планировщик не мог опубликовать пост — одна удалённая страница рушила весь тик.
"""
import sqlite3
from pathlib import Path

from sqlalchemy import select, text

from app.db import SessionFactory, engine
from app.models import Post, PostAttachment, PostTarget
from app.util import utcnow

from .conftest import upload_photo


def _db_path() -> str:
    return str(Path(str(engine.url.database)))


def _raw_sql(*statements: tuple[str, tuple]) -> None:
    """Выполняет SQL в отдельном соединении с выключенными внешними ключами —
    так можно создать «висящие» строки, как это делала старая версия приложения."""
    con = sqlite3.connect(_db_path())
    try:
        con.execute("PRAGMA foreign_keys=OFF")
        for stmt, params in statements:
            con.execute(stmt, params)
        con.commit()
    finally:
        con.close()


async def _make_post(client, headers, pages, media_ids=None):
    payload = {
        "texts": [{"text": "Пост с вложением"}],
        "page_ids": pages[:1],
        "publication_when_type": 2,
        "publication_date": utcnow().isoformat(),
    }
    if media_ids:
        payload["media_ids"] = media_ids
    r = await client.post("/api/web/posts", headers=headers, json=payload)
    assert r.status_code == 200, r.text
    return r.json()


async def test_deleted_media_file_does_not_break_posts_list(client, auth_headers, mock_pages):
    """Удалённый файл не должен ронять список постов (было 500)."""
    photo = await upload_photo(client, auth_headers)
    post = await _make_post(client, auth_headers, mock_pages, [photo["id"]])
    assert post["media_ids"] == [photo["id"]]

    # удаляем файл — в SQLite без проверки FK остаётся «висящее» вложение
    _raw_sql(("DELETE FROM media_files WHERE id = ?", (photo["id"],)))

    r = await client.get("/api/web/posts", headers=auth_headers)
    assert r.status_code == 200, r.text
    ids = [p["id"] for p in r.json()["list"]]
    assert post["id"] in ids
    # вложение исчезло из выдачи, но пост остался в списке
    found = next(p for p in r.json()["list"] if p["id"] == post["id"])
    assert found["media_ids"] == []

    # и одиночный пост отдаётся без 500
    r = await client.get(f"/api/web/posts/{post['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["id"] == post["id"]


async def test_deleted_page_does_not_break_posts_list(client, auth_headers, mock_pages):
    """Удалённая страница: цель остаётся в посте, но не роняет выдачу."""
    post = await _make_post(client, auth_headers, mock_pages)
    assert post["targets"]

    _raw_sql(("DELETE FROM social_pages WHERE id = ?", (mock_pages[0],)))

    r = await client.get("/api/web/posts", headers=auth_headers)
    assert r.status_code == 200, r.text
    found = next(p for p in r.json()["list"] if p["id"] == post["id"])
    assert len(found["targets"]) == 1
    # имя страницы пустое, но цель не потерялась
    assert found["targets"][0]["page_name"] == ""


async def test_scheduler_survives_deleted_page(client, auth_headers, mock_pages):
    """Удалённая страница не должна рушить весь тик планировщика."""
    post = await _make_post(client, auth_headers, mock_pages)

    _raw_sql(("DELETE FROM social_pages WHERE id = ?", (mock_pages[0],)))
    # подводим пост к сроку
    async with SessionFactory() as s:
        p = (await s.execute(select(Post).where(Post.id == post["id"]))).scalar_one()
        p.publication_date = utcnow()
        await s.commit()

    from app.scheduler import tick_once
    await tick_once(SessionFactory)  # не должно бросить исключение

    r = await client.get(f"/api/web/posts/{post['id']}", headers=auth_headers)
    assert r.status_code == 200, r.text
    # цель помечена ошибкой, а не «упала»
    assert r.json()["targets"][0]["status"] == "failed"
    assert "Страница удалена" in r.json()["targets"][0]["error"]


async def test_publish_target_handles_missing_page(client, auth_headers, mock_pages):
    """publish_target на удалённой странице — ошибка цели, а не исключение."""
    from sqlalchemy.orm import selectinload

    from app.services import publish as publish_service

    post = await _make_post(client, auth_headers, mock_pages)
    _raw_sql(("DELETE FROM social_pages WHERE id = ?", (mock_pages[0],)))

    async with SessionFactory() as s:
        p = (await s.execute(
            select(Post).options(selectinload(Post.targets).selectinload(PostTarget.page))
            .where(Post.id == post["id"]))).scalar_one()
        target = p.targets[0]
        assert target.page is None  # страница исчезла из БД
        await publish_service.publish_target(s, p, target)
        await s.commit()
        assert target.status == "failed"
        assert target.error == "Страница удалена"


async def test_sqlite_enforces_foreign_keys():
    """Внешние ключи включены: удалённые ссылки больше не появляются."""
    async with SessionFactory() as s:
        fk = (await s.execute(text("PRAGMA foreign_keys"))).scalar()
    assert fk == 1, "PRAGMA foreign_keys должен быть включён для SQLite"


async def test_unknown_schedule_id_rejected(client, auth_headers, mock_pages):
    """Несуществующее расписание — 400, а не ошибка целостности (500)."""
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "По несуществующему расписанию"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 3,
        "schedule_id": 999999,
    })
    assert r.status_code == 400, r.text
    assert "not found" in r.json()["detail"]


async def test_unknown_project_id_rejected_on_update(client, auth_headers, mock_pages):
    """Несуществующий проект при обновлении — 400, а не 500."""
    post = await _make_post(client, auth_headers, mock_pages)
    r = await client.put(f"/api/web/posts/{post['id']}", headers=auth_headers, json={
        "texts": [{"text": "Обновление"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 2,
        "publication_date": utcnow().isoformat(),
        "project_id": 999999,
    })
    assert r.status_code == 400, r.text
    assert "не найден" in r.json()["detail"]


async def test_startup_cleanup_removes_dangling_rows(client, auth_headers, mock_pages):
    """_cleanup_dangling убирает «сиротские» строки и чинит цели без страниц."""
    from app.main import _cleanup_dangling

    post = await _make_post(client, auth_headers, mock_pages)
    # удаляем файл и страницу в обход ORM — остаются «висящие» ссылки
    _raw_sql(
        ("DELETE FROM media_files WHERE id = ?", ("no-such-file",)),
        ("DELETE FROM social_pages WHERE id = ?", (mock_pages[0],)),
        ("INSERT INTO post_attachments (post_id, media_file_id, position) "
         "VALUES (?, 'no-such-file', 0)", (post["id"],)),
    )

    async with engine.begin() as conn:
        await _cleanup_dangling(conn)

    async with SessionFactory() as s:
        assert (await s.execute(select(PostAttachment).where(
            PostAttachment.media_file_id == "no-such-file"))).scalars().all() == []
        target = (await s.execute(select(PostTarget).where(
            PostTarget.post_id == post["id"]))).scalar_one()
        assert target.status == "failed"
        assert target.error == "Страница удалена"
