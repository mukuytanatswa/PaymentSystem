from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers import webhook_controller
from app.middleware.webhook_auth import verify_stitch_signature
from app.models.schemas import StitchWebhookPayload
from app.services.alert_service import send_alert

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"])


@router.post("/stitch")
async def stitch_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    raw_body = await request.body()
    sig = request.headers.get("x-stitch-signature", "")
    try:
        verify_stitch_signature(raw_body, sig)
    except HTTPException:
        await send_alert("webhook.signature_invalid")
        raise
    payload = StitchWebhookPayload.model_validate_json(raw_body)
    return await webhook_controller.handle_stitch_webhook(payload, db)
