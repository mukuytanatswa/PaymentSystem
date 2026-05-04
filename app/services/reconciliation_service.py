import asyncio
import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import text

from app.config.database import AsyncSessionLocal
from app.models.schemas import PaymentStatus, ReconciliationStatus, SplitStatus
from app.services.alert_service import send_alert

logger = logging.getLogger(__name__)

SAST = timezone(timedelta(hours=2))
# Run once daily — sleep until 23:59 SAST, then reconcile for that calendar date
RECONCILIATION_HOUR = 23
RECONCILIATION_MINUTE = 59


async def run_reconciliation(target_date: date) -> dict:
    async with AsyncSessionLocal() as session:
        # Total pay-ins: completed payments created on target_date (SAST)
        payins_row = await session.execute(
            text("""
                SELECT COALESCE(SUM(amount), 0) AS total
                FROM payments
                WHERE status = :completed
                  AND (created_at AT TIME ZONE 'Africa/Johannesburg')::date = :target_date
            """),
            {"target_date": target_date, "completed": PaymentStatus.completed.value},
        )
        total_payins = payins_row.scalar()

        # Total payouts and fees: splits that are 'paid', linked to completed payments on that date
        paid_row = await session.execute(
            text("""
                SELECT
                    COALESCE(SUM(s.amount), 0)       AS total_payouts,
                    COALESCE(SUM(s.platform_fee), 0) AS total_fees
                FROM splits s
                JOIN payments p ON p.id = s.payment_id
                WHERE s.status = :paid
                  AND p.status = :completed
                  AND (p.created_at AT TIME ZONE 'Africa/Johannesburg')::date = :target_date
            """),
            {"target_date": target_date, "paid": SplitStatus.paid.value, "completed": PaymentStatus.completed.value},
        )
        paid = paid_row.fetchone()
        total_payouts = paid.total_payouts
        total_fees = paid.total_fees

        # Soft difference: pending/failed splits on completed payments from that date
        soft_rows = await session.execute(
            text("""
                SELECT
                    s.id,
                    COALESCE(s.amount + s.platform_fee, 0) AS held
                FROM splits s
                JOIN payments p ON p.id = s.payment_id
                WHERE s.status IN (:s_pending, :s_failed)
                  AND p.status = :completed
                  AND (p.created_at AT TIME ZONE 'Africa/Johannesburg')::date = :target_date
            """),
            {"target_date": target_date, "s_pending": SplitStatus.pending.value, "s_failed": SplitStatus.failed.value, "completed": PaymentStatus.completed.value},
        )
        soft_splits = soft_rows.fetchall()
        soft_difference = sum(r.held for r in soft_splits)
        soft_split_ids = [str(r.id) for r in soft_splits]

        hard_difference = total_payins - (total_payouts + total_fees + soft_difference)
        status = ReconciliationStatus.balanced.value if hard_difference == 0 else ReconciliationStatus.unbalanced.value

        await session.execute(
            text("""
                INSERT INTO reconciliation_records
                    (date, total_payins, total_payouts, total_fees, soft_difference, hard_difference, status)
                VALUES
                    (:date, :total_payins, :total_payouts, :total_fees, :soft_difference, :hard_difference, :status)
                ON CONFLICT (date) DO UPDATE SET
                    total_payins    = EXCLUDED.total_payins,
                    total_payouts   = EXCLUDED.total_payouts,
                    total_fees      = EXCLUDED.total_fees,
                    soft_difference = EXCLUDED.soft_difference,
                    hard_difference = EXCLUDED.hard_difference,
                    status          = EXCLUDED.status
            """),
            {
                "date": target_date,
                "total_payins": str(total_payins),
                "total_payouts": str(total_payouts),
                "total_fees": str(total_fees),
                "soft_difference": str(soft_difference),
                "hard_difference": str(hard_difference),
                "status": status,
            },
        )
        await session.commit()

    result = {
        "date": str(target_date),
        "total_payins": float(total_payins),
        "total_payouts": float(total_payouts),
        "total_fees": float(total_fees),
        "soft_difference": float(soft_difference),
        "hard_difference": float(hard_difference),
        "status": status,
    }
    logger.info("reconciliation_complete", extra=result)

    if hard_difference != 0:
        # Identify splits that are unaccounted for (not pending/failed, yet money is missing)
        async with AsyncSessionLocal() as session:
            unresolved_rows = await session.execute(
                text("""
                    SELECT s.id
                    FROM splits s
                    JOIN payments p ON p.id = s.payment_id
                    WHERE p.status = 'completed'
                      AND s.status NOT IN (:paid)
                      AND (p.created_at AT TIME ZONE 'Africa/Johannesburg')::date = :target_date
                """),
                {"target_date": target_date, "paid": SplitStatus.paid.value},
            )
            unresolved_split_ids = [str(r.id) for r in unresolved_rows.fetchall()]

        await send_alert(
            "reconciliation.hard_difference",
            date=str(target_date),
            hard_difference=float(hard_difference),
            soft_difference=float(soft_difference),
            total_payins=float(total_payins),
            unresolved_split_ids=unresolved_split_ids,
        )

    return result


def _seconds_until_next_run() -> float:
    now = datetime.now(SAST)
    target = now.replace(hour=RECONCILIATION_HOUR, minute=RECONCILIATION_MINUTE, second=0, microsecond=0)
    if now >= target:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def _backfill_missed_days() -> None:
    async with AsyncSessionLocal() as session:
        row = await session.execute(text("SELECT MAX(date) FROM reconciliation_records"))
        last_date = row.scalar()

    if last_date is None:
        return

    today = datetime.now(SAST).date()
    current = last_date + timedelta(days=1)
    while current < today:
        logger.info("reconciliation_backfill", extra={"date": str(current)})
        try:
            await run_reconciliation(current)
        except Exception:
            logger.exception("reconciliation_backfill_error", extra={"date": str(current)})
        current += timedelta(days=1)


async def start_reconciliation_worker() -> None:
    await _backfill_missed_days()
    while True:
        await asyncio.sleep(_seconds_until_next_run())
        target_date = datetime.now(SAST).date()
        logger.info("reconciliation_worker_run", extra={"date": str(target_date)})
        try:
            await run_reconciliation(target_date)
        except Exception:
            logger.exception("reconciliation_worker_error", extra={"date": str(target_date)})
