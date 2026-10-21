CREATE TABLE serving_releases (
    release_id text PRIMARY KEY CHECK (release_id ~ '^h-[a-f0-9]{64}$'),
    manifest_sha256 text NOT NULL CHECK (manifest_sha256 ~ '^[a-f0-9]{64}$'),
    data_as_of date NOT NULL,
    prepared_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE serving_pointer (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    active_id text REFERENCES serving_releases(release_id),
    previous_id text REFERENCES serving_releases(release_id),
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO serving_pointer(singleton) VALUES (true);
