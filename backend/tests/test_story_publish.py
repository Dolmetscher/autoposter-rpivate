"""Галочка «Публиковать как историю» (publish_as_story).

VK — полный флоу stories.getPhotoUploadServer → загрузка → stories.save:
раньше код передавал несуществующий параметр add_news, ждал от сервера
загрузки поле response.items (которого нет — ответ приходит как
response.upload_result) и вызывал выдуманный метод stories.post, из-за
чего история не публиковалась вовсе.

Сети без историй (ОК, FB, Twitter, Макс, Tumblr, Threads, Telegram-бот)
галочку игнорируют и публикуют обычный пост — как обещает раздел 4.8
руководства: «Неактивные для выбранных сетей настройки просто
игнорируются».
"""
import pytest

from app.integrations.base import AdapterError, PublishContent
from app.integrations.ok import OKAdapter
from app.integrations.telegram import TelegramAdapter
from app.integrations.vk import VKAdapter


class _Acc:
    def __init__(self, creds):
        self.credentials = creds


class _Page:
    def __init__(self, page_id, creds, kind="group"):
        self.social_page_id = page_id
        self.social_page_name = str(page_id)
        self.page_kind = kind
        self.account = _Acc(creds)


class _Media:
    def __init__(self, mtype="photo", name="pic.jpg"):
        self.type = mtype
        self.name = name
        self.title = ""
        self.description = ""
        self.file_path = f"media/{name}"


def _content(settings, media):
    return PublishContent(text="текст поста", media=media, documents=[],
                          settings=settings)


class _Resp:
    def __init__(self, payload):
        self._p = payload

    def json(self):
        return self._p


class _VKClient:
    """Мок httpx-клиента: ответы разложены по префиксам URL."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def post(self, url, data=None, files=None):
        self.calls.append((url, dict(data or {}), files))
        for prefix, payload in self.routes.items():
            if url.startswith(prefix):
                return _Resp(payload)
        raise AssertionError(f"неожидаемый запрос: {url}")


def _story_routes(upload_payload):
    return {
        "https://api.vk.com/method/stories.getPhotoUploadServer":
            {"response": {"upload_url": "https://pu.vk.com/upload"}},
        "https://pu.vk.com/upload": upload_payload,
        "https://api.vk.com/method/stories.save":
            {"response": {"count": 1,
                          "items": [{"id": 777, "owner_id": -456}]}},
    }


# ---------- ВКонтакте: флоу истории ----------

async def _fake_read(m):
    return b"image-bytes"


@pytest.mark.asyncio
async def test_vk_story_full_flow(monkeypatch):
    monkeypatch.setattr("app.integrations.vk.read_media_bytes", _fake_read)
    client = _VKClient(_story_routes({"response": {"upload_result": "UR-ABC"}}))
    page = _Page("-456", {"access_token": "tok"})

    out = await VKAdapter()._post_story(client, "tok", page,
                                        _content({"publish_as_story": True},
                                                 [_Media("photo")]))

    assert out.ok
    assert out.external_id == "story777"
    assert out.external_url == "https://vk.com/story-456_777"
    urls = [c[0] for c in client.calls]
    # ровно три запроса: сервер загрузки → аплоад → stories.save
    assert urls == ["https://api.vk.com/method/stories.getPhotoUploadServer",
                    "https://pu.vk.com/upload",
                    "https://api.vk.com/method/stories.save"]
    assert not any("stories.post" in u for u in urls), \
        "метода stories.post не существует — должна зваться stories.save"
    # параметры сервера загрузки: add_to_news (не add_news!) и group_id
    data0 = client.calls[0][1]
    assert data0.get("add_to_news") == 1
    assert "add_news" not in data0
    assert data0.get("group_id") == 456
    # поле файла в аплоаде историй — file (а не «photo», как на стене)
    assert "file" in (client.calls[1][2] or {})
    assert "photo" not in (client.calls[1][2] or {})
    # stories.save публикует историю по upload_result из ответа аплоада
    data2 = client.calls[2][1]
    assert data2.get("upload_results") == "UR-ABC"
    assert data2.get("group_id") == 456


@pytest.mark.asyncio
async def test_vk_story_upload_error_reported(monkeypatch):
    monkeypatch.setattr("app.integrations.vk.read_media_bytes", _fake_read)
    client = _VKClient(_story_routes(
        {"error": {"error_code": 100, "error_msg": "неверное изображение"}}))
    page = _Page("-456", {"access_token": "tok"})

    with pytest.raises(AdapterError, match="загрузка не удалась"):
        await VKAdapter()._post_story(client, "tok", page,
                                      _content({"publish_as_story": True},
                                               [_Media("photo")]))


@pytest.mark.asyncio
async def test_vk_story_no_upload_result_fails(monkeypatch):
    """Регрессия: раньше код искал в ответе аплоада response.items —
    его там нет, и история вечно «не загружалась»."""
    monkeypatch.setattr("app.integrations.vk.read_media_bytes", _fake_read)
    client = _VKClient(_story_routes({"response": {}}))
    page = _Page("-456", {"access_token": "tok"})

    with pytest.raises(AdapterError, match="загрузка не удалась"):
        await VKAdapter()._post_story(client, "tok", page,
                                      _content({"publish_as_story": True},
                                               [_Media("photo")]))


@pytest.mark.asyncio
async def test_vk_story_requires_media():
    page = _Page("-456", {"access_token": "tok"})
    with pytest.raises(AdapterError, match="нужен файл"):
        await VKAdapter()._post_story(_VKClient({}), "tok", page,
                                      _content({"publish_as_story": True}, []))


@pytest.mark.asyncio
async def test_vk_story_dispatch_from_publish(monkeypatch):
    """publish() с галочкой уходит в story-ветку, а не на wall.post."""
    monkeypatch.setattr("app.integrations.vk.read_media_bytes", _fake_read)

    class _Client:
        def __init__(self, **kw):
            self.inner = _VKClient(_story_routes(
                {"response": {"upload_result": "UR"}}))

        async def __aenter__(self):
            return self.inner

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr("app.integrations.vk.httpx.AsyncClient", _Client)
    out = await VKAdapter().publish(
        _Page("-456", {"access_token": "tok"}),
        _content({"publish_as_story": True}, [_Media("photo")]))
    assert out.ok and out.external_url == "https://vk.com/story-456_777"


# ---------- Сети без историй: галочка игнорируется ----------

@pytest.mark.asyncio
async def test_ok_story_flag_publishes_regular_post(monkeypatch):
    called = {}

    async def fake_call(self, method, creds, params=None):
        called["method"] = method
        called["params"] = params
        return "12345"

    monkeypatch.setattr(OKAdapter, "call", fake_call)
    out = await OKAdapter().publish(
        _Page("123", {"access_token": "t"}),
        _content({"publish_as_story": True}, []))

    assert out.ok  # раньше: AdapterError «истории через API не поддерживаются»
    assert called["method"] == "mediatopic.post"
    assert out.external_id == "12345"


@pytest.mark.asyncio
async def test_telegram_bot_story_flag_publishes_regular_post(monkeypatch):
    sent = []

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, files=None, json=None):
            sent.append(url)
            return _Resp({"ok": True, "result": {"message_id": 42}})

    monkeypatch.setattr("app.integrations.telegram.httpx.AsyncClient", _Client)
    out = await TelegramAdapter().publish(
        _Page("@channel", {"bot_token": "111:AAA"}),
        _content({"publish_as_story": True}, []))

    assert out.ok  # раньше: AdapterError «истории доступны только для аккаунтов»
    assert any("sendMessage" in u for u in sent)
    assert out.external_id == "@channel_42"
