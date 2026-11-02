-- Failed computations release the partition while reserving their exact input
-- versions. Original prompts/results remain in GenerationRequests and Models.
ALTER TABLE companion.growth_inputs ADD COLUMN retry_key text;
ALTER TABLE companion.organize_state ADD COLUMN reviewed_material_at timestamptz;

CREATE TABLE companion.growth_retry_batches (
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    retry_key text NOT NULL,
    batch jsonb NOT NULL,
    current_material_key text NOT NULL,
    previous_material_key text,
    attempt_number integer NOT NULL DEFAULT 1 CHECK(attempt_number>0),
    state text NOT NULL CHECK(state IN ('waiting','active','completed','blocked')),
    due_at timestamptz,
    last_error text,
    waiting_on_original boolean NOT NULL DEFAULT false,
    explicit_retry boolean NOT NULL DEFAULT false,
    history jsonb NOT NULL DEFAULT '[]' CHECK(jsonb_typeof(history)='array'),
    outcome text,
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(actor_id,scope_key,retry_key)
);
CREATE INDEX growth_retry_due ON companion.growth_retry_batches(due_at,actor_id,scope_key)
    WHERE state IN ('waiting','active');
