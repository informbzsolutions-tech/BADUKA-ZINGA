from fastapi import APIRouter, Depends, HTTPException
from openai import OpenAI, OpenAIError
from sqlalchemy.orm import Session

from app.config import settings
from app.db import get_db
from app.deps import portal_user
from app.models import AIUsage, User
from app.schemas import AIRequest, AIResponse
from app.services.credits import charge_one, refund_one, total_credits

router = APIRouter(prefix="/ai", tags=["AI"])


@router.post("/respond", response_model=AIResponse)
def respond(
    body: AIRequest,
    user: User = Depends(portal_user),
    db: Session = Depends(get_db),
) -> AIResponse:
    if not settings.openai_api_key:
        raise HTTPException(status_code=503, detail="AI provider is not configured")
    bucket = charge_one(db, user)
    client = OpenAI(api_key=settings.openai_api_key, timeout=60.0, max_retries=1)
    try:
        result = client.responses.create(
            model=settings.openai_model,
            instructions=(
                "You are BADUKA AI, a software development assistant. Be clear, secure, and practical. "
                "Never claim that code was deployed or executed unless a connected tool confirms it."
            ),
            input=[{"role": item.role, "content": item.content} for item in body.messages],
            max_output_tokens=settings.openai_max_output_tokens,
            store=False,
        )
    except OpenAIError as exc:
        refund_one(db, user, bucket)
        db.add(
            AIUsage(
                user_id=user.id,
                model=settings.openai_model,
                credits_charged=0,
                status="failed",
            )
        )
        db.commit()
        raise HTTPException(status_code=502, detail="AI provider request failed") from exc
    db.add(
        AIUsage(
            user_id=user.id,
            model=settings.openai_model,
            input_tokens=result.usage.input_tokens if result.usage else 0,
            output_tokens=result.usage.output_tokens if result.usage else 0,
            credits_charged=0 if bucket is None else 1,
            status="completed",
        )
    )
    db.commit()
    db.refresh(user)
    return AIResponse(
        text=result.output_text,
        model=settings.openai_model,
        credits_remaining=None if user.role == "admin" else total_credits(user),
    )
