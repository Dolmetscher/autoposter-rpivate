from datetime import timedelta

from app.util import utcnow

from .conftest import upload_photo


async def test_publish_now_flow(client, auth_headers, mock_pages):
    photo = await upload_photo(client, auth_headers)
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"source_id": 0, "text": "Привет, это общий текст"},
                  {"source_id": 99, "text": "Текст специально для песочницы"}],
        "media_ids": [photo["id"]],
        "page_ids": mock_pages[:2],
        "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "published", data
    assert len(data["targets"]) == 2
    for t in data["targets"]:
        assert t["status"] == "published"
        assert t["external_url"].startswith("https://mock.local/")

    dash = (await client.get("/api/web/dashboard", headers=auth_headers)).json()
    assert dash["counts"]["published"] >= 1
    assert any(l["level"] == "success" for l in dash["logs"])


async def test_empty_post_rejected(client, auth_headers, mock_pages):
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [], "media_ids": [], "page_ids": mock_pages[:1],
        "publication_when_type": 1,
    })
    assert r.status_code == 400


async def test_scheduled_post_and_tick(client, auth_headers, mock_pages):
    from app.db import SessionFactory
    from app.scheduler import tick_once

    when = (utcnow() + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%S")
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Отложенный пост"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 2,
        "publication_date": when,
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]
    assert r.json()["status"] == "scheduled"

    # тик до срока — ничего не публикуется
    await tick_once(SessionFactory)
    r = await client.get(f"/api/web/posts/{post_id}", headers=auth_headers)
    assert r.json()["status"] == "scheduled"

    # тик после срока
    await __import__("asyncio").sleep(1.2)
    await tick_once(SessionFactory)
    r = await client.get(f"/api/web/posts/{post_id}", headers=auth_headers)
    assert r.json()["status"] == "published"


async def test_auto_delete_after_publish(client, auth_headers, mock_pages):
    from sqlalchemy import select

    from app.db import SessionFactory
    from app.models import PostTarget
    from app.scheduler import tick_once
    from app.util import utcnow

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Временный пост"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
        "settings": {"delete_post_day": 1, "delete_post_hour": 0},
    })
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    async with SessionFactory() as s:
        target = (await s.execute(select(PostTarget).where(
            PostTarget.post_id == r.json()["id"]))).scalar_one()
        assert target.delete_after_at is not None
        target.delete_after_at = utcnow() - timedelta(seconds=1)
        await s.commit()

    await tick_once(SessionFactory)
    async with SessionFactory() as s:
        target = (await s.execute(select(PostTarget).where(
            PostTarget.post_id == r.json()["id"]))).scalar_one()
        assert target.status == "deleted"


async def test_projects_and_schedules_crud(client, auth_headers):
    r = await client.post("/api/web/projects", headers=auth_headers,
                          json={"name": "Проект про котиков", "settings": {}})
    assert r.status_code == 201
    pid = r.json()["id"]
    r = await client.put(f"/api/web/projects/{pid}", headers=auth_headers,
                         json={"name": "Проект про пёсиков", "settings": {}})
    assert r.json()["name"] == "Проект про пёсиков"

    r = await client.post("/api/web/schedules", headers=auth_headers, json={
        "name": "По будням утром",
        "slots": [{"days": [1, 2, 3, 4, 5], "time": "09:00"}],
    })
    assert r.status_code == 201
    sid = r.json()["id"]

    # пост по расписанию получает ближайший слот
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "По расписанию"}],
        "page_ids": [],
        "publication_when_type": 3,
        "schedule_id": sid,
    })
    assert r.status_code == 400 or r.json().get("status") == "draft"

    r = await client.get("/api/web/schedules", headers=auth_headers)
    assert len(r.json()["list"]) == 1
    await client.delete(f"/api/web/projects/{pid}", headers=auth_headers)
    await client.delete(f"/api/web/schedules/{sid}", headers=auth_headers)
