import datetime
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.controllers.vendor_controller import (
    delete_vendor,
    get_vendor,
    get_vendor_kyc,
    get_vendor_ledger,
    register_vendor,
)
from app.models.schemas import KycStatus, VendorCreate

PLATFORM_ID = uuid.uuid4()
VENDOR_ID = uuid.uuid4()


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


VENDOR_ROW = SimpleNamespace(
    id=VENDOR_ID,
    platform_id=PLATFORM_ID,
    name="Alice",
    bank_account="ENCRYPTED",
    bank_code="632005",
    kyc_status=KycStatus.verified.value,
    balance=Decimal("0.00"),
    fee_percentage=None,
    created_at=datetime.datetime.utcnow(),
)


# ---------------------------------------------------------------------------
# register_vendor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.controllers.vendor_controller.encrypt", return_value="ENCRYPTED")
@patch("app.controllers.vendor_controller.verify_vendor", new_callable=AsyncMock)
async def test_register_vendor_verified(mock_verify, mock_encrypt):
    mock_verify.return_value = {"result": "verified"}

    vendor_insert = _result([SimpleNamespace(id=VENDOR_ID, balance=Decimal("0"), created_at=datetime.datetime.utcnow())])
    kyc_insert = _result([])

    db = _make_db(vendor_insert, kyc_insert)

    payload = VendorCreate(
        name="Alice",
        bank_account="1234567890",
        bank_code="632005",
        id_number="9001015009087",
    )
    result = await register_vendor(payload, PLATFORM_ID, db)

    assert result.kyc_status == KycStatus.verified
    assert result.bank_account == "1234567890"  # plaintext returned
    mock_encrypt.assert_called_once_with("1234567890")


@pytest.mark.asyncio
@patch("app.controllers.vendor_controller.encrypt", return_value="ENCRYPTED")
@patch("app.controllers.vendor_controller.verify_vendor", new_callable=AsyncMock)
async def test_register_vendor_rejected_on_kyc_failure(mock_verify, mock_encrypt):
    from app.services.kyc_service import KycVerificationError
    mock_verify.side_effect = KycVerificationError({"result": "failed"})

    vendor_insert = _result([SimpleNamespace(id=VENDOR_ID, balance=Decimal("0"), created_at=datetime.datetime.utcnow())])
    kyc_insert = _result([])

    db = _make_db(vendor_insert, kyc_insert)

    payload = VendorCreate(
        name="Bob",
        bank_account="0987654321",
        bank_code="632005",
        id_number="9001015009087",
    )
    result = await register_vendor(payload, PLATFORM_ID, db)
    assert result.kyc_status == KycStatus.rejected


# ---------------------------------------------------------------------------
# get_vendor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@patch("app.controllers.vendor_controller.decrypt", return_value="1234567890")
async def test_get_vendor_returns_decrypted_bank(mock_decrypt):
    db = _make_db(_result([VENDOR_ROW]), _result([]))  # SELECT + audit INSERT

    result = await get_vendor(VENDOR_ID, PLATFORM_ID, db)
    assert result.bank_account == "1234567890"
    mock_decrypt.assert_called_once_with("ENCRYPTED")


@pytest.mark.asyncio
@patch("app.controllers.vendor_controller.decrypt", return_value="1234567890")
async def test_get_vendor_not_found(mock_decrypt):
    from fastapi import HTTPException
    db = _make_db(_result([]))

    with pytest.raises(HTTPException) as exc:
        await get_vendor(VENDOR_ID, PLATFORM_ID, db)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_vendor_ledger
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_vendor_ledger_returns_entries():
    ledger_row = SimpleNamespace(
        id=uuid.uuid4(),
        vendor_id=VENDOR_ID,
        payment_id=uuid.uuid4(),
        type="credit",
        amount=Decimal("97.50"),
        balance_after=Decimal("97.50"),
        created_at=datetime.datetime.utcnow(),
    )
    db = _make_db(
        _result([VENDOR_ROW]),   # exists check
        _result([]),              # audit INSERT
        _result([ledger_row]),   # SELECT ledger
    )
    result = await get_vendor_ledger(VENDOR_ID, PLATFORM_ID, db)
    assert len(result) == 1
    assert result[0].amount == Decimal("97.50")


@pytest.mark.asyncio
async def test_get_vendor_ledger_not_found():
    from fastapi import HTTPException
    db = _make_db(_result([]))

    with pytest.raises(HTTPException) as exc:
        await get_vendor_ledger(VENDOR_ID, PLATFORM_ID, db)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# get_vendor_kyc
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_vendor_kyc_returns_records():
    kyc_row = SimpleNamespace(
        id=uuid.uuid4(),
        vendor_id=VENDOR_ID,
        status=KycStatus.verified.value,
        verification_result={"result": "ok"},
        verified_at=datetime.datetime.utcnow(),
    )
    db = _make_db(
        _result([VENDOR_ROW]),
        _result([]),
        _result([kyc_row]),
    )
    result = await get_vendor_kyc(VENDOR_ID, PLATFORM_ID, db)
    assert len(result) == 1
    assert result[0].status == KycStatus.verified


# ---------------------------------------------------------------------------
# delete_vendor
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_vendor_fica_blocks_within_5_years():
    from fastapi import HTTPException

    db = _make_db(
        _result([VENDOR_ROW]),                      # exists check
        _result([SimpleNamespace(last_tx=datetime.datetime.utcnow())]),  # MAX ledger date
        _result([SimpleNamespace(within_window=True)]),  # 5-year check
        _result([]),                                  # erasure_refused audit INSERT
    )

    with pytest.raises(HTTPException) as exc:
        await delete_vendor(VENDOR_ID, PLATFORM_ID, db)
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_vendor_not_found():
    from fastapi import HTTPException
    db = _make_db(_result([]))

    with pytest.raises(HTTPException) as exc:
        await delete_vendor(VENDOR_ID, PLATFORM_ID, db)
    assert exc.value.status_code == 404
