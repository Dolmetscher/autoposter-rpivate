"""Личные страницы и репост из пабликов/групп:

- VK: пост на личную страницу — wall.post с положительным owner_id и
  from_group=0 (ключ пользователя), фото без group_id.
- OK: пост на личную страницу — mediatopic.post type=USER без gid.
- FB: публикация на личный профиль запрещена API (Meta, 2018) — понятная
  ошибка вместо невнятного ответа Graph.
- FB-источник кросспостинга: лента своей страницы (/{page-id}/posts),
  чужие паблики — закрытая фича Page Public Content Access.
- OK-источник: лента своего паблика — group.getStatTopics (ID и время
  топиков, доступно владельцу/администратору) + mediatopic.getByIds
  (содержимое); чужие группы ОК не отдаёт.
- Отложенный репост: источник с delay_minutes создаёт пост «scheduled»
  со сроком «сейчас + N минут», планировщик публикует его в срок.
"""
from types import SimpleNamespace

import pytest

from app.integrations.base import AdapterError, PublishContent, SourceItem
from app.integrations.facebook import FacebookAdapter
from app.integrations.mock import MockAdapter
from app.integrations.ok import OKAdapter
from app.integrations.vk import VKAdapter
from app.util import utcnow


class _RawResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def _install(monkeypatch, client):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: client)


# ---------- VK: личная страница ----------

async def test_vk_personal_page_publish(monkeypatch):
    """Пост на личную страницу: owner_id положительный, from_group=0,
    фото загружается без group_id (альбом пользователя)."""
    calls = []

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **kw):
            calls.append((url, data))
            if "photos.getWallUploadServer" in url:
                assert "group_id" not in (data or {}), \
                    "для личной страницы group_id быть не должно"
                return _RawResp({"response": {
                    "upload_url": "https://pu.vk.com/upload.php"}})
            if "upload.php" in url:
                return _RawResp({"server": 1, "photo": '[{"photo":"x","sizes":[]}]',
                                 "hash": "h"})
            if "photos.saveWallPhoto" in url:
                return _RawResp({"response": [{"id": 456, "owner_id": 777}]})
            if "wall.post" in url:
                return _RawResp({"response": {"post_id": 42}})
            return _RawResp({"response": 1})

    _install(monkeypatch, FakeClient())

    async def fake_read(media):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("app.integrations.vk.read_media_bytes", fake_read)

    class Acc:
        credentials = {"access_token": "user-token"}

    page = SimpleNamespace(account=Acc(), social_page_id="777",
                           page_kind="user", account_id=1)
    media = SimpleNamespace(name="snap.png", file_path="media/p.png",
                            type="photo", title="", description="")
    out = await VKAdapter().publish(
        page, PublishContent(text="В личную ленту", media=[media]))

    assert out.ok
    assert out.external_url == "https://vk.com/wall777_42"
    wall = next(d for u, d in calls if "wall.post" in u)
    assert wall["owner_id"] == 777
    assert wall["from_group"] == 0
    assert wall["attachments"] == "photo777_456"
    assert wall["access_token"] == "user-token"


# ---------- OK: личная страница ----------

async def test_ok_personal_page_publish(monkeypatch):
    """Пост в ленту профиля: mediatopic.post с type=USER и без gid."""
    captured = {}

    async def fake_call(self, method, creds, extra_params=None):
        captured["method"] = method
        captured["params"] = extra_params or {}
        return "987654321"

    monkeypatch.setattr(OKAdapter, "call", fake_call)

    class Acc:
        credentials = {"access_token": "t", "application_key": "k",
                       "session_secret_key": "s"}

    page = SimpleNamespace(account=Acc(), social_page_id="70001",
                           page_kind="user", account_id=1)
    out = await OKAdapter().publish(
        page, PublishContent(text="Личная новость ОК"))

    assert out.ok
    assert captured["method"] == "mediatopic.post"
    assert captured["params"]["type"] == "USER"
    assert "gid" not in captured["params"]
    assert out.external_url == "https://ok.ru/profile/70001/987654321"


# ---------- Facebook: личный профиль ----------

