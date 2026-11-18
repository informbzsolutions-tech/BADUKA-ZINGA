import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import current_user
from app.emailer import send_action_link
from app.models import AuthSession, AuthToken, User
from app.schemas import AuthResult, EmailRequest, LoginRequest, RegisterRequest, UserProfile
from app.security import create_access_token, hash_password, new_secret, token_digest, verify_password

router = APIRouter(prefix="/auth", tags=["Authentication"])
logger = logging.getLogger(__name__)
DUMMY_HASH = hash_password("not-a-real-password-for-timing-only")


@router.get("/me", response_model=UserProfile)
def me(user: User = Depends(current_user)) -> UserProfile:
    return profile(user)


def profile(user: User) -> UserProfile:
    return UserProfile(
        id=user.id,
        email=user.email,
        display_name=user.display_name,
        role=user.role,
        status=user.status,
        portal_access=user.portal_access,
        plan=user.plan,
        total_credits=(user.free_credits_remaining + user.monthly_credits_remaining + user.purchased_credits),
        free_credits_remaining=user.free_credits_remaining,
        monthly_credits_remaining=user.monthly_credits_remaining,
        purchased_credits=user.purchased_credits,
        subscription_status=user.subscription_status,
        email_verified=user.email_verified_at is not None,
        created_at=user.created_at,
    )


def require_same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in {item.rstrip("/") for item in settings.frontend_origins}:
        raise HTTPException(status_code=403, detail="Origin not allowed")


def set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        "baduka_refresh",
        token,
        httponly=True,
        secure=settings.app_env == "production",
        samesite="strict",
        max_age=settings.refresh_token_days * 24 * 60 * 60,
        path=f"{settings.api_prefix}/auth",
    )


def issue_session(db: Session, user: User, response: Response) -> AuthResult:
    refresh = new_secret()
    db.add(
        AuthSession(
            user_id=user.id,
            refresh_token_hash=token_digest(refresh),
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
        )
    )
    db.commit()
    set_refresh_cookie(response, refresh)
    return AuthResult(
        access_token=create_access_token(user.id),
        expires_in=settings.access_token_minutes * 60,
        user=profile(user),
    )


def create_action_token(db: Session, user: User, purpose: str, expires: timedelta) -> str:
    raw = new_secret()
    db.execute(delete(AuthToken).where(AuthToken.user_id == user.id, AuthToken.purpose == purpose))
    db.add(
        AuthToken(
            user_id=user.id,
            token_hash=token_digest(raw),
            purpose=purpose,
            expires_at=datetime.now(UTC) + expires,
        )
    )
    db.commit()
    return raw


