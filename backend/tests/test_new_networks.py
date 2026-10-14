"""Новые адаптеры (Twitter, Threads, Tumblr), заглушки planned-сетей,
OAuth1-подпись и исправления ревью (YouTube RSS, стриминг медиа)."""
import json
from types import SimpleNamespace

import pytest

from app.integrations._oauth1 import oauth1_header
from app.integrations.base import AdapterError
from app.integrations.registry import get_adapter
from app.integrations.youtube import YouTubeAdapter


# ---------- OAuth 1.0a ----------

def test_oauth1_header_contains_signature_and_params():
    creds = {"api_key": "k1", "api_secret": "s1",
             "access_token": "tok1", "access_token_secret": "ts1"}
    h = oauth1_header("POST", "https://api.twitter.com/2/tweets", creds,
                      params={"text": "hi"}, nonce="nonce123", timestamp="1700000000")
    assert h.startswith("OAuth ")
    assert 'oauth_consumer_key="k1"' in h
    assert 'oauth_token="tok1"' in h
    assert 'oauth_signature_method="HMAC-SHA1"' in h
    assert 'oauth_signature="' in h
    # подпись детерминирована при фиксированных nonce/timestamp
    h2 = oauth1_header("POST", "https://api.twitter.com/2/tweets", creds,
                       params={"text": "hi"}, nonce="nonce123", timestamp="1700000000")
    assert h == h2


def test_oauth1_header_percent_encoding():
    creds = {"api_key": "k1", "api_secret": "s1",
             "access_token": "tok1", "access_token_secret": "ts1"}
    h = oauth1_header("GET", "https://x.test/p?a=b", creds,
                      params={"q": "a b&c/d"}, nonce="n", timestamp="1")
    assert h.startswith("OAuth ")


# ---------- Twitter ----------

def _tw_adapter():
    from app.integrations.twitter import TwitterAdapter
    return TwitterAdapter()


class _A:
    def __init__(self, creds):
        self.credentials = creds


class _Media:
    def __init__(self, type, name="media.bin"):  # noqa: A002
        self.type = type
        self.name = name
        self.title = ""
        self.description = ""
        self.file_path = f"posts/photos/{name}"


def _tw_creds():
    return {"api_key": "k", "api_secret": "s", "access_token": "t",
            "access_token_secret": "ts"}


def test_twitter_connect_fields():
    fields = {f["name"]: f["required"] for f in _tw_adapter().connect_requires()}
    assert fields == {"api_key": True, "api_secret": True,
                      "access_token": True, "access_token_secret": True}


def test_twitter_incomplete_creds_hint():
    with pytest.raises(AdapterError, match="api_key"):
        _tw_adapter()._creds(_A({"access_token": "t"}))


def _fake_upload(value):
    async def upload(*a, **kw):
        return value
    return upload


async def test_twitter_publish_media_limit():
    ad = _tw_adapter()
    ad._upload_media = _fake_upload("mid")
    page = SimpleNamespace(account=_A(_tw_creds()), social_page_id="1",
                           social_page_name="Tester (@tester)")
    content = SimpleNamespace(text="hi", media=[_Media("photo")] * 5,
                              documents=[], settings={})
    with pytest.raises(AdapterError, match="не более 4"):
        await ad.publish(page, content)


async def test_twitter_publish_ok():
    from app.integrations import twitter as tw

    ad = _tw_adapter()
    ad._upload_media = _fake_upload("m1")
    page = SimpleNamespace(account=_A(_tw_creds()), social_page_id="123",
                           social_page_name="Tester (@tester)")

    async def fake_request_json(self, method, url, *, body, params=None):
        assert url.endswith("/2/tweets")
        assert body["media"]["media_ids"] == ["m1"]
        return _FakeResp({"data": {"id": "999"}})

    tw.OAuth1Client.request_json = fake_request_json
    content = SimpleNamespace(text="hi", media=[_Media("photo")],
                              documents=[], settings={})
    out = await ad.publish(page, content)
    assert out.ok and out.external_id == "999"
    assert out.external_url == "https://x.com/tester/status/999"


class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status
        self.text = json.dumps(payload)

    def json(self):
        return self._p


# ---------- Threads ----------

