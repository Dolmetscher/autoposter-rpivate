"""Режим «только видео» (video_only) для ВК и ОК: ролик уходит в раздел
видеозаписей сообщества, пост на стене/медитопик не создаётся."""
import pytest

from app.integrations.base import AdapterError, PublishContent
from app.integrations.ok import OKAdapter
from app.integrations.vk import VKAdapter


class _Acc:
    def __init__(self, creds):
        self.credentials = creds


class _Page:
    def __init__(self, page_id, creds, kind="group"):
        self.social_page_id = page_id
        self.page_kind = kind
        self.account = _Acc(creds)


class _Media:
    def __init__(self, mtype="video", name="clip.mp4"):
        self.type = mtype
        self.name = name
        self.title = ""
        self.description = ""
        self.file_path = f"media/{name}"


def _content(settings, media):
    return PublishContent(text="текст поста", media=media, documents=[],
                          settings=settings)


# ---------- ВКонтакте ----------

@pytest.mark.asyncio
async def test_vk_video_only_uploads_without_wall_post(monkeypatch):
    uploaded = []

    async def fake_upload(self, client, token, group_id, media):
        uploaded.append((group_id, media.name))
        return "video-777_42"

    monkeypatch.setattr(VKAdapter, "_upload_video", fake_upload)

    async def fail_call(self, method, token, **params):
        raise AssertionError(f"wall.post не должен вызываться, был {method}")

    monkeypatch.setattr(VKAdapter, "call", fail_call)

    page = _Page("-777", {"access_token": "t", "user_token": "u"})
    out = await VKAdapter().publish(
        page, _content({"video_only": True}, [_Media("video", "a.mp4")]))

    assert out.ok
    assert out.external_id == "-777_video42"
    assert out.external_url == "https://vk.com/video-777_42"
    assert uploaded == [(777, "a.mp4")]


@pytest.mark.asyncio
async def test_vk_video_only_multiple_videos(monkeypatch):
    async def fake_upload(self, client, token, group_id, media):
        return f"video-777_{media.name[0]}"

    monkeypatch.setattr(VKAdapter, "_upload_video", fake_upload)
    page = _Page("-777", {"access_token": "t"})
    out = await VKAdapter().publish(
        page, _content({"video_only": True},
                       [_Media("video", "a.mp4"), _Media("video", "b.mp4")]))
    assert out.ok
    assert out.external_id == "-777_videoa"
    assert out.external_url == "https://vk.com/video-777_a"


@pytest.mark.asyncio
async def test_vk_video_only_without_videos_raises():
    page = _Page("-777", {"access_token": "t"})
    with pytest.raises(AdapterError, match="только видео"):
        await VKAdapter().publish(
            page, _content({"video_only": True}, [_Media("photo", "p.jpg")]))


@pytest.mark.asyncio
async def test_vk_delete_video_entry(monkeypatch):
    calls = []

    async def fake_call(self, method, token, **params):
        calls.append((method, params))
        return {}

    monkeypatch.setattr(VKAdapter, "call", fake_call)
    page = _Page("-777", {"access_token": "t"})
    assert await VKAdapter().delete(page, "-777_video42") is True
    assert calls == [("video.delete", {"owner_id": -777, "video_id": 42})]


@pytest.mark.asyncio
async def test_vk_delete_wall_post_unchanged(monkeypatch):
    calls = []

    async def fake_call(self, method, token, **params):
        calls.append((method, params))
        return {}

    monkeypatch.setattr(VKAdapter, "call", fake_call)
    page = _Page("-777", {"access_token": "t"})
    assert await VKAdapter().delete(page, "-777_123") is True
    assert calls == [("wall.delete", {"owner_id": -777, "post_id": 123})]


@pytest.mark.asyncio
async def test_vk_stats_skips_video_entries(monkeypatch):
    async def fail_call(self, method, token, **params):
        raise AssertionError("wall.getById не должен вызываться для видео")

    monkeypatch.setattr(VKAdapter, "call", fail_call)
    page = _Page("-777", {"access_token": "t"})
    assert await VKAdapter().fetch_stats(page, "-777_video42") is None


