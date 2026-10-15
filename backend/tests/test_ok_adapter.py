"""Одноклассники: подключение аккаунта. group.getInfo возвращает прямой массив
групп — парсер не должен падать с AttributeError: 'list' object has no attribute 'get'."""
import pytest

from app.integrations.base import AdapterError
from app.integrations.ok import OKAdapter


class _A:
    def __init__(self, creds=None):
        self.credentials = creds or {}


def _fake_call(responses: dict):
    async def call(self, method, creds, extra_params=None):
        return responses[method]
    return call


async def test_fetch_pages_list_response(monkeypatch):
    """Реальный формат: group.getInfo отдаёт JSON-массив групп."""
    monkeypatch.setattr(OKAdapter, "call", _fake_call({
        "users.getCurrentUser": {"uid": "111", "name": "Я", "pic_1": "http://ok/me.png"},
        "group.getUserGroupsV2": {"groups": [{"groupId": "g1"}, {"groupId": "g2"}]},
        "group.getInfo": [
            {"uid": "g1", "name": "Группа 1", "status": "ACTIVE"},
            {"uid": "g2", "name": "Группа 2", "status": "BLOCKED"},
        ],
    }))
    pages = await OKAdapter().fetch_pages(_A())
    assert [p.social_page_id for p in pages] == ["111", "g1", "g2"]
    assert pages[0].kind == "user" and pages[1].kind == "group"
    # у групп аватарка больше не запрашивается — пустая строка (у профиля — pic_1)
    assert all(p.photo == "" for p in pages[1:])
    assert pages[0].photo == "http://ok/me.png"
    assert [p.can_post for p in pages[1:]] == [True, False]  # BLOCKED не может постить


async def test_fetch_pages_dict_response(monkeypatch):
    """Словарная форма ответа (id → группа) тоже поддерживается."""
    monkeypatch.setattr(OKAdapter, "call", _fake_call({
        "users.getCurrentUser": {"uid": "111", "name": "Я"},
        "group.getUserGroupsV2": {"groups": [{"groupId": "g1"}]},
        "group.getInfo": {"g1": {"name": "Группа 1", "status": "ACTIVE"}},
    }))
    pages = await OKAdapter().fetch_pages(_A())
    assert len(pages) == 2
    assert pages[1].social_page_id == "g1"
    assert pages[1].name == "Группа 1"


async def test_fetch_pages_bad_profile_raises_hint(monkeypatch):
    """Если профиль не вернулся — понятная ошибка вместо AttributeError/KeyError."""
    monkeypatch.setattr(OKAdapter, "call", _fake_call({
        "users.getCurrentUser": [],
        "group.getUserGroupsV2": {"groups": []},
        "group.getInfo": [],
    }))
    with pytest.raises(AdapterError, match="профиль"):
        await OKAdapter().fetch_pages(_A())


async def test_fetch_pages_no_groups(monkeypatch):
    monkeypatch.setattr(OKAdapter, "call", _fake_call({
        "users.getCurrentUser": {"uid": "111", "name": "Я"},
        "group.getUserGroupsV2": {"groups": []},
        "group.getInfo": [],
    }))
    pages = await OKAdapter().fetch_pages(_A())
    assert len(pages) == 1 and pages[0].kind == "user"


def _base_responses(info_response):
    return {
        "users.getCurrentUser": {"uid": "111", "name": "Я"},
        "group.getUserGroupsV2": {"groups": [{"groupId": "g1"}, {"groupId": "g2"}]},
        "group.getInfo": info_response,
    }


async def test_fetch_pages_single_group_object(monkeypatch):
    """Один uid: API может вернуть объект группы напрямую, а не массив/словарь."""
    monkeypatch.setattr(OKAdapter, "call", _fake_call(_base_responses(
        {"uid": "g1", "name": "Моя группа", "status": "ACTIVE"})))
    pages = await OKAdapter().fetch_pages(_A({"groupId": "g1"}))
    assert len(pages) == 3
    g = pages[1]
    assert g.social_page_id == "g1" and g.name == "Моя группа"


async def test_fetch_pages_names_without_id_keys(monkeypatch):
    """В ответе нет uid/groupId — названия маппятся по порядку запроса."""
    monkeypatch.setattr(OKAdapter, "call", _fake_call(_base_responses([
        {"name": "Первая", "status": "ACTIVE"},
        {"name": "Вторая", "status": "ACTIVE"},
    ])))
    pages = await OKAdapter().fetch_pages(_A())
    assert [p.name for p in pages[1:]] == ["Первая", "Вторая"]


async def test_fetch_pages_uppercase_fields(monkeypatch):
    """Поля в UPPERCASE (как в перечислении GroupBeanFields) тоже читаются."""
    monkeypatch.setattr(OKAdapter, "call", _fake_call(_base_responses([
        {"uid": "g1", "NAME": "Заглавная", "STATUS": "ACTIVE"},
        {"uid": "g2", "name": "Обычная", "status": "BLOCKED"},
    ])))
    pages = await OKAdapter().fetch_pages(_A())
    names = [p.name for p in pages[1:]]
    assert names == ["Заглавная", "Обычная"]
    assert [p.can_post for p in pages[1:]] == [True, False]
