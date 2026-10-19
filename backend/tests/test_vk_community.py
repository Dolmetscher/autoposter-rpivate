"""Подключение ВКонтакте по токену сообщества (без OAuth)."""
import pytest

from app.integrations.vk import VKAdapter


def _adapter():
    return VKAdapter()


@pytest.fixture
def account():
    class A:
        credentials = {"access_token": "vk1-community-token"}

    return A()


def test_connect_form_fields():
    fields = {f["name"]: f["required"] for f in _adapter().connect_requires()}
    assert fields == {"access_token": True, "group_ref": False, "name": False}


def test_no_oauth_flow():
    """OAuth-поток у VK удалён: кнопки «Авторизоваться через ВК» быть не должно."""
    assert _adapter().oauth_authorize_url("x") is None
    import pytest as _pytest

    from app.constants import get_source
    assert get_source(1).oauth is False


@pytest.mark.parametrize("raw,expected", [
    ("https://vk.com/club123", "123"),
    ("https://vk.com/public77", "77"),
    ("https://vk.com/mygroup?w=wall", "mygroup"),
    ("@name", "name"),
    ("-123456", "123456"),
    ("777", "777"),
    ("", ""),
])
def test_normalize_group_ref(raw, expected):
    assert VKAdapter._normalize_group_ref(raw) == expected


async def test_fetch_pages_with_group_ref(account, monkeypatch):
    captured = {}

    async def fake_call(self, method, token, **params):
        captured["method"] = method
        captured["group_id"] = params.get("group_id")
        return {"groups": [{"id": 987, "name": "Моё сообщество",
                            "photo_100": "http://vk/img.png"}]}

    monkeypatch.setattr(VKAdapter, "call", fake_call)
    pages = await _adapter().fetch_pages(account, extra={"group_ref": "https://vk.com/club987"})
    assert captured["method"] == "groups.getById"
    assert captured["group_id"] == "987"
    assert len(pages) == 1
    assert pages[0].social_page_id == "987"
    assert pages[0].kind == "group"
    assert pages[0].name == "Моё сообщество"


async def test_fetch_pages_auto_detects_community(account, monkeypatch):
    async def fake_call(self, method, token, **params):
        assert params.get("group_id") is None  # без ссылки — автоопределение
        return {"groups": [{"id": 555, "name": "Автосообщество", "photo_100": ""}]}

    monkeypatch.setattr(VKAdapter, "call", fake_call)
    pages = await _adapter().fetch_pages(account)
    assert pages[0].social_page_id == "555"
    # поле «Название» перекрывает имя из API
    account.credentials["name"] = "Моё название"
    pages = await _adapter().fetch_pages(account)
    assert pages[0].name == "Моё название"


async def test_fetch_pages_auto_detect_failure_hint(account, monkeypatch):
    from app.integrations.base import AdapterError

    async def fake_call(self, method, token, **params):
        from app.integrations.base import AdapterError as E
        raise E("VK groups.getById: ошибка")

    monkeypatch.setattr(VKAdapter, "call", fake_call)
    with pytest.raises(AdapterError, match="укажите"):
        await _adapter().fetch_pages(account)


# ---------- публикация со ссылкой ----------

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _fake_client(monkeypatch, responses: dict, captured: dict):
    """Подменяет httpx.AsyncClient внутри модуля vk: responses — по URL метода."""
    import httpx as _httpx

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **kw):
            captured.setdefault("calls", []).append((url, data))
            key = next((k for k in responses if k in url), None)
            if key is None:
                return _Resp({"response": 1})
            payload = responses[key]
            if isinstance(payload, Exception):
                return _Resp({"error": {"error_msg": str(payload)}})
            return _Resp({"response": payload})

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    return orig


def _page(account):
    from types import SimpleNamespace
    return SimpleNamespace(account=account, social_page_id="987",
                           page_kind="group", account_id=1)


