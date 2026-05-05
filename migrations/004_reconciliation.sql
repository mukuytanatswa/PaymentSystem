-- Daily reconciliation records: aggregate-only, no PII, kept permanently
CREATE TABLE IF NOT EXISTS reconciliation_records (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    date             DATE NOT NULL UNIQUE,
    total_payins     NUMERIC(18, 2) NOT NULL,
    total_payouts    NUMERIC(18, 2) NOT NULL,
    total_fees       NUMERIC(18, 2) NOT NULL,
    soft_difference  NUMERIC(18, 2) NOT NULL,
    hard_difference  NUMERIC(18, 2) NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('balanced', 'unbalanced')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
