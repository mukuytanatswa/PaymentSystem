import json
from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from app.services.reconciliation_service import run_reconciliation
from tests.integration.conftest import make_stitch_sig


async def test_happy_path_create_webhook_ledger(http_client, test_vendor, db_session):
    vendor_id = str(test_vendor["id"])

    with patch("app.services.stitch_client.create_payment", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = ("stitch-pay-001", "https://checkout.stitch.money/test")
        resp = await http_client.post("/api/v1/payments", json={
            "total_amount": "100.00",
            "currency": "ZAR",
            "splits": [{"vendor_id": vendor_id, "gross_amount": "100.00"}],
        })

    assert resp.status_code == 201
    data = resp.json()
    payment_id = data["id"]
    stitch_id = data["stitch_payment_id"]
    assert data["checkout_url"] == "https://checkout.stitch.money/test"
    assert data["status"] == "pending"

    body = json.dumps({
        "type": "payment_initiation_request.completed",
        "data": {"id": stitch_id},
    }).encode()
    sig = make_stitch_sig(body)

    with patch("app.services.stitch_client.create_payout", new_callable=AsyncMock) as mock_payout:
        mock_payout.return_value = "stitch-payout-001"
        webhook_resp = await http_client.post(
            "/api/v1/webhooks/stitch",
            content=body,
            headers={"content-type": "application/json", "x-stitch-signature": sig},
        )

    assert webhook_resp.status_code == 200
    assert webhook_resp.json()["status"] == "completed"

    get_resp = await http_client.get(f"/api/v1/payments/{payment_id}")
    assert get_resp.status_code == 200
    payment = get_resp.json()
    assert payment["status"] == "completed"
    assert payment["splits"][0]["status"] == "paid"
    assert payment["splits"][0]["payout_id"] == "stitch-payout-001"

    result = await db_session.execute(
        text("SELECT COUNT(*) FROM ledger WHERE payment_id = :pid"),
        {"pid": payment_id},
    )
    assert result.scalar() == 1


async def test_idempotency_key_prevents_duplicate_stitch_call(http_client, test_vendor):
    vendor_id = str(test_vendor["id"])

    with patch("app.services.stitch_client.create_payment", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = ("stitch-pay-idem", "https://checkout.stitch.money/idem")

        resp1 = await http_client.post(
            "/api/v1/payments",
            json={
                "total_amount": "50.00",
                "currency": "ZAR",
                "splits": [{"vendor_id": vendor_id, "gross_amount": "50.00"}],
            },
            headers={"Idempotency-Key": "key-abc-123"},
        )
        resp2 = await http_client.post(
            "/api/v1/payments",
            json={
                "total_amount": "50.00",
                "currency": "ZAR",
                "splits": [{"vendor_id": vendor_id, "gross_amount": "50.00"}],
            },
            headers={"Idempotency-Key": "key-abc-123"},
        )

    assert resp1.status_code == 201
    assert resp2.status_code == 201
    assert resp1.json()["id"] == resp2.json()["id"]
    assert mock_create.call_count == 1


async def test_reconciliation_balanced_after_completed_payment(http_client, test_vendor, db_session):
    vendor_id = str(test_vendor["id"])
    today = date.today()

    with patch("app.services.stitch_client.create_payment", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = ("stitch-pay-recon", "https://checkout.stitch.money/recon")
        resp = await http_client.post("/api/v1/payments", json={
            "total_amount": "200.00",
            "currency": "ZAR",
            "splits": [{"vendor_id": vendor_id, "gross_amount": "200.00"}],
        })
    stitch_id = resp.json()["stitch_payment_id"]

    body = json.dumps({
        "type": "payment_initiation_request.completed",
        "data": {"id": stitch_id},
    }).encode()
    sig = make_stitch_sig(body)

    with patch("app.services.stitch_client.create_payout", new_callable=AsyncMock) as mock_payout:
        mock_payout.return_value = "stitch-payout-recon"
        await http_client.post(
            "/api/v1/webhooks/stitch",
            content=body,
            headers={"content-type": "application/json", "x-stitch-signature": sig},
        )

    result = await run_reconciliation(today)

    assert float(result["hard_difference"]) == 0.0
    assert result["status"] == "balanced"
    assert float(result["total_payins"]) == 200.0

    row = await db_session.execute(
        text("SELECT status, hard_difference FROM reconciliation_records WHERE date = :d"),
        {"d": today},
    )
    rec = row.fetchone()
    assert rec is not None
    assert rec.status == "balanced"
    assert float(rec.hard_difference) == 0.0
