import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.reconciliation_service import _backfill_missed_days, run_reconciliation


def _make_session_factory(execute_results):
    call_index = [0]

    def factory():
        results = execute_results[call_index[0]] if call_index[0] < len(execute_results) else []
        call_index[0] += 1
        session = AsyncMock()
        session.execute = AsyncMock(side_effect=list(results))
        session.commit = AsyncMock()
        ctx = MagicMock()
        ctx.__aenter__ = AsyncMock(return_value=session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        return ctx

    return factory


def _scalar(value):
    r = MagicMock()
    r.scalar.return_value = value
    return r


def _row_result(fetchone_val=None, fetchall_val=None, scalar_val=None):
    r = MagicMock()
    r.fetchone.return_value = fetchone_val
    r.fetchall.return_value = fetchall_val or []
    r.scalar.return_value = scalar_val
    return r


TODAY = datetime.date(2026, 5, 4)


def _paid_result(total_payouts, total_fees):
    r = MagicMock()
    r.fetchone.return_value = SimpleNamespace(total_payouts=total_payouts, total_fees=total_fees)
    return r


def _soft_result(soft_splits):
    r = MagicMock()
    r.fetchall.return_value = soft_splits
    return r


def _make_recon_session(payins, total_payouts, total_fees, soft_splits=None):
    session = AsyncMock()
    session.commit = AsyncMock()
    session.execute = AsyncMock(side_effect=[
        _row_result(scalar_val=payins),
        _paid_result(total_payouts, total_fees),
        _soft_result(soft_splits or []),
        MagicMock(),  # INSERT reconciliation_records
    ])
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
@patch("app.services.reconciliation_service.AsyncSessionLocal")
async def test_run_reconciliation_balanced_day(mock_session_local):
    mock_session_local.return_value = _make_recon_session(
        Decimal("1000.00"), Decimal("950.00"), Decimal("50.00")
    )

    result = await run_reconciliation(TODAY)
    assert result["status"] == "balanced"
    assert result["hard_difference"] == 0.0


@pytest.mark.asyncio
@patch("app.services.reconciliation_service.AsyncSessionLocal")
async def test_run_reconciliation_soft_difference_only(mock_session_local):
    soft_split = SimpleNamespace(id="split-1", held=Decimal("50.00"))
    mock_session_local.return_value = _make_recon_session(
        Decimal("1000.00"), Decimal("950.00"), Decimal("0.00"), soft_splits=[soft_split]
    )

    result = await run_reconciliation(TODAY)
    assert result["soft_difference"] == 50.0
    assert result["hard_difference"] == 0.0
    assert result["status"] == "balanced"


@pytest.mark.asyncio
@patch("app.services.reconciliation_service.send_alert", new_callable=AsyncMock)
@patch("app.services.reconciliation_service.AsyncSessionLocal")
async def test_run_reconciliation_hard_difference_triggers_alert(mock_session_local, mock_alert):
    unresolved_session = AsyncMock()
    unresolved_session.commit = AsyncMock()
    unresolved_session.execute = AsyncMock(return_value=MagicMock(fetchall=lambda: []))
    unresolved_ctx = MagicMock()
    unresolved_ctx.__aenter__ = AsyncMock(return_value=unresolved_session)
    unresolved_ctx.__aexit__ = AsyncMock(return_value=False)

    call_count = [0]

    def session_factory():
        if call_count[0] == 0:
            call_count[0] += 1
            return _make_recon_session(Decimal("1000.00"), Decimal("800.00"), Decimal("50.00"))
        return unresolved_ctx

    mock_session_local.side_effect = session_factory

    result = await run_reconciliation(TODAY)
    assert result["status"] == "unbalanced"
    assert result["hard_difference"] == 150.0
    mock_alert.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.services.reconciliation_service.run_reconciliation", new_callable=AsyncMock)
@patch("app.services.reconciliation_service.AsyncSessionLocal")
async def test_backfill_runs_for_missing_days(mock_session_local, mock_run):
    last_date = datetime.date(2026, 5, 1)
    last_date_result = MagicMock()
    last_date_result.scalar.return_value = last_date

    session = AsyncMock()
    session.execute = AsyncMock(return_value=last_date_result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    mock_session_local.return_value = ctx

    with patch("app.services.reconciliation_service.datetime") as mock_dt:
        mock_dt.now.return_value.date.return_value = datetime.date(2026, 5, 4)
        await _backfill_missed_days()

    assert mock_run.await_count == 2  # May 2 and May 3
    backfilled = [call.args[0] for call in mock_run.call_args_list]
    assert datetime.date(2026, 5, 2) in backfilled
    assert datetime.date(2026, 5, 3) in backfilled


@pytest.mark.asyncio
@patch("app.services.reconciliation_service.run_reconciliation", new_callable=AsyncMock)
@patch("app.services.reconciliation_service.AsyncSessionLocal")
async def test_backfill_does_nothing_if_no_records(mock_session_local, mock_run):
    result = MagicMock()
    result.scalar.return_value = None

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=session)
    ctx.__aexit__ = AsyncMock(return_value=False)
    mock_session_local.return_value = ctx

    await _backfill_missed_days()
    mock_run.assert_not_awaited()
