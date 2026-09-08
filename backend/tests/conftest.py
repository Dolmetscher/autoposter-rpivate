import os
import shutil
import tempfile
from pathlib import Path

# Переменные окружения задаются ДО импорта app.*
_TMP = Path(tempfile.mkdtemp(prefix="autoposter_test_"))
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TMP / 'test.db'}"
os.environ["STORAGE_DIR"] = str(_TMP / "storage")
os.environ["SCHEDULER_MODE"] = "off"
os.environ["PUBLIC_BASE_URL"] = "http://testserver"
os.environ["SECRET_KEY"] = "test-secret"

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402

from app.db import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


@pytest_asyncio.fixture(autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest_asyncio.fixture
async def auth_headers(client):
    await client.post("/api/web/auth/register",
                      json={"email": "user@test.io", "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": "user@test.io", "password": "secret123"})
    token = r.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def mock_pages(client, auth_headers):
    """Подключённый аккаунт песочницы + id страниц."""
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 99, "credentials": {"nickname": "tester"}})
    assert r.status_code == 200, r.text
    pages = r.json()["pages"]
    return [p["id"] for p in pages]


PNG_BYTES = (b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
             b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
             b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82")


async def upload_photo(client, headers, name="photo.png"):
    r = await client.post("/api/web/files/media/upload", headers=headers,
                          files={"file": (name, PNG_BYTES, "image/png")})
    assert r.status_code == 200, r.text
    return r.json()
