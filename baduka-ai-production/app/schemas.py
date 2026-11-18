from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=12, max_length=256)

    @field_validator("display_name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        return " ".join(value.split())


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=256)


class EmailRequest(BaseModel):
    email: EmailStr


class UserProfile(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: EmailStr
    display_name: str
    role: str
    status: str
    portal_access: bool
    plan: str
    total_credits: int
    free_credits_remaining: int
    monthly_credits_remaining: int
    purchased_credits: int
    subscription_status: str
    email_verified: bool
    created_at: datetime


class AuthResult(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    user: UserProfile


class AIMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20_000)


class AIRequest(BaseModel):
    messages: list[AIMessage] = Field(min_length=1, max_length=30)


class AIResponse(BaseModel):
    text: str
    model: str
    credits_remaining: int | None


class AdminUserUpdate(BaseModel):
    status: Literal["active", "suspended"] | None = None
    portal_access: bool | None = None


class CheckoutPlanRequest(BaseModel):
    plan: Literal["Pro", "Business"]


class CheckoutCreditsRequest(BaseModel):
    pack: Literal["100", "300", "700"]


class CheckoutResult(BaseModel):
    checkout_url: str


class BillingPortalResult(BaseModel):
    portal_url: str
