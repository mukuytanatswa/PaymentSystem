import logging
from uuid import UUID, uuid4

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import KycStatus, PaymentCreate, PaymentResponse, PaymentStatus, SplitResponse, SplitStatus
from app.services import stitch_client
from app.services.split_engine import SplitValidationError, calculate_splits

logger = logging.getLogger(__name__)


async def initiate_payment(
    payload: PaymentCreate,
    platform_id: UUID,
    platform_name: str,
    platform_fee_percentage,
    db: AsyncSession,
    idempotency_key: str | None = None,
) -> PaymentResponse:
    if idempotency_key:
        cached = await db.execute(
            text("""
                SELECT response FROM idempotency_keys
                WHERE key = :key AND platform_id = :platform_id
            """),
            {"key": idempotency_key, "platform_id": str(platform_id)},
        )
        hit = cached.fetchone()
        if hit:
            return PaymentResponse.model_validate_json(hit.response)

    vendor_ids = [str(s.vendor_id) for s in payload.splits]

    rows = await db.execute(
        text("""
            SELECT id, fee_percentage, kyc_status
            FROM vendors
            WHERE id = ANY(:ids::uuid[]) AND platform_id = :platform_id
        """),
        {"ids": vendor_ids, "platform_id": str(platform_id)},
    )
    vendors = {row.id: row for row in rows.fetchall()}

    if len(vendors) != len(payload.splits):
        raise HTTPException(status_code=404, detail="One or more vendor IDs not found for this platform")

    for v in vendors.values():
        if v.kyc_status != KycStatus.verified.value:
            raise HTTPException(status_code=422, detail=f"Vendor {v.id} has not completed KYC")

    vendor_fee_overrides = {vid: row.fee_percentage for vid, row in vendors.items()}

    try:
        splits = calculate_splits(
            payload.splits,
            payload.total_amount,
            platform_fee_percentage,
            vendor_fee_overrides,
        )
    except SplitValidationError as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Generate UUID upfront so Stitch and DB share the same identifier.
    payment_uuid = uuid4()

    # Call Stitch before any DB writes — if this fails, nothing is written
    stitch_id, checkout_url = await stitch_client.create_payment(
        amount=payload.total_amount,
        currency=payload.currency,
        reference=f"{platform_name}-{str(payment_uuid)[:8]}",
        payment_id=str(payment_uuid),
    )

    # Single atomic transaction: payment + splits + idempotency key
    async with db.begin():
        payment_row = await db.execute(
            text("""
                INSERT INTO payments (id, platform_id, amount, currency, status, stitch_payment_id, checkout_url)
                VALUES (:id, :platform_id, :amount, :currency, :pending, :stitch_id, :checkout_url)
                RETURNING id, created_at
            """),
            {
                "id": str(payment_uuid),
                "pending": PaymentStatus.pending.value,
                "platform_id": str(platform_id),
                "amount": str(payload.total_amount),
                "currency": payload.currency,
                "stitch_id": stitch_id,
                "checkout_url": checkout_url,
            },
        )
        payment = payment_row.fetchone()

        split_rows = []
        for s in splits:
            row = await db.execute(
                text("""
                    INSERT INTO splits (payment_id, vendor_id, amount, platform_fee, status)
                    VALUES (:payment_id, :vendor_id, :net, :fee, :pending)
                    RETURNING id, created_at
                """),
                {
                    "payment_id": str(payment.id),
                    "vendor_id": str(s.vendor_id),
                    "net": str(s.net_amount),
                    "fee": str(s.platform_fee),
                    "pending": SplitStatus.pending.value,
                },
            )
            split_rows.append((s, row.fetchone()))

        payment_response = PaymentResponse(
            id=payment.id,
            platform_id=platform_id,
            amount=payload.total_amount,
            currency=payload.currency,
            status=PaymentStatus.pending,
            checkout_url=checkout_url,
            stitch_payment_id=stitch_id,
            splits=[
                SplitResponse(
                    id=row.id,
                    vendor_id=s.vendor_id,
                    gross_amount=s.gross_amount,
                    platform_fee=s.platform_fee,
                    net_amount=s.net_amount,
                    status=SplitStatus.pending,
                    payout_id=None,
                    created_at=row.created_at,
                )
                for s, row in split_rows
            ],
            created_at=payment.created_at,
        )

        if idempotency_key:
            await db.execute(
                text("""
                    INSERT INTO idempotency_keys (key, platform_id, response)
                    VALUES (:key, :platform_id, :response::jsonb)
                """),
                {
                    "key": idempotency_key,
                    "platform_id": str(platform_id),
                    "response": payment_response.model_dump_json(),
                },
            )

    logger.info(
        "payment_initiated",
        extra={
            "payment_id": str(payment.id),
            "platform_id": str(platform_id),
            "amount": str(payload.total_amount),
            "currency": payload.currency,
            "vendor_count": len(splits),
            "stitch_payment_id": stitch_id,
        },
    )

    return payment_response


async def get_payment(payment_id: UUID, platform_id: UUID, db: AsyncSession) -> PaymentResponse:
    row = await db.execute(
        text("""
            SELECT id, platform_id, amount, currency, status, stitch_payment_id, checkout_url, created_at
            FROM payments WHERE id = :id AND platform_id = :platform_id
        """),
        {"id": str(payment_id), "platform_id": str(platform_id)},
    )
    payment = row.fetchone()
    if not payment:
        raise HTTPException(status_code=404, detail="Payment not found")

    splits_rows = await db.execute(
        text("""
            SELECT id, vendor_id, amount, platform_fee, status, payout_id, created_at,
                   amount + platform_fee AS gross_amount
            FROM splits WHERE payment_id = :pid
        """),
        {"pid": str(payment_id)},
    )

    return PaymentResponse(
        id=payment.id,
        platform_id=payment.platform_id,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status,
        checkout_url=payment.checkout_url,
        stitch_payment_id=payment.stitch_payment_id,
        splits=[
            SplitResponse(
                id=r.id,
                vendor_id=r.vendor_id,
                gross_amount=r.gross_amount,
                platform_fee=r.platform_fee,
                net_amount=r.amount,
                status=r.status,
                payout_id=r.payout_id,
                created_at=r.created_at,
            )
            for r in splits_rows.fetchall()
        ],
        created_at=payment.created_at,
    )
