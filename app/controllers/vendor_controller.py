import json
from uuid import UUID

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.schemas import KycRecord, KycStatus, LedgerEntry, VendorCreate, VendorResponse
from app.services.encryption_service import decrypt, encrypt
from app.services.kyc_service import KycVerificationError, verify_vendor


async def _write_audit_log(
    db: AsyncSession,
    event_type: str,
    platform_id: UUID | None,
    vendor_id: UUID | None,
    metadata: dict | None = None,
) -> None:
    await db.execute(
        text("""
            INSERT INTO audit_log (event_type, platform_id, vendor_id, metadata)
            VALUES (:event_type, :platform_id, :vendor_id, :metadata::jsonb)
        """),
        {
            "event_type": event_type,
            "platform_id": str(platform_id) if platform_id else None,
            "vendor_id": str(vendor_id) if vendor_id else None,
            "metadata": json.dumps(metadata) if metadata else None,
        },
    )


async def register_vendor(
    payload: VendorCreate,
    platform_id: UUID,
    db: AsyncSession,
) -> VendorResponse:
    kyc_result = {}
    try:
        kyc_result = await verify_vendor(
            payload.name, payload.id_number, payload.bank_account, payload.bank_code
        )
        kyc_status = "verified"
    except KycVerificationError as e:
        kyc_status = "rejected"
        kyc_result = e.response

    async with db.begin():
        row = await db.execute(
            text("""
                INSERT INTO vendors (platform_id, name, bank_account, bank_code, kyc_status, fee_percentage)
                VALUES (:platform_id, :name, :bank_account, :bank_code, :kyc_status, :fee_pct)
                RETURNING id, balance, created_at
            """),
            {
                "platform_id": str(platform_id),
                "name": payload.name,
                "bank_account": encrypt(payload.bank_account),
                "bank_code": payload.bank_code,
                "kyc_status": kyc_status,
                "fee_pct": str(payload.fee_percentage) if payload.fee_percentage is not None else None,
            },
        )
        vendor = row.fetchone()

        await db.execute(
            text("""
                INSERT INTO kyc_records (vendor_id, status, verification_result)
                VALUES (:vendor_id, :status, :result::jsonb)
            """),
            {
                "vendor_id": str(vendor.id),
                "status": kyc_status,
                "result": json.dumps(kyc_result),
            },
        )

    return VendorResponse(
        id=vendor.id,
        platform_id=platform_id,
        name=payload.name,
        bank_account=payload.bank_account,
        bank_code=payload.bank_code,
        kyc_status=kyc_status,
        balance=vendor.balance,
        fee_percentage=payload.fee_percentage,
        created_at=vendor.created_at,
    )


async def get_vendor(
    vendor_id: UUID,
    platform_id: UUID,
    db: AsyncSession,
    request: Request | None = None,
) -> VendorResponse:
    meta = {"endpoint": "/vendors/{id}"}
    if request:
        meta["ip"] = request.client.host if request.client else None

    async with db.begin():
        row = await db.execute(
            text("""
                SELECT id, platform_id, name, bank_account, bank_code, kyc_status, balance, fee_percentage, created_at
                FROM vendors WHERE id = :id AND platform_id = :platform_id
            """),
            {"id": str(vendor_id), "platform_id": str(platform_id)},
        )
        vendor = row.fetchone()
        if not vendor:
            raise HTTPException(status_code=404, detail="Vendor not found")
        await _write_audit_log(db, "vendor_read", platform_id, vendor_id, meta)

    return VendorResponse(
        id=vendor.id,
        platform_id=vendor.platform_id,
        name=vendor.name,
        bank_account=decrypt(vendor.bank_account),
        bank_code=vendor.bank_code,
        kyc_status=vendor.kyc_status,
        balance=vendor.balance,
        fee_percentage=vendor.fee_percentage,
        created_at=vendor.created_at,
    )


