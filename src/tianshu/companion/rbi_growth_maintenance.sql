-- Domain-local scan throttles; never contain prompts, results or business evidence.
CREATE TABLE companion.rbi_growth_maintenance_turn (
    runtime_generation text PRIMARY KEY,
    next_lane smallint NOT NULL DEFAULT 0 CHECK (next_lane BETWEEN 0 AND 2)
);

CREATE TABLE companion.rbi_growth_maintenance_cursors (
    runtime_generation text NOT NULL,
    lane text NOT NULL CHECK (lane IN ('retry', 'input', 'organize')),
    due_at timestamptz,
    actor_id text,
    scope_key text,
    work_key text,
    PRIMARY KEY (runtime_generation, lane)
);

CREATE TABLE companion.rbi_growth_maintenance_scans (
    runtime_generation text NOT NULL,
    lane text NOT NULL CHECK (lane IN ('retry', 'input', 'organize')),
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    work_key text NOT NULL,
    observed_revision bigint NOT NULL,
    next_scan_at timestamptz NOT NULL,
    PRIMARY KEY (runtime_generation, lane, actor_id, scope_key, work_key)
);
CREATE INDEX rbi_growth_maintenance_scan_due
    ON companion.rbi_growth_maintenance_scans(runtime_generation, lane, next_scan_at);

CREATE INDEX rbi_growth_maintenance_retry_due
    ON companion.growth_retry_batches ((batch->>'runtime_generation'), due_at, actor_id, scope_key, retry_key)
    WHERE due_at IS NOT NULL AND (state='waiting' OR
        (state='blocked' AND batch->'rbi_review'->>'state'='requested'));
CREATE INDEX rbi_growth_maintenance_input_due
    ON companion.growth_material_failures (runtime_generation, due_at, actor_id, scope_key, failure_key)
    WHERE state='blocked' AND due_at IS NOT NULL;
CREATE INDEX rbi_growth_maintenance_organize
    ON companion.organize_state (runtime_generation, actor_id, scope_key);