async def test_publish_link_goes_to_attachments(account, monkeypatch):
    """Ссылка уходит прямо в attachments: отдельного метода API для ссылки нет,
    а VK сам разворачивает из URL превью (Open Graph)."""
    from app.integrations.base import PublishContent

    captured = {}
    orig = _fake_client(monkeypatch, {"wall.post": {"post_id": 42}}, captured)
    try:
        out = await _adapter().publish(
            _page(account),
            PublishContent(text="Материал", settings={"link": "https://zennoprirokat.ru/x"}),
        )
    finally:
        import httpx as _httpx
        _httpx.AsyncClient = orig

    assert out.ok
    wall = next(c for u, c in captured["calls"] if "wall.post" in u)
    assert wall["attachments"] == "https://zennoprirokat.ru/x"
    # ссылка не дублируется в тексте
    assert wall["message"] == "Материал"
    # вызова несуществующего links.add быть не должно
    assert not any("links.add" in u for u, _ in captured["calls"])


async def test_publish_without_link_has_empty_attachments(account, monkeypatch):
    from app.integrations.base import PublishContent

    captured = {}
    orig = _fake_client(monkeypatch, {"wall.post": {"post_id": 42}}, captured)
    try:
        out = await _adapter().publish(
            _page(account), PublishContent(text="Материал"))
    finally:
        import httpx as _httpx
        _httpx.AsyncClient = orig

    assert out.ok
    wall = next(c for u, c in captured["calls"] if "wall.post" in u)
    assert wall["attachments"] == ""
    assert wall["message"] == "Материал"


# ---------- загрузка фото на стену ----------

