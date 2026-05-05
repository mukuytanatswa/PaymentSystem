from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers import vendor_controller
from app.models.schemas import KycRecord, LedgerEntry, VendorCreate, VendorResponse

router = APIRouter(prefix="/api/v1/vendors", tags=["vendors"])


@router.post("", response_model=VendorResponse, status_code=201)
async def register_vendor(
    payload: VendorCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await vendor_controller.register_vendor(payload, request.state.platform_id, db)


@router.get("/{vendor_id}", response_model=VendorResponse)
async def get_vendor(
    vendor_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await vendor_controller.get_vendor(vendor_id, request.state.platform_id, db, request)


@router.get("/{vendor_id}/ledger", response_model=list[LedgerEntry])
async def get_ledger(
    vendor_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    return await vendor_controller.get_vendor_ledger(
        vendor_id, request.state.platform_id, db, limit, offset, request
    )


@router.get("/{vendor_id}/kyc", response_model=list[KycRecord])
async def get_kyc(
    vendor_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await vendor_controller.get_vendor_kyc(vendor_id, request.state.platform_id, db, request)


@router.delete("/{vendor_id}", status_code=204)
async def delete_vendor(
    vendor_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await vendor_controller.delete_vendor(vendor_id, request.state.platform_id, db, request)
    return Response(status_code=204)
