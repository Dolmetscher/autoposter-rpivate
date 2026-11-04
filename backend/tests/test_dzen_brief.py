"""Дзен, экспериментальный режим «пост» через editor-api (≤4096 символов) и
фоллбэк на RSS-фид для длинных текстов/медиа/ошибок сессии."""
import json

import pytest

from app.integrations.base import (AdapterError, PublishContent,
                                   PublishOutcome)
from app.integrations.dzen import (BRIEF_MAX, DzenAdapter, slug_from_link,
                                   text_to_content_state)

CSRF = "a" * 40 + ":1793823951609"
PUBID = "59d5f0d2482677560d9913c4"
EDITOR_HTML = (f'{{"csrfToken":"{CSRF}","other":1}},'
               f'"publisherId":"{PUBID}",'
               f'"id":"{PUBID}","name":"Oldfag TV","nickname":{{}}')


class _Acc:
    def __init__(self, creds):
        self.credentials = creds


class _Page:
    def __init__(self, page_id="oldfag_tv", creds=None):
        self.social_page_id = page_id
        self.account = _Acc(creds or {})


class _Resp:
    def __init__(self, status_code=200, text="", payload=None):
        self.status_code = status_code
        self.text = text
        self._payload = payload

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class _FakeClient:
    """Подмена httpx.AsyncClient: ответы по подстроке URL."""

    def __init__(self, responses):
        self.responses = responses  # [(фрагмент URL, _Resp), ...]
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def _match(self, url):
        for frag, resp in self.responses:
            if frag in url:
                return resp
        return _Resp(404, "")

    async def get(self, url, **kw):
        self.calls.append(("GET", url))
        return self._match(url)

    async def post(self, url, **kw):
        self.calls.append(("POST", url, kw.get("json")))
        return self._match(url)


def _content(text="привет\nмир", settings=None, media=None):
    return PublishContent(text=text, media=media or [], documents=[],
                          settings=settings or {})


# ---------- сборка contentState ----------

def test_text_to_content_state_double_encoded():
    state = text_to_content_state("первая строка\n\nвторая")
    assert isinstance(state, str)
    outer = json.loads(state)
    assert outer["gallery"]["items"] == []
    inner = json.loads(outer["content"])
    texts = [b["text"] for b in inner["blocks"]]
    assert texts == ["первая строка", "", "вторая"]
    for b in inner["blocks"]:
        assert b["type"] == "unstyled"
        assert b["entityRanges"] == []
        assert set(b) >= {"data", "depth", "key", "type", "text",
                          "entityRanges", "inlineStyleRanges"}


def test_text_to_content_state_trims_blank_edges():
    inner = json.loads(json.loads(text_to_content_state("\n\n\nхай\n\n\n"))["content"])
    assert [b["text"] for b in inner["blocks"]] == ["хай"]


# ---------- slug из ссылки ----------

@pytest.mark.parametrize("link,expected", [
    ("https://dzen.ru/oldfag_tv", "oldfag_tv"),
    ("https://dzen.ru/profile/editor/oldfag_tv", "oldfag_tv"),
    ("dzen.ru/oldfag_tv?state=published", "oldfag_tv"),
    ("oldfag_tv", "oldfag_tv"),
    ("https://dzen.ru/id/mxo8ybw6", ""),          # профиль без ника не подходит
    ("https://dzen.ru/media/some", ""),           # служебные пути
    ("", ""),
])
def test_slug_from_link(link, expected):
    assert slug_from_link(link) == expected


# ---------- выбор пути публикации ----------

@pytest.mark.asyncio
async def test_brief_used_for_short_text(monkeypatch):
    async def fake_brief(self, page, creds, text):
        return PublishOutcome(ok=True, external_id="brief-x", external_url="https://dzen.ru/post/x")

    monkeypatch.setattr(DzenAdapter, "_publish_brief", fake_brief)
    page = _Page(creds={"session_cookie": "c"})
    out = await DzenAdapter().publish(page, _content("короткий"))
    assert out.external_id == "brief-x"


@pytest.mark.asyncio
async def test_rss_used_for_media_posts(monkeypatch):
    async def fail_brief(self, page, creds, text):
        raise AssertionError("с картинками должен идти RSS, не brief")

    monkeypatch.setattr(DzenAdapter, "_publish_brief", fail_brief)
    page = _Page(creds={"session_cookie": "c"})
    out = await DzenAdapter().publish(
        page, _content("текст", settings={"rss_feed_url": "https://x/f.xml"},
                       media=[object()]))
    assert out.ok
    assert out.external_id.startswith("rss-")


