"""Проверки правок по итогам ревью модуля интеграций.

Закрытые проблемы:
— Telegram.delete() в режиме аккаунта падал с NameError (переменная session);
— текст обрезался по лимитам сетей (Instagram 2200, Threads 500, YouTube 5000,
  LinkedIn 3000) — иначе API отклонял пост;
— Twitter проверял количество медиа после загрузки файлов, видео+фото
  вместе не пропускались;
— OK отдаёт ссылку загрузки видео строкой, а не объектом;
— Pinterest для видео требовал обложку;
— пустое имя файла уходило в multipart.
"""
from types import SimpleNamespace

import pytest

from app.integrations.base import AdapterError, media_filename


def _page(creds=None, page_id="42"):
    return SimpleNamespace(account=SimpleNamespace(credentials=creds or {}),
                           social_page_id=page_id, page_kind="group",
                           social_page_name="Тест")


def _media(mtype="photo", name="photo.png"):
    return SimpleNamespace(type=mtype, name=name, file_path="p/photo.png",
                           title="", description="", file_thumbnail_path="")


# ---------- имя файла ----------

def test_media_filename_keeps_name():
    assert media_filename(_media(name="my photo.jpg"), "photo.jpg") == "my photo.jpg"


def test_media_filename_fallback_has_extension():
    """VK и OK определяют тип файла по расширению — оно должно быть."""
    assert media_filename(_media(name=""), "photo.jpg") == "photo.jpg"
    assert media_filename(_media(mtype="video", name=""), "video.mp4") == "video.mp4"
    assert media_filename(_media(mtype="document", name="  "), "document") == "document"


# ---------- Telegram: удаление в режиме аккаунта ----------

async def test_telegram_delete_account_requires_credentials(monkeypatch):
    """Без api_id/api_hash/session удаление должно дать понятную ошибку, а не NameError."""
    from app.integrations.telegram import TelegramAdapter

    with pytest.raises(AdapterError, match="не настроен"):
        await TelegramAdapter().delete(
            _page({"mode": "account", "api_id": 1, "api_hash": "h"}), "42_7")


async def test_telegram_delete_account_uses_session(monkeypatch):
    """Сессия берётся из credentials аккаунта."""
    from app.integrations import telegram as tg

    seen = {}

    class FakeClient:
        def __init__(self, session, api_id, api_hash):
            seen["session"] = session
            seen["api_id"] = api_id
            seen["api_hash"] = api_hash

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def delete_messages(self, chat, ids):
            seen["chat"] = chat
            seen["ids"] = ids

    monkeypatch.setattr(tg, "TelegramClient", FakeClient, raising=False)

    # подменяем импорт telethon внутри функции
    import sys
    import types as pytypes
    fake_telethon = pytypes.ModuleType("telethon")
    fake_telethon.TelegramClient = FakeClient
    fake_sessions = pytypes.ModuleType("telethon.sessions")
    fake_sessions.StringSession = lambda s: f"session:{s}"
    fake_telethon.sessions = fake_sessions
    monkeypatch.setitem(sys.modules, "telethon", fake_telethon)
    monkeypatch.setitem(sys.modules, "telethon.sessions", fake_sessions)

    ok = await tg.TelegramAdapter().delete(
        _page({"mode": "account", "api_id": 123, "api_hash": "hash", "session": "sess"}),
        "42_7")
    assert ok is True
    assert seen["session"] == "session:sess"
    assert seen["api_id"] == 123
    assert seen["chat"] == 42
    assert seen["ids"] == [7]


# ---------- лимиты текста ----------

async def test_threads_text_truncated_to_500(monkeypatch):
    from app.integrations import threads as th

    captured = []

    class FakeResp:
        def json(self):
            return {"id": "1", "status_code": "FINISHED"}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **kw):
            captured.append(data or {})
            return FakeResp()

    import httpx as _httpx
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        await th.ThreadsAdapter().publish(
            _page({"access_token": "t", "threads_user_id": "uid"}, page_id="uid"),
            th.PublishContent(text="я" * 1000),
        )
    finally:
        _httpx.AsyncClient = orig

    text = next(d["text"] for d in captured if "text" in d)
    assert len(text) == 500


