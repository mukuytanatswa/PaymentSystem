import json
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.middleware.admin_auth import require_admin_key
from app.models.schemas import PaymentStatus

router = APIRouter(
    prefix="/api/v1/admin",
    tags=["admin"],
    dependencies=[Depends(require_admin_key)],
)

ALLOWED_STATUSES = {s.value for s in PaymentStatus}


class ForceStatusRequest(BaseModel):
    status: Literal["pending", "processing", "completed", "failed"]
    reason: str


@router.post("/payments/{payment_id}/force-status", status_code=200)
async def force_payment_status(
    payment_id: UUID,
    body: ForceStatusRequest,
    db: AsyncSession = Depends(get_db),
):
    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE payments SET status = :status
                WHERE id = :id
                RETURNING id, status
            """),
            {"id": str(payment_id), "status": body.status},
        )
        row = result.fetchone()

    if not row:
        raise HTTPException(status_code=404, detail="Payment not found")

    async with db.begin():
        await db.execute(
            text("""
                INSERT INTO audit_log (event_type, vendor_id, platform_id, metadata)
                VALUES ('admin.force_status', NULL, NULL, CAST(:meta AS jsonb))
            """),
            {
                "meta": json.dumps({
                    "payment_id": str(payment_id),
                    "new_status": body.status,
                    "reason": body.reason,
                }),
            },
        )

    return {"payment_id": str(row.id), "status": row.status}