class _RawResp:
    """Ответ сервера загрузки — сырой JSON (без обёртки {"response": ...})."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class _Media:
    name = "звонок1.png"
    type = "photo"
    file_path = "media/x.png"
    title = ""
    description = ""


BLOB = ('[{"markers_restarted":true,"photo":"143975546f:z","sizes":[],'
        '"kid":"53539d9aa22e3956f05528953c955335","sizes2":[]}]')


def _fake_upload_client(monkeypatch, upload_payload, save_responses, captured):
    """Подменяет httpx.AsyncClient для сценария загрузки фото.

    upload_payload — сырой ответ сервера загрузки (pu.vk.com);
    save_responses — ответы photos.saveWallPhoto (каждый вызов — следующий по списку).
    """
    import httpx as _httpx

    state = {"save": 0}

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, data=None, **kw):
            captured.setdefault("calls", []).append((url, data))
            if "getWallUploadServer" in url:
                return _RawResp({"response": {
                    "upload_url": "https://pu.vk.com/c1/ss2/upload.php?act=do_add",
                    "album_id": -14, "user_id": 987}})
            if "upload.php" in url:
                captured["files"] = kw.get("files")
                captured["upload_url"] = url
                return _RawResp(upload_payload)
            if "saveWallPhoto" in url:
                idx = min(state["save"], len(save_responses) - 1)
                state["save"] += 1
                return _RawResp(save_responses[idx])
            if "wall.post" in url:
                return _RawResp({"response": {"post_id": 42}})
            return _RawResp({"response": 1})

    orig = _httpx.AsyncClient
    _httpx.AsyncClient = FakeClient
    return orig


async def _publish_with_photo(monkeypatch, upload_payload, save_responses):
    """Публикует пост с фото через подменённый клиент VK."""
    from app.integrations.base import PublishContent

    captured = {}
    orig = _fake_upload_client(monkeypatch, upload_payload, save_responses, captured)

    async def fake_read(media):
        return b"\x89PNG\r\n\x1a\nfake"

    monkeypatch.setattr("app.integrations.vk.read_media_bytes", fake_read)
    try:
        out = await _adapter().publish(
            _page(_FakeAccount()), PublishContent(text="Материал", media=[_Media()]))
    finally:
        import httpx as _httpx
        _httpx.AsyncClient = orig
    return out, captured


class _FakeAccount:
    credentials = {"access_token": "vk1-community-token"}


async def test_photo_upload_modern_format(account, monkeypatch):
    """Новый формат сервера загрузки: photo — JSON с данными фото."""
    out, captured = await _publish_with_photo(
        monkeypatch,
        {"server": 856312, "photo": BLOB, "hash": "abc123"},
        [{"response": [{"id": 456, "owner_id": -987, "album_id": -14}]}],
    )

    assert out.ok
    wall = next(c for u, c in captured["calls"] if "wall.post" in u)
    assert wall["attachments"] == "photo-987_456"
    save = next(c for u, c in captured["calls"] if "saveWallPhoto" in u)
    assert save["photo"] == BLOB
    assert save["server"] == "856312"
    assert save["hash"] == "abc123"


async def test_photo_upload_sends_ascii_filename(account, monkeypatch):
    """Имя файла в multipart — ASCII: кириллица ломает разбор формы на стороне VK."""
    out, captured = await _publish_with_photo(
        monkeypatch,
        {"server": 856312, "photo": BLOB, "hash": "abc123"},
        [{"response": [{"id": 456, "owner_id": -987}]}],
    )

    assert out.ok
    files = captured["files"]
    name, _, content_type = files["photo"]
    assert name.isascii(), name
    assert name.endswith(".png")
    assert content_type == "image/png"


async def test_photo_upload_rejected_by_vk(account, monkeypatch):
    """Сервер загрузки не принял файл (photo пустой) — понятная ошибка вместо
    невнятного «photo is undefined» от photos.saveWallPhoto."""
    from app.integrations.base import AdapterError

    with pytest.raises(AdapterError) as ei:
        await _publish_with_photo(
            monkeypatch,
            {"server": 856312, "photo": "", "hash": "abc123"},
            [{"response": [{"id": 456, "owner_id": -987}]}],
        )

    msg = str(ei.value)
    assert "не принял файл" in msg
    assert "photo is undefined" not in msg


async def test_photo_upload_error_body(account, monkeypatch):
    """Ошибка в ответе сервера загрузки — показываем текст VK."""
    from app.integrations.base import AdapterError

    with pytest.raises(AdapterError) as ei:
        await _publish_with_photo(
            monkeypatch,
            {"error": {"error_code": 100, "error_msg": "Ошибка загрузки"}},
            [{"response": [{"id": 456, "owner_id": -987}]}],
        )

    assert "Ошибка загрузки" in str(ei.value)


async def test_photo_upload_falls_back_to_photo_id(account, monkeypatch):
    """Если VK не принял JSON-формат photo — пробуем идентификатор фото из него."""
    out, captured = await _publish_with_photo(
        monkeypatch,
        {"server": 856312, "photo": BLOB, "hash": "abc123"},
        [
            {"error": {"error_code": 100, "error_msg":
                       "One of the parameters specified was missing or invalid: photo is undefined"}},
            {"response": [{"id": 456, "owner_id": -987}]},
        ],
    )

    assert out.ok
    saves = [c for u, c in captured["calls"] if "saveWallPhoto" in u]
    assert len(saves) == 2
    assert saves[0]["photo"] == BLOB
    assert saves[1]["photo"] == "143975546f"
    wall = next(c for u, c in captured["calls"] if "wall.post" in u)
    assert wall["attachments"] == "photo-987_456"


async def test_photo_upload_dict_response(account, monkeypatch):
    """Ответ photos.saveWallPhoto объектом, а не списком — тоже обрабатывается."""
    out, captured = await _publish_with_photo(
        monkeypatch,
        {"server": 856312, "photo": BLOB, "hash": "abc123"},
        [{"response": {"id": 456, "owner_id": -987}}],
    )

    assert out.ok
    wall = next(c for u, c in captured["calls"] if "wall.post" in u)
    assert wall["attachments"] == "photo-987_456"


async def test_photo_upload_non_json_response(account, monkeypatch):
    """Не-JSON ответ (прокси/заглушка) — понятная ошибка вместо исключения."""
    from app.integrations.base import AdapterError

    with pytest.raises(AdapterError) as ei:
        await _publish_with_photo(
            monkeypatch,
            ValueError("Expecting value: line 1 column 1 (char 0)"),
            [{"response": [{"id": 456, "owner_id": -987}]}],
        )

    assert "не-JSON" in str(ei.value)


def test_upload_filename_is_ascii():
    """Имя файла для загрузки — ASCII с сохранением расширения."""
    from types import SimpleNamespace

    from app.integrations.base import upload_filename

    assert upload_filename(SimpleNamespace(name="звонок1.png", type="photo"), "photo.jpg") \
        == "photo.png"
    assert upload_filename(SimpleNamespace(name="My Photo.png", type="photo"), "photo.jpg") \
        == "My_Photo.png"
    assert upload_filename(SimpleNamespace(name="", type="photo"), "photo.jpg") == "photo.jpg"
    assert upload_filename(SimpleNamespace(name="ролик.mp4", type="video"), "video.mp4") \
        == "video.mp4"
