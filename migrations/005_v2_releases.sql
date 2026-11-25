ALTER TABLE serving_releases
    DROP CONSTRAINT IF EXISTS serving_releases_release_id_check;

ALTER TABLE serving_releases
    ADD CONSTRAINT serving_releases_release_id_check
    CHECK (release_id ~ '^(h|v2)-[a-f0-9]{64}$');

CREATE INDEX IF NOT EXISTS interaction_events_stream_customer_receipt
    ON interaction_events (stream_id, customer_id, received_at);
