from datetime import date

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import ReconciliationRecord, ReconciliationStatus


async def get_reconciliation(target_date: date, db: AsyncSession) -> ReconciliationRecord:
    row = await db.execute(
        text("""
            SELECT id, date, total_payins, total_payouts, total_fees,
                   soft_difference, hard_difference, status, created_at
            FROM reconciliation_records
            WHERE date = :target_date
        """),
        {"target_date": target_date},
    )
    record = row.fetchone()
    if not record:
        raise HTTPException(
            status_code=404,
            detail=f"No reconciliation record found for {target_date}. "
                   "The daily job runs at 23:59 SAST — check back after midnight.",
        )

    return ReconciliationRecord(
        id=record.id,
        date=record.date,
        total_payins=record.total_payins,
        total_payouts=record.total_payouts,
        total_fees=record.total_fees,
        soft_difference=record.soft_difference,
        hard_difference=record.hard_difference,
        status=ReconciliationStatus(record.status),
        created_at=record.created_at,
    )
