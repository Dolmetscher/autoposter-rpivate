"""Цели публикации: проект как источник страниц + черновики без целей.

Проект = источник целей: создание и правка поста с проектом разворачивают
его страницы в цели; черновик можно сохранить вообще без целей."""
import pytest

MOCK_SOURCE_ID = 99  # песочница (MockAdapter) — без обращений к внешним сетям


async def _connect_account(client, headers, nickname: str) -> tuple[int, list[int]]:
    r = await client.post("/api/web/accounts/connect", headers=headers,
                          json={"source_id": MOCK_SOURCE_ID,
                                "credentials": {"nickname": nickname}})
    assert r.status_code == 200, r.text
    return r.json()["id"], [p["id"] for p in r.json()["pages"]]


async def _make_project(client, headers, acc_id: int) -> int:
    r = await client.post("/api/web/projects", headers=headers,
                          json={"name": "Proj", "settings": {}, "account_ids": [acc_id]})
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_draft_without_targets(client, auth_headers):
    """Черновик сохраняется без единой страницы и без проекта."""
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Черновик без целей"}],
        "page_ids": [], "status": "draft", "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "draft"
    assert data["targets"] == []


@pytest.mark.asyncio
async def test_scheduled_without_targets_rejected(client, auth_headers):
    """Отложенный/мгновенный пост без страниц и без проекта — 400."""
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Некуда публиковать"}],
        "page_ids": [], "publication_when_type": 1,
    })
    assert r.status_code == 400, r.text
    assert "проект" in r.json()["detail"].lower()


@pytest.mark.asyncio
async def test_project_only_publish(client, auth_headers):
    """Публикация с проектом и без галочек: цели берутся из проекта."""
    acc_id, page_ids = await _connect_account(client, auth_headers, "tester")
    proj_id = await _make_project(client, auth_headers, acc_id)

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост через проект"}],
        "page_ids": [], "project_id": proj_id, "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "published"
    assert {t["page_id"] for t in data["targets"]} == set(page_ids)


@pytest.mark.asyncio
async def test_edit_keeps_project_targets(client, auth_headers):
    """Правка поста с проектом и без галочек не опустошает цели."""
    acc_id, page_ids = await _connect_account(client, auth_headers, "tester")
    proj_id = await _make_project(client, auth_headers, acc_id)

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Черновик с проектом"}],
        "page_ids": [], "project_id": proj_id,
        "status": "draft", "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]
    assert {t["page_id"] for t in r.json()["targets"]} == set(page_ids)

    # правка: цели не передаём вовсе — они должны восстановиться из проекта
    r = await client.put(f"/api/web/posts/{post_id}", headers=auth_headers, json={
        "texts": [{"text": "Черновик с проектом (правка)"}],
        "page_ids": [], "project_id": proj_id,
        "status": "draft", "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    assert {t["page_id"] for t in r.json()["targets"]} == set(page_ids)


@pytest.mark.asyncio
async def test_publish_now_without_targets_rejected(client, auth_headers):
    """«Опубликовать сейчас» у черновика без целей — понятная 400, а не тихий ноль."""
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Черновик"}],
        "page_ids": [], "status": "draft", "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]

    r = await client.post(f"/api/web/posts/{post_id}/publish", headers=auth_headers)
    assert r.status_code == 400, r.text
    assert "публикации" in r.json()["detail"].lower()
