CREATE TABLE manager_access (
 telegram_id BIGINT PRIMARY KEY REFERENCES users(telegram_id),
 enabled BOOLEAN NOT NULL DEFAULT true,
 can_create BOOLEAN NOT NULL DEFAULT false
);
CREATE TABLE order_managers (
 order_id BIGINT NOT NULL REFERENCES orders(id),
 manager_id BIGINT NOT NULL REFERENCES manager_access(telegram_id),
 can_edit BOOLEAN NOT NULL DEFAULT false,
 PRIMARY KEY(order_id,manager_id)
);
CREATE INDEX order_managers_manager ON order_managers(manager_id,order_id);
ALTER TABLE orders ADD COLUMN tracking_number VARCHAR(100) NOT NULL DEFAULT '';
ALTER TABLE orders ADD COLUMN carrier VARCHAR(100) NOT NULL DEFAULT '';
ALTER TABLE orders ADD COLUMN tracking_updated_at TIMESTAMPTZ;
ALTER TABLE orders ADD COLUMN external_updated_at TIMESTAMPTZ;
