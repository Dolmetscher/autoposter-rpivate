"""Биллинг: подписки на тарифы через ЮKassa + ручная активация.
Без ключей ЮKassa оплата недоступна (ValueError → 400/501 в API),
но ручная активация и история работают. Идемпотентно по provider_payment_id."""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..models import AppState, BillingSubscription, User
from ..util import utcnow

log = logging.getLogger("billing")


def configured() -> bool:
    return bool(settings.yookassa_shop_id and settings.yookassa_secret_key)


def price_kop(plan_key: str, period: str) -> int:
    """Цена подписки в копейках."""
    plan = settings.plans.get(plan_key) or {}
    rub = int(plan.get("price_year" if period == "year" else "price_month", 0))
    return rub * 100


def period_days(period: str) -> int:
    return 365 if period == "year" else 30


# --- ЮKassa API ---

async def api_create_payment(*, amount_kop: int, description: str, return_url: str,
                             metadata: dict) -> dict:
    """Создание платежа (redirect-confirmation). Вызывается только при configured()."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{settings.yookassa_api_url}/payments",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key),
            headers={"Idempotence-Key": str(uuid.uuid4())},
            json={
                "amount": {"value": f"{amount_kop / 100:.2f}", "currency": "RUB"},
                "capture": True,
                "confirmation": {"type": "redirect", "return_url": return_url},
                "description": description[:128],
                "metadata": metadata,
            })
        r.raise_for_status()
        return r.json()


async def api_get_payment(payment_id: str) -> dict:
    """Перепроверка статуса платежа (вебхуки не доверяем вслепую)."""
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"{settings.yookassa_api_url}/payments/{payment_id}",
            auth=(settings.yookassa_shop_id, settings.yookassa_secret_key))
        r.raise_for_status()
        return r.json()


# --- Применение оплаты ---

async def activate_subscription(s: AsyncSession, sub: BillingSubscription) -> None:
    """Активирует подписку с продлением от текущего срока того же тарифа."""
    if sub.status == "active":
        return
    now = utcnow()
    sub.status = "active"
    sub.paid_at = now
    base = now
    latest = (await s.execute(
        select(BillingSubscription)
        .where(BillingSubscription.user_id == sub.user_id,
               BillingSubscription.plan == sub.plan,
               BillingSubscription.status == "active",
               BillingSubscription.id != sub.id,
               BillingSubscription.valid_until.is_not(None))
        .order_by(BillingSubscription.valid_until.desc()).limit(1)
    )).scalar_one_or_none()
    if latest and latest.valid_until and latest.valid_until > now:
        base = latest.valid_until
    sub.valid_until = base + timedelta(days=period_days(sub.period))
    user = await s.get(User, sub.user_id)
    if user is not None:
        user.plan = sub.plan
    log.info("Подписка #%s активирована: user=%s план=%s до %s",
             sub.id, user.email if user else "?", sub.plan, sub.valid_until)


async def handle_webhook(s: AsyncSession, event: str, ext_id: str) -> dict:
    """Вебхук ЮKassa. Статус платежа перепроверяется запросом к API.
    Возвращает краткую сводку; ValueError — на некорректные ситуации (400)."""
    if not configured():
        raise ValueError("Биллинг не настроен")
    if not ext_id:
        raise ValueError("Нет id платежа в уведомлении")

    remote = await api_get_payment(ext_id)  # поднимет исключение при недоступности
    status = remote.get("status")
    sub = (await s.execute(
        select(BillingSubscription).where(
            BillingSubscription.provider_payment_id == ext_id))).scalar_one_or_none()
    if sub is None:
        # платёж создан вне системы — не наш кейс, отвечаем ок
        return {"matched": False}

    if status == "succeeded" and event in ("payment.succeeded", "payment.waiting_for_capture"):
        if sub.status != "active":
            await activate_subscription(s, sub)
        await s.flush()
        return {"matched": True, "activated": True}
    if status == "canceled":
        sub.status = "canceled"
        await s.flush()
        return {"matched": True, "canceled": True}
    return {"matched": True, "status": status}


async def expire_overdue(s: AsyncSession, now=None) -> int:
    """Переводит истёкшие подписки в expired и опускает тариф до free,
    если активных подписок у пользователя больше нет."""
    now = now or utcnow()
    overdue = (await s.execute(
        select(BillingSubscription)
        .where(BillingSubscription.status == "active",
               BillingSubscription.valid_until.is_not(None),
               BillingSubscription.valid_until < now)
        .limit(200))).scalars().all()
    downgraded = 0
    for sub in overdue:
        sub.status = "expired"
        still_active = (await s.execute(
            select(BillingSubscription.id).where(
                BillingSubscription.user_id == sub.user_id,
                BillingSubscription.status == "active",
                BillingSubscription.valid_until > now).limit(1))).scalar_one_or_none()
        if still_active is None:
            user = await s.get(User, sub.user_id)
            if user is not None and user.plan != "free":
                user.plan = "free"
                downgraded += 1
    await s.flush()
    return downgraded


async def expire_if_due(s: AsyncSession, now=None) -> int:
    """Обёртка для тика планировщика — не чаще раза в час."""
    now = now or utcnow()
    state = await s.get(AppState, "billing_run")
    if state and state.value.get("ts"):
        try:
            if now - datetime.fromisoformat(state.value["ts"]) < timedelta(hours=1):
                return 0
        except ValueError:
            pass
    count = await expire_overdue(s, now)
    if state is not None:
        state.value = {"ts": now.isoformat()}
        state.updated_at = now
    else:
        s.add(AppState(key="billing_run", value={"ts": now.isoformat()}, updated_at=now))
    await s.flush()
    return count
