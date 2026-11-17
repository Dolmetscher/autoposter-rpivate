"""GIF-анимации: .gif помечается загрузчиком как обычное «фото» (type="photo"),
но VK и Telegram без специальной обработки превращают их в статичную картинку.

VK: GIF уходит через загрузку документов (_upload_doc) — ВК сам показывает
документ с расширением .gif на стене анимированным.
Telegram (Bot API): одиночный GIF отправляется sendAnimation вместо sendPhoto;
в media group анимации не принимаются API — GIF остаётся обычным фото.
"""
from types import SimpleNamespace

from app.integrations.telegram import TelegramAdapter
from app.integrations.vk import VKAdapter


class _RawResp:
    """Ответ сервера — сырой JSON (VK: без обёртки, Telegram: {ok, result})."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class _FakeClient:
    """Собирает все POST-запросы; ответы по подстроке URL."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def post(self, url, data=None, **kw):
        self.calls.append((url, data, kw))
        for needle, payload in self.routes:
            if needle in url:
                return _RawResp(payload)
        raise AssertionError(f"неожиданный запрос: {url}")


def _install(monkeypatch, client):
    """Подменяет httpx.AsyncClient фейковым клиентом; monkeypatch вернёт
    оригинал по завершении теста."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *a, **kw: client)


# ---------- is_gif_media ----------

def test_is_gif_media():
    from app.integrations.base import is_gif_media

    assert is_gif_media(SimpleNamespace(name="кот.gif", file_path="media/x.gif",
                                        type="photo"))
    # расширение только в пути хранилища — тоже GIF
    assert is_gif_media(SimpleNamespace(name="", file_path="media/abc.GIF",
                                        type="photo"))
    # обычные фото/видео — не GIF
    assert not is_gif_media(SimpleNamespace(name="звонок1.png",
                                            file_path="media/x.png", type="photo"))
    assert not is_gif_media(SimpleNamespace(name="clip.mp4",
                                            file_path="media/v.mp4", type="video"))
    # .gif внутри имени не на конце — не GIF
    assert not is_gif_media(SimpleNamespace(name="gift.png",
                                            file_path="media/gift.png", type="photo"))


# ---------- VK ----------

async def test_vk_gif_uploaded_as_document(monkeypatch):
    """GIF публикуется документом: doc{owner}_{id} в attachments wall.post,
    файл уходит на docs.getWallUploadServer (photos.* не используется)."""
    from app.integrations.base import PublishContent

    client = _FakeClient([
        ("docs.getWallUploadServer",
         {"response": {"upload_url": "https://pu.vk.com/upload_docs.php"}}),
        ("upload_docs.php", {"file": "docfile123"}),
        ("docs.save", {"response": [{"id": 77, "owner_id": -987}]}),
        ("wall.post", {"response": {"post_id": 42}}),
    ])
    _install(monkeypatch, client)

    async def fake_read(media):
        return b"GIF89a fake-animation"

    monkeypatch.setattr("app.integrations.vk.read_media_bytes", fake_read)

    class Acc:
        credentials = {"access_token": "community-token", "user_token": "user-token"}

    page = SimpleNamespace(account=Acc(), social_page_id="987",
                           page_kind="group", account_id=1)
    media = SimpleNamespace(name="анимация.gif", file_path="media/anim.gif",
                            type="photo", title="", description="")
    out = await VKAdapter().publish(
        page, PublishContent(text="Смотри, как летает", media=[media]))

    assert out.ok
    urls = [u for u, _, _ in client.calls]
    assert "https://api.vk.com/method/docs.getWallUploadServer" in urls
    assert not any("photos.getWallUploadServer" in u for u in urls), \
        "GIF не должен уходить через загрузку фото"
    wall = next(d for u, d, _ in client.calls if "wall.post" in u)
    assert wall["attachments"] == "doc-987_77"
    # имя файла в multipart сохраняет расширение .gif — по нему ВК понимает,
    # что документ анимированный
    upload = next(kw for u, d, kw in client.calls if "upload_docs.php" in u)
    fname = upload["files"]["file"][0]
    assert fname.endswith(".gif"), fname


async def test_vk_regular_photo_not_affected(monkeypatch):
    """Обычное .png по-прежнему уходит через photos.getWallUploadServer."""
    from app.integrations.base import PublishContent

    client = _FakeClient([
        ("photos.getWallUploadServer",
         {"response": {"upload_url": "https://pu.vk.com/upload.php"}}),
        ("upload.php",
         {"server": 856312, "photo": '[{"photo":"x","sizes":[]}]', "hash": "h"}),
        ("photos.saveWallPhoto",
         {"response": [{"id": 456, "owner_id": -987}]}),
        ("wall.post", {"response": {"post_id": 42}}),
    ])
    _install(monkeypatch, client)

    async def fake_read(media):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("app.integrations.vk.read_media_bytes", fake_read)

    class Acc:
        credentials = {"access_token": "community-token", "user_token": "user-token"}

    page = SimpleNamespace(account=Acc(), social_page_id="987",
                           page_kind="group", account_id=1)
    media = SimpleNamespace(name="photo.png", file_path="media/p.png",
                            type="photo", title="", description="")
    out = await VKAdapter().publish(
        page, PublishContent(text="Обычное фото", media=[media]))

    assert out.ok
    urls = [u for u, _, _ in client.calls]
    assert any("photos.getWallUploadServer" in u for u in urls)
    assert not any("docs.getWallUploadServer" in u for u in urls)
    wall = next(d for u, d, _ in client.calls if "wall.post" in u)
    assert wall["attachments"] == "photo-987_456"


# ---------- Telegram (Bot API) ----------

TG_OK = {"ok": True, "result": {"message_id": 55}}


def _tg_page():
    class Acc:
        credentials = {"mode": "bot", "bot_token": "123:ABC"}

    return SimpleNamespace(account=Acc(), social_page_id="@channel",
                           social_page_name="@channel", account_id=1)


def _tg_media(name):
    return SimpleNamespace(name=name, file_path=f"media/{name}", type="photo",
                           title="", description="")


async def _tg_publish(monkeypatch, media_list):
    from app.integrations.base import PublishContent

    client = _FakeClient([
        ("sendAnimation", TG_OK),
        ("sendPhoto", TG_OK),
        ("sendVideo", TG_OK),
        ("sendDocument", TG_OK),
        ("sendMediaGroup", {"ok": True, "result": [{"message_id": 55},
                                                   {"message_id": 56}]}),
    ])
    _install(monkeypatch, client)

    async def fake_read(media):
        return b"GIF89a fake-animation"

    monkeypatch.setattr("app.integrations.telegram.read_media_bytes", fake_read)
    out = await TelegramAdapter().publish(
        _tg_page(), PublishContent(text="Анимация", media=media_list))
    return out, client


async def test_telegram_single_gif_sent_as_animation(monkeypatch):
    """Одиночный .gif уходит через sendAnimation с полем animation."""
    out, client = await _tg_publish(monkeypatch, [_tg_media("взлёт.gif")])

    assert out.ok
    url, data, kw = next(c for c in client.calls if "sendAnimation" in c[0])
    assert not any("sendPhoto" in c[0] for c in client.calls)
    assert "animation" in kw["files"], kw["files"].keys()
    assert data["caption"] == "Анимация"


async def test_telegram_single_photo_still_send_photo(monkeypatch):
    """Обычное фото по-прежнему уходит через sendPhoto с полем photo."""
    out, client = await _tg_publish(monkeypatch, [_tg_media("snap.png")])

    assert out.ok
    url, data, kw = next(c for c in client.calls if "sendPhoto" in c[0])
    assert not any("sendAnimation" in c[0] for c in client.calls)
    assert "photo" in kw["files"], kw["files"].keys()


async def test_telegram_gif_in_group_stays_photo(monkeypatch):
    """В media group анимации API не принимает: GIF уходит как обычное фото
    (иначе весь альбом падал бы с ошибкой)."""
    import json as _json

    out, client = await _tg_publish(
        monkeypatch, [_tg_media("взлёт.gif"), _tg_media("snap.png")])

    assert out.ok
    url, data, _ = next(c for c in client.calls if "sendMediaGroup" in c[0])
    group = _json.loads(data["media"])
    assert [e["type"] for e in group] == ["photo", "photo"]
    assert not any("sendAnimation" in c[0] for c in client.calls)
