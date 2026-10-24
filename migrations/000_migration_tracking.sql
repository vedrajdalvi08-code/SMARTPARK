-- SMARTPARK migration 000: migration ledger.
-- This file is intentionally idempotent and must be applied before later files.

CREATE TABLE IF NOT EXISTS smartpark_migrations (
    version varchar(128) PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
);
