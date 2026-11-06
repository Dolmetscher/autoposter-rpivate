"""Нечитаемые секреты: счётчик crypto, флаг в /accounts и понятная ошибка
публикации, когда расшифровка упала (смены ключа SECRET_KEY/CREDENTIALS_SECRET)."""
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


async def test_accounts_secrets_flag_ok(client, auth_headers, mock_pages):
    r = await client.get("/api/web/accounts", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert r.json()["secrets_broken"] is False


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
