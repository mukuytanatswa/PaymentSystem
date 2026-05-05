import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import text

from app.config.database import AsyncSessionLocal

logger = logging.getLogger(__name__)

RETENTION_YEARS = 5
RETENTION_CHECK_INTERVAL_SECONDS = 30 * 24 * 60 * 60  # 30 days


async def _purge_expired_vendors() -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(days=365 * RETENTION_YEARS)

    async with AsyncSessionLocal() as session:
        # Find vendors whose last ledger entry is older than 5 years,
        # or who have no ledger entries at all and were created > 5 years ago.
        expired = await session.execute(
            text("""
                SELECT v.id
                FROM vendors v
                LEFT JOIN ledger l ON l.vendor_id = v.id
                GROUP BY v.id, v.created_at
                HAVING
                    (MAX(l.created_at) IS NOT NULL AND MAX(l.created_at) < :cutoff)
                    OR (MAX(l.created_at) IS NULL AND v.created_at < :cutoff)
            """),
            {"cutoff": cutoff},
        )
        vendor_ids = [str(r.id) for r in expired.fetchall()]

    if not vendor_ids:
        logger.info("retention_no_expired_vendors")
        return

    logger.info("retention_purge_start", extra={"count": len(vendor_ids)})

    for vendor_id in vendor_ids:
        try:
            async with AsyncSessionLocal() as session:
                # Delete in dependency order: kyc_records, ledger, splits, then vendor row.
                # splits reference payments (not vendors directly), so delete via vendor_id join.
                await session.execute(
                    text("DELETE FROM kyc_records WHERE vendor_id = :vid"),
                    {"vid": vendor_id},
                )
                await session.execute(
                    text("DELETE FROM ledger WHERE vendor_id = :vid"),
                    {"vid": vendor_id},
                )
                await session.execute(
                    text("DELETE FROM splits WHERE vendor_id = :vid"),
                    {"vid": vendor_id},
                )
                await session.execute(
                    text("DELETE FROM audit_log WHERE vendor_id = :vid"),
                    {"vid": vendor_id},
                )
                await session.execute(
                    text("""
                        INSERT INTO audit_log (event_type, vendor_id, metadata)
                        VALUES ('vendor_purged', NULL, :meta::jsonb)
                    """),
                    {"meta": f'{{"vendor_id": "{vendor_id}", "retention_years": {RETENTION_YEARS}}}'},
                )
                await session.execute(
                    text("DELETE FROM vendors WHERE id = :vid"),
                    {"vid": vendor_id},
                )
                await session.commit()
            logger.info("retention_vendor_purged", extra={"vendor_id": vendor_id})
        except Exception:
            logger.exception("retention_purge_error", extra={"vendor_id": vendor_id})


async def start_retention_worker() -> None:
    while True:
        await asyncio.sleep(RETENTION_CHECK_INTERVAL_SECONDS)
        logger.info("retention_worker_run")
        try:
            await _purge_expired_vendors()
        except Exception:
            logger.exception("retention_worker_error")
