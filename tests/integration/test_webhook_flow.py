import json
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import text

from tests.integration.conftest import make_stitch_sig


async def _create_pending_payment(http_client, vendor_id: str, stitch_id: str) -> str:
    """Helper: create a payment in pending state, return payment_id."""
    with patch("app.services.stitch_client.create_payment", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = (stitch_id, "https://checkout.stitch.money/x")
        resp = await http_client.post("/api/v1/payments", json={
            "total_amount": "80.00",
            "currency": "ZAR",
            "splits": [{"vendor_id": vendor_id, "gross_amount": "80.00"}],
        })
    assert resp.status_code == 201
    return resp.json()["id"]


async def test_valid_signature_processes_completed_webhook(http_client, test_vendor):
    stitch_id = "stitch-valid-001"
    await _create_pending_payment(http_client, str(test_vendor["id"]), stitch_id)

    body = json.dumps({
        "type": "payment_initiation_request.completed",
        "data": {"id": stitch_id},
    }).encode()
    sig = make_stitch_sig(body)

    with patch("app.services.stitch_client.create_payout", new_callable=AsyncMock) as mock_payout:
        mock_payout.return_value = "payout-valid-001"
        resp = await http_client.post(
            "/api/v1/webhooks/stitch",
            content=body,
            headers={"content-type": "application/json", "x-stitch-signature": sig},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "completed"


async def test_invalid_signature_returns_401(http_client, test_vendor):
    stitch_id = "stitch-badsig-001"
    await _create_pending_payment(http_client, str(test_vendor["id"]), stitch_id)

    body = json.dumps({
        "type": "payment_initiation_request.completed",
        "data": {"id": stitch_id},
    }).encode()
    wrong_sig = make_stitch_sig(body, secret="wrong-secret")

    resp = await http_client.post(
        "/api/v1/webhooks/stitch",
        content=body,
        headers={"content-type": "application/json", "x-stitch-signature": wrong_sig},
    )

    assert resp.status_code == 401


async def test_failed_payment_webhook_marks_payment_failed(http_client, test_vendor, db_session):
    stitch_id = "stitch-fail-001"
    payment_id = await _create_pending_payment(http_client, str(test_vendor["id"]), stitch_id)

    body = json.dumps({
        "type": "payment_initiation_request.failed",
        "data": {"id": stitch_id},
    }).encode()
    sig = make_stitch_sig(body)

    resp = await http_client.post(
        "/api/v1/webhooks/stitch",
        content=body,
        headers={"content-type": "application/json", "x-stitch-signature": sig},
    )

    assert resp.status_code == 200
    assert resp.json()["status"] == "marked_failed"

    row = await db_session.execute(
        text("SELECT status FROM payments WHERE id = :pid"),
        {"pid": payment_id},
    )
    assert row.scalar() == "failed"


async def test_duplicate_completed_webhook_is_idempotent(http_client, test_vendor):
    stitch_id = "stitch-dup-001"
    await _create_pending_payment(http_client, str(test_vendor["id"]), stitch_id)

    body = json.dumps({
        "type": "payment_initiation_request.completed",
        "data": {"id": stitch_id},
    }).encode()
    sig = make_stitch_sig(body)

    with patch("app.services.stitch_client.create_payout", new_callable=AsyncMock) as mock_payout:
        mock_payout.return_value = "payout-dup-001"

        first = await http_client.post(
            "/api/v1/webhooks/stitch",
            content=body,
            headers={"content-type": "application/json", "x-stitch-signature": sig},
        )
        second = await http_client.post(
            "/api/v1/webhooks/stitch",
            content=body,
            headers={"content-type": "application/json", "x-stitch-signature": sig},
        )

    assert first.status_code == 200
    assert first.json()["status"] == "completed"
    assert second.status_code == 200
    assert second.json()["status"] == "already_processed_or_not_found"
    assert mock_payout.call_count == 1
