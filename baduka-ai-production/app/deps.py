from collections.abc import Generator
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import InvalidTokenError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.models import User

bearer = HTTPBearer(auto_error=False)


def current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    db: Session = Depends(get_db),
) -> User:
    error = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise error
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.jwt_secret,
            algorithms=["HS256"],
            issuer=settings.jwt_issuer,
            options={"require": ["sub", "exp", "iat", "iss"]},
        )
        user_id = UUID(payload["sub"])
    except (InvalidTokenError, ValueError, KeyError):
        raise error from None
    user = db.get(User, user_id)
    if user is None or user.status != "active" or user.email_verified_at is None:
        raise error
    return user


def portal_user(user: User = Depends(current_user)) -> User:
    if user.role != "admin" and not user.portal_access:
        raise HTTPException(status_code=403, detail="Portal access has not been granted")
    return user


def admin_user(user: User = Depends(current_user)) -> User:
    if user.email != str(settings.owner_email).lower() or user.role != "admin":
        raise HTTPException(status_code=403, detail="Administrator access required")
    return user


DbSession = Generator[Session, None, None]
