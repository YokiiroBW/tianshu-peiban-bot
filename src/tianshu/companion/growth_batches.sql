-- One fixed subjective-reflection batch survives new events and worker restarts.
-- The immutable model input remains owned by conversation.generation_requests.
ALTER TABLE companion.organize_state
    ADD COLUMN active_batch jsonb,
    ADD COLUMN blocked_material jsonb;

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
CREATE INDEX growth_input_episode_status ON companion.growth_inputs(actor_id,episode_id);