def test_threads_connect_fields():
    from app.integrations.threads import ThreadsAdapter
    fields = {f["name"] for f in ThreadsAdapter().connect_requires()}
    assert fields == {"access_token", "threads_user_id"}


async def test_threads_rejects_empty_and_story():
    from app.integrations.threads import ThreadsAdapter
    ad = ThreadsAdapter()
    page = SimpleNamespace(account=_A({"access_token": "t", "threads_user_id": "u"}))
    with pytest.raises(AdapterError, match="пустой"):
        await ad.publish(page, SimpleNamespace(text="", media=[], documents=[],
                                               settings={}))
    with pytest.raises(AdapterError, match="историй"):
        await ad.publish(page, SimpleNamespace(text="x", media=[], documents=[],
                                               settings={"publish_as_story": True}))


# ---------- Tumblr ----------

def test_tumblr_connect_fields():
    from app.integrations.tumblr import TumblrAdapter
    fields = {f["name"] for f in TumblrAdapter().connect_requires()}
    assert fields == {"api_key", "api_secret", "access_token", "access_token_secret"}


async def test_tumblr_rejects_video_and_empty():
    from app.integrations.tumblr import TumblrAdapter
    ad = TumblrAdapter()
    page = SimpleNamespace(account=_A({"api_key": "k", "api_secret": "s",
                                       "access_token": "t", "access_token_secret": "ts"}),
                           social_page_id="blog")
    with pytest.raises(AdapterError, match="видео"):
        await ad.publish(page, SimpleNamespace(text="txt", media=[_Media("video")],
                                               documents=[], settings={}))
    with pytest.raises(AdapterError, match="пустой"):
        await ad.publish(page, SimpleNamespace(text="", media=[], documents=[],
                                               settings={}))


# ---------- Planned-заглушки ----------

@pytest.mark.parametrize("sid,slug", [(5, "moimir"), (7, "viber"), (16, "whatsapp"),
                                      (18, "yappy"), (21, "vibes")])
async def test_planned_adapters_fail_with_hint(sid, slug):
    ad = get_adapter(sid)
    assert ad.source_id == sid
    with pytest.raises(AdapterError, match="недоступна"):
        await ad.publish(SimpleNamespace(), SimpleNamespace())


# ---------- YouTube (исправления ревью) ----------

async def test_youtube_rss_items_parse_direct():
    """Прямая проверка парсинга RSS без сети: два валидных entry из трёх."""
    import httpx as _httpx
    xml = ("<feed><entry><yt:videoId>abc123</yt:videoId><title>Раз</title></entry>"
           "<entry><title>нет id</title></entry>"
           "<entry><yt:videoId>def456</yt:videoId><title>Два</title></entry></feed>")

    class FakeResp:
        status_code = 200
        text = xml

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, **kw):
            return FakeResp()

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    try:
        items = await YouTubeAdapter().fetch_source_items("UC123")
    finally:
        _httpx.AsyncClient = orig
    assert [i.external_id for i in items] == ["abc123", "def456"]
    assert items[0].url == "https://youtu.be/abc123"


async def test_youtube_publish_requires_video():
    page = SimpleNamespace(account=_A({"refresh_token": "r", "client_id": "c",
                                       "client_secret": "s"}))
    with pytest.raises(AdapterError, match="видеофайл"):
        await YouTubeAdapter().publish(page, SimpleNamespace(
            text="", media=[], documents=[], settings={}))


async def test_read_media_bytes_to_file(tmp_path):
    """Стриминг из локального хранилища: содержимое копируется, размер возвращается."""
    from app.integrations.base import read_media_bytes_to_file
    from app.config import settings
    from pathlib import Path
    root = Path(settings.storage_dir)
    (root / "posts" / "photos").mkdir(parents=True, exist_ok=True)
    src = root / "posts" / "photos" / "clip.mp4"
    payload = b"\x00\x01video-bytes" * 1000
    src.write_bytes(payload)
    media = SimpleNamespace(file_path="posts/photos/clip.mp4")
    dst = tmp_path / "out.mp4"
    size = await read_media_bytes_to_file(media, str(dst))
    assert size == len(payload)
    assert dst.read_bytes() == payload
