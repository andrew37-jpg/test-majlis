-- Existing migration 001 is deliberately unchanged.
ALTER TABLE orders ADD COLUMN customer_generation INTEGER NOT NULL DEFAULT 1 CHECK (customer_generation > 0);
ALTER TABLE orders ADD COLUMN integration_sequence BIGINT NOT NULL DEFAULT 0 CHECK (integration_sequence >= 0);
ALTER TABLE events ADD COLUMN customer_id BIGINT REFERENCES users(telegram_id);
ALTER TABLE events ADD COLUMN customer_generation INTEGER NOT NULL DEFAULT 0 CHECK (customer_generation >= 0);
-- Do not guess ownership for legacy histories whose order was transferred.
UPDATE events e SET customer_id=o.customer_id, customer_generation=1
FROM orders o WHERE o.id=e.order_id AND NOT EXISTS (
    SELECT 1 FROM audit_log a WHERE a.order_id=o.id AND a.action='details'
    AND a.before_data->>'customer_id' IS DISTINCT FROM a.after_data->>'customer_id'
);
CREATE INDEX events_owner_idx ON events(order_id,customer_generation,id);
ALTER TABLE integration_events ADD COLUMN payload_hash VARCHAR(64) NOT NULL DEFAULT '';
