import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from tests.integration.conftest import make_stitch_sig


async def _setup_completed_payment(http_client, vendor_id: str) -> tuple[str, str]:
    """Create a payment and simulate a successful webhook. Returns (payment_id, stitch_id)."""
    with patch("app.services.stitch_client.create_payment", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = ("stitch-refund-pay", "https://checkout.stitch.money/r")
        resp = await http_client.post("/api/v1/payments", json={
            "total_amount": "120.00",
            "currency": "ZAR",
            "splits": [{"vendor_id": vendor_id, "gross_amount": "120.00"}],
        })
    assert resp.status_code == 201
    stitch_id = resp.json()["stitch_payment_id"]
    payment_id = resp.json()["id"]

    body = json.dumps({
        "type": "payment_initiation_request.completed",
        "data": {"id": stitch_id},
    }).encode()
    sig = make_stitch_sig(body)

    with patch("app.services.stitch_client.create_payout", new_callable=AsyncMock) as mock_payout:
        mock_payout.return_value = "payout-for-refund-test"
        wh = await http_client.post(
            "/api/v1/webhooks/stitch",
            content=body,
            headers={"content-type": "application/json", "x-stitch-signature": sig},
        )
    assert wh.json()["status"] == "completed"
    return payment_id, stitch_id


async def test_refund_happy_path(http_client, test_vendor, db_session):
    payment_id, _ = await _setup_completed_payment(http_client, str(test_vendor["id"]))

    with patch("app.services.stitch_client.reverse_disbursement", new_callable=AsyncMock) as mock_rev:
        mock_rev.return_value = "stitch-reverse-001"
        resp = await http_client.post(
            f"/api/v1/payments/{payment_id}/refund",
            json={"reason": "Customer requested refund"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "completed"
    assert data["stitch_reverse_id"] == "stitch-reverse-001"
    assert data["payment_id"] == payment_id

    payment_row = await db_session.execute(
        text("SELECT status FROM payments WHERE id = :id"),
        {"id": payment_id},
    )
    assert payment_row.scalar() == "refunded"

    ledger_row = await db_session.execute(
        text("SELECT COUNT(*) FROM ledger WHERE payment_id = :id AND type = 'debit'"),
        {"id": payment_id},
    )
    assert ledger_row.scalar() == 1


async def test_refund_non_completed_payment_returns_422(http_client, test_vendor):
    with patch("app.services.stitch_client.create_payment", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = ("stitch-pending-pay", "https://checkout.stitch.money/p")
        resp = await http_client.post("/api/v1/payments", json={
            "total_amount": "60.00",
            "currency": "ZAR",
            "splits": [{"vendor_id": str(test_vendor["id"]), "gross_amount": "60.00"}],
        })
    payment_id = resp.json()["id"]

    refund_resp = await http_client.post(
        f"/api/v1/payments/{payment_id}/refund",
        json={"reason": "Changing my mind"},
    )

    assert refund_resp.status_code == 422
    assert "pending" in refund_resp.json()["detail"].lower()


async def test_refund_failure_handling(http_client, test_vendor, db_session):
    payment_id, _ = await _setup_completed_payment(http_client, str(test_vendor["id"]))

    with patch("app.services.stitch_client.reverse_disbursement", new_callable=AsyncMock) as mock_rev:
        mock_rev.side_effect = RuntimeError("Stitch reversal failed")
        resp = await http_client.post(
            f"/api/v1/payments/{payment_id}/refund",
            json={"reason": "Reversal test"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "failed"

    payment_row = await db_session.execute(
        text("SELECT status FROM payments WHERE id = :id"),
        {"id": payment_id},
    )
    assert payment_row.scalar() == "completed"
