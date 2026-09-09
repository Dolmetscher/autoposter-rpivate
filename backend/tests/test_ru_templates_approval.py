"""Тесты: MAX и Рутуб (регистрация/валидация), шаблоны, согласование постов."""
import pytest

from app.integrations.max import MaxAdapter
from app.integrations.registry import get_adapter
from app.integrations.rutube import RutubeAdapter


def test_new_adapters_registered():
    mx = get_adapter(22)
    rt = get_adapter(15)
    assert mx.name == "Макс" and "channel" in mx.capabilities
    assert rt.name == "Рутуб" and rt.capabilities == ("video",)
    assert [f["name"] for f in mx.connect_requires()] == ["access_token"]
    assert [f["name"] for f in rt.connect_requires()] == ["token"]


async def test_max_connect_requires_token(client, auth_headers):
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 22, "credentials": {}})
    assert r.status_code == 400
    assert "токен" in r.json()["detail"].lower()


async def test_rutube_connect_requires_token(client, auth_headers):
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 15, "credentials": {}})
    assert r.status_code == 400
    assert "Рутуб" in r.json()["detail"] or "токен" in r.json()["detail"].lower()


async def test_templates_crud_and_apply(client, auth_headers, mock_pages):
    photo = (await client.post("/api/web/files/media/upload", headers=auth_headers,
                               files={"file": ("t.png", b"\x89PNG\r\n\x1a\nxxx", "image/png")})).json()
    r = await client.post("/api/web/templates", headers=auth_headers, json={
        "name": "Дежурный анонс",
        "texts": [{"source_id": 0, "text": "Напоминаем: завтра в 12:00 стрим!"}],
        "media_ids": [photo["id"]],
        "settings": {"pin_post": True},
    })
    assert r.status_code == 201, r.text
    tpl_id = r.json()["id"]

    r = await client.get("/api/web/templates", headers=auth_headers)
    assert len(r.json()["list"]) == 1
    assert r.json()["list"][0]["settings"]["pin_post"] is True

    # пост из шаблона публикуется как обычный
    tpl = r.json()["list"][0]
    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": tpl["texts"],
        "media_ids": tpl["media_ids"],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
        "settings": tpl["settings"],
    })
    assert r.status_code == 200 and r.json()["status"] == "published"

    r = await client.delete(f"/api/web/templates/{tpl_id}", headers=auth_headers)
    assert r.json()["success"] is True


async def test_approval_workflow(client, auth_headers, mock_pages):
    editor = await _register_and_login(client, "red@t.io")
    await client.post("/api/web/team/invite", headers=auth_headers,
                      json={"email": "red@t.io", "role": "editor"})

    # редактор запрашивает согласование: пост уходит в черновик со статусом pending
    r = await client.post("/api/web/posts", headers=editor, json={
        "texts": [{"text": "Важный пост на согласование"}],
        "page_ids": mock_pages[:1],
        "publication_when_type": 1,
        "settings": {"approval": {"required": True, "status": "pending"}},
    })
    assert r.status_code == 200, r.text
    post = r.json()
    post_id = post["id"]
    assert post["status"] == "draft"
    assert post["settings"]["approval"]["status"] == "pending"

    # редактор не может ни опубликовать, ни сам себя одобрить
    r = await client.post(f"/api/web/posts/{post_id}/publish", headers=editor)
    assert r.status_code == 403
    # попытка выставить approved самому и запланировать — сбрасывается в pending, 403
    r = await client.put(f"/api/web/posts/{post_id}", headers=editor, json={
        "texts": [{"text": "hack"}], "page_ids": mock_pages[:1],
        "publication_when_type": 2, "publication_date": "01.01.2030 10:00",
        "status": "scheduled",
        "settings": {"approval": {"required": True, "status": "approved"}},
    })
    assert r.status_code == 403
    r = await client.get(f"/api/web/posts/{post_id}", headers=auth_headers)
    assert r.json()["settings"]["approval"]["status"] == "pending"

    # владелец одобряет — пост публикуется редактором
    r = await client.post(f"/api/web/posts/{post_id}/approve", headers=auth_headers,
                          json={"decision": "approved"})
    assert r.status_code == 200
    assert r.json()["settings"]["approval"]["status"] == "approved"

    r = await client.post(f"/api/web/posts/{post_id}/publish", headers=editor)
    assert r.status_code == 200
    assert r.json()["status"] == "published"

    # отклонение: rejected-решение и валидация decision
    r = await client.post("/api/web/posts", headers=editor, json={
        "texts": [{"text": "Ещё один пост"}], "page_ids": [],
        "publication_when_type": 1,
        "settings": {"approval": {"required": True, "status": "pending"}},
    })
    pid2 = r.json()["id"]
    r = await client.post(f"/api/web/posts/{pid2}/approve", headers=auth_headers,
                          json={"decision": "wrong"})
    assert r.status_code == 400
    r = await client.post(f"/api/web/posts/{pid2}/approve", headers=editor,
                          json={"decision": "rejected"})
    assert r.status_code == 403  # редактор не согласовывает


async def _register_and_login(client, email):
    await client.post("/api/web/auth/register",
                      json={"email": email, "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": email, "password": "secret123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