@pytest.mark.asyncio
async def test_rss_used_for_long_text(monkeypatch):
    async def fail_brief(self, page, creds, text):
        raise AssertionError("длинный текст должен идти в RSS")

    monkeypatch.setattr(DzenAdapter, "_publish_brief", fail_brief)
    page = _Page(creds={"session_cookie": "c"})
    out = await DzenAdapter().publish(
        page, _content("а" * (BRIEF_MAX + 1),
                       settings={"rss_feed_url": "https://x/f.xml"}))
    assert out.ok and out.external_id.startswith("rss-")


@pytest.mark.asyncio
async def test_brief_failure_falls_back_to_rss(monkeypatch):
    """Протухшая сессия — публикация всё равно уходит через RSS."""

    async def fail_brief(self, page, creds, text):
        raise AdapterError("Дзен: сессия протухла")

    monkeypatch.setattr(DzenAdapter, "_publish_brief", fail_brief)
    page = _Page(creds={"session_cookie": "bad",
                        "channel_link": "https://dzen.ru/oldfag_tv"})
    out = await DzenAdapter().publish(
        page, _content("текст", settings={"rss_feed_url": "https://x/f.xml"}))
    assert out.ok and out.external_id.startswith("rss-")


# ---------- полный путь _publish_brief с подменой HTTP ----------

@pytest.mark.asyncio
async def test_publish_brief_full_flow(monkeypatch):
    responses = [
        ("profile/editor", _Resp(200, EDITOR_HTML)),
        ("add-publication", _Resp(200, "", payload={"id": "abc123"})),
        ("update-publication-content-and-publish",
         _Resp(200, "", payload={"link": "https://dzen.ru/post/xyz", "id": "abc123"})),
    ]
    fake = _FakeClient(responses)
    import app.integrations.dzen as dz
    monkeypatch.setattr(dz.httpx, "AsyncClient", lambda **kw: fake)

    page = _Page(creds={"session_cookie": "c"})
    out = await DzenAdapter()._publish_brief(page, page.account.credentials, "строка один\nстрока два")

    assert out.ok
    assert out.external_id == "brief-abc123"
    assert out.external_url == "https://dzen.ru/post/xyz"

    kinds = [(c[0], c[1]) for c in fake.calls]
    assert len(kinds) == 3
    assert "/profile/editor/oldfag_tv" in kinds[0][1]

    add_url, add_payload = fake.calls[1][1], fake.calls[1][2]
    assert "add-publication" in add_url and PUBID in add_url
    assert add_payload == {"title": "", "publisherId": PUBID,
                           "publicationType": "brief"}

    pub_url, pub_payload = fake.calls[2][1], fake.calls[2][2]
    assert "update-publication-content-and-publish" in pub_url
    assert pub_payload["id"] == "abc123"
    assert "fp" not in pub_payload
    state = json.loads(pub_payload["articleContent"]["contentState"])
    inner = json.loads(state["content"])
    assert [b["text"] for b in inner["blocks"]] == ["строка один", "строка два"]


@pytest.mark.asyncio
async def test_publish_brief_expired_session_raises(monkeypatch):
    responses = [("profile/editor", _Resp(403, "forbidden"))]
    fake = _FakeClient(responses)
    import app.integrations.dzen as dz
    monkeypatch.setattr(dz.httpx, "AsyncClient", lambda **kw: fake)

    page = _Page(creds={"session_cookie": "old", "channel_link": "https://dzen.ru/oldfag_tv"})
    with pytest.raises(AdapterError, match="сессия"):
        await DzenAdapter()._publish_brief(page, page.account.credentials, "текст")


@pytest.mark.asyncio
async def test_brief_slug_uses_channel_link_when_page_is_synthetic():
    page = _Page(page_id="dzen")
    creds = {"channel_link": "https://dzen.ru/oldfag_tv"}
    assert DzenAdapter()._brief_slug(page, creds) == "oldfag_tv"


@pytest.mark.asyncio
async def test_no_cookie_and_no_feed_raises_hint():
    page = _Page()
    with pytest.raises(AdapterError, match="RSS-фид"):
        await DzenAdapter().publish(page, _content("тест"))
