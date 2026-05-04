import logging

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import AsyncSessionLocal
from app.models.schemas import StitchWebhookPayload
from app.services import stitch_client
from app.services.alert_service import send_alert
from app.services.encryption_service import decrypt

logger = logging.getLogger(__name__)


async def handle_stitch_webhook(payload: StitchWebhookPayload, db: AsyncSession) -> dict:
    if payload.type == "payment_initiation_request.failed":
        stitch_payment_id = payload.data.get("id")
        if stitch_payment_id:
            async with db.begin():
                await db.execute(
                    text("UPDATE payments SET status = 'failed' WHERE stitch_payment_id = :id AND status = 'pending'"),
                    {"id": stitch_payment_id},
                )
            logger.info("payment_marked_failed", extra={"stitch_payment_id": stitch_payment_id})
        return {"status": "marked_failed"}

    if payload.type != "payment_initiation_request.completed":
        return {"status": "ignored"}

    stitch_payment_id = payload.data.get("id")
    if not stitch_payment_id:
        raise HTTPException(status_code=400, detail="Missing payment id in webhook payload")

    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE payments
                SET status = 'processing'
                WHERE stitch_payment_id = :stitch_id AND status = 'pending'
                RETURNING id, amount, currency
            """),
            {"stitch_id": stitch_payment_id},
        )
        payment = result.fetchone()

    if not payment:
        logger.info("webhook_already_processed_or_not_found", extra={"stitch_payment_id": stitch_payment_id})
        return {"status": "already_processed_or_not_found"}

    logger.info("webhook_received", extra={"stitch_payment_id": stitch_payment_id, "type": payload.type})

    async with AsyncSessionLocal() as fetch_session:
        splits_rows = await fetch_session.execute(
            text("""
                SELECT s.id, s.vendor_id, s.amount, v.bank_account, v.bank_code
                FROM splits s
                JOIN vendors v ON v.id = s.vendor_id
                WHERE s.payment_id = :pid AND s.status = 'pending'
            """),
            {"pid": str(payment.id)},
        )
        splits = splits_rows.fetchall()

    all_paid = True
    for split in splits:
        try:
            payout_id = await stitch_client.create_payout(
                vendor_id=str(split.vendor_id),
                amount=split.amount,
                currency=payment.currency,
                bank_account=decrypt(split.bank_account),
                bank_code=split.bank_code,
                reference=f"{split.id}-attempt-0",
            )
            async with AsyncSessionLocal() as split_session:
                async with split_session.begin():
                    await split_session.execute(
                        text("UPDATE splits SET status = 'paid', payout_id = :payout_id WHERE id = :split_id"),
                        {"payout_id": payout_id, "split_id": str(split.id)},
                    )
                    await split_session.execute(
                        text("UPDATE vendors SET balance = balance + :amount WHERE id = :vendor_id"),
                        {"amount": str(split.amount), "vendor_id": str(split.vendor_id)},
                    )
                    await split_session.execute(
                        text("""
                            INSERT INTO ledger (vendor_id, payment_id, type, amount, balance_after)
                            SELECT :vendor_id, :payment_id, 'credit', :amount, balance
                            FROM vendors WHERE id = :vendor_id
                        """),
                        {
                            "vendor_id": str(split.vendor_id),
                            "payment_id": str(payment.id),
                            "amount": str(split.amount),
                        },
                    )
            logger.info(
                "payout_succeeded",
                extra={
                    "split_id": str(split.id),
                    "vendor_id": str(split.vendor_id),
                    "payout_id": payout_id,
                    "amount": str(split.amount),
                },
            )
        except Exception:
            logger.exception("payout_failed", extra={"split_id": str(split.id), "vendor_id": str(split.vendor_id), "amount": str(split.amount)})
            async with AsyncSessionLocal() as fail_session:
                async with fail_session.begin():
                    await fail_session.execute(
                        text("UPDATE splits SET status = 'failed' WHERE id = :split_id"),
                        {"split_id": str(split.id)},
                    )
            await send_alert("payout.failed", split_id=str(split.id), vendor_id=str(split.vendor_id), amount=str(split.amount))
            all_paid = False

    final_status = "completed" if all_paid else "failed"
    async with AsyncSessionLocal() as final_session:
        async with final_session.begin():
            await final_session.execute(
                text("UPDATE payments SET status = :status WHERE id = :pid"),
                {"status": final_status, "pid": str(payment.id)},
            )

    return {"status": final_status}
