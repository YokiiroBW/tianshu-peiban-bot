CREATE TABLE IF NOT EXISTS channels.media_supplements (
    id text PRIMARY KEY,
    event_key text NOT NULL REFERENCES channels.events(id),
    part_index integer NOT NULL,
    input_id text,
    context jsonb,
    original_ref jsonb,
    deployment_generation jsonb,
    lineage_id text,
    lineage_version text,
    active_attempt_id text,
    probe_token text,
    payload bytea,
    state text NOT NULL CHECK (state IN ('waiting_ingest','waiting_retry','fetching','waiting_config','complete','withdrawn','unavailable')),
    attempts integer NOT NULL DEFAULT 0,
    config_revision bigint NOT NULL,
    error_code text,
    next_due timestamptz,
    lease_until timestamptz,
    attempt_started_at timestamptz,
    observed_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(event_key, part_index)
);
CREATE INDEX IF NOT EXISTS channel_supplements_pending ON channels.media_supplements(state, next_due);
ALTER TABLE channels.events ADD COLUMN IF NOT EXISTS original_digest text;
ALTER TABLE channels.events ADD COLUMN IF NOT EXISTS deployment_generation jsonb;
ALTER TABLE channels.events ADD COLUMN IF NOT EXISTS actor_id text;
ALTER TABLE channels.calls ADD COLUMN IF NOT EXISTS deployment_generation jsonb;
