import json
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.services.encryption_service import encrypt


async def test_unverified_vendor_blocks_payment(http_client, db_session, test_platform):
    """A vendor whose kyc_status != 'verified' must cause a 422 on payment creation."""
    encrypted_account = encrypt("9876543210")
    result = await db_session.execute(
        text("""
            INSERT INTO vendors (platform_id, name, bank_account, bank_code, kyc_status, fee_percentage)
            VALUES (:pid, :name, :bank_account, :bank_code, 'pending', NULL)
            RETURNING id
        """),
        {
            "pid": str(test_platform["id"]),
            "name": "Unverified Vendor",
            "bank_account": encrypted_account,
            "bank_code": "632005",
        },
    )
    await db_session.commit()
    vendor_id = str(result.fetchone().id)

    resp = await http_client.post("/api/v1/payments", json={
        "total_amount": "100.00",
        "currency": "ZAR",
        "splits": [{"vendor_id": vendor_id, "gross_amount": "100.00"}],
    })

    assert resp.status_code == 422
    assert "KYC" in resp.json()["detail"]


async def test_vendor_from_wrong_platform_returns_404(db_session, test_platform):
    """A vendor registered under platform A must be invisible to platform B."""
    other_api_key = f"other-key-{uuid4().hex[:8]}"
    platform_b = await db_session.execute(
        text("""
            INSERT INTO platforms (name, api_key, fee_percentage)
            VALUES ('Platform B', :key, 2.50)
            RETURNING id, api_key
        """),
        {"key": other_api_key},
    )
    await db_session.commit()
    platform_b_row = platform_b.fetchone()

    encrypted_account = encrypt("5555555555")
    vendor_result = await db_session.execute(
        text("""
            INSERT INTO vendors (platform_id, name, bank_account, bank_code, kyc_status)
            VALUES (:pid, 'Vendor A', :bank, '632005', 'verified')
            RETURNING id
        """),
        {"pid": str(test_platform["id"]), "bank": encrypted_account},
    )
    await db_session.commit()
    vendor_id = str(vendor_result.fetchone().id)

    from httpx import ASGITransport, AsyncClient
    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-api-key": platform_b_row.api_key},
    ) as client_b:
        with patch("app.services.stitch_client.create_payment", new_callable=AsyncMock) as mock_create:
            mock_create.return_value = ("stitch-x", "https://checkout.stitch.money/x")
            resp = await client_b.post("/api/v1/payments", json={
                "total_amount": "100.00",
                "currency": "ZAR",
                "splits": [{"vendor_id": vendor_id, "gross_amount": "100.00"}],
            })

    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


@pytest.mark.skip(reason="KYC_MODE=manual not yet implemented in app code")
async def test_manual_kyc_mode_placeholder():
    """When KYC_MODE=manual, vendor registration should bypass Smile Identity."""
    pass
