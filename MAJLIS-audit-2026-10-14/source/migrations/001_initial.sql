CREATE TABLE users (
    telegram_id BIGINT PRIMARY KEY CHECK (telegram_id > 0),
    first_name VARCHAR(100) NOT NULL DEFAULT '',
    bot_started BOOLEAN NOT NULL DEFAULT false,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE orders (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    number VARCHAR(64) UNIQUE NOT NULL,
    customer_id BIGINT NOT NULL REFERENCES users(telegram_id),
    customer_name VARCHAR(100) NOT NULL,
    item VARCHAR(300) NOT NULL,
    amount BIGINT NOT NULL CHECK (amount > 0 AND amount <= 1000000000000),
    shipment VARCHAR(100) NOT NULL DEFAULT '',
    status VARCHAR(32) NOT NULL CHECK (status IN ('confirmed','purchased','in_transit','customs','warehouse','last_mile','delivered')),
    eta DATE,
    eta_confirmed BOOLEAN NOT NULL DEFAULT false,
    payment_url TEXT NOT NULL DEFAULT '',
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (NOT eta_confirmed OR eta IS NOT NULL)
);
CREATE INDEX orders_customer_idx ON orders(customer_id, id DESC);
CREATE INDEX orders_shipment_idx ON orders(shipment) WHERE shipment <> '';
CREATE TABLE events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id BIGINT NOT NULL REFERENCES orders(id),
    status VARCHAR(32) NOT NULL,
    note VARCHAR(500) NOT NULL,
    source VARCHAR(32) NOT NULL,
    actor_id BIGINT REFERENCES users(telegram_id),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX events_order_idx ON events(order_id, id);
CREATE TABLE outbox (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    event_id BIGINT UNIQUE NOT NULL REFERENCES events(id),
    customer_id BIGINT NOT NULL REFERENCES users(telegram_id),
    text TEXT NOT NULL,
    state VARCHAR(16) NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','sending','sent','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ,
    last_error VARCHAR(100) NOT NULL DEFAULT '',
    sent_at TIMESTAMPTZ
);
CREATE INDEX outbox_ready_idx ON outbox(available_at, id) WHERE state IN ('pending','sending');
CREATE TABLE integration_events (external_id VARCHAR(128) PRIMARY KEY, created_at TIMESTAMPTZ NOT NULL DEFAULT now());
CREATE TABLE audit_log (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    actor_id BIGINT REFERENCES users(telegram_id),
    action VARCHAR(32) NOT NULL,
    order_id BIGINT REFERENCES orders(id),
    before_data JSONB NOT NULL DEFAULT '{}',
    after_data JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE bot_state (key VARCHAR(32) PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE rate_limits (telegram_id BIGINT PRIMARY KEY, window_start TIMESTAMPTZ NOT NULL, requests INTEGER NOT NULL);
