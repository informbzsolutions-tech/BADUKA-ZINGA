from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import CreditLedger, User


def total_credits(user: User) -> int:
    return user.free_credits_remaining + user.monthly_credits_remaining + user.purchased_credits


def charge_one(db: Session, user: User) -> str | None:
    if user.role == "admin":
        return None
    locked = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked is None:
        raise HTTPException(status_code=401, detail="Account not found")
    if locked.free_credits_remaining > 0:
        locked.free_credits_remaining -= 1
        bucket = "free"
    elif locked.monthly_credits_remaining > 0:
        locked.monthly_credits_remaining -= 1
        bucket = "monthly"
    elif locked.purchased_credits > 0:
        locked.purchased_credits -= 1
        bucket = "purchased"
    else:
        raise HTTPException(status_code=402, detail="No AI credits remain")
    db.add(CreditLedger(user_id=locked.id, delta=-1, bucket=bucket, reason="ai_response"))
    db.commit()
    return bucket


def refund_one(db: Session, user: User, bucket: str | None) -> None:
    if bucket is None:
        return
    locked = db.scalar(select(User).where(User.id == user.id).with_for_update())
    if locked is None:
        return
    column = {
        "free": "free_credits_remaining",
        "monthly": "monthly_credits_remaining",
        "purchased": "purchased_credits",
    }[bucket]
    setattr(locked, column, getattr(locked, column) + 1)
    db.add(CreditLedger(user_id=locked.id, delta=1, bucket=bucket, reason="ai_failure_refund"))
    db.commit()
