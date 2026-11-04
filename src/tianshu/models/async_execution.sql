-- A model submission owns its immutable input and optional completion wake.
-- The domain stores only the attempt reference; the response stays in attempts.
CREATE TABLE models.submissions (
    attempt_id text PRIMARY KEY REFERENCES models.attempts(attempt_id) ON DELETE CASCADE,
    request_digest text NOT NULL,
    request jsonb,
    streaming boolean NOT NULL,
    completion jsonb,
    completion_digest text,
    completion_enqueued_at timestamptz,
    stream_truncated boolean NOT NULL DEFAULT false,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX models_submissions_completion_pending ON models.submissions(attempt_id)
    WHERE completion IS NOT NULL AND completion_enqueued_at IS NULL;

CREATE TABLE models.stream_events (
    attempt_id text NOT NULL REFERENCES models.submissions(attempt_id) ON DELETE CASCADE,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    event jsonb NOT NULL,
    PRIMARY KEY(attempt_id, sequence)
);

ALTER TABLE models.attempts ADD COLUMN http_timing jsonb;
ALTER TABLE models.attempts ADD COLUMN diagnostic jsonb;
ALTER TABLE models.attempts ADD COLUMN wire_digest text;
ALTER TABLE models.attempts ADD COLUMN route_selection jsonb;
ALTER TABLE models.attempts ADD COLUMN provider_revision bigint;
ALTER TABLE models.attempts ADD COLUMN estimated_input_tokens bigint;
-- Existing concurrency/reserve values continue to govern real external
-- resources. Cloud concurrency is enforced only when explicitly configured.
ALTER TABLE models.quota_groups ADD COLUMN cloud_concurrency integer CHECK (cloud_concurrency > 0);
ALTER TABLE models.bindings ADD COLUMN fallback_routes text[] NOT NULL DEFAULT '{}';
CREATE TABLE models.quota_cooldowns (
    quota_group text PRIMARY KEY REFERENCES models.quota_groups(quota_group) ON DELETE CASCADE,
    blocked_until timestamptz NOT NULL,
    reason text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE models.route_cooldowns (
    route_id text PRIMARY KEY REFERENCES models.routes(route_id) ON DELETE CASCADE,
    blocked_until timestamptz NOT NULL,
    reason text NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE models.configuration_reloads (
    event_id text PRIMARY KEY,
    operation text NOT NULL,
    revision bigint NOT NULL,
    state text NOT NULL CHECK (state IN ('pending','applied')),
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    applied_at timestamptz
);
CREATE INDEX models_configuration_reloads_pending ON models.configuration_reloads(created_at,event_id)
    WHERE state='pending';
