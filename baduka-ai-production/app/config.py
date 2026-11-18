from functools import lru_cache

from pydantic import EmailStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    app_name: str = "BADUKA AI"
    api_prefix: str = "/api"
    frontend_origins: list[str] = ["http://localhost:3000"]
    frontend_url: str = "http://localhost:3000"
    database_url: str = "postgresql+psycopg://baduka:local@localhost:5432/baduka"
    jwt_secret: str = "development-only-change-this-secret-before-launch-0123456789"
    jwt_issuer: str = "baduka-ai"
    access_token_minutes: int = 15
    refresh_token_days: int = 30
    owner_email: EmailStr = "pedro.baduka@badukaezingasolucoes.com"
    openai_api_key: str | None = None
    openai_model: str = "gpt-5.6-luna"
    openai_max_output_tokens: int = 3000
    stripe_secret_key: str | None = None
    stripe_webhook_secret: str | None = None
    stripe_price_pro: str | None = None
    stripe_price_business: str | None = None
    stripe_price_credits_100: str | None = None
    stripe_price_credits_300: str | None = None
    stripe_price_credits_700: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str = "conta@badukaezingasolucoes.com"
    smtp_use_tls: bool = True
    stripe_success_url: str = "http://localhost:3000/billing/success?session_id={CHECKOUT_SESSION_ID}"
    stripe_cancel_url: str = "http://localhost:3000/billing"
    verify_email_base_url: str = "http://localhost:3000/verify-email"
    log_level: str = "INFO"

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def enforce_production_secrets(self) -> "Settings":
        if self.app_env == "production":
            if len(self.jwt_secret.encode()) < 32 or self.jwt_secret.startswith("development-only"):
                raise ValueError("JWT_SECRET must be a unique random secret of at least 32 bytes")
            if not all((self.smtp_host, self.smtp_user, self.smtp_password)):
                raise ValueError("SMTP must be configured in production for email verification")
            if not self.stripe_secret_key or not self.stripe_webhook_secret:
                raise ValueError("Stripe keys must be configured in production")
            if not self.stripe_price_pro or not self.stripe_price_business:
                raise ValueError("Stripe subscription Price IDs must be configured in production")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
