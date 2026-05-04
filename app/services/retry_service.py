import asyncio
import logging

from sqlalchemy import text

from app.config.database import AsyncSessionLocal
from app.config.env import settings
from app.models.schemas import KycStatus, SplitStatus
from app.services import stitch_client
from app.services.alert_service import send_alert
from app.services.encryption_service import decrypt

logger = logging.getLogger(__name__)

RETRY_INTERVAL_SECONDS = 600  # 10 minutes


async def _retry_failed_splits() -> None:
    # Count available splits for queue-size alerting (no lock needed)
    async with AsyncSessionLocal() as session:
        count_row = await session.execute(
            text("""
                SELECT COUNT(*)
                FROM splits s
                JOIN vendors v ON v.id = s.vendor_id
                WHERE s.status = :failed
                  AND s.retry_count < :max_retries
                  AND v.kyc_status = :verified
            """),
            {"max_retries": settings.max_split_retries, "failed": SplitStatus.failed.value, "verified": KycStatus.verified.value},
        )
        count = count_row.scalar()

    logger.info("retry_queue_size", extra={"count": count})

    if count > settings.max_retry_queue_size:
        await send_alert("retry_queue.overflow", count=count, threshold=settings.max_retry_queue_size)

    # Each split gets its own session with FOR UPDATE SKIP LOCKED — safe for parallel execution.
    # Capped at CONCURRENCY to avoid overwhelming Stitch's API.
    CONCURRENCY = 10

    async def _process_one() -> None:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    text("""
                        SELECT s.id, s.payment_id, s.vendor_id, s.amount, s.retry_count,
                               p.currency, v.bank_account, v.bank_code
                        FROM splits s
                        JOIN payments p ON p.id = s.payment_id
                        JOIN vendors v ON v.id = s.vendor_id
                        WHERE s.status = :failed
                          AND s.retry_count < :max_retries
                          AND v.kyc_status = :verified
                        FOR UPDATE OF s SKIP LOCKED
                        LIMIT 1
                    """),
                    {"max_retries": settings.max_split_retries, "failed": SplitStatus.failed.value, "verified": KycStatus.verified.value},
                )
                row = result.fetchone()
                if not row:
                    return

                try:
                    payout_id = await stitch_client.create_payout(
                        vendor_id=str(row.vendor_id),
                        amount=row.amount,
                        currency=row.currency,
                        bank_account=decrypt(row.bank_account),
                        bank_code=row.bank_code,
                        reference=f"{row.id}-attempt-{row.retry_count}",
                    )
                    await session.execute(
                        text("UPDATE splits SET status = :paid, payout_id = :payout_id WHERE id = :split_id"),
                        {"paid": SplitStatus.paid.value, "payout_id": payout_id, "split_id": str(row.id)},
                    )
                    logger.info(
                        "retry_succeeded",
                        extra={"split_id": str(row.id), "payout_id": payout_id, "retry_count": row.retry_count},
                    )
                except Exception:
                    new_count = row.retry_count + 1
                    logger.exception("retry_failed", extra={"split_id": str(row.id), "retry_count": new_count})
                    if new_count >= settings.max_split_retries:
                        await session.execute(
                            text("UPDATE splits SET status = :dead, retry_count = :count WHERE id = :split_id"),
                            {"dead": SplitStatus.dead.value, "count": new_count, "split_id": str(row.id)},
                        )
                        await send_alert("split.dead", split_id=str(row.id), retry_count=new_count)
                    else:
                        await session.execute(
                            text("UPDATE splits SET retry_count = :count WHERE id = :split_id"),
                            {"count": new_count, "split_id": str(row.id)},
                        )

    sem = asyncio.Semaphore(CONCURRENCY)

    async def _bounded() -> None:
        async with sem:
            await _process_one()

    await asyncio.gather(*[_bounded() for _ in range(count)])


async def start_retry_worker() -> None:
    while True:
        await asyncio.sleep(RETRY_INTERVAL_SECONDS)
        logger.info("retry_worker_run")
        await _retry_failed_splits()
