"""Нечитаемые секреты: маркер FailedSecrets у конкретной записи, точечные флаги
в /accounts и /settings, понятная ошибка публикации, когда расшифровка упала
(смена ключа SECRET_KEY/CREDENTIALS_SECRET)."""
import pytest

from app.util import utcnow  # noqa: F401


def test_decrypt_failure_counter(monkeypatch):
    from app.services import crypto

    crypto._fernet = None
    monkeypatch.setattr(crypto.settings, "credentials_secret", "key-one")
    enc = crypto.encrypt_value({"access_token": "abc"})

    # ключ сменился — секрет больше не расшифровывается
    monkeypatch.setattr(crypto.settings, "credentials_secret", "key-two")
    crypto._fernet = None
    assert crypto.decrypt_value(enc) == {}
    assert crypto.decrypt_failures() > 0

    # сохранили новый секрет — счётчик сбрасывается (пользователь чинит)
    crypto.encrypt_value({"access_token": "abc"})
    assert crypto.decrypt_failures() == 0

    # legacy-запись в открытом виде — не ошибка
    assert crypto.decrypt_value({"access_token": "abc"}) == {"access_token": "abc"}
    assert crypto.decrypt_failures() == 0
    crypto._fernet = None  # не оставлять тестовый ключ в глобальном состоянии


def test_decrypt_returns_marker(monkeypatch):
    """Неудачная расшифровка возвращает маркер FailedSecrets: пустой dict
    (старый код работает как с пустыми credentials), но isinstance показывает,
    какое поле не читается."""
    from app.services import crypto

    crypto._fernet = None
    monkeypatch.setattr(crypto.settings, "credentials_secret", "key-one")
    enc = crypto.encrypt_value({"access_token": "abc"})
    monkeypatch.setattr(crypto.settings, "credentials_secret", "key-two")
    crypto._fernet = None

    out = crypto.decrypt_value(enc, "social_accounts.credentials")
    assert isinstance(out, crypto.FailedSecrets)
    assert not out                       # falsy, как пустой dict
    assert out.get("access_token") is None
    assert crypto.is_failed(out)
    assert not crypto.is_failed({})      # обычный dict — всё в порядке
    assert not crypto.is_failed({"access_token": "abc"})  # legacy без __enc
    crypto._fernet = None


def test_decrypt_warn_once_with_context(monkeypatch, caplog):
    """Предупреждение пишется один раз на поле (users.llm_config читается при
    каждом запросе — иначе лог захламляется), но счётчик растёт всегда."""
    import logging

    from app.services import crypto

    crypto._fernet = None
    monkeypatch.setattr(crypto.settings, "credentials_secret", "key-one")
    enc = crypto.encrypt_value({"api_key": "sk"})
    monkeypatch.setattr(crypto.settings, "credentials_secret", "key-two")
    crypto._fernet = None

    with caplog.at_level(logging.WARNING, logger="crypto"):
        crypto.decrypt_value(enc, "users.llm_config")
        crypto.decrypt_value(enc, "users.llm_config")
        crypto.decrypt_value(enc, "social_accounts.credentials")
    warnings = [r for r in caplog.records if "users.llm_config" in r.message]
    assert len(warnings) == 1  # одно на контекст
    assert any("social_accounts.credentials" in r.message for r in caplog.records)
    assert crypto.decrypt_failures() >= 3  # считаются все неудачи, не только залогированные

    # пересохранение секрета снова разрешает предупреждения
    crypto.encrypt_value({"api_key": "sk2"})
    assert crypto._warned == set()
    crypto._fernet = None


