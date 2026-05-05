import logging
from datetime import datetime, timezone

import httpx

from app.config.env import settings

logger = logging.getLogger(__name__)


async def send_alert(event: str, **fields) -> None:
    logger.error(event, extra=fields)
    if not settings.dlq_alert_webhook_url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                settings.dlq_alert_webhook_url,
                json={"event": event, "timestamp": datetime.now(timezone.utc).isoformat(), **fields},
            )
    except Exception:
        logger.exception("alert_send_failed", extra={"event": event})
