from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers import reconciliation_controller
from app.middleware.admin_auth import require_admin_key
from app.models.schemas import ReconciliationRecord

router = APIRouter(
    prefix="/api/v1/reconciliation",
    tags=["reconciliation"],
    dependencies=[Depends(require_admin_key)],
)


@router.get("", response_model=ReconciliationRecord)
async def get_reconciliation(
    date: date = Query(..., description="Date in YYYY-MM-DD format"),
    db: AsyncSession = Depends(get_db),
):
    return await reconciliation_controller.get_reconciliation(date, db)
