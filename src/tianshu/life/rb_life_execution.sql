-- Each activity owns its action/checkpoint; unrelated work is not actor-exclusive.
DROP INDEX IF EXISTS life.activity_one_running;

-- Application outcomes belong to the original generation, alongside its status.
ALTER TABLE life.plan_generations
    ADD COLUMN IF NOT EXISTS application_results jsonb,
    ADD COLUMN IF NOT EXISTS recovery_lineage_id text,
    ADD COLUMN IF NOT EXISTS recovery_owner text,
    ADD COLUMN IF NOT EXISTS recovery_version text,
    ADD COLUMN IF NOT EXISTS recovery_probe_token text,
    ADD COLUMN IF NOT EXISTS recovery_attempt_number integer,
    ADD COLUMN IF NOT EXISTS recovery_state text,
    ADD COLUMN IF NOT EXISTS recovery_dependency text,
    ADD COLUMN IF NOT EXISTS recovery_current_dependency text;
