from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import admin_user
from app.models import AIUsage, AuthSession, BillingRecord, CreditLedger, User
from app.routes.auth import profile
from app.schemas import AdminUserUpdate, UserProfile

router = APIRouter(prefix="/admin", tags=["Administration"])


@router.get("/users", response_model=list[UserProfile])
def list_users(
    search: str | None = Query(default=None, max_length=160),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=100),
    _admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> list[UserProfile]:
    query = select(User).order_by(User.created_at.desc()).offset(offset).limit(limit)
    if search:
        term = f"%{search.strip()}%"
        query = query.where(or_(User.email.ilike(term), User.display_name.ilike(term)))
    return [profile(user) for user in db.scalars(query).all()]


@router.patch("/users/{user_id}", response_model=UserProfile)
def update_user(
    user_id: UUID,
    body: AdminUserUpdate,
    admin: User = Depends(admin_user),
    db: Session = Depends(get_db),
) -> UserProfile:
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.email == str(settings.owner_email).lower():
        raise HTTPException(status_code=409, detail="The owner account is protected")
    if body.status is None and body.portal_access is None:
        raise HTTPException(status_code=422, detail="No changes were provided")
    if body.status == "active" and user.email_verified_at is None:
        raise HTTPException(status_code=409, detail="The user must verify their email first")
    if body.status is not None:
        user.status = body.status
    if body.portal_access is not None:
        if body.portal_access and (user.status != "active" or user.email_verified_at is None):
            raise HTTPException(status_code=409, detail="Only verified active accounts can access the portal")
        user.portal_access = body.portal_access
    if user.status == "suspended":
        db.query(AuthSession).filter(AuthSession.user_id == user.id, AuthSession.revoked_at.is_(None)).update(
            {AuthSession.revoked_at: func.now()}, synchronize_session=False
        )
        user.portal_access = False
    db.commit()
    db.refresh(user)
    return profile(user)


@router.get("/overview")
def overview(
    _admin: User = Depends(admin_user), db: Session = Depends(get_db)
) -> dict[str, int]:
    return {
        "users_total": db.scalar(select(func.count()).select_from(User)) or 0,
        "users_active": db.scalar(select(func.count()).select_from(User).where(User.status == "active")) or 0,
        "users_waiting_for_access": db.scalar(
            select(func.count()).select_from(User).where(
                User.status == "active", User.portal_access.is_(False)
            )
        ) or 0,
        "ai_requests": db.scalar(select(func.count()).select_from(AIUsage)) or 0,
        "credits_used": db.scalar(select(func.coalesce(func.sum(AIUsage.credits_charged), 0))) or 0,
        "credits_remaining": db.scalar(
            select(func.coalesce(func.sum(User.free_credits_remaining + User.monthly_credits_remaining + User.purchased_credits), 0))
        ) or 0,
        "active_subscriptions": db.scalar(
            select(func.count()).select_from(User).where(User.subscription_status.in_(["active", "trialing"]))
        ) or 0,
        "monthly_revenue_cents": db.scalar(
            select(func.coalesce(func.sum(BillingRecord.amount_cents), 0)).where(
                BillingRecord.status == "paid",
                BillingRecord.kind == "subscription",
                BillingRecord.created_at >= func.date_trunc("month", func.now()),
            )
        ) or 0,
        "paid_orders": db.scalar(
            select(func.count()).select_from(BillingRecord).where(BillingRecord.status == "paid")
        ) or 0,
    }