async def test_fb_personal_profile_clear_error():
    """Публикация в личный профиль запрещена Meta — понятная ошибка, а не
    загадочный ответ Graph API."""
    class Acc:
        credentials = {"access_token": "user-token"}

    page = SimpleNamespace(account=Acc(), social_page_id="100001",
                           page_kind="user", account_id=1)
    with pytest.raises(AdapterError) as ei:
        await FacebookAdapter().publish(
            page, PublishContent(text="Привет, профиль"))
    msg = str(ei.value)
    assert "личную страницу" in msg
    assert "2018" in msg  # год, когда Meta закрыла публикацию в профили


def test_fb_parse_page_ref():
    parse = FacebookAdapter._parse_page_ref
    assert parse("123456789") == "123456789"
    assert parse("https://www.facebook.com/mypage") == "mypage"
    assert parse("facebook.com/mypage") == "mypage"
    assert parse("https://www.facebook.com/pages/My-Page/123456") == "123456"
    assert parse("https://www.facebook.com/profile.php?id=10000123") == "10000123"
    with pytest.raises(AdapterError):
        parse("")


# ---------- Facebook: источник кросспостинга ----------

async def test_fb_fetch_source_items_own_page(monkeypatch):
    """Лента своей страницы: посты читаются и маппятся, запрос идёт
    с токеном страницы (token_<pid>), а не с user-токеном."""
    captured = {}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            captured["url"] = url
            captured["params"] = params
            return _RawResp({"data": [
                {"id": "111_222", "message": "Первый пост",
                 "full_picture": "https://scontent.example/1.jpg"},
                {"id": "111_333", "message": "",
                 "full_picture": "https://scontent.example/2.jpg"},
                {"id": "111_444", "message": "Только текст"},
            ]})

    _install(monkeypatch, FakeClient())

    class Acc:
        credentials = {"access_token": "user-token", "token_123456": "page-token"}

    items = await FacebookAdapter().fetch_source_items(
        "https://www.facebook.com/pages/My-Page/123456", limit=10, account=Acc())

    assert captured["url"].endswith("/123456/posts")
    assert captured["params"]["access_token"] == "page-token"
    by_id = {i.external_id: i for i in items}
    assert by_id["111_222"].text == "Первый пост"
    assert by_id["111_222"].media == [{"url": "https://scontent.example/1.jpg",
                                       "kind": "photo"}]
    # пост без текста и без фото не попадает в выдачу
    assert "111_444" in by_id  # текст есть
    assert all(i.url.startswith("https://www.facebook.com/") for i in items)


async def test_fb_fetch_source_items_foreign_page_denied(monkeypatch):
    """Чужой паблик: Graph отвечает про Page Public Content Access —
    ошибка объясняет, что читать можно только свои страницы."""
    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None):
            return _RawResp({"error": {
                "code": 300,
                "message": "Requires Page Public Content Access"}})

    _install(monkeypatch, FakeClient())

    class Acc:
        credentials = {"access_token": "user-token", "token_999": "page-token"}

    with pytest.raises(AdapterError) as ei:
        await FacebookAdapter().fetch_source_items(
            "999", limit=10, account=Acc())
    assert "Page Public Content Access" in str(ei.value)


async def test_fb_fetch_source_requires_account():
    with pytest.raises(AdapterError):
        await FacebookAdapter().fetch_source_items("123", account=None)


# ---------- Отложенный репост ----------

@pytest.fixture
def one_source_item(monkeypatch):
    items = [SourceItem(external_id="delayed_1", text="Пост с отложкой",
                        media=[])]

    async def fake_fetch(self, source_ref, limit=10, account=None):
        return items

    monkeypatch.setattr(MockAdapter, "fetch_source_items", fake_fetch)
    return items