async def test_accounts_secrets_flag_ok(client, auth_headers, mock_pages):
    r = await client.get("/api/web/accounts", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["secrets_broken"] is False


async def test_banner_not_shown_when_only_llm_key_broken(client, auth_headers, monkeypatch):
    """Регрессия (инцидент 21.11): не расшифровался только LLM-ключ/токен бота,
    токены аккаунтов в порядке — баннер «ключи не расшифровались» показываться
    не должен: публикации работают. Проблема видна точечно в Настройках.

    Раньше флаг брался из глобального счётчика ошибок: он «лип» после первой
    же неудачной расшифровки любого поля и не сбрасывался."""
    from app.services import crypto

    crypto._fernet = None
    # LLM-ключ и токен бота сохранены при «старом» ключе шифрования
    r = await client.put("/api/web/settings", headers=auth_headers, json={
        "llm_config": {"base_url": "", "api_key": "sk-old", "model": "", "prompt": ""},
        "notify": {"bot_token": "123:abc", "on_error": True, "on_success": False,
                   "inbox_page_ids": []},
    })
    assert r.status_code == 200, r.text

    # ключ шифрования сменился (обновление стека Portainer)
    monkeypatch.setattr(crypto.settings, "credentials_secret", "new-encryption-key")
    crypto._fernet = None

    # аккаунт подключается уже при новом ключе — его токены в порядке
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 99, "credentials": {"nickname": "tester"}})
    assert r.status_code == 200, r.text

    # баннер не показывается: все аккаунты читаются
    r = await client.get("/api/web/accounts", headers=auth_headers)
    d = r.json()
    assert d["secrets_broken"] is False
    assert all(a["secrets_broken"] is False for a in d["list"])

    # а в Настройках видно, что LLM-ключ и токен бота не читаются
    r = await client.get("/api/web/settings", headers=auth_headers)
    d = r.json()
    assert d["llm_secrets_broken"] is True
    assert d["notify_secrets_broken"] is True
    assert d["llm_config"].get("api_key") != "***"  # ключ пуст — не маскируем

    # повторный ввод LLM-ключа при новом ключе шифрования чинит флаг
    r = await client.put("/api/web/settings", headers=auth_headers, json={
        "llm_config": {"base_url": "", "api_key": "sk-new", "model": "", "prompt": ""},
    })
    assert r.status_code == 200, r.text
    assert r.json()["llm_secrets_broken"] is False

    crypto._fernet = None  # не оставлять тестовый ключ в глобальном состоянии


async def test_banner_shown_for_broken_account_tokens(client, auth_headers,
                                                      mock_pages, monkeypatch):
    """Токены аккаунта не расшифровались — баннер показывается, у аккаунта
    стоит флаг; после повторного подключения аккаунта флаг исчезает."""
    from app.services import crypto

    crypto._fernet = None
    # аккаунт подключён (mock_pages) при старом ключе, затем ключ сменился
    monkeypatch.setattr(crypto.settings, "credentials_secret", "another-key")
    crypto._fernet = None

    r = await client.get("/api/web/accounts", headers=auth_headers)
    d = r.json()
    assert d["secrets_broken"] is True
    assert d["list"][0]["secrets_broken"] is True

    # чиним: старый аккаунт с нечитаемыми токенами удаляем и подключаем
    # заново уже при новом ключе шифрования
    for a in (a for a in d["list"] if a["secrets_broken"]):
        r = await client.delete(f"/api/web/accounts/{a['id']}", headers=auth_headers)
        assert r.status_code == 200, r.text
    r = await client.post("/api/web/accounts/connect", headers=auth_headers,
                          json={"source_id": 99, "credentials": {"nickname": "tester"}})
    assert r.status_code == 200, r.text
    r = await client.get("/api/web/accounts", headers=auth_headers)
    d = r.json()
    assert d["secrets_broken"] is False
    assert all(a["secrets_broken"] is False for a in d["list"])

    crypto._fernet = None


async def test_publish_empty_credentials_clear_error(client, auth_headers):
    """Аккаунт реальной сети (Threads) с пустыми секретами: пост падает
    с точным диагнозом вместо «аккаунта не настроено» из адаптера."""
    from sqlalchemy import select

    from app.db import SessionFactory
    from app.models import SocialAccount, SocialPage, User

    async with SessionFactory() as s:
        user = (await s.execute(
            select(User).where(User.email == "user@test.io"))).scalar_one()
        acc = SocialAccount(user_id=user.id, source_id=12, credentials={})
        s.add(acc)
        await s.flush()
        page = SocialPage(user_id=user.id, account_id=acc.id, source_id=12,
                          social_page_id="t1", social_page_name="Тест Threads",
                          page_kind="user", can_post=True)
        s.add(page)
        await s.commit()
        page_id = page.id

    r = await client.post("/api/web/posts", headers=auth_headers, json={
        "texts": [{"text": "Пост без секретов"}],
        "page_ids": [page_id],
        "publication_when_type": 1,
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["status"] == "failed", data
    assert data["targets"][0]["status"] == "failed"
    assert "Секреты аккаунта недоступны" in data["targets"][0]["error"]
