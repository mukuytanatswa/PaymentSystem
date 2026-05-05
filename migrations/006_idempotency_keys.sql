CREATE TABLE IF NOT EXISTS idempotency_keys (
    key         TEXT NOT NULL,
    platform_id UUID NOT NULL REFERENCES platforms(id),
    response    JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (key, platform_id)
);
CREATE INDEX ON idempotency_keys(created_at);
