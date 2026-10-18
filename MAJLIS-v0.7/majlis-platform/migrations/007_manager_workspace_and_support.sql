ALTER TABLE manager_access ADD COLUMN all_orders BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE manager_access ADD COLUMN can_read_inbox BOOLEAN NOT NULL DEFAULT false;
CREATE TABLE customer_messages (
 id BIGSERIAL PRIMARY KEY,
 customer_id BIGINT NOT NULL REFERENCES users(telegram_id),
 source TEXT NOT NULL CHECK (source IN ('miniapp','telegram')),
 text TEXT NOT NULL CHECK (char_length(text) BETWEEN 1 AND 4096),
 kind TEXT NOT NULL DEFAULT 'text' CHECK(kind IN ('text','photo','document','voice','video','other')),
 telegram_chat_id BIGINT,
 telegram_message_id BIGINT,
 idempotency_key VARCHAR(64),
 payload_hash TEXT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(telegram_chat_id,telegram_message_id),
 UNIQUE(customer_id,idempotency_key)
);
CREATE INDEX customer_messages_customer ON customer_messages(customer_id,id DESC);
