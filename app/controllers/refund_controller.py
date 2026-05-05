import logging
from decimal import Decimal
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import AsyncSessionLocal
from app.models.schemas import PaymentStatus, RefundCreate, RefundResponse, SplitStatus
from app.services import stitch_client
from app.services.alert_service import send_alert

logger = logging.getLogger(__name__)


async def initiate_refund(
    payment_id: UUID,
    platform_id: UUID,
    platform_name: str,
    payload: RefundCreate,
    db: AsyncSession,
) -> RefundResponse:
    result = await db.execute(
        text("""
            SELECT id, platform_id, amount, currency, status
            FROM payments WHERE id = :id AND platform_id = :platform_id
        """),
        {"id": str(payment_id), "platform_id": str(platform_id)},
    )
    payment = result.fetchone()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")
    if payment.status != PaymentStatus.completed.value:
        raise HTTPException(
            status_code=422,
            detail=f"Only completed payments can be refunded (current status: {payment.status})",
        )

    splits_result = await db.execute(
        text("""
            SELECT id, vendor_id, amount, payout_id
            FROM splits WHERE payment_id = :pid AND status = :paid
        """),
        {"pid": str(payment_id), "paid": SplitStatus.paid.value},
    )
    paid_splits = splits_result.fetchall()

    if not paid_splits:
        raise HTTPException(status_code=422, detail="No paid splits found for this payment")

    async with db.begin():
        refund_row = await db.execute(
            text("""
                INSERT INTO refunds (payment_id, reason, amount, status, initiated_by)
                VALUES (:pid, :reason, :amount, 'pending', :initiated_by)
                RETURNING id, created_at
            """),
            {
                "pid": str(payment_id),
                "reason": payload.reason,
                "amount": str(payment.amount),
                "initiated_by": platform_name,
            },
        )
        refund = refund_row.fetchone()
        refund_id = refund.id

    await send_alert(
        "refund.initiated",
        payment_id=str(payment_id),
        refund_id=str(refund_id),
        amount=str(payment.amount),
        initiated_by=platform_name,
    )

    all_reversed = True
    last_reverse_id = None

    for split in paid_splits:
        if not split.payout_id:
            logger.warning("split_missing_payout_id_skip_reversal", extra={"split_id": str(split.id)})
            continue
        try:
            reverse_id = await stitch_client.reverse_disbursement(split.payout_id)
            last_reverse_id = reverse_id

            async with AsyncSessionLocal() as s:
                async with s.begin():
                    await s.execute(
                        text("UPDATE vendors SET balance = balance - :amount WHERE id = :vid"),
                        {"amount": str(split.amount), "vid": str(split.vendor_id)},
                    )
                    await s.execute(
                        text("""
                            INSERT INTO ledger (vendor_id, payment_id, type, amount, balance_after)
                            SELECT :vid, :pid, 'debit', :amount, balance
                            FROM vendors WHERE id = :vid
                        """),
                        {
                            "vid": str(split.vendor_id),
                            "pid": str(payment_id),
                            "amount": str(split.amount),
                        },
                    )
            logger.info(
                "split_reversed",
                extra={"split_id": str(split.id), "vendor_id": str(split.vendor_id), "reverse_id": reverse_id},
            )
        except Exception:
            logger.exception("split_reversal_failed", extra={"split_id": str(split.id)})
            all_reversed = False

    final_refund_status = "completed" if all_reversed else "failed"
    async with AsyncSessionLocal() as s:
        async with s.begin():
            await s.execute(
                text("""
                    UPDATE refunds
                    SET status = :status, stitch_reverse_id = :reverse_id
                    WHERE id = :rid
                """),
                {"status": final_refund_status, "reverse_id": last_reverse_id, "rid": str(refund_id)},
            )
            if all_reversed:
                await s.execute(
                    text("UPDATE payments SET status = 'refunded' WHERE id = :pid"),
                    {"pid": str(payment_id)},
                )

    if not all_reversed:
        await send_alert(
            "refund.failed",
            payment_id=str(payment_id),
            refund_id=str(refund_id),
        )

    return RefundResponse(
        id=refund_id,
        payment_id=payment_id,
        reason=payload.reason,
        amount=Decimal(str(payment.amount)),
        status=final_refund_status,
        initiated_by=platform_name,
        stitch_reverse_id=last_reverse_id,
        created_at=refund.created_at,
    )