async def _make_source(client, auth_headers, mock_pages, **overrides):
    payload = {
        "source_id": 99,
        "name": "Отложка",
        "source_ref": "mock_channel",
        "target_page_ids": mock_pages[:1],
        "poll_interval_min": 15,
    }
    payload.update(overrides)
    r = await client.post("/api/web/crosspost", headers=auth_headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def test_delayed_repost_creates_scheduled_post(client, auth_headers,
                                                     mock_pages, one_source_item):
    """delay_minutes > 0: пост создаётся со статусом scheduled и сроком
    «сейчас + N минут», сразу не публикуется."""
    import datetime as dt

    out = await _make_source(client, auth_headers, mock_pages, delay_minutes=30)
    assert out["delay_minutes"] == 30

    r = await client.post(f"/api/web/crosspost/{out['id']}/run", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["created"] == 1

    r = await client.get("/api/web/posts", headers=auth_headers)
    posts = r.json()["list"]
    assert len(posts) == 1
    post = posts[0]
    assert post["status"] == "scheduled"
    due = dt.datetime.fromisoformat(post["publication_date"])
    delta = (due - utcnow().replace(tzinfo=None)).total_seconds()
    assert 29 * 60 <= delta <= 31 * 60, f"срок должен быть ≈ +30 мин, получено {delta}s"


async def test_delayed_repost_publishes_in_time(client, auth_headers,
                                                mock_pages, one_source_item):
    """Когда срок наступает, планировщик публикует отложенный репост."""
    from sqlalchemy import select

    from app.db import SessionFactory
    from app.models import Post
    from app.services.publish import run_due_posts

    out = await _make_source(client, auth_headers, mock_pages, delay_minutes=30)
    await client.post(f"/api/web/crosspost/{out['id']}/run", headers=auth_headers)

    # подвинули срок в прошлое — как будто время пришло
    async with SessionFactory() as s:
        post = (await s.execute(select(Post))).scalar_one()
        post.publication_date = utcnow().replace(tzinfo=None)
        await s.commit()

    async with SessionFactory() as s:
        processed = await run_due_posts(s)
        await s.commit()
    assert processed >= 1

    r = await client.get("/api/web/posts", headers=auth_headers)
    posts = r.json()["list"]
    assert posts[0]["status"] == "published"


async def test_zero_delay_still_publishes_immediately(client, auth_headers,
                                                      mock_pages, one_source_item):
    """delay_minutes = 0 (по умолчанию) — прежнее поведение: сразу опубликовано."""
    out = await _make_source(client, auth_headers, mock_pages)
    assert out["delay_minutes"] == 0

    await client.post(f"/api/web/crosspost/{out['id']}/run", headers=auth_headers)
    r = await client.get("/api/web/posts", headers=auth_headers)
    posts = r.json()["list"]
    assert posts[0]["status"] == "published"


# ---------- OK: источник — свой паблик ----------

def test_ok_parse_group_ref():
    parse = OKAdapter._parse_group_ref
    assert parse("https://ok.ru/group/123456789") == "123456789"
    assert parse("https://ok.ru/group/123456789/") == "123456789"
    assert parse("https://odnoklassniki.ru/group/987654321") == "987654321"
    assert parse("123456789") == "123456789"
    # короткий адрес без числового ID не принимается
    with pytest.raises(AdapterError) as ei:
        parse("https://ok.ru/mypublik")
    assert "числовую ссылку" in str(ei.value)
    with pytest.raises(AdapterError):
        parse("")


async def test_ok_fetch_source_items_own_public(monkeypatch):
    """Лента своего паблика: ID топиков из статистики (свежие — первыми),
    содержимое из mediatopic.getByIds; текст склеивается, фото — по
    самой крупной pic_-ссылке, топики без контента пропускаются."""
    calls = []

    async def fake_call(self, method, creds, extra_params=None):
        calls.append((method, extra_params or {}))
        if method == "group.getStatTopics":
            # специально вразнобой — сортировка по created_ms обязательна
            return {"topics": [
                {"id": "t_old", "created_ms": 1000, "views": 5},
                {"id": "t_new", "created_ms": 3000, "views": 9},
                {"id": "t_mid", "created_ms": 2000, "views": 7},
            ], "has_more": False}
        if method == "mediatopic.getByIds":
            return [
                {"id": "t_new", "media": [
                    {"type": "text", "text": "Свежий пост"},
                    {"type": "photo", "list": [
                        {"id": "p1", "pic_max": "https://i.mycdn.me/p1.jpg"},
                        # без pic_max — берём самое крупное из доступных
                        {"id": "p2", "pic640x480": "https://i.mycdn.me/p2.jpg"},
                    ]},
                ]},
                {"id": "t_mid", "media": [
                    {"type": "text", "text": "Средний пост"},
                    {"type": "movie", "list": [{"id": "v1"}]},  # видео пропускается
                ]},
                {"id": "t_old", "media": [
                    {"type": "text", "text": "Старый пост"},
                ]},
            ]
        raise AssertionError(f"unexpected method {method}")

    monkeypatch.setattr(OKAdapter, "call", fake_call)

    class Acc:
        credentials = {"access_token": "t", "application_key": "k",
                       "session_secret_key": "s"}

    items = await OKAdapter().fetch_source_items(
        "https://ok.ru/group/555777", limit=2, account=Acc())

    # свежие первыми, лимит соблюдён
    assert [i.external_id for i in items] == ["t_new", "t_mid"]
    # запрос статистики: своя группа, счётчик топиков
    stat_call = next((p for m, p in calls if m == "group.getStatTopics"))
    assert stat_call["gid"] == "555777"
    assert stat_call["count"] == 2
    # содержимое: ID через запятую, свежие первыми
    topics_call = next((p for m, p in calls if m == "mediatopic.getByIds"))
    assert topics_call["topic_ids"] == "t_new,t_mid"

    by_id = {i.external_id: i for i in items}
    assert by_id["t_new"].text == "Свежий пост"
    assert by_id["t_new"].media == [
        {"url": "https://i.mycdn.me/p1.jpg", "kind": "photo"},
        {"url": "https://i.mycdn.me/p2.jpg", "kind": "photo"}]
    # видео в топике не репостится — только текст
    assert by_id["t_mid"].media == []
    assert by_id["t_mid"].text == "Средний пост"
    assert all(i.url == f"https://ok.ru/group/555777/topic/{i.external_id}"
               for i in items)


async def test_ok_fetch_source_items_skips_empty_topics(monkeypatch):
    """Топик без текста и фото в выдачу не попадает; пустая статистика —
    второй запрос не делается вовсе."""
    calls = []

    async def fake_call(self, method, creds, extra_params=None):
        calls.append(method)
        if method == "group.getStatTopics":
            return {"topics": [{"id": "t_empty", "created_ms": 100}]}
        if method == "mediatopic.getByIds":
            return [{"id": "t_empty", "media": []}]
        raise AssertionError(method)

    monkeypatch.setattr(OKAdapter, "call", fake_call)

    class Acc:
        credentials = {"access_token": "t"}

    items = await OKAdapter().fetch_source_items("555777", account=Acc())
    assert items == []
    assert "mediatopic.getByIds" in calls  # содержимое спросили

    # и вариант «топиков нет вообще» — без второго запроса
    calls.clear()

    async def fake_call_empty(self, method, creds, extra_params=None):
        calls.append(method)
        return {"topics": []}

    monkeypatch.setattr(OKAdapter, "call", fake_call_empty)
    items = await OKAdapter().fetch_source_items("555777", account=Acc())
    assert items == []
    assert calls == ["group.getStatTopics"]


async def test_ok_fetch_source_items_foreign_group_hint(monkeypatch):
    """Чужая группа: статистика не отдаётся — ошибка объясняет, что
    репост работает только со своих пабликов."""

    async def fake_call(self, method, creds, extra_params=None):
        raise AdapterError("OK group.getStatTopics: Permission denied")

    monkeypatch.setattr(OKAdapter, "call", fake_call)

    class Acc:
        credentials = {"access_token": "t"}

    with pytest.raises(AdapterError) as ei:
        await OKAdapter().fetch_source_items("https://ok.ru/group/111222",
                                             account=Acc())
    msg = str(ei.value)
    assert "владельцу/администратору" in msg
    assert "свой паблик" in msg


async def test_ok_fetch_source_requires_account():
    with pytest.raises(AdapterError):
        await OKAdapter().fetch_source_items("12345", account=None)
