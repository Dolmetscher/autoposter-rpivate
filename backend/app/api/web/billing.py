"""Биллинг: тарифы с ценами, история подписок, создание платежа (ЮKassa),
ручная активация (для оффлайн-платежей владельцем)."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...db import get_session
from ...deps import get_current_user
from ...models import BillingSubscription, User
from ...services import billing as billing_service
from ...services import limits

router = APIRouter(prefix="/billing", tags=["billing"])

PAID_PLANS = [k for k, v in settings.plans.items() if v.get("price_month", 0) > 0]


class PayIn(BaseModel):
    plan: str
    period: str = "month"  # month | year


def _out(sub: BillingSubscription) -> dict:
    return {"id": sub.id, "plan": sub.plan, "period": sub.period,
            "status": sub.status, "provider": sub.provider,
            "amount_kop": sub.amount,
            "created_at": sub.created_at.isoformat() if sub.created_at else None,
            "paid_at": sub.paid_at.isoformat() if sub.paid_at else None,
            "valid_until": sub.valid_until.isoformat() if sub.valid_until else None}


@router.get("")
async def billing_overview(user: User = Depends(get_current_user),
                           s: AsyncSession = Depends(get_session)):
    subs = (await s.execute(
        select(BillingSubscription).where(BillingSubscription.user_id == user.id)
        .order_by(BillingSubscription.id.desc()).limit(20))).scalars().all()
    return {
        "plans": settings.plans,
        "current_plan": user.plan,
        "provider_configured": billing_service.configured(),
        "subscriptions": [_out(x) for x in subs],
        "usage": await limits.usage(s, user),
    }


@router.post("/pay")
async def pay(payload: PayIn, user: User = Depends(get_current_user),
              s: AsyncSession = Depends(get_session)):
    if payload.plan not in PAID_PLANS:
        raise HTTPException(400, "Бесплатный тариф не оплачивается")
    if payload.period not in ("month", "year"):
        raise HTTPException(400, "period: month или year")
    if not billing_service.configured():
        raise HTTPException(501, "Платёжный провайдер не настроен: задайте "
                                 "YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY на сервере")
    amount = billing_service.price_kop(payload.plan, payload.period)
    sub = BillingSubscription(user_id=user.id, plan=payload.plan,
                              period=payload.period, status="pending",
                              provider="yookassa", amount=amount)
    s.add(sub)
    await s.flush()
    try:
        resp = await billing_service.api_create_payment(
            amount_kop=amount,
            description=f"Autoposter: тариф {settings.plans[payload.plan]['name']} "
                        f"({payload.period})",
            return_url=f"{settings.public_base_url.rstrip('/')}{settings.billing_return_path}",
            metadata={"subscription_id": sub.id, "user_id": user.id,
                      "plan": payload.plan, "period": payload.period},
        )
    except Exception as e:
        sub.status = "canceled"
        await s.commit()
        raise HTTPException(502, f"ЮKassa недоступна: {e}")
    sub.provider_payment_id = resp.get("id", "")
    await s.commit()
    confirmation = (resp.get("confirmation") or {}).get("confirmation_url", "")
    if not confirmation:
        raise HTTPException(502, "ЮKassa не вернула ссылку на оплату")
    return {"confirmation_url": confirmation, "subscription_id": sub.id}


class ActivateIn(BaseModel):
    plan: str
    period: str = "month"


@router.post("/activate", status_code=201)
async def activate_manually(payload: ActivateIn,
                            user: User = Depends(get_current_user),
                            s: AsyncSession = Depends(get_session)):
    """Ручная активация: деньги получены вне системы (переводом) — владелец
    активирует тариф себе сам. Виден в истории как provider=manual."""
    if payload.plan not in PAID_PLANS:
        raise HTTPException(400, "Бесплатный тариф не активируется")
    if payload.period not in ("month", "year"):
        raise HTTPException(400, "period: month или year")
    sub = BillingSubscription(user_id=user.id, plan=payload.plan,
                              period=payload.period, status="pending",
                              provider="manual",
                              amount=billing_service.price_kop(payload.plan, payload.period))
    s.add(sub)
    await s.flush()
    await billing_service.activate_subscription(s, sub)
    await s.commit()
    await s.refresh(sub)
    return _out(sub)


@router.get("/plans")
async def plans_only(user: User = Depends(get_current_user)):
    return {"plans": settings.plans, "current_plan": user.plan,
            "provider_configured": billing_service.configured()}
