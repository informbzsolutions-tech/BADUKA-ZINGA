import stripe
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import portal_user
from app.models import BillingRecord, CreditLedger, StripeEvent, User
from app.schemas import (
    BillingPortalResult,
    CheckoutCreditsRequest,
    CheckoutPlanRequest,
    CheckoutResult,
)

router = APIRouter(prefix="/billing", tags=["Billing"])
PLANS = {
    "Pro": (settings.stripe_price_pro, 600),
    "Business": (settings.stripe_price_business, 9000),
}
PACKS = {
    "100": (settings.stripe_price_credits_100, 100),
    "300": (settings.stripe_price_credits_300, 300),
    "700": (settings.stripe_price_credits_700, 700),
}
ACTIVE_SUBSCRIPTION_STATUSES = {"active", "trialing"}


def stripe_client() -> None:
    if not settings.stripe_secret_key:
        raise HTTPException(status_code=503, detail="Billing provider is not configured")
    stripe.api_key = settings.stripe_secret_key


def ensure_customer(user: User) -> str:
    stripe_client()
    if user.stripe_customer_id:
        return user.stripe_customer_id
    customer = stripe.Customer.create(email=user.email, name=user.display_name, metadata={"user_id": str(user.id)})
    user.stripe_customer_id = customer.id
    return customer.id


@router.post("/checkout", response_model=CheckoutResult)
def subscription_checkout(
    body: CheckoutPlanRequest,
    user: User = Depends(portal_user),
    db: Session = Depends(get_db),
) -> CheckoutResult:
    price_id, allowance = PLANS[body.plan]
    if not price_id:
        raise HTTPException(status_code=503, detail=f"Stripe price for {body.plan} is not configured")
    if user.subscription_status in ACTIVE_SUBSCRIPTION_STATUSES:
        raise HTTPException(status_code=409, detail="An active subscription already exists")
    customer_id = ensure_customer(user)
    db.commit()
    session = stripe.checkout.Session.create(
        mode="subscription",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=settings.stripe_success_url,
        cancel_url=settings.stripe_cancel_url,
        client_reference_id=str(user.id),
        metadata={"user_id": str(user.id), "plan": body.plan},
        subscription_data={"metadata": {"user_id": str(user.id), "plan": body.plan, "credits": str(allowance)}},
    )
    return CheckoutResult(checkout_url=session.url)


@router.post("/credits/checkout", response_model=CheckoutResult)
def credits_checkout(
    body: CheckoutCreditsRequest,
    user: User = Depends(portal_user),
    db: Session = Depends(get_db),
) -> CheckoutResult:
    price_id, credits = PACKS[body.pack]
    if not price_id:
        raise HTTPException(status_code=503, detail="This credit pack is not configured")
    customer_id = ensure_customer(user)
    db.commit()
    session = stripe.checkout.Session.create(
        mode="payment",
        customer=customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=settings.stripe_success_url,
        cancel_url=settings.stripe_cancel_url,
        client_reference_id=str(user.id),
        metadata={"user_id": str(user.id), "kind": "credits", "pack": body.pack, "credits": str(credits)},
    )
    return CheckoutResult(checkout_url=session.url)


@router.post("/portal", response_model=BillingPortalResult)
def billing_portal(user: User = Depends(portal_user)) -> BillingPortalResult:
    if not user.stripe_customer_id:
        raise HTTPException(status_code=409, detail="No billing account exists yet")
    stripe_client()
    portal = stripe.billing_portal.Session.create(
        customer=user.stripe_customer_id,
        return_url=settings.frontend_url,
    )
    return BillingPortalResult(portal_url=portal.url)


def _record_event(db: Session, event: dict) -> bool:
    if db.scalar(select(StripeEvent.id).where(StripeEvent.event_id == event["id"])):
        return False
    db.add(StripeEvent(event_id=event["id"], event_type=event["type"]))
    db.flush()
    return True


def _grant_credits(db: Session, user: User, credits: int, bucket: str, source: str) -> None:
    if credits <= 0:
        return
    if bucket == "monthly":
        user.monthly_credit_allowance = credits
        user.monthly_credits_remaining = credits
    elif bucket == "purchased":
        user.purchased_credits += credits
    db.add(CreditLedger(user_id=user.id, delta=credits, bucket=bucket, reason="stripe_payment"))