async def test_linkedin_commentary_truncated_to_3000(monkeypatch):
    from app.integrations import linkedin as li

    captured = {}

    class FakeResp:
        status_code = 201
        headers = {"x-restli-id": "urn:li:activity:999"}
        text = ""

        def json(self):
            return {}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None, **kw):
            captured["body"] = json
            return FakeResp()

    import httpx as _httpx
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        await li.LinkedInAdapter().publish(
            _page({"access_token": "t"}),
            li.PublishContent(text="я" * 5000),
        )
    finally:
        _httpx.AsyncClient = orig

    assert len(captured["body"]["commentary"]) == 3000


async def test_instagram_caption_truncated_to_2200(monkeypatch):
    from app.integrations import instagram as ig

    captured = []

    class FakeResp:
        def json(self):
            return {"id": "1", "permalink": "https://ig/p/x"}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **kw):
            captured.append(data or {})
            return FakeResp()

        async def get(self, url, params=None, **kw):
            return FakeResp()

    import httpx as _httpx
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        await ig.InstagramAdapter().publish(
            _page({"access_token": "t", "ig_user_id": "9"}),
            ig.PublishContent(text="я" * 4000, media=[_media()]),
        )
    finally:
        _httpx.AsyncClient = orig

    caption = next(d["caption"] for d in captured if "caption" in d)
    assert len(caption) == 2200


# ---------- Twitter: валидация до загрузки ----------

async def test_twitter_rejects_video_with_photos_before_upload(monkeypatch):
    """Видео+фото — ошибка сразу, без загрузки файлов."""
    from app.integrations import twitter as tw

    uploaded = []

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def request(self, *a, **kw):
            uploaded.append(a)
            raise AssertionError("файлы не должны загружаться")

    monkeypatch.setattr(tw, "OAuth1Client", FakeClient)
    monkeypatch.setattr(tw, "read_media_bytes", lambda m: None)

    with pytest.raises(AdapterError, match="единственным"):
        await tw.TwitterAdapter().publish(
            _page({"api_key": "k", "api_secret": "s",
                   "access_token": "a", "access_token_secret": "as"}),
            tw.PublishContent(text="текст", media=[_media("video"), _media("photo")]),
        )
    assert uploaded == []


async def test_twitter_rejects_more_than_four_media(monkeypatch):
    from app.integrations import twitter as tw

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def request(self, *a, **kw):
            raise AssertionError("файлы не должны загружаться")

    monkeypatch.setattr(tw, "OAuth1Client", FakeClient)

    with pytest.raises(AdapterError, match="4 медиа"):
        await tw.TwitterAdapter().publish(
            _page({"api_key": "k", "api_secret": "s",
                   "access_token": "a", "access_token_secret": "as"}),
            tw.PublishContent(text="текст", media=[_media() for _ in range(5)]),
        )


# ---------- OK: ссылка загрузки видео строкой ----------

async def test_ok_video_upload_url_as_string(monkeypatch):
    """video.getUploadUrl отдаёт строку — раньше был TypeError."""
    from app.integrations import ok as ok_mod

    posted = {}

    class FakeResp:
        def __init__(self, payload):
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, files=None, **kw):
            posted["url"] = url
            return FakeResp({"video_id": "v1"})

    async def fake_call(self, method, creds, extra_params=None):
        if method == "video.getUploadUrl":
            return "https://upload.example/video"
        return "topic-1"

    async def fake_read(m):
        return b"bytes"

    monkeypatch.setattr(ok_mod.OKAdapter, "call", fake_call)
    monkeypatch.setattr(ok_mod, "read_media_bytes", fake_read)

    import httpx as _httpx
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        out = await ok_mod.OKAdapter().publish(
            _page({"access_token": "t", "application_key": "k",
                   "session_secret_key": "s"}),
            ok_mod.PublishContent(text="Материал", media=[_media("video", "clip.mp4")]),
        )
    finally:
        _httpx.AsyncClient = orig

    assert out.ok
    assert posted["url"] == "https://upload.example/video"


# ---------- Pinterest: обложка для видео ----------

async def test_pinterest_video_has_cover(monkeypatch):
    from app.integrations import pinterest as pin

    captured = {}

    class FakeResp:
        status_code = 201

        def json(self):
            return {"id": "42"}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, headers=None, json=None, **kw):
            captured["body"] = json
            return FakeResp()

    import httpx as _httpx
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        await pin.PinterestAdapter().publish(
            _page({"access_token": "t"}),
            pin.PublishContent(text="Видео", media=[_media("video", "clip.mp4")]),
        )
    finally:
        _httpx.AsyncClient = orig

    src = captured["body"]["media_source"]
    assert src["source_type"] == "video_url"
    assert src["cover_image_url"], "для видео Pinterest требует обложку"
