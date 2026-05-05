import time
from decimal import Decimal

import httpx

from app.config.env import settings

_token_cache: dict = {"token": None, "expires_at": 0.0}

_CREATE_PAYMENT_MUTATION = """
mutation CreatePayment($input: PaymentInitiationRequestInput!) {
  clientPaymentInitiationRequestCreate(input: $input) {
    paymentInitiationRequest {
      id
      url
    }
  }
}
"""

_BAVS_QUERY = """
query VerifyBankAccount($input: BankAccountVerificationInput!) {
  client {
    verifyBankAccountDetails(input: $input) {
      accountVerificationResult
      detailedAccountVerificationResults {
        accountExists
        identityDocumentMatch
      }
      accountStatus {
        isOpen
        acceptsCredits
      }
    }
  }
}
"""

_CREATE_PAYOUT_MUTATION = """
mutation CreateDisbursement($input: DisbursementCreateInput!) {
  disbursementCreate(input: $input) {
    disbursement {
      id
      status
    }
  }
}
"""

_REVERSE_DISBURSEMENT_MUTATION = """
mutation ReverseDisbursement($input: DisbursementReverseInput!) {
  disbursementReverse(input: $input) {
    disbursement {
      id
      status
    }
  }
}
"""


async def _get_access_token() -> str:
    if _token_cache["token"] and time.time() < _token_cache["expires_at"] - 60:
        return _token_cache["token"]
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "https://secure.stitch.money/connect/token",
            data={
                "grant_type": "client_credentials",
                "client_id": settings.stitch_client_id,
                "client_secret": settings.stitch_client_secret,
                "scope": "client_paymentrequest disbursements client_bankaccountverification",
                "audience": "https://secure.stitch.money/connect/token",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        _token_cache["token"] = data["access_token"]
        _token_cache["expires_at"] = time.time() + data.get("expires_in", 3600)
        return _token_cache["token"]


async def _graphql(query: str, variables: dict) -> dict:
    token = await _get_access_token()
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            settings.stitch_api_url,
            json={"query": query, "variables": variables},
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
        )
        resp.raise_for_status()
        body = resp.json()
        if "errors" in body:
            raise RuntimeError(f"Stitch GraphQL error: {body['errors']}")
        return body["data"]


async def verify_bank_account(
    account_number: str,
    bank_id: str,
    id_number: str,
) -> dict:
    """Returns the verifyBankAccountDetails result dict."""
    data = await _graphql(
        _BAVS_QUERY,
        {
            "input": {
                "accountNumber": account_number,
                "bankId": {"bankId": bank_id},
                "accountHolder": {
                    "individual": {
                        "identifyingDocument": {
                            "identityDocument": {
                                "country": "ZA",
                                "number": id_number,
                            }
                        }
                    }
                },
            }
        },
    )
    return data["client"]["verifyBankAccountDetails"]


async def create_payment(
    amount: Decimal,
    currency: str,
    reference: str,
    payment_id: str,
) -> tuple[str, str]:
    """Returns (stitch_payment_id, checkout_url)."""
    data = await _graphql(
        _CREATE_PAYMENT_MUTATION,
        {
            "input": {
                "amount": {"quantity": str(amount), "currency": currency},
                "payerReference": reference,
                "beneficiaryReference": payment_id,
                "externalReference": payment_id,
                "redirectUrl": settings.stitch_redirect_uri,
            }
        },
    )
    pir = data["clientPaymentInitiationRequestCreate"]["paymentInitiationRequest"]
    return pir["id"], pir["url"]


async def create_payout(
    vendor_id: str,
    amount: Decimal,
    currency: str,
    bank_account: str,
    bank_code: str,
    reference: str,
) -> str:
    """Returns the Stitch disbursement id."""
    data = await _graphql(
        _CREATE_PAYOUT_MUTATION,
        {
            "input": {
                "amount": {"quantity": str(amount), "currency": currency},
                "beneficiary": {
                    "bankAccount": {
                        "accountNumber": bank_account,
                        "bankId": bank_code,
                    }
                },
                "externalReference": reference,
            }
        },
    )
    return data["disbursementCreate"]["disbursement"]["id"]


async def reverse_disbursement(disbursement_id: str) -> str:
    """Reverses a disbursement. Returns the Stitch reverse disbursement id."""
    data = await _graphql(
        _REVERSE_DISBURSEMENT_MUTATION,
        {"input": {"disbursementId": disbursement_id}},
    )
    return data["disbursementReverse"]["disbursement"]["id"]
