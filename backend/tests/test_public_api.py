"""Публичный API в формате hooppy: /accounts, /posts, /files/... с Bearer API-токеном."""
from .conftest import upload_photo


async def create_token(client, headers, name="Основной"):
    r = await client.post("/api/web/settings/tokens", headers=headers,
                          json={"name": name})
    assert r.status_code == 201, r.text
    return r.json()["token"]


def auth(token):
    return {"Authorization": f"Bearer {token}"}


async def test_public_api_requires_token(client):
    r = await client.get("/api/accounts")
    assert r.status_code == 401
    r = await client.get("/api/accounts", headers=auth("ap_wrong"))
    assert r.status_code == 401


async def test_public_accounts_and_pages(client, auth_headers):
    await client.post("/api/web/accounts/connect", headers=auth_headers,
                      json={"source_id": 99, "credentials": {}})
    token = await create_token(client, auth_headers)

    r = await client.get("/api/accounts", headers=auth(token))
    assert r.status_code == 200
    data = r.json()
    assert set(data.keys()) == {"list", "total_rows", "is_has_more", "rows_limit"}
    assert data["rows_limit"] == 20
    assert data["total_rows"] == 1
    acc = data["list"][0]
    assert acc["source_id"] == 99

    r = await client.get("/api/accounts/pages", headers=auth(token),
                         params={"source_id": 99})
    assert r.status_code == 200
    assert r.json()["total_rows"] == 3


async def test_public_post_lifecycle(client, auth_headers, mock_pages):
    token = await create_token(client, auth_headers)
    # файл грузим через публичный API
    from .conftest import PNG_BYTES
    r = await client.post("/api/files/media/upload", headers=auth(token),
                          files={"file": ("pub.png", PNG_BYTES, "image/png")})
    assert r.status_code == 200
    photo = r.json()["photo"]
    assert photo["type"] == "photo" and "file_path" in photo

    # создание поста в формате hooppy: отложенная публикация
    r = await client.post("/api/posts", headers=auth(token), json={
        "publication_when_type": 2,
        "publication_how_type": 1,
        "publication_date": "01.01.2030 12:00",
        "selected_pages_ids": mock_pages[:2],
        "texts": [{"text": "Пост через публичный API", "source_id": 0}],
        "attachments": [
            {"type": "photos", "data": [photo]},
            {"type": "settings", "data": {"pin_post": False, "repeat_type": 0}},
        ],
    })
    assert r.status_code == 200, r.text
    post_id = r.json()["id"]

    r = await client.get("/api/posts", headers=auth(token),
                         params={"is_published": 0})
    assert r.status_code == 200
    assert any(p["id"] == post_id for p in r.json()["list"])

    r = await client.get("/api/posts", headers=auth(token),
                         params={"publication_date": "01.01.2030"})
    assert any(p["id"] == post_id for p in r.json()["list"])

    r = await client.delete(f"/api/posts/{post_id}", headers=auth(token))
    assert r.json()["success"] is True
    r = await client.get("/api/posts", headers=auth(token), params={"is_published": 0})
    assert not any(p["id"] == post_id for p in r.json()["list"])


async def test_public_post_now_and_batch_delete(client, auth_headers, mock_pages):
    token = await create_token(client, auth_headers)
    ids = []
    for i in range(2):
        r = await client.post("/api/posts", headers=auth(token), json={
            "publication_when_type": 1,
            "selected_pages_ids": mock_pages[:1],
            "texts": [{"text": f"Немедленный пост {i}", "source_id": 0}],
            "attachments": [],
        })
        assert r.status_code == 200, r.text
        ids.append(r.json()["id"])

    r = await client.get("/api/posts", headers=auth(token), params={"is_published": 1})
    assert {p["id"] for p in r.json()["list"]} >= set(ids)
    assert all(p["targets"][0]["status"] == "published" for p in r.json()["list"]
               if p["id"] in ids)

    r = await client.post("/api/posts/batch/delete", headers=auth(token),
                          json={"ids": ",".join(map(str, ids))})
    assert r.json()["success"] is True
    r = await client.get("/api/posts", headers=auth(token), params={"is_published": 1})
    assert not any(p["id"] in ids for p in r.json()["list"])


async def test_public_projects(client, auth_headers):
    await client.post("/api/web/projects", headers=auth_headers,
                      json={"name": "API проект", "settings": {}})
    token = await create_token(client, auth_headers)
    r = await client.get("/api/posts/projects", headers=auth(token))
    assert r.status_code == 200
    assert r.json()["list"][0]["name"] == "API проект"
