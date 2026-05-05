ALTER TABLE splits ADD COLUMN IF NOT EXISTS retry_count INT NOT NULL DEFAULT 0;

ALTER TABLE splits DROP CONSTRAINT IF EXISTS splits_status_check;
ALTER TABLE splits ADD CONSTRAINT splits_status_check
    CHECK (status IN ('pending', 'paid', 'failed', 'dead'));

CREATE INDEX IF NOT EXISTS splits_dead_idx ON splits(status) WHERE status = 'dead';