async def get_vendor_ledger(
    vendor_id: UUID,
    platform_id: UUID,
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    request: Request | None = None,
) -> list[LedgerEntry]:
    meta = {"endpoint": "/vendors/{id}/ledger", "limit": limit, "offset": offset}
    if request:
        meta["ip"] = request.client.host if request.client else None

    async with db.begin():
        check = await db.execute(
            text("SELECT id FROM vendors WHERE id = :id AND platform_id = :platform_id"),
            {"id": str(vendor_id), "platform_id": str(platform_id)},
        )
        if not check.fetchone():
            raise HTTPException(status_code=404, detail="Vendor not found")
        await _write_audit_log(db, "ledger_read", platform_id, vendor_id, meta)
        rows = await db.execute(
            text("""
                SELECT id, vendor_id, payment_id, type, amount, balance_after, created_at
                FROM ledger
                WHERE vendor_id = :vendor_id
                ORDER BY created_at DESC
                LIMIT :limit OFFSET :offset
            """),
            {"vendor_id": str(vendor_id), "limit": limit, "offset": offset},
        )
        result_rows = rows.fetchall()

    return [
        LedgerEntry(
            id=r.id,
            vendor_id=r.vendor_id,
            payment_id=r.payment_id,
            type=r.type,
            amount=r.amount,
            balance_after=r.balance_after,
            created_at=r.created_at,
        )
        for r in result_rows
    ]


async def get_vendor_kyc(
    vendor_id: UUID,
    platform_id: UUID,
    db: AsyncSession,
    request: Request | None = None,
) -> list[KycRecord]:
    meta = {"endpoint": "/vendors/{id}/kyc"}
    if request:
        meta["ip"] = request.client.host if request.client else None

    async with db.begin():
        check = await db.execute(
            text("SELECT id FROM vendors WHERE id = :id AND platform_id = :platform_id"),
            {"id": str(vendor_id), "platform_id": str(platform_id)},
        )
        if not check.fetchone():
            raise HTTPException(status_code=404, detail="Vendor not found")
        await _write_audit_log(db, "kyc_read", platform_id, vendor_id, meta)
        rows = await db.execute(
            text("""
                SELECT id, vendor_id, status, verification_result, verified_at
                FROM kyc_records
                WHERE vendor_id = :vendor_id
                ORDER BY verified_at DESC
            """),
            {"vendor_id": str(vendor_id)},
        )
        result_rows = rows.fetchall()

    return [
        KycRecord(
            id=r.id,
            vendor_id=r.vendor_id,
            status=KycStatus(r.status),
            verification_result=r.verification_result,
            verified_at=r.verified_at,
        )
        for r in result_rows
    ]


async def delete_vendor(
    vendor_id: UUID,
    platform_id: UUID,
    db: AsyncSession,
    request: Request | None = None,
) -> None:
    ip = request.client.host if (request and request.client) else None

    # Transaction 1: existence check + FICA window check.
    # If FICA blocks erasure, write erasure_refused audit log and let the transaction commit
    # before raising — so the refusal is persisted regardless.
    fica_blocked = False
    async with db.begin():
        check = await db.execute(
            text("SELECT id FROM vendors WHERE id = :id AND platform_id = :platform_id"),
            {"id": str(vendor_id), "platform_id": str(platform_id)},
        )
        if not check.fetchone():
            raise HTTPException(status_code=404, detail="Vendor not found")

        last_activity = await db.execute(
            text("SELECT MAX(created_at) AS last_tx FROM ledger WHERE vendor_id = :vid"),
            {"vid": str(vendor_id)},
        )
        last_tx = last_activity.scalar()

        if last_tx is not None:
            within_window = await db.execute(
                text("SELECT :last_tx > NOW() - INTERVAL '5 years' AS within_window"),
                {"last_tx": last_tx},
            )
            if within_window.scalar():
                fica_blocked = True
                await _write_audit_log(
                    db, "erasure_refused", platform_id, vendor_id,
                    {"reason": "FICA 5-year retention window active", "ip": ip},
                )

    if fica_blocked:
        raise HTTPException(
            status_code=409,
            detail=(
                "Erasure refused: FICA requires retention of financial records for 5 years "
                "after the last transaction. This vendor's records are still within that window."
            ),
        )

    # Transaction 2: audit the erasure request and delete all vendor records atomically.
    async with db.begin():
        await _write_audit_log(
            db, "erasure_request", platform_id, vendor_id,
            {"endpoint": "DELETE /vendors/{id}", "ip": ip},
        )
        await db.execute(text("DELETE FROM kyc_records WHERE vendor_id = :vid"), {"vid": str(vendor_id)})
        await db.execute(text("DELETE FROM ledger WHERE vendor_id = :vid"), {"vid": str(vendor_id)})
        await db.execute(text("DELETE FROM splits WHERE vendor_id = :vid"), {"vid": str(vendor_id)})
        await db.execute(text("DELETE FROM vendors WHERE id = :vid"), {"vid": str(vendor_id)})
