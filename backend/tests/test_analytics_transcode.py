"""Тесты аналитики, транскодинга и новых адаптеров (Вордпресс, Facebook)."""
import shutil

import pytest

from app.integrations.mock import MockAdapter
from app.integrations.registry import get_adapter
from app.util import utcnow

from .conftest import upload_photo


async def test_analytics_flow(client, auth_headers, mock_pages):
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост для аналитики"}],
        "page_ids": mock_pages[:2],
        "publication_when_type": 1,
    })
    assert r.status_code == 200
    post_id = r.json()["id"]
    assert r.json()["status"] == "published"

    from app.db import SessionFactory
    from app.services import stats as stats_service
    async with SessionFactory() as s:
        collected = await stats_service.update_stats(s)
        await s.commit()
    assert collected == 2  # две цели

    r = await client.get("/api/web/analytics?days=30", headers=auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert data["totals"]["all"]["posts"] == 2
    assert data["totals"]["all"]["views"] > 0  # mock-адаптер даёт ненулевые метрики
    rows = [x for x in data["list"] if x["post_id"] == post_id]
    assert len(rows) == 2
    assert rows[0]["stats_at"] is not None
    assert rows[0]["views"] >= 0
    # статистика есть по сети 99 в by_source
    assert "99" in data["totals"]["by_source"]


async def test_stats_interval_guard(client, auth_headers, mock_pages):
    await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    from app.db import SessionFactory
    from app.services import stats as stats_service
    async with SessionFactory() as s:
        first = await stats_service.update_stats_if_due(s)
        await s.commit()
    assert first >= 1
    async with SessionFactory() as s:
        second = await stats_service.update_stats_if_due(s)
        await s.commit()
    assert second == 0  # час ещё не прошёл


async def test_transcode_requires_video(client, auth_headers):
    photo = await upload_photo(client, auth_headers)
    r = await client.post(f"/api/web/files/{photo['id']}/transcode", headers=auth_headers,
                          json={"mode": "vertical"})
    assert r.status_code == 400
    assert "только для видео" in r.json()["detail"]


async def test_transcode_video(client, auth_headers):
    import io

    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        # без ffmpeg сервис отвечает понятной ошибкой на любой вход
        r = await client.post("/api/web/files/media/upload", headers=auth_headers,
                              files={"file": ("clip.mp4", io.BytesIO(b"\x00" * 128), "video/mp4")})
        vid = r.json()
        r = await client.post(f"/api/web/files/{vid['id']}/transcode", headers=auth_headers,
                              json={"mode": "vertical"})
        assert r.status_code == 400
        assert "ffmpeg" in r.json()["detail"]
        return

    # настоящий крошечный mp4 (фейк из нулей ffmpeg не откроет: moov atom not found)
    import subprocess
    import tempfile
    import os

    with tempfile.TemporaryDirectory() as td:
        gen = os.path.join(td, "gen.mp4")
        subprocess.run(
            [ffmpeg, "-f", "lavfi", "-i", "testsrc=duration=0.3:size=128x128:rate=10",
             "-pix_fmt", "yuv420p", "-y", gen],
            capture_output=True, check=True)
        with open(gen, "rb") as f:
            mp4 = f.read()
    assert len(mp4) > 128

    r = await client.post("/api/web/files/media/upload", headers=auth_headers,
                          files={"file": ("clip.mp4", io.BytesIO(mp4), "video/mp4")})
    vid = r.json()
    r = await client.post(f"/api/web/files/{vid['id']}/transcode", headers=auth_headers,
                          json={"mode": "vertical"})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["type"] == "video" and data["id"] != vid["id"]
    assert data["name"].endswith("_9x16.mp4")


def test_new_adapters_registered():
    fb = get_adapter(3)
    wp = get_adapter(10)
    assert fb.name == "Facebook" and "post" in fb.capabilities
    assert wp.name == "Вордпресс" and "post" in wp.capabilities
    assert fb.connect_requires()[0]["name"] == "access_token"
    names = [f["name"] for f in wp.connect_requires()]
    assert names == ["base_url", "username", "app_password"]


async def test_wordpress_connect_validation(client, auth_headers):
    # неполные креды → понятная ошибка 400
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 10, "credentials": {"base_url": ""}})
    assert r.status_code == 400
    assert "WP" in r.json()["detail"] or "base_url" in r.json()["detail"]


def test_mock_stats_deterministic():
    import asyncio

    async def run():
        from app.integrations.base import PageInfo
        page = PageInfo("m1_blog", "x")
        a = await MockAdapter().fetch_stats(page, "mock_1")
        b = await MockAdapter().fetch_stats(page, "mock_1")
        return a, b
    a, b = asyncio.run(run())
    assert a == b and set(a.keys()) == {"views", "likes", "reposts", "comments"}
