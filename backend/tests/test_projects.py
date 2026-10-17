"""Тесты для Projects: создание/обновление проекта с аккаунтами и публикация поста в проект."""
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

MOCK_SOURCE_ID = 99  # песочница (MockAdapter) — без обращений к внешним сетям


@pytest_asyncio.fixture
async def auth_headers(client):
    await client.post("/api/web/auth/register",
                      json={"email": "user@test.io", "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": "user@test.io", "password": "secret123"})
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def _connect_account(client, headers, nickname: str) -> int:
    r = await client.post("/api/web/accounts/connect", headers=headers,
                          json={"source_id": MOCK_SOURCE_ID,
                                "credentials": {"nickname": nickname}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_create_project_with_accounts(client, auth_headers):
    """Project создается с указанием account_ids, и они привязываются."""
    acc_id = await _connect_account(client, auth_headers, "tester")
    r = await client.post("/api/web/projects", headers=auth_headers,
                          json={"name": "Test project", "settings": {}, "account_ids": [acc_id]})
    assert r.status_code == 201, r.text
    assert r.json()["account_ids"] == [acc_id]
    # Аккаунт другого пользователя привязать нельзя
    await client.post("/api/web/auth/register",
                      json={"email": "other@test.io", "password": "secret123"})
    r_login = await client.post("/api/web/auth/login",
                                json={"email": "other@test.io", "password": "secret123"})
    other = {"Authorization": f"Bearer {r_login.json()['access_token']}"}
    r2 = await client.post("/api/web/projects", headers=other,
                           json={"name": "Steal", "settings": {}, "account_ids": [acc_id]})
    assert r2.status_code == 400


@pytest.mark.asyncio
async def test_update_project_accounts(client, auth_headers):
    """Обновление проекта заменяет список аккаунтов; PUT без поля — сохраняет."""
    r1 = await client.post("/api/web/projects", headers=auth_headers,
                           json={"name": "Proj1", "settings": {}})
    assert r1.status_code == 201, r1.text
    proj_id = r1.json()["id"]

    acc1_id = await _connect_account(client, auth_headers, "tester1")
    r2 = await client.put(f"/api/web/projects/{proj_id}", headers=auth_headers,
                          json={"name": "Proj1", "settings": {}, "account_ids": [acc1_id]})
    assert r2.status_code == 200, r2.text
    assert r2.json()["account_ids"] == [acc1_id]

    # PUT без account_ids не должен стирать привязку
    r3 = await client.put(f"/api/web/projects/{proj_id}", headers=auth_headers,
                          json={"name": "Proj1 renamed", "settings": {}})
    assert r3.status_code == 200
    assert r3.json()["account_ids"] == [acc1_id]

    # Меняем на аккаунт 2
    acc2_id = await _connect_account(client, auth_headers, "tester2")
    r4 = await client.put(f"/api/web/projects/{proj_id}", headers=auth_headers,
                          json={"name": "Proj1", "settings": {}, "account_ids": [acc2_id]})
    assert r4.status_code == 200
    assert r4.json()["account_ids"] == [acc2_id]

    # Пустой список явно отвязывает все аккаунты
    r5 = await client.put(f"/api/web/projects/{proj_id}", headers=auth_headers,
                          json={"name": "Proj1", "settings": {}, "account_ids": []})
    assert r5.status_code == 200
    assert r5.json()["account_ids"] == []

    # Список проектов тоже отдает account_ids
    r6 = await client.get("/api/web/projects", headers=auth_headers)
    assert r6.status_code == 200
    assert all("account_ids" in p for p in r6.json()["list"])


@pytest.mark.asyncio
async def test_create_post_with_project_includes_account_pages(client, auth_headers):
    """Пост с project_id включает страницы из всех аккаунтов проекта."""
    r = await client.post("/api/web/projects", headers=auth_headers,
                          json={"name": "Test project", "settings": {}})
    assert r.status_code == 201, r.text
    proj_id = r.json()["id"]

    acc_id = await _connect_account(client, auth_headers, "tester")
    r2 = await client.put(f"/api/web/projects/{proj_id}", headers=auth_headers,
                          json={"name": "Test project", "settings": {}, "account_ids": [acc_id]})
    assert r2.status_code == 200, r2.text

    from tests.conftest import PNG_BYTES
    file_r = await client.post("/api/web/files/media/upload", headers=auth_headers,
                               files={"file": ("test.png", PNG_BYTES, "image/png")})
    assert file_r.status_code == 200, file_r.text
    photo_id = file_r.json()["id"]
    # Внутренний формат веб-клиента: страницы не выбраны — берутся из проекта
    r3 = await client.post("/api/web/posts", headers=auth_headers, json={
        "publication_when_type": 2,
        "publication_date": "2030-01-01T12:00:00",
        "project_id": proj_id,
        "page_ids": [],
        "texts": [{"text": "Пост в проект", "source_id": 0}],
        "media_ids": [photo_id],
        "status": "scheduled",
    })
    assert r3.status_code == 200, r3.text
    post_id = r3.json()["id"]
    # Все страницы (can_post) аккаунта проекта стали целями поста
    targets = r3.json()["targets"]
    assert len(targets) == 3, f"Expected 3 sandbox pages, got {len(targets)}"
    # Пост попал в список запланированных
    r4 = await client.get("/api/web/posts", headers=auth_headers,
                          params={"status": "scheduled"})
    assert r4.status_code == 200
    post_list = r4.json()["list"]
    post_found = [p for p in post_list if p["id"] == post_id]
    assert len(post_found) == 1, f"Post {post_id} not found in list"