-- Existing event fields and 001/002 remain compatible; legacy events default to status.
ALTER TABLE events ADD COLUMN kind VARCHAR(16) NOT NULL DEFAULT 'status'
CHECK (kind IN ('status','transfer','details','payment','commercial','import'));
-- A target-side provenance map, NOT an assumed schema of the old system.
CREATE TABLE legacy_imports (
    source VARCHAR(64) NOT NULL,
    legacy_id VARCHAR(128) NOT NULL,
    payload_hash VARCHAR(64) NOT NULL,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(source,legacy_id)
);
-- Runtime receives no permissions on this table. Test import runs as migrator.
