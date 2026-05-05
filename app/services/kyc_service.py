import asyncio
import hashlib
import hmac
import time
import uuid

import httpx

from app.config.env import settings
from app.services import stitch_client

# Smile Identity result codes
_SMILE_PASS_CODES = {"1020", "1021"}  # Exact Match, Partial Match


class KycVerificationError(Exception):
    def __init__(self, message: str, response: dict | None = None):
        super().__init__(message)
        self.response = response or {}


def _smile_signature(timestamp: str) -> str:
    msg = (timestamp + settings.smile_identity_partner_id).encode()
    return hmac.new(
        settings.smile_identity_api_key.encode(), msg, hashlib.sha256
    ).hexdigest()


async def _verify_identity(id_number: str) -> dict:
    timestamp = str(int(time.time()))
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{settings.smile_identity_base_url}/v2/verify",
            json={
                "country": "ZA",
                "id_type": "NATIONAL_ID",
                "id_number": id_number,
                "partner_params": {
                    "job_id": str(uuid.uuid4()),
                    "user_id": str(uuid.uuid4()),
                    "job_type": 5,
                },
            },
            headers={
                "smileid-partner-id": settings.smile_identity_partner_id,
                "smileid-request-signature": _smile_signature(timestamp),
                "smileid-timestamp": timestamp,
                "smileid-source-sdk": "rest_api",
            },
        )
        resp.raise_for_status()
        return resp.json()


async def _verify_bank_account_with_retry(
    bank_account: str, bank_code: str, id_number: str
) -> dict:
    for attempt in range(2):
        try:
            return await stitch_client.verify_bank_account(
                account_number=bank_account,
                bank_id=bank_code,
                id_number=id_number,
            )
        except RuntimeError as exc:
            if "RESULT_PENDING" in str(exc) and attempt == 0:
                await asyncio.sleep(5)
                continue
            raise
    raise KycVerificationError("Bank account verification timed out")


async def verify_vendor(
    name: str,
    id_number: str,
    bank_account: str,
    bank_code: str,
) -> dict:
    smile_result = await _verify_identity(id_number)

    if smile_result.get("ResultCode") not in _SMILE_PASS_CODES:
        raise KycVerificationError(
            smile_result.get("ResultText", "Identity verification failed"),
            {"smile_identity": smile_result},
        )

    bavs_result = await _verify_bank_account_with_retry(bank_account, bank_code, id_number)
    combined = {"smile_identity": smile_result, "stitch_bavs": bavs_result}

    if bavs_result.get("accountVerificationResult") != "verified":
        raise KycVerificationError("Bank account verification failed", combined)

    return combined
