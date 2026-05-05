from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.controllers import payment_controller, refund_controller
from app.models.schemas import PaymentCreate, PaymentResponse, RefundCreate, RefundResponse

router = APIRouter(prefix="/api/v1/payments", tags=["payments"])


@router.post("", response_model=PaymentResponse, status_code=201)
async def create_payment(
    payload: PaymentCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
):
    return await payment_controller.initiate_payment(
        payload,
        platform_id=request.state.platform_id,
        platform_name=request.state.platform_name,
        platform_fee_percentage=request.state.platform_fee_percentage,
        db=db,
        idempotency_key=idempotency_key,
    )


@router.get("/{payment_id}", response_model=PaymentResponse)
async def get_payment(
    payment_id: UUID,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await payment_controller.get_payment(payment_id, request.state.platform_id, db)


@router.post("/{payment_id}/refund", response_model=RefundResponse, status_code=200)
async def refund_payment(
    payment_id: UUID,
    payload: RefundCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    return await refund_controller.initiate_refund(
        payment_id=payment_id,
        platform_id=request.state.platform_id,
        platform_name=request.state.platform_name,
        payload=payload,
        db=db,
    )
