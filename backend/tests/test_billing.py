"""Биллинг: создание платежа (мок ЮKassa), вебхук, ручная активация,
истечение подписок, guard ролей."""
import pytest

from app.services import billing as billing_service
from app.util import utcnow


async def test_pay_without_provider(client, auth_headers):
    r = await client.post("/api/web/billing/pay", headers=auth_headers,
                          json={"plan": "pro", "period": "month"})
    assert r.status_code == 501
    assert "YOOKASSA" in r.json()["detail"]


async def test_pay_creates_subscription_and_redirect(client, auth_headers, monkeypatch):
    async def fake_create(**kwargs):
        assert kwargs["amount_kop"] == 999 * 100  # pro, месяц
        assert kwargs["metadata"]["plan"] == "pro"
        return {"id": "pay_123", "status": "pending",
                "confirmation": {"type": "redirect",
                                 "confirmation_url": "https://yoomoney.ru/checkout/pay?x=1"}}

    monkeypatch.setattr(billing_service, "api_create_payment", fake_create)
    monkeypatch.setattr(billing_service, "configured", lambda: True)

    r = await client.post("/api/web/billing/pay", headers=auth_headers,
                          json={"plan": "pro", "period": "month"})
    assert r.status_code == 200, r.text
    assert r.json()["confirmation_url"].startswith("https://yoomoney.ru")

    # годовая цена = 10 месяцев
    async def fake_create_year(**kwargs):
        assert kwargs["amount_kop"] == 9990 * 100
        return {"id": "pay_year", "status": "pending",
                "confirmation": {"confirmation_url": "https://yoomoney.ru/y"}}
    monkeypatch.setattr(billing_service, "api_create_payment", fake_create_year)
    r = await client.post("/api/web/billing/pay", headers=auth_headers,
                          json={"plan": "pro", "period": "year"})
    assert r.status_code == 200

    # бесплатный тариф не оплачивается
    r = await client.post("/api/web/billing/pay", headers=auth_headers,
                          json={"plan": "free"})
    assert r.status_code == 400


async def test_webhook_activates_subscription(client, auth_headers, monkeypatch):
    async def fake_create(**kwargs):
        return {"id": "pay_web", "status": "pending",
                "confirmation": {"confirmation_url": "https://yoomoney.ru/w"}}
    monkeypatch.setattr(billing_service, "api_create_payment", fake_create)
    monkeypatch.setattr(billing_service, "configured", lambda: True)
    r = await client.post("/api/web/billing/pay", headers=auth_headers,
                          json={"plan": "business", "period": "month"})
    sub_id = r.json()["subscription_id"]

    async def fake_get(payment_id):
        assert payment_id == "pay_web"
        return {"id": payment_id, "status": "succeeded"}
    monkeypatch.setattr(billing_service, "api_get_payment", fake_get)

    # вебхук без авторизации (публичный)
    r = await client.post("/api/yookassa/webhook",
                          json={"event": "payment.succeeded", "object": {"id": "pay_web"}})
    assert r.status_code == 200, r.text
    assert r.json()["activated"] is True

    r = await client.get("/api/web/settings/plans", headers=auth_headers)
    assert r.json()["current"] == "business"

    # повторный вебхук — идемпотентно
    r = await client.post("/api/yookassa/webhook",
                          json={"event": "payment.succeeded", "object": {"id": "pay_web"}})
    assert r.status_code == 200

    # незнакомый платёж — ок, не наш
    async def fake_get_any(payment_id):
        return {"id": payment_id, "status": "succeeded"}
    monkeypatch.setattr(billing_service, "api_get_payment", fake_get_any)
    r = await client.post("/api/yookassa/webhook",
                          json={"event": "payment.succeeded", "object": {"id": "stranger"}})
    assert r.json()["matched"] is False

    # без id — 400
    r = await client.post("/api/yookassa/webhook", json={"event": "payment.succeeded"})
    assert r.status_code == 400


async def test_manual_activation_and_expiry(client, auth_headers):
    r = await client.post("/api/web/billing/activate", headers=auth_headers,
                          json={"plan": "pro", "period": "month"})
    assert r.status_code == 201, r.text
    sub = r.json()
    assert sub["status"] == "active" and sub["provider"] == "manual"
    assert sub["valid_until"] is not None

    r = await client.get("/api/web/settings/plans", headers=auth_headers)
    assert r.json()["current"] == "pro"

    # история подписок
    r = await client.get("/api/web/billing", headers=auth_headers)
    subs = r.json()["subscriptions"]
    assert len(subs) == 1 and subs[0]["status"] == "active"

    # тик до истечения — ничего не меняет
    from app.db import SessionFactory
    from app.services import billing as bs
    async with SessionFactory() as s:
        assert await bs.expire_if_due(s) == 0
        await s.commit()
    r = await client.get("/api/web/settings/plans", headers=auth_headers)
    assert r.json()["current"] == "pro"

    # отодвигаем valid_until в прошлое — тик понижает до free
    from datetime import timedelta
    from app.models import BillingSubscription
    async with SessionFactory() as s:
        row = (await s.execute(
            BillingSubscription.__table__.select().where(
                BillingSubscription.id == sub["id"]))).first()
        obj = await s.get(BillingSubscription, sub["id"])
        obj.valid_until = utcnow() - timedelta(days=1)
        await s.commit()
    async with SessionFactory() as s:
        # сброс AppState-гейда, чтобы тик отработал сразу
        from app.models import AppState
        st = await s.get(AppState, "billing_run")
        if st:
            await s.delete(st)
        await s.commit()
    async with SessionFactory() as s:
        downgraded = await bs.expire_if_due(s)
        await s.commit()
    assert downgraded == 1
    r = await client.get("/api/web/settings/plans", headers=auth_headers)
    assert r.json()["current"] == "free"


async def test_billing_guard_for_roles(client, auth_headers):
    # приглашаем редактора — ему нельзя оплачивать и активировать
    await client.post("/api/web/auth/register",
                      json={"email": "billed@t.io", "password": "secret123"})
    r = await client.post("/api/web/auth/login",
                          json={"email": "billed@t.io", "password": "secret123"})
    editor = {"Authorization": f"Bearer {r.json()['access_token']}"}
    await client.post("/api/web/team/invite", headers=auth_headers,
                      json={"email": "billed@t.io", "role": "editor"})

    r = await client.post("/api/web/billing/activate", headers=editor,
                          json={"plan": "pro", "period": "month"})
    assert r.status_code == 403
    r = await client.post("/api/web/billing/pay", headers=editor,
                          json={"plan": "pro", "period": "month"})
    assert r.status_code == 403
    # чтение доступно
    r = await client.get("/api/web/billing", headers=editor)
    assert r.status_code == 200
