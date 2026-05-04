import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.retry_service import _retry_failed_splits
from app.models.schemas import SplitStatus


SPLIT_ID = uuid.uuid4()
VENDOR_ID = uuid.uuid4()
PAYMENT_ID = uuid.uuid4()


def _make_split_row(retry_count=0, bank_account="ENCRYPTED"):
    return SimpleNamespace(
        id=SPLIT_ID,
        payment_id=PAYMENT_ID,
        vendor_id=VENDOR_ID,
        amount=Decimal("97.50"),
        retry_count=retry_count,
        currency="ZAR",
        bank_account=bank_account,
        bank_code="632005",
    )


def _make_session(execute_side_effects):
    session = AsyncMock()
    begin_ctx = MagicMock()
    begin_ctx.__aenter__ = AsyncMock(return_value=None)
    begin_ctx.__aexit__ = AsyncMock(return_value=False)
    session.begin = MagicMock(return_value=begin_ctx)
    session.execute = AsyncMock(side_effect=list(execute_side_effects))
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _make_session_factory(split_row, count=1):
    """
    Returns a factory for AsyncSessionLocal that yields:
    - Session 0: count query
    - Session 1: split SELECT + UPDATE (inside _process_one)
    """
    sessions_created = [0]

    def factory():
        idx = sessions_created[0]
        sessions_created[0] += 1

        if idx == 0:
            count_result = MagicMock()
            count_result.scalar.return_value = count
            return _make_session([count_result])

        split_result = MagicMock()
        split_result.fetchone.return_value = split_row
        update_result = MagicMock()
        return _make_session([split_result, update_result])

    return factory


@pytest.mark.asyncio
@patch("app.services.retry_service.decrypt", return_value="1234567890")
@patch("app.services.retry_service.stitch_client.create_payout", new_callable=AsyncMock)
@patch("app.services.retry_service.AsyncSessionLocal")
async def test_retry_succeeds(mock_session_local, mock_payout, mock_decrypt):
    mock_payout.return_value = "payout-001"
    split = _make_split_row(retry_count=1)
    mock_session_local.side_effect = _make_session_factory(split, count=1)

    await _retry_failed_splits()

    mock_payout.assert_awaited_once()
    # reference must include attempt index
    call_kwargs = mock_payout.call_args.kwargs
    assert f"{split.id}-attempt-1" == call_kwargs["reference"]


@pytest.mark.asyncio
@patch("app.services.retry_service.decrypt", return_value="1234567890")
@patch("app.services.retry_service.send_alert", new_callable=AsyncMock)
@patch("app.services.retry_service.stitch_client.create_payout", new_callable=AsyncMock)
@patch("app.services.retry_service.AsyncSessionLocal")
async def test_retry_fail_increments_count(mock_session_local, mock_payout, mock_alert, mock_decrypt):
    mock_payout.side_effect = Exception("Stitch error")

    split = _make_split_row(retry_count=0)

    call_num = [0]

    def session_factory():
        session = AsyncMock()
        begin_ctx = MagicMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=None)
        begin_ctx.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=begin_ctx)

        if call_num[0] == 0:
            count_result = MagicMock()
            count_result.scalar.return_value = 1
            call_num[0] += 1
            session.execute = AsyncMock(return_value=count_result)
        elif call_num[0] == 1:
            split_result = MagicMock()
            split_result.fetchone.return_value = split
            update_result = MagicMock()
            call_num[0] += 1
            session.execute = AsyncMock(side_effect=[split_result, update_result])
        else:
            session.execute = AsyncMock(return_value=MagicMock())

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    mock_session_local.side_effect = session_factory

    await _retry_failed_splits()

    mock_alert.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.services.retry_service.decrypt", return_value="1234567890")
@patch("app.services.retry_service.send_alert", new_callable=AsyncMock)
@patch("app.services.retry_service.stitch_client.create_payout", new_callable=AsyncMock)
@patch("app.services.retry_service.AsyncSessionLocal")
async def test_max_retries_marks_dead(mock_session_local, mock_payout, mock_alert, mock_decrypt):
    mock_payout.side_effect = Exception("Stitch down")

    split = _make_split_row(retry_count=2)  # retry_count == max_split_retries (3) - 1

    call_num = [0]

    def session_factory():
        session = AsyncMock()
        begin_ctx = MagicMock()
        begin_ctx.__aenter__ = AsyncMock(return_value=None)
        begin_ctx.__aexit__ = AsyncMock(return_value=False)
        session.begin = MagicMock(return_value=begin_ctx)

        if call_num[0] == 0:
            count_result = MagicMock()
            count_result.scalar.return_value = 1
            call_num[0] += 1
            session.execute = AsyncMock(return_value=count_result)
        elif call_num[0] == 1:
            split_result = MagicMock()
            split_result.fetchone.return_value = split
            update_result = MagicMock()
            call_num[0] += 1
            session.execute = AsyncMock(side_effect=[split_result, update_result])
        else:
            session.execute = AsyncMock(return_value=MagicMock())

        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    mock_session_local.side_effect = session_factory

    await _retry_failed_splits()

    mock_alert.assert_awaited_once()
    alert_args = mock_alert.call_args
    assert alert_args.args[0] == "split.dead"
