from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.kyc_service import KycVerificationError, verify_vendor


def _mock_smile_response(result_code="1020"):
    return {
        "ResultCode": result_code,
        "ResultText": "Verified" if result_code in ("1020", "1021") else "Not Verified",
    }


def _mock_bavs_response(verified=True):
    return {"accountVerificationResult": "verified" if verified else "not_verified"}


def _make_smile_client(result_code="1020"):
    mock_response = MagicMock()
    mock_response.json.return_value = _mock_smile_response(result_code)
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


@pytest.mark.asyncio
@patch("app.services.kyc_service.stitch_client.verify_bank_account", new_callable=AsyncMock)
@patch("app.services.kyc_service.httpx.AsyncClient")
async def test_verify_vendor_smile_pass_bavs_pass(mock_client_cls, mock_bavs):
    mock_bavs.return_value = _mock_bavs_response(verified=True)
    mock_client_cls.return_value = _make_smile_client("1020")

    result = await verify_vendor("Alice", "9001015009087", "1234567890", "632005")
    assert "smile_identity" in result
    assert "stitch_bavs" in result


@pytest.mark.asyncio
@patch("app.services.kyc_service.stitch_client.verify_bank_account", new_callable=AsyncMock)
@patch("app.services.kyc_service.httpx.AsyncClient")
async def test_verify_vendor_smile_fail_raises(mock_client_cls, mock_bavs):
    mock_client_cls.return_value = _make_smile_client("9999")  # failing ResultCode

    with pytest.raises(KycVerificationError):
        await verify_vendor("Bob", "9001015009087", "1234567890", "632005")

    mock_bavs.assert_not_awaited()


@pytest.mark.asyncio
@patch("app.services.kyc_service.stitch_client.verify_bank_account", new_callable=AsyncMock)
@patch("app.services.kyc_service.httpx.AsyncClient")
async def test_verify_vendor_bavs_fail_raises(mock_client_cls, mock_bavs):
    mock_bavs.return_value = _mock_bavs_response(verified=False)
    mock_client_cls.return_value = _make_smile_client("1020")

    with pytest.raises(KycVerificationError):
        await verify_vendor("Carol", "9001015009087", "1234567890", "632005")


@pytest.mark.asyncio
@patch("app.services.kyc_service.asyncio.sleep", new_callable=AsyncMock)
@patch("app.services.kyc_service.stitch_client.verify_bank_account", new_callable=AsyncMock)
@patch("app.services.kyc_service.httpx.AsyncClient")
async def test_verify_vendor_bavs_pending_retries_once(mock_client_cls, mock_bavs, mock_sleep):
    # First call raises RuntimeError with RESULT_PENDING; second call succeeds
    mock_bavs.side_effect = [
        RuntimeError("RESULT_PENDING"),
        _mock_bavs_response(verified=True),
    ]
    mock_client_cls.return_value = _make_smile_client("1020")

    result = await verify_vendor("Dave", "9001015009087", "1234567890", "632005")
    assert mock_bavs.await_count == 2
    mock_sleep.assert_awaited_once()
    assert result
