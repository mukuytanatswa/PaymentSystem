from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


# --- Enums ---

class KycStatus(str, Enum):
    pending = "pending"
    verified = "verified"
    rejected = "rejected"


class PaymentStatus(str, Enum):
    pending = "pending"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class SplitStatus(str, Enum):
    pending = "pending"
    paid = "paid"
    failed = "failed"
    dead = "dead"


class LedgerType(str, Enum):
    credit = "credit"
    debit = "debit"


# --- Vendor ---

class VendorCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    bank_account: str = Field(..., min_length=1)
    bank_code: str = Field(..., min_length=1)
    id_number: str = Field(..., description="South African ID number for KYC")
    fee_percentage: Optional[Decimal] = Field(None, ge=0, le=100)


class VendorResponse(BaseModel):
    id: UUID
    platform_id: UUID
    name: str
    bank_account: str
    bank_code: str
    kyc_status: KycStatus
    balance: Decimal
    fee_percentage: Optional[Decimal]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Payments ---

class SplitInput(BaseModel):
    vendor_id: UUID
    gross_amount: Decimal = Field(..., gt=0)


class PaymentCreate(BaseModel):
    total_amount: Decimal = Field(..., gt=0)
    currency: str = Field(default="ZAR", max_length=3)
    splits: list[SplitInput] = Field(..., min_length=1)


class SplitResponse(BaseModel):
    id: UUID
    vendor_id: UUID
    gross_amount: Decimal
    platform_fee: Decimal
    net_amount: Decimal
    status: SplitStatus
    payout_id: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PaymentResponse(BaseModel):
    id: UUID
    platform_id: UUID
    amount: Decimal
    currency: str
    status: PaymentStatus
    checkout_url: Optional[str]
    stitch_payment_id: Optional[str]
    splits: list[SplitResponse]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Ledger ---

class LedgerEntry(BaseModel):
    id: UUID
    vendor_id: UUID
    payment_id: UUID
    type: LedgerType
    amount: Decimal
    balance_after: Decimal
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Webhook ---

class StitchWebhookPayload(BaseModel):
    type: str
    data: dict


# --- KYC ---

class KycRecord(BaseModel):
    id: UUID
    vendor_id: UUID
    status: KycStatus
    verification_result: dict[str, Any]
    verified_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Reconciliation ---

class ReconciliationStatus(str, Enum):
    balanced = "balanced"
    unbalanced = "unbalanced"


class ReconciliationRecord(BaseModel):
    id: UUID
    date: date
    total_payins: Decimal
    total_payouts: Decimal
    total_fees: Decimal
    soft_difference: Decimal
    hard_difference: Decimal
    status: ReconciliationStatus
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


# --- Audit ---

class AuditLog(BaseModel):
    id: UUID
    event_type: str
    platform_id: Optional[UUID]
    vendor_id: Optional[UUID]
    metadata: Optional[dict[str, Any]]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)
