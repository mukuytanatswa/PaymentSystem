-- Extend payment status to include 'refunded'
ALTER TABLE payments DROP CONSTRAINT IF EXISTS payments_status_check;
ALTER TABLE payments ADD CONSTRAINT payments_status_check
    CHECK (status IN ('pending', 'processing', 'completed', 'failed', 'refunded'));

-- Refunds table
CREATE TABLE IF NOT EXISTS refunds (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    payment_id      UUID NOT NULL REFERENCES payments(id),
    reason          TEXT NOT NULL,
    amount          NUMERIC(18,2) NOT NULL,
    status          TEXT NOT NULL CHECK (status IN ('pending','completed','failed')),
    initiated_by    TEXT NOT NULL,
    stitch_reverse_id TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS refunds_payment_id_idx ON refunds(payment_id);
