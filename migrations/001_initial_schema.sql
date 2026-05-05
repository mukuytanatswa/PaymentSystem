CREATE EXTENSION IF NOT EXISTS "pgcrypto";

-- Platforms (marketplaces using QuickPayments)
CREATE TABLE IF NOT EXISTS platforms (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    api_key         TEXT NOT NULL UNIQUE,
    fee_percentage  NUMERIC(5, 2) NOT NULL DEFAULT 2.50,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Vendors registered under a platform
CREATE TABLE IF NOT EXISTS vendors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_id     UUID NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    bank_account    TEXT NOT NULL,
    bank_code       TEXT NOT NULL,
    kyc_status      TEXT NOT NULL DEFAULT 'pending' CHECK (kyc_status IN ('pending', 'verified', 'rejected')),
    balance         NUMERIC(18, 2) NOT NULL DEFAULT 0.00,
    fee_percentage  NUMERIC(5, 2),           -- NULL = inherit platform default
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS vendors_platform_id_idx ON vendors(platform_id);

-- Payments initiated by a platform
CREATE TABLE IF NOT EXISTS payments (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    platform_id         UUID NOT NULL REFERENCES platforms(id) ON DELETE CASCADE,
    amount              NUMERIC(18, 2) NOT NULL,
    currency            CHAR(3) NOT NULL DEFAULT 'ZAR',
    status              TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    stitch_payment_id   TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS payments_platform_id_idx ON payments(platform_id);
CREATE INDEX IF NOT EXISTS payments_stitch_id_idx   ON payments(stitch_payment_id);

-- Individual vendor splits within a payment
CREATE TABLE IF NOT EXISTS splits (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id      UUID NOT NULL REFERENCES payments(id) ON DELETE CASCADE,
    vendor_id       UUID NOT NULL REFERENCES vendors(id),
    amount          NUMERIC(18, 2) NOT NULL,   -- net amount (vendor receives)
    platform_fee    NUMERIC(18, 2) NOT NULL,   -- fee retained by QuickPayments
    status          TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'paid', 'failed')),
    payout_id       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS splits_payment_id_idx ON splits(payment_id);
CREATE INDEX IF NOT EXISTS splits_vendor_id_idx  ON splits(vendor_id);
CREATE INDEX IF NOT EXISTS splits_status_idx     ON splits(status) WHERE status = 'failed';

-- Ledger: immutable record of every balance movement per vendor
CREATE TABLE IF NOT EXISTS ledger (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id       UUID NOT NULL REFERENCES vendors(id),
    payment_id      UUID NOT NULL REFERENCES payments(id),
    type            TEXT NOT NULL CHECK (type IN ('credit', 'debit')),
    amount          NUMERIC(18, 2) NOT NULL,
    balance_after   NUMERIC(18, 2) NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ledger_vendor_id_idx ON ledger(vendor_id);
