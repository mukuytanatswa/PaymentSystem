import hashlib
import hmac
import logging

from fastapi import HTTPException

from app.config.env import settings

logger = logging.getLogger(__name__)


def verify_stitch_signature(raw_body: bytes, signature_header: str) -> None:
    if not signature_header.startswith("sha256="):
        logger.warning(
            "webhook_signature_missing",
            extra={"provided_prefix": signature_header[:10]},
        )
        raise HTTPException(status_code=401, detail="Missing webhook signature")

    expected = hmac.new(
        key=settings.stitch_webhook_secret.encode(),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    provided = signature_header.removeprefix("sha256=")

    if not hmac.compare_digest(expected, provided):
        logger.warning(
            "webhook_signature_invalid",
            extra={"provided_prefix": provided[:10]},
        )
        raise HTTPException(status_code=401, detail="Invalid webhook signature")
