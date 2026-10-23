CREATE TABLE pipeline_targets (
    stream_id uuid PRIMARY KEY REFERENCES event_streams(stream_id),
    release_id text NOT NULL,
    data_as_of date NOT NULL,
    receipt_cursor timestamptz NOT NULL,
    enabled boolean NOT NULL DEFAULT true,
    updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