@router.post("/register", status_code=status.HTTP_202_ACCEPTED)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    email = str(body.email).strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    is_owner = email == str(settings.owner_email).lower()
    user = User(
        email=email,
        display_name="Pedro Baduka" if is_owner else body.display_name,
        password_hash=hash_password(body.password),
        role="admin" if is_owner else "member",
        status="pending",
        portal_access=False,
        plan="Free",
        free_credits_remaining=100,
        free_credits_claimed=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = create_action_token(db, user, "email_verify", timedelta(hours=24))
    link = f"{settings.verify_email_base_url}?token={token}"
    try:
        send_action_link(email, "Confirme o seu email — BADUKA AI", link)
    except Exception:
        logger.exception("Verification email delivery failed for user %s", user.id)
        raise HTTPException(status_code=503, detail="Verification email could not be sent; request a new link") from None
    return {"message": "Check your email to verify the account. Portal access is granted by the administrator."}


@router.post("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)) -> dict[str, str]:
    record = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == token_digest(token),
            AuthToken.purpose == "email_verify",
        )
    )
    now = datetime.now(UTC)
    if record is None or record.expires_at <= now:
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
    user = db.get(User, record.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
    user.email_verified_at = now
    user.status = "active"
    if user.email == str(settings.owner_email).lower():
        user.role = "admin"
        user.display_name = "Pedro Baduka"
        user.portal_access = True
    db.delete(record)
    db.commit()
    return {"message": "Email verified. You can now sign in."}


@router.post("/login", response_model=AuthResult)
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)) -> AuthResult:
    email = str(body.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        verify_password(body.password, DUMMY_HASH)
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    now = datetime.now(UTC)
    if user.locked_until and user.locked_until > now:
        raise HTTPException(status_code=429, detail="Too many failed attempts. Try again later.")
    if not verify_password(body.password, user.password_hash):
        user.failed_login_count += 1
        if user.failed_login_count >= 5:
            user.locked_until = now + timedelta(minutes=15)
            user.failed_login_count = 0
        db.commit()
        raise HTTPException(status_code=401, detail="Email or password is incorrect")
    if user.email_verified_at is None:
        raise HTTPException(status_code=403, detail="Verify your email before signing in")
    if user.status != "active":
        raise HTTPException(status_code=403, detail="This account is not active")
    user.failed_login_count = 0
    user.locked_until = None
    if not user.portal_access and user.role != "admin":
        db.commit()
        raise HTTPException(status_code=403, detail="Portal access has not been approved")
    if email == str(settings.owner_email).lower():
        user.role = "admin"
        user.display_name = "Pedro Baduka"
        user.portal_access = True
    db.commit()
    db.refresh(user)
    return issue_session(db, user, response)


@router.post("/refresh", response_model=AuthResult)
def refresh(request: Request, response: Response, db: Session = Depends(get_db)) -> AuthResult:
    require_same_origin(request)
    raw = request.cookies.get("baduka_refresh")
    if not raw:
        raise HTTPException(status_code=401, detail="Refresh session required")
    session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == token_digest(raw)))
    now = datetime.now(UTC)
    if session is None or session.revoked_at is not None or session.expires_at <= now:
        raise HTTPException(status_code=401, detail="Refresh session expired")
    user = db.get(User, session.user_id)
    if user is None or user.status != "active" or user.email_verified_at is None:
        session.revoked_at = now
        db.commit()
        raise HTTPException(status_code=401, detail="Account is not active")
    if not user.portal_access and user.role != "admin":
        session.revoked_at = now
        db.commit()
        response.delete_cookie("baduka_refresh", path=f"{settings.api_prefix}/auth", samesite="strict")
        raise HTTPException(status_code=403, detail="Portal access has not been approved")
    session.revoked_at = now
    result = issue_session(db, user, response)
    return result


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> Response:
    require_same_origin(request)
    raw = request.cookies.get("baduka_refresh")
    if raw:
        session = db.scalar(select(AuthSession).where(AuthSession.refresh_token_hash == token_digest(raw)))
        if session and session.revoked_at is None:
            session.revoked_at = datetime.now(UTC)
            db.commit()
    response.delete_cookie("baduka_refresh", path=f"{settings.api_prefix}/auth", samesite="strict")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/password-reset/request", status_code=202)
def request_password_reset(body: EmailRequest, db: Session = Depends(get_db)) -> dict[str, str]:
    email = str(body.email).strip().lower()
    user = db.scalar(select(User).where(User.email == email, User.status == "active"))
    if user:
        token = create_action_token(db, user, "password_reset", timedelta(minutes=30))
        link = f"{settings.frontend_url}/reset-password?token={token}"
        try:
            send_action_link(email, "Repor a senha — BADUKA AI", link)
        except Exception:
            logger.exception("Password reset delivery failed for user %s", user.id)
    return {"message": "If the account exists, a reset link will be sent."}


@router.post("/password-reset/confirm")
def confirm_password_reset(token: str, new_password: str, db: Session = Depends(get_db)) -> dict[str, str]:
    if len(new_password) < 12 or len(new_password) > 256:
        raise HTTPException(status_code=422, detail="Password must contain at least 12 characters")
    record = db.scalar(
        select(AuthToken).where(
            AuthToken.token_hash == token_digest(token),
            AuthToken.purpose == "password_reset",
        )
    )
    now = datetime.now(UTC)
    if record is None or record.expires_at <= now:
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    user = db.get(User, record.user_id)
    if user is None:
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    user.password_hash = hash_password(new_password)
    db.execute(delete(AuthSession).where(AuthSession.user_id == user.id))
    db.delete(record)
    db.commit()
    return {"message": "Password updated. Sign in again."}
