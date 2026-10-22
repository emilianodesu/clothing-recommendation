CREATE TABLE event_streams (
    stream_id uuid PRIMARY KEY,
    release_id text NOT NULL REFERENCES serving_releases(release_id),
    mode text NOT NULL CHECK (mode IN ('synthetic','simulation')),
    dataset_sha256 text NOT NULL,
    seed_cutoff date NOT NULL,
    data_as_of date NOT NULL CHECK (data_as_of >= seed_cutoff),
    revision bigint NOT NULL DEFAULT 0,
    received_before timestamptz NOT NULL DEFAULT '1970-01-01Z',
    processed_events bigint NOT NULL DEFAULT 0,
    published_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    popularity text[] NOT NULL DEFAULT '{}'
);
CREATE TABLE stream_catalog (
    stream_id uuid NOT NULL REFERENCES event_streams(stream_id),
    article_id text NOT NULL,
    PRIMARY KEY(stream_id,article_id)
);
CREATE TABLE stream_daily_seed (
    stream_id uuid NOT NULL REFERENCES event_streams(stream_id),
    customer_id text NOT NULL,
    article_id text NOT NULL,
    day date NOT NULL,
    frequency bigint NOT NULL CHECK (frequency > 0),
    PRIMARY KEY(stream_id,customer_id,article_id,day)
);
CREATE TABLE interaction_events (
    event_id uuid PRIMARY KEY,
    stream_id uuid NOT NULL REFERENCES event_streams(stream_id),
    customer_id text NOT NULL,
    article_id text NOT NULL,
    kind text NOT NULL CHECK (kind IN ('purchase','view','click')),
    event_time timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT clock_timestamp(),
    payload jsonb NOT NULL
);
CREATE INDEX interaction_events_receipt ON interaction_events(stream_id,received_at);
CREATE TABLE operational_daily (
    stream_id uuid NOT NULL REFERENCES event_streams(stream_id),
    customer_id text NOT NULL,
    article_id text NOT NULL,
    day date NOT NULL,
    frequency bigint NOT NULL CHECK (frequency > 0),
    PRIMARY KEY(stream_id,customer_id,article_id,day)
);
CREATE TABLE operational_profiles (
    stream_id uuid NOT NULL REFERENCES event_streams(stream_id),
    customer_id text NOT NULL,
    history text[] NOT NULL,
    PRIMARY KEY(stream_id,customer_id)
);
