import asyncio
import hashlib
import hmac
import os
import re
from pathlib import Path
from uuid import uuid4

# Override DATABASE_URL before any app module is imported.
# tests/conftest.py uses setdefault (a dummy URL); we need a real test DB here.
_test_db_url = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://postgres:postgres@localhost:5432/splyt_test",
)
os.environ["DATABASE_URL"] = _test_db_url

import asyncpg  # noqa: E402
import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from httpx import ASGITransport, AsyncClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.config.database import AsyncSessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.services.encryption_service import encrypt  # noqa: E402


def _raw_dsn(url: str) -> str:
    return re.sub(r"\+asyncpg", "", url, count=1)


@pytest.fixture(scope="session", autouse=True)
def apply_migrations():
    """Apply all migrations once per test session using asyncpg directly."""
    async def _run() -> None:
        dsn = _raw_dsn(_test_db_url)
        conn = await asyncpg.connect(dsn)
        try:
            migrations_dir = Path(__file__).parent.parent.parent / "migrations"
            for sql_file in sorted(migrations_dir.glob("*.sql")):
                sql = sql_file.read_text(encoding="utf-8")
                await conn.execute(sql)
        finally:
            await conn.close()

    asyncio.run(_run())


@pytest_asyncio.fixture(autouse=True)
async def truncate_tables(apply_migrations):
    """Truncate all data tables before each test for isolation."""
    dsn = _raw_dsn(_test_db_url)
    conn = await asyncpg.connect(dsn)
    try:
        await conn.execute("""
            TRUNCATE TABLE
                reconciliation_records, ledger, splits, idempotency_keys,
                payments, vendors, platforms, kyc_records, audit_log
            RESTART IDENTITY CASCADE
        """)
    finally:
        await conn.close()


@pytest_asyncio.fixture
async def db_session():
    async with AsyncSessionLocal() as session:
        yield session


@pytest_asyncio.fixture
async def test_platform(db_session):
    api_key = f"test-key-{uuid4().hex[:12]}"
    result = await db_session.execute(
        text("""
            INSERT INTO platforms (name, api_key, fee_percentage)
            VALUES (:name, :api_key, :fee)
            RETURNING id, name, api_key, fee_percentage
        """),
        {"name": "Test Platform", "api_key": api_key, "fee": "2.50"},
    )
    await db_session.commit()
    row = result.fetchone()
    return {
        "id": row.id,
        "name": row.name,
        "api_key": row.api_key,
        "fee_percentage": row.fee_percentage,
    }


@pytest_asyncio.fixture
async def test_vendor(db_session, test_platform):
    encrypted_account = encrypt("1234567890")
    result = await db_session.execute(
        text("""
            INSERT INTO vendors (platform_id, name, bank_account, bank_code, kyc_status, fee_percentage)
            VALUES (:pid, :name, :bank_account, :bank_code, 'verified', NULL)
            RETURNING id, platform_id, name, bank_code, kyc_status, balance
        """),
        {
            "pid": str(test_platform["id"]),
            "name": "Test Vendor",
            "bank_account": encrypted_account,
            "bank_code": "632005",
        },
    )
    await db_session.commit()
    row = result.fetchone()
    return {
        "id": row.id,
        "platform_id": row.platform_id,
        "name": row.name,
        "bank_code": row.bank_code,
    }


@pytest_asyncio.fixture
async def http_client(test_platform) -> AsyncClient:
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
        headers={"x-api-key": test_platform["api_key"]},
    ) as client:
        yield client


def make_stitch_sig(body: bytes, secret: str = "test-webhook-secret") -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"
