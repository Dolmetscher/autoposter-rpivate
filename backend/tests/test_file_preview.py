"""Превью файлов: /api/web/files/{id}/raw должен работать и с заголовком
Authorization, и с query-параметром ?token= (браузер в <img src> не шлёт заголовок)."""
from httpx import ASGITransport  # noqa: F401


async def _upload(client, auth_headers) -> str:
    r = await client.post("/api/web/files/media/upload",
                          files={"file": ("pic.png", b"\x89PNG\r\n\x1a\nfake", "image/png")},
                          headers=auth_headers)
    assert r.status_code == 200, r.text
    return r.json()["id"]


async def test_raw_file_accepts_token_in_query_param(client, auth_headers):
    """Токен в ?token= даёт 200 — иначе превью в редакторе было пустым."""
    from app.models import User
    from app.security import create_access_token
    from app.db import SessionFactory
    from sqlalchemy import select

    file_id = await _upload(client, auth_headers)

    async with SessionFactory() as s:
        u = (await s.execute(
            select(User).where(User.email == "user@test.io"))).scalar_one()
    token = create_access_token(u.id)

    # без токена — 401
    r = await client.get(f"/api/web/files/{file_id}/raw")
    assert r.status_code == 401
    # с заголовком — 200
    r = await client.get(f"/api/web/files/{file_id}/raw", headers=auth_headers)
    assert r.status_code == 200
    # с query-параметром — 200
    r = await client.get(f"/api/web/files/{file_id}/raw?token={token}")
    assert r.status_code == 200, f"ожидалось 200, получили {r.status_code}: {r.text}"


async def test_raw_file_rejects_invalid_token(client, auth_headers):
    """Чужой/битый токен в query — 401, файл не отдаём."""
    file_id = await _upload(client, auth_headers)
    r = await client.get(f"/api/web/files/{file_id}/raw?token=garbage")
    assert r.status_code == 401
