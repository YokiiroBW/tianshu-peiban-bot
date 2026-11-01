-- One fixed subjective-reflection batch survives new events and worker restarts.
-- The immutable model input remains owned by conversation.generation_requests.
ALTER TABLE companion.organize_state
    ADD COLUMN active_batch jsonb,
    ADD COLUMN blocked_material jsonb,
    ADD COLUMN legacy_state text NOT NULL DEFAULT 'requests'
        CHECK(legacy_state IN ('requests','episodes','complete')),
    ADD COLUMN legacy_request_cursor text,
    ADD COLUMN legacy_episode_cursor text,
    ADD COLUMN legacy_missing text[] NOT NULL DEFAULT '{}',
    ADD COLUMN legacy_started_at timestamptz NOT NULL DEFAULT now();

-- SQL sequences are allocated before commit: a later commit may carry a lower
-- sequence. Actual Episode versions, not max(sequence), own consumer coverage.
CREATE TABLE companion.growth_inputs (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    episode_id text NOT NULL,
    revision bigint NOT NULL,
    sequence bigint NOT NULL,
    reviewed_revision bigint NOT NULL DEFAULT 0,
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','reviewed','unavailable')),
    missing text[] NOT NULL DEFAULT '{}',
    context jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(actor_id,scope_key,episode_id)
);
CREATE INDEX growth_pending_versions ON companion.growth_inputs(actor_id,scope_key,sequence,episode_id)
    WHERE reviewed_revision<revision;

-- A finite conversion manifest references original intent, never copies/rebuilds
-- its prompt or assigns a replacement attempt to an unresolved execution.
CREATE TABLE companion.growth_legacy_requests (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    material_key text NOT NULL,
    context jsonb,
    inputs jsonb NOT NULL DEFAULT '[]',
    end_cursor text,
    state text NOT NULL DEFAULT 'pending'
        CHECK(state IN ('pending','reviewed','abandoned','invalidated')),
    error_code text,
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(actor_id,scope_key,material_key)
);
