-- KYC records: immutable FICA-compliant verification history per vendor
CREATE TABLE IF NOT EXISTS kyc_records (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id           UUID NOT NULL REFERENCES vendors(id),
    status              TEXT NOT NULL CHECK (status IN ('verified', 'rejected')),
    verification_result JSONB NOT NULL,
    verified_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS kyc_records_vendor_id_idx ON kyc_records(vendor_id);

-- Audit log: POPIA-required record of who accessed or modified personal data
CREATE TABLE IF NOT EXISTS audit_log (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type  TEXT NOT NULL,
    platform_id UUID REFERENCES platforms(id),
    vendor_id   UUID REFERENCES vendors(id),
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS audit_log_vendor_id_idx   ON audit_log(vendor_id);
CREATE INDEX IF NOT EXISTS audit_log_event_type_idx  ON audit_log(event_type);
