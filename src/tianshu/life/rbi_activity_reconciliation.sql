-- A bounded keyset page visits only activities that may own an original Action.
CREATE INDEX IF NOT EXISTS life_rbi_activity_observation_scan
    ON life.activities(id)
    WHERE state IN ('running','dormant') AND action_id IS NOT NULL AND context IS NOT NULL;

-- Older generations remain NULL: their trigger and request equivalence are unknown.
ALTER TABLE life.plan_generations ADD COLUMN IF NOT EXISTS decision_trace jsonb;

-- A completed activity's derived wakes have their own durable, bounded owner.
CREATE TABLE IF NOT EXISTS life.rbi_activity_wake_intents (
    id text PRIMARY KEY,
    data_generation text NOT NULL,
    actor_id text NOT NULL,
    activity_id text NOT NULL,
    action_id text NOT NULL,
    receipt_id text NOT NULL,
    result_revision bigint NOT NULL,
    context jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    targets jsonb NOT NULL,
    next_index integer NOT NULL DEFAULT 0,
    skipped jsonb NOT NULL DEFAULT '[]'::jsonb,
    due_at timestamptz NOT NULL,
    job_id bigint,
    state text NOT NULL DEFAULT 'pending',
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS life_rbi_activity_wake_pending
    ON life.rbi_activity_wake_intents(data_generation,id) WHERE state='pending';
CREATE INDEX IF NOT EXISTS life_rbi_activity_wake_targets
    ON life.activities(actor_id,wake_condition,id)
    WHERE wake_condition IS NOT NULL AND state IN ('waiting','dormant','paused','scheduled');
CREATE INDEX IF NOT EXISTS life_rbi_commitment_wake_targets
    ON life.commitments(actor_id,wake_condition,id)
    WHERE wake_condition IS NOT NULL AND state IN ('waiting','dormant','paused','scheduled');