def _checkout_completed(db: Session, session: dict) -> None:
    metadata = session.get("metadata") or {}
    user = db.get(User, metadata.get("user_id")) if metadata.get("user_id") else None
    if user is None:
        return
    user.stripe_customer_id = session.get("customer") or user.stripe_customer_id
    if session.get("mode") == "payment" and session.get("payment_status") == "paid":
        pack = PACKS.get(str(metadata.get("pack")))
        if metadata.get("kind") != "credits" or pack is None:
            return
        credits = pack[1]
        source = session["id"]
        if db.scalar(select(BillingRecord.id).where(BillingRecord.stripe_object_id == source)):
            return
        user.purchased_credits += credits
        db.add(CreditLedger(user_id=user.id, delta=credits, bucket="purchased", reason="credit_pack_purchase"))
        db.add(BillingRecord(
            user_id=user.id, stripe_object_id=source, kind="credit_pack", credits=credits,
            amount_cents=session.get("amount_total") or 0, currency=session.get("currency") or "usd", status="paid"
        ))


def _invoice_paid(db: Session, invoice: dict) -> None:
    subscription = invoice.get("subscription")
    details = invoice.get("subscription_details") or {}
    metadata = details.get("metadata") or {}
    user = db.get(User, metadata.get("user_id")) if metadata.get("user_id") else None
    if user is None and subscription:
        stripe_client()
        sub = stripe.Subscription.retrieve(subscription)
        metadata = sub.get("metadata") or {}
        user = db.get(User, metadata.get("user_id")) if metadata.get("user_id") else None
    if user is None:
        return
    user.stripe_customer_id = invoice.get("customer") or user.stripe_customer_id
    user.stripe_subscription_id = subscription or user.stripe_subscription_id
    user.plan = metadata.get("plan", user.plan)
    user.subscription_status = "active"
    allowance = PLANS.get(user.plan, (None, 0))[1]
    source = invoice["id"]
    if db.scalar(select(BillingRecord.id).where(BillingRecord.stripe_object_id == source)):
        return
    _grant_credits(db, user, allowance, "monthly", source)
    db.add(BillingRecord(
        user_id=user.id, stripe_object_id=source, kind="subscription", plan=user.plan, credits=allowance,
        amount_cents=invoice.get("amount_paid") or 0, currency=invoice.get("currency") or "usd", status="paid"
    ))


def _subscription_changed(db: Session, sub: dict, deleted: bool = False) -> None:
    subscription_id = sub.get("id")
    user = db.scalar(select(User).where(User.stripe_subscription_id == subscription_id))
    metadata = sub.get("metadata") or {}
    if user is None and metadata.get("user_id"):
        user = db.get(User, metadata["user_id"])
    if user is None:
        return
    user.stripe_customer_id = sub.get("customer") or user.stripe_customer_id
    user.stripe_subscription_id = subscription_id
    user.plan = metadata.get("plan", user.plan)
    user.subscription_status = "canceled" if deleted else sub.get("status", "unknown")
    if user.subscription_status not in ACTIVE_SUBSCRIPTION_STATUSES:
        user.monthly_credits_remaining = 0
        user.monthly_credit_allowance = 0
        user.plan = "Free"
    else:
        user.monthly_credit_allowance = PLANS.get(user.plan, (None, 0))[1]


@router.post("/webhook")
async def stripe_webhook(request: Request, db: Session = Depends(get_db)) -> dict[str, bool]:
    if not settings.stripe_webhook_secret:
        raise HTTPException(status_code=503, detail="Webhook verification is not configured")
    payload = await request.body()
    signature = request.headers.get("stripe-signature", "")
    try:
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
    except (ValueError, stripe.SignatureVerificationError) as exc:
        raise HTTPException(status_code=400, detail="Invalid Stripe webhook signature") from exc
    event = dict(event)
    if not _record_event(db, event):
        db.rollback()
        return {"received": True}
    data = event.get("data", {}).get("object", {})
    event_type = event.get("type")
    if event_type in {"checkout.session.completed", "checkout.session.async_payment_succeeded"}:
        _checkout_completed(db, data)
    elif event_type == "invoice.paid":
        _invoice_paid(db, data)
    elif event_type == "invoice.payment_failed":
        sub_id = data.get("subscription")
        user = db.scalar(select(User).where(User.stripe_subscription_id == sub_id)) if sub_id else None
        if user:
            user.subscription_status = "past_due"
            existing = db.scalar(select(BillingRecord).where(BillingRecord.stripe_object_id == data["id"]))
            if existing:
                existing.status = "payment_failed"
            else:
                db.add(BillingRecord(
                    user_id=user.id, stripe_object_id=data["id"], kind="subscription", plan=user.plan,
                    amount_cents=data.get("amount_due") or 0, currency=data.get("currency") or "usd", status="payment_failed"
                ))
    elif event_type in {"customer.subscription.created", "customer.subscription.updated"}:
        _subscription_changed(db, data)
    elif event_type == "customer.subscription.deleted":
        _subscription_changed(db, data, deleted=True)
    db.commit()
    return {"received": True}
