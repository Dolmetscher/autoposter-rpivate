"""Адаптация под слабый сервер: WAL-прагмы, отключаемый транскодинг,
очистка неприкреплённых медиафайлов."""
import io

import pytest


async def test_sqlite_wal_pragma_applied():
    """Прагмы применяются к каждому новому соединению с SQLite."""
    from sqlalchemy import text

    from app.db import engine
    async with engine.connect() as conn:
        mode = (await conn.execute(text("PRAGMA journal_mode"))).scalar()
        busy = (await conn.execute(text("PRAGMA busy_timeout"))).scalar()
        sync = (await conn.execute(text("PRAGMA synchronous"))).scalar()
    assert str(mode).lower() == "wal"
    assert int(busy) == 5000
    # NORMAL = 1 (полная синхронизация 2 не нужна для WAL)
    assert int(sync) == 1


async def test_transcode_disabled_returns_503(client, auth_headers, monkeypatch):
    from app.config import settings

    r = await client.post("/api/web/files/media/upload", headers=auth_headers,
                          files={"file": ("clip.mp4", io.BytesIO(b"\x00" * 64), "video/mp4")})
    vid = r.json()
    monkeypatch.setattr(settings, "transcode_enabled", False)
    r = await client.post(f"/api/web/files/{vid['id']}/transcode", headers=auth_headers,
                          json={"mode": "vertical"})
    assert r.status_code == 503
    assert "TRANSCODE_ENABLED" in r.json()["detail"]

    # включённый флаг сохраняет прежнее поведение: без ffmpeg → 400,
    # с ffmpeg битый файл тоже даёт 400 — важно, что это уже не 503
    monkeypatch.setattr(settings, "transcode_enabled", True)
    r = await client.post(f"/api/web/files/{vid['id']}/transcode", headers=auth_headers,
                          json={"mode": "vertical"})
    assert r.status_code == 400


async def test_cleanup_deletes_orphans_keeps_attached(client, auth_headers, mock_pages,
                                                      monkeypatch):
    from app.config import settings
    from app.services import cleanup as cleanup_service

    # два файла: один старый-осиротевший, второй старый-прикреплённый к посту
    old = await client.post("/api/web/files/media/upload", headers=auth_headers,
                            files={"file": ("old.png", io.BytesIO(b"png1"), "image/png")})
    keep = await client.post("/api/web/files/media/upload", headers=auth_headers,
                             files={"file": ("keep.png", io.BytesIO(b"png2"), "image/png")})
    assert old.status_code == 200 and keep.status_code == 200

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "пост с медиа"}],
        "media_ids": [keep.json()["id"]],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert r.status_code == 200

    from datetime import timedelta

    from app.db import SessionFactory
    from app.models import MediaFile
    old_ts = str(int((__import__("app.util", fromlist=["utcnow"]).utcnow()
                      - timedelta(days=60)).timestamp()))
    async with SessionFactory() as s:
        for fid in (old.json()["id"], keep.json()["id"]):
            mf = await s.get(MediaFile, fid)
            mf.updated_date = old_ts
        await s.commit()

    monkeypatch.setattr(settings, "media_retention_days", 30)
    async with SessionFactory() as s:
        deleted = await cleanup_service.run_cleanup(s)
        await s.commit()
    assert deleted == 1  # только осиротевший

    async with SessionFactory() as s:
        assert await s.get(MediaFile, old.json()["id"]) is None
        assert await s.get(MediaFile, keep.json()["id"]) is not None

    # retention_days=0 — очистка выключена
    monkeypatch.setattr(settings, "media_retention_days", 0)
    async with SessionFactory() as s:
        assert await cleanup_service.cleanup_if_due(s) == 0


async def test_upload_streams_via_tempfile(client, auth_headers):
    """Стриминг не меняет API: файл загружается, размер корректен."""
    payload = b"\x89PNG" + b"0" * 1000
    r = await client.post("/api/web/files/media/upload", headers=auth_headers,
                          files={"file": ("stream.png", io.BytesIO(payload), "image/png")})
    assert r.status_code == 200
    assert r.json()["size"] == len(payload)
    from app.services.storage import local_full
    assert local_full(r.json()["file_path"]).exists()
