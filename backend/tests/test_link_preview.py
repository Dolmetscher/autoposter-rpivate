"""Ссылка на материал (Zén и другие сайты) не должна теряться при публикации.

Правила:
— сети, умеющие публиковать ссылку сама (VK — вложение links.add,
  Telegram — подпись с флагом превью), получают ссылку как есть;
— остальные сети получают ссылку дописанной в текст — тогда превью
  (Open Graph) развернёт сама сеть.
"""
from types import SimpleNamespace

from app.integrations.base import PublishContent, SocialAdapter


def _page(creds=None):
    return SimpleNamespace(account=SimpleNamespace(credentials=creds or {}),
                           social_page_id="42", page_kind="group",
                           social_page_name="Тест")


# ---------- конвейер публикации ----------

async def test_link_appended_to_text_for_plain_networks(monkeypatch, client,
                                                        auth_headers, mock_pages):
    """Сеть без поддержки ссылок получает её в тексте поста."""
    from app.integrations.mock import MockAdapter

    captured = {}
    original = MockAdapter.publish

    async def spy(self, page, content):
        captured["text"] = content.text
        return await original(self, page, content)

    monkeypatch.setattr(MockAdapter, "publish", spy)

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"source_id": 0, "text": "Материал недели"}],
        "media_ids": [],
        "page_ids": mock_pages[:1],
        "settings": {"link": "https://zennoprirokat.ru/material"},
        "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    assert captured["text"] == "Материал недели\nhttps://zennoprirokat.ru/material"


def test_text_with_link_variants():
    c = PublishContent(text="Текст", settings={"link": "https://example.com/a"})
    assert c.text_with_link() == "Текст\nhttps://example.com/a"

    # только ссылка, без текста
    c = PublishContent(text="", settings={"link": "https://example.com/a"})
    assert c.text_with_link() == "https://example.com/a"

    # ссылка уже есть в тексте — не дублируем
    c = PublishContent(text="Ссылка: https://example.com/a",
                       settings={"link": "https://example.com/a"})
    assert c.text_with_link() == "Ссылка: https://example.com/a"

    # ссылки нет — текст без изменений
    c = PublishContent(text="Текст", settings={})
    assert c.text_with_link() == "Текст"

    c = PublishContent(text="Текст", settings={"link": "   "})
    assert c.text_with_link() == "Текст"


def test_handles_link_flags():
    """VK и Telegram публикуют ссылку сами — конвейер не трогает их текст."""
    from app.integrations.telegram import TelegramAdapter
    from app.integrations.vk import VKAdapter

    assert VKAdapter.handles_link is True
    assert TelegramAdapter.handles_link is True
    assert SocialAdapter.handles_link is False


# ---------- Telegram ----------

async def test_telegram_bot_keeps_link_without_media(monkeypatch):
    """Без медиа Telegram шлёт sendMessage — ссылка должна попасть в текст."""
    import httpx as _httpx

    from app.integrations.telegram import TelegramAdapter

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 7}}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, data=None, files=None, **kw):
            captured["url"] = url
            captured["json"] = json
            captured["data"] = data
            return FakeResp()

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        out = await TelegramAdapter().publish(
            _page({"bot_token": "t"}),
            PublishContent(text="Материал", settings={"link": "https://example.com/a"}),
        )
    finally:
        _httpx.AsyncClient = orig

    assert out.ok
    assert "sendMessage" in captured["url"]
    assert captured["json"]["text"] == "Материал\nhttps://example.com/a"
    # превью не отключено
    assert captured["json"]["disable_web_page_preview"] is False


async def test_telegram_bot_link_preview_can_be_disabled(monkeypatch):
    import httpx as _httpx

    from app.integrations.telegram import TelegramAdapter

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 7}}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, data=None, files=None, **kw):
            captured["json"] = json
            return FakeResp()

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        await TelegramAdapter().publish(
            _page({"bot_token": "t"}),
            PublishContent(text="Материал", settings={"link": "https://example.com/a",
                                                     "link_preview": False}),
        )
    finally:
        _httpx.AsyncClient = orig

    assert captured["json"]["disable_web_page_preview"] is True


async def test_telegram_caption_reserves_room_for_link(monkeypatch):
    """Лимит подписи 1024 — ссылка не должна обрезаться первой."""
    import httpx as _httpx

    from app.integrations import telegram as tg

    captured = {}

    class FakeResp:
        status_code = 200

        def json(self):
            return {"ok": True, "result": {"message_id": 7}}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json=None, data=None, files=None, **kw):
            captured["data"] = data
            return FakeResp()

    async def fake_read(media):
        return b"bytes"

    monkeypatch.setattr(tg, "read_media_bytes", fake_read)

    link = "https://zennoprirokat.ru/material"
    long_text = "я" * 2000
    photo = SimpleNamespace(type="photo", name="photo.png", file_path="p.png")

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        await tg.TelegramAdapter().publish(
            _page({"bot_token": "t"}),
            PublishContent(text=long_text, media=[photo], settings={"link": link}),
        )
    finally:
        _httpx.AsyncClient = orig

    caption = captured["data"]["caption"]
    assert len(caption) <= 1024
    # ссылка целиком, в конце — её не обрезало
    assert caption.endswith(link)


# ---------- VK ----------

async def test_vk_link_not_duplicated_in_text(monkeypatch):
    """У VK ссылка уходит в attachments — в тексте её дублировать не надо."""
    from app.integrations.base import PublishContent as PC
    from app.integrations.vk import VKAdapter

    captured = {}

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

        async def post(self, url, data=None, **kw):
            captured["wall"] = data
            return FakeResp({"response": {"post_id": 5}})

    import httpx as _httpx
    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        out = await VKAdapter().publish(
            _page({"access_token": "tok"}),
            PC(text="Материал", settings={"link": "https://example.com/a"}),
        )
    finally:
        _httpx.AsyncClient = orig

    assert out.ok
    assert captured["wall"]["attachments"] == "https://example.com/a"
    assert captured["wall"]["message"] == "Материал"
