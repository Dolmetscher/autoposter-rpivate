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