# ---------- Одноклассники ----------

@pytest.mark.asyncio
async def test_ok_video_only_uploads_without_mediatopic(monkeypatch):
    async def fake_upload(self, creds, gid, media):
        assert gid == "g1"
        return "12345"

    monkeypatch.setattr(OKAdapter, "_upload_video", fake_upload)

    async def fail_call(self, method, creds, extra_params=None):
        raise AssertionError(f"mediatopic.post не должен вызываться, был {method}")

    monkeypatch.setattr(OKAdapter, "call", fail_call)

    page = _Page("g1", {"access_token": "t", "application_key": "k",
                        "session_secret_key": "s"})
    out = await OKAdapter().publish(
        page, _content({"video_only": True}, [_Media("video", "v.mp4")]))

    assert out.ok
    assert out.external_id == "video12345"
    assert out.external_url == "https://ok.ru/video/12345"


@pytest.mark.asyncio
async def test_ok_video_only_without_videos_raises():
    page = _Page("g1", {"access_token": "t", "application_key": "k",
                        "session_secret_key": "s"})
    with pytest.raises(AdapterError, match="только видео"):
        await OKAdapter().publish(
            page, _content({"video_only": True}, [_Media("photo", "p.jpg")]))


@pytest.mark.asyncio
async def test_ok_video_only_two_videos_raises(monkeypatch):
    async def fake_upload(self, creds, gid, media):
        return "1"

    monkeypatch.setattr(OKAdapter, "_upload_video", fake_upload)
    page = _Page("g1", {"access_token": "t", "application_key": "k",
                        "session_secret_key": "s"})
    with pytest.raises(AdapterError, match="одно видео"):
        await OKAdapter().publish(
            page, _content({"video_only": True},
                           [_Media("video", "a.mp4"), _Media("video", "b.mp4")]))


@pytest.mark.asyncio
async def test_ok_upload_video_helper(monkeypatch):
    """Хелпер: video.getUploadUrl → POST файла → video_id из ответа."""

    async def fake_call(self, method, creds, extra_params=None):
        assert method == "video.getUploadUrl"
        assert extra_params == {"file_name": "v.mp4", "gid": "g1"}
        return "http://upload"

    monkeypatch.setattr(OKAdapter, "call", fake_call)

    async def fake_read(media):
        return b"videobytes"

    monkeypatch.setattr("app.integrations.ok.read_media_bytes", fake_read)

    class _Resp:
        content = b"{}"

        def json(self):
            return {"video_id": 987}

    class _Client:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, **kw):
            assert url == "http://upload"
            return _Resp()

    monkeypatch.setattr("app.integrations.ok.httpx.AsyncClient", _Client)

    adapter = OKAdapter()
    vid = await adapter._upload_video({}, "g1", _Media("video", "v.mp4"))
    assert vid == "987"


@pytest.mark.asyncio
async def test_ok_delete_video_and_topic(monkeypatch):
    calls = []

    async def fake_call(self, method, creds, extra_params=None):
        calls.append((method, dict(extra_params or {})))
        return {}

    monkeypatch.setattr(OKAdapter, "call", fake_call)
    page = _Page("g1", {"access_token": "t", "application_key": "k",
                        "session_secret_key": "s"})
    adapter = OKAdapter()

    assert await adapter.delete(page, "video987") is True
    assert await adapter.delete(page, "700000000123") is True
    assert calls == [("video.delete", {"video_id": "987"}),
                     ("mediatopic.delete", {"topic_id": "700000000123"})]


# ---------- общее ----------

def test_vk_note_mentions_channels():
    """Примечание ВК объясняет ситуацию с каналами (im/channels) без API."""
    note = VKAdapter.note
    assert "каналы" in note.lower()
    assert "api" in note.lower()
