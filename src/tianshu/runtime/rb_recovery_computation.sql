-- A lineage's recovery kind is fixed at registration; the conservative default
-- leaves all existing and external-effect work observation-only on unknown.
ALTER TABLE runtime.recovery_lineages
    ADD COLUMN recovery_kind text NOT NULL DEFAULT 'external_effect'
    CHECK (recovery_kind IN ('external_effect', 'pure_computation'));

-- An independent computation retains its original unknown attempt and budget.
ALTER TABLE runtime.recovery_attempts
    ADD COLUMN supersedes_attempt_id text REFERENCES runtime.recovery_attempts(attempt_id);
CREATE INDEX recovery_attempts_supersedes ON runtime.recovery_attempts (supersedes_attempt_id)
    WHERE supersedes_attempt_id IS NOT NULL;

CREATE TABLE runtime.recovery_computation_unknowns (
    attempt_id text PRIMARY KEY REFERENCES runtime.recovery_attempts(attempt_id),
    models_finished_at timestamptz NOT NULL,
    due_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT now()
);
