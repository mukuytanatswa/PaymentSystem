import asyncio
import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.controllers.webhook_controller import handle_stitch_webhook
from app.models.schemas import StitchWebhookPayload

STITCH_ID = "stitch-pay-001"

PAYLOAD = StitchWebhookPayload(
    type="payment_initiation_request.completed",
    data={"id": STITCH_ID},
)

PAYMENT_ROW = SimpleNamespace(
    id=uuid.uuid4(),
    amount=Decimal("100.00"),
    currency="ZAR",
)


def _make_db(claim_row):
    """Build a mock AsyncSession for the claim-only transaction (db.begin block)."""
    db = AsyncMock()

    begin_ctx = AsyncMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    db.begin = MagicMock(return_value=begin_ctx)

    claim_result = MagicMock()
    claim_result.fetchone.return_value = claim_row

    # Only the claim UPDATE goes through db.execute; all other calls use AsyncSessionLocal.
    db.execute = AsyncMock(return_value=claim_result)
    return db


def _make_session_local_factory(fetchall_result=None):
    """Return a callable mock for AsyncSessionLocal that yields a clean session each call."""
    fetchall_result = fetchall_result if fetchall_result is not None else []

    def factory():
        session = AsyncMock()
        begin_ctx = AsyncMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=None)
        begin_ctx.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=begin_ctx)
        result = MagicMock()
        result.fetchall.return_value = fetchall_result
        result.fetchone.return_value = MagicMock()
        session.execute = AsyncMock(return_value=result)
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    return factory


@pytest.mark.asyncio
@patch("app.controllers.webhook_controller.stitch_client.create_payout", new_callable=AsyncMock)
@patch("app.controllers.webhook_controller.AsyncSessionLocal")
async def test_first_webhook_processes_successfully(mock_session_local, mock_payout):
    mock_session_local.side_effect = _make_session_local_factory(fetchall_result=[])
    db = _make_db(claim_row=PAYMENT_ROW)
    result = await handle_stitch_webhook(PAYLOAD, db)
    assert result == {"status": "completed"}


@pytest.mark.asyncio
@patch("app.controllers.webhook_controller.stitch_client.create_payout", new_callable=AsyncMock)
@patch("app.controllers.webhook_controller.AsyncSessionLocal")
async def test_duplicate_webhook_returns_already_processed(mock_session_local, mock_payout):
    mock_session_local.side_effect = _make_session_local_factory()
    db = _make_db(claim_row=None)  # UPDATE matched nothing — already claimed
    result = await handle_stitch_webhook(PAYLOAD, db)
    assert result == {"status": "already_processed_or_not_found"}
    mock_payout.assert_not_called()


@pytest.mark.asyncio
@patch("app.controllers.webhook_controller.stitch_client.create_payout", new_callable=AsyncMock)
@patch("app.controllers.webhook_controller.AsyncSessionLocal")
async def test_concurrent_webhooks_only_one_processes(mock_session_local, mock_payout):
    """
    Simulate two concurrent deliveries of the same webhook. The atomic
    UPDATE WHERE status='pending' means only one claim_row wins; the other
    gets None. Assert exactly one response is 'completed' and one is
    'already_processed_or_not_found'.
    """
    mock_session_local.side_effect = _make_session_local_factory(fetchall_result=[])

    call_count = 0

    def fetchone_side_effect():
        nonlocal call_count
        call_count += 1
        return PAYMENT_ROW if call_count == 1 else None

    def make_concurrent_db():
        db = AsyncMock()
        begin_ctx = AsyncMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=None)
        begin_ctx.__aexit__ = AsyncMock(return_value=False)
        db.begin = MagicMock(return_value=begin_ctx)
        claim_result = MagicMock()
        claim_result.fetchone.side_effect = fetchone_side_effect
        db.execute = AsyncMock(return_value=claim_result)
        return db

    results = await asyncio.gather(
        handle_stitch_webhook(PAYLOAD, make_concurrent_db()),
        handle_stitch_webhook(PAYLOAD, make_concurrent_db()),
    )

    statuses = {r["status"] for r in results}
    assert "completed" in statuses
    assert "already_processed_or_not_found" in statuses
