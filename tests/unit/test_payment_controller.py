import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.controllers.payment_controller import get_payment, initiate_payment
from app.models.schemas import PaymentStatus, SplitStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

PLATFORM_ID = uuid.uuid4()
PLATFORM_NAME = "Chow"
VENDOR_ID = uuid.uuid4()
PAYMENT_ID = uuid.uuid4()


def _make_db(*execute_returns):
    db = AsyncMock()
    begin_ctx = MagicMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    db.begin = MagicMock(return_value=begin_ctx)
    db.execute = AsyncMock(side_effect=list(execute_returns))
    return db


def _result(rows):
    r = MagicMock()
    r.fetchall.return_value = rows
    r.fetchone.return_value = rows[0] if rows else None
    r.scalar.return_value = rows[0] if rows else None
    return r


def _vendor_row(kyc_status="verified", fee=None):
    return SimpleNamespace(id=VENDOR_ID, kyc_status=kyc_status, fee_percentage=fee)


def _payment_row():
    import datetime
    return SimpleNamespace(id=PAYMENT_ID, created_at=datetime.datetime.utcnow())


def _split_row():
    import datetime
    return SimpleNamespace(
        id=uuid.uuid4(),
        vendor_id=VENDOR_ID,
        amount=Decimal("97.50"),
        platform_fee=Decimal("2.50"),
        status=SplitStatus.pending.value,
        payout_id=None,
        gross_amount=Decimal("100.00"),
        created_at=datetime.datetime.utcnow(),
    )


# ---------------------------------------------------------------------------
# initiate_payment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.controllers.payment_controller.stitch_client.create_payment", new_callable=AsyncMock)
async def test_initiate_payment_happy_path(mock_create_payment):
    mock_create_payment.return_value = ("stitch-001", "https://checkout.example.com")

    vendor_result = _result([_vendor_row()])
    payment_result = _result([_payment_row()])
    split_result = _result([_split_row()])

    db = _make_db(
        vendor_result,   # SELECT vendors
        payment_result,  # INSERT payments RETURNING
        split_result,    # INSERT splits RETURNING
    )

    from app.models.schemas import SplitInput
    payload = MagicMock()
    payload.total_amount = Decimal("100.00")
    payload.currency = "ZAR"
    payload.splits = [SplitInput(vendor_id=VENDOR_ID, gross_amount=Decimal("100.00"))]

    result = await initiate_payment(
        payload,
        platform_id=PLATFORM_ID,
        platform_name=PLATFORM_NAME,
        platform_fee_percentage=Decimal("2.5"),
        db=db,
        idempotency_key=None,
    )

    assert result.status == PaymentStatus.pending
    assert result.checkout_url == "https://checkout.example.com"
    mock_create_payment.assert_awaited_once()
    # reference must contain platform name and a UUID fragment
    call_kwargs = mock_create_payment.call_args.kwargs
    assert call_kwargs["reference"].startswith(f"{PLATFORM_NAME}-")
    assert len(call_kwargs["payment_id"]) == 36  # full UUID


@pytest.mark.asyncio
@patch("app.controllers.payment_controller.stitch_client.create_payment", new_callable=AsyncMock)
async def test_initiate_payment_vendor_not_found(mock_create_payment):
    from fastapi import HTTPException

    db = _make_db(_result([]))  # empty vendor result

    from app.models.schemas import SplitInput
    payload = MagicMock()
    payload.total_amount = Decimal("100.00")
    payload.currency = "ZAR"
    payload.splits = [SplitInput(vendor_id=VENDOR_ID, gross_amount=Decimal("100.00"))]

    with pytest.raises(HTTPException) as exc:
        await initiate_payment(
            payload,
            platform_id=PLATFORM_ID,
            platform_name=PLATFORM_NAME,
            platform_fee_percentage=Decimal("2.5"),
            db=db,
        )
    assert exc.value.status_code == 404
    mock_create_payment.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.controllers.payment_controller.stitch_client.create_payment", new_callable=AsyncMock)
async def test_initiate_payment_kyc_not_verified(mock_create_payment):
    from fastapi import HTTPException

    db = _make_db(_result([_vendor_row(kyc_status="pending")]))

    from app.models.schemas import SplitInput
    payload = MagicMock()
    payload.total_amount = Decimal("100.00")
    payload.currency = "ZAR"
    payload.splits = [SplitInput(vendor_id=VENDOR_ID, gross_amount=Decimal("100.00"))]

    with pytest.raises(HTTPException) as exc:
        await initiate_payment(
            payload,
            platform_id=PLATFORM_ID,
            platform_name=PLATFORM_NAME,
            platform_fee_percentage=Decimal("2.5"),
            db=db,
        )
    assert exc.value.status_code == 422
    mock_create_payment.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.controllers.payment_controller.stitch_client.create_payment", new_callable=AsyncMock)
async def test_initiate_payment_idempotency_hit(mock_create_payment):
    import datetime
    from app.models.schemas import PaymentResponse, SplitResponse

    cached_response = PaymentResponse(
        id=PAYMENT_ID,
        platform_id=PLATFORM_ID,
        amount=Decimal("100.00"),
        currency="ZAR",
        status=PaymentStatus.pending,
        checkout_url="https://cached.example.com",
        stitch_payment_id="stitch-cached",
        splits=[],
        created_at=datetime.datetime.utcnow(),
    )

    idem_result = MagicMock()
    idem_result.fetchone.return_value = SimpleNamespace(response=cached_response.model_dump_json())

    db = _make_db(idem_result)

    from app.models.schemas import SplitInput
    payload = MagicMock()
    payload.total_amount = Decimal("100.00")
    payload.currency = "ZAR"
    payload.splits = [SplitInput(vendor_id=VENDOR_ID, gross_amount=Decimal("100.00"))]

    result = await initiate_payment(
        payload,
        platform_id=PLATFORM_ID,
        platform_name=PLATFORM_NAME,
        platform_fee_percentage=Decimal("2.5"),
        db=db,
        idempotency_key="idem-key-1",
    )

    assert result.checkout_url == "https://cached.example.com"
    mock_create_payment.assert_not_awaited()


# ---------------------------------------------------------------------------
# get_payment
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_payment_not_found():
    from fastapi import HTTPException

    db = _make_db(_result([]))
    with pytest.raises(HTTPException) as exc:
        await get_payment(PAYMENT_ID, PLATFORM_ID, db)
    assert exc.value.status_code == 404


@pytest.mark.asyncio
async def test_get_payment_returns_splits():
    import datetime
    payment = SimpleNamespace(
        id=PAYMENT_ID,
        platform_id=PLATFORM_ID,
        amount=Decimal("100.00"),
        currency="ZAR",
        status=PaymentStatus.pending.value,
        stitch_payment_id="stitch-001",
        checkout_url="https://example.com",
        created_at=datetime.datetime.utcnow(),
    )

    db = _make_db(_result([payment]), _result([_split_row()]))
    result = await get_payment(PAYMENT_ID, PLATFORM_ID, db)

    assert result.id == PAYMENT_ID
    assert len(result.splits) == 1
