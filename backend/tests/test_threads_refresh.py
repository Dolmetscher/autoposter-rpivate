"""Автопродление долгоживущего токена Threads (60 дней).

Токен продлевается перед публикацией, пока ещё действует: refresh_access_token
при возрасте токена больше 20 дней; свежий штамп token_updated_at ставится и
при подключении (fetch_pages)."""
from datetime import timedelta

from app.util import utcnow


class _FakeResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


class _FakeClient:
    """Подменяет httpx.AsyncClient: собирает URL и отдаёт заготовленные ответы."""

    last_calls = []

    def __init__(self, *a, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None, **kw):
        _FakeClient.last_calls.append((url, params))
        if "refresh_access_token" in url:
            return _FakeResp({"access_token": "NEW-TOKEN", "expires_in": 5184000})
        return _FakeResp({"id": "uid", "username": "tester",
                          "threads_profile_picture_url": ""})

    async def post(self, url, data=None, **kw):
        _FakeClient.last_calls.append((url, data))
        return _FakeResp({"id": "container-1"})


def _account(creds):
    class A:
        id = 1

        def __init__(self, c):
            self.credentials = c

    return A(creds)


async def test_refresh_old_token(monkeypatch):
    import httpx as _httpx

    from app.integrations import threads as th

    adapter = th.ThreadsAdapter()
    acc = _account({"access_token": "OLD", "threads_user_id": "uid",
                    "token_updated_at": (utcnow() - timedelta(days=45)).isoformat()})

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = _FakeClient
    try:
        fresh = await adapter.refresh_long_lived_token(acc)
    finally:
        _httpx.AsyncClient = orig

    assert fresh and fresh["access_token"] == "NEW-TOKEN"
    assert fresh["token_updated_at"]
    assert fresh["threads_user_id"] == "uid"  # остальные поля сохранены
    url, params = _FakeClient.last_calls[-1]
    assert "graph.threads.com/refresh_access_token" in url
    assert params["grant_type"] == "th_refresh_token"


async def test_refresh_skips_fresh_token(monkeypatch):
    import httpx as _httpx

    from app.integrations import threads as th

    adapter = th.ThreadsAdapter()
    acc = _account({"access_token": "T", "threads_user_id": "uid",
                    "token_updated_at": (utcnow() - timedelta(days=5)).isoformat()})

    _FakeClient.last_calls = []
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = _FakeClient
    try:
        assert await adapter.refresh_long_lived_token(acc) is None
    finally:
        _httpx.AsyncClient = orig
    assert _FakeClient.last_calls == []  # до API не доходит


async def test_refresh_without_token_is_noop():
    from app.integrations import threads as th

    assert await th.ThreadsAdapter().refresh_long_lived_token(_account({})) is None


async def test_refresh_expired_token_returns_none(monkeypatch):
    """Истёкший токен API продлить не может — возвращаем None без правки кредов."""
    import httpx as _httpx

    from app.integrations import threads as th

    class ExpiredClient(_FakeClient):
        async def get(self, url, params=None, **kw):
            return _FakeResp({"error": {"message": "The access token is invalid"}})

    adapter = th.ThreadsAdapter()
    acc = _account({"access_token": "DEAD", "threads_user_id": "uid",
                    "token_updated_at": (utcnow() - timedelta(days=90)).isoformat()})
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = ExpiredClient
    try:
        assert await adapter.refresh_long_lived_token(acc) is None
    finally:
        _httpx.AsyncClient = orig


async def test_fetch_pages_stamps_token_updated_at(monkeypatch):
    import httpx as _httpx

    from app.integrations import threads as th

    adapter = th.ThreadsAdapter()
    acc = _account({"access_token": "T", "threads_user_id": "uid"})
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = _FakeClient
    try:
        pages = await adapter.fetch_pages(acc)
    finally:
        _httpx.AsyncClient = orig

    assert pages and pages[0].social_page_id == "uid"
    assert acc.credentials["token_updated_at"]


async def test_publish_flow_refreshes_credentials(client, auth_headers, monkeypatch):
    """Конвейер публикации продлевает токен и сохраняет новые креды в БД."""
    import httpx as _httpx
    from sqlalchemy import select

    from app.db import SessionFactory
    from app.integrations import threads as th
    from app.integrations.base import PublishOutcome
    from app.models import SocialAccount, SocialPage, User

    async def fake_publish(self, page, content):
        return PublishOutcome(True, external_id="ext-1",
                              external_url="https://www.threads.com/@t/post/ext-1")

    monkeypatch.setattr(th.ThreadsAdapter, "publish", fake_publish)

    async with SessionFactory() as s:
        user = (await s.execute(
            select(User).where(User.email == "user@test.io"))).scalar_one()
        acc = SocialAccount(user_id=user.id, source_id=12, credentials={
            "access_token": "OLD", "threads_user_id": "uid",
            "token_updated_at": (utcnow() - timedelta(days=45)).isoformat()})
        s.add(acc)
        await s.flush()
        page = SocialPage(user_id=user.id, account_id=acc.id, source_id=12,
                          social_page_id="uid", social_page_name="Тест Threads",
                          page_kind="user", can_post=True)
        s.add(page)
        await s.commit()
        acc_id, page_id = acc.id, page.id

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = _FakeClient
    try:
        r = await client.post("/api/web/posts", headers=auth_headers, json={
            "texts": [{"text": "Пост с продлением токена"}],
            "page_ids": [page_id],
            "publication_when_type": 1,
        })
    finally:
        _httpx.AsyncClient = orig

    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published", r.json()

    async with SessionFactory() as s:
        acc = await s.get(SocialAccount, acc_id)
        assert acc.credentials["access_token"] == "NEW-TOKEN"
        assert acc.credentials["token_updated_at"]
