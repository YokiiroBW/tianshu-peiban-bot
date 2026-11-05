-- A retirement records business ownership of the Models admission fence.
ALTER TABLE life.plan_generations
    ADD COLUMN IF NOT EXISTS retirement_reason text,
    ADD COLUMN IF NOT EXISTS retirement_model_state text,
    ADD COLUMN IF NOT EXISTS retirement_original_status text,
    ADD COLUMN IF NOT EXISTS retirement_observe_after timestamptz,
    ADD COLUMN IF NOT EXISTS retirement_observed_at timestamptz,
    ADD COLUMN IF NOT EXISTS retirement_observation_count integer NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS retirement_data_generation uuid,
    ADD COLUMN IF NOT EXISTS retirement_requested_at timestamptz,
    ADD COLUMN IF NOT EXISTS retired_at timestamptz;
CREATE INDEX IF NOT EXISTS life_plan_retirement_observe_due ON life.plan_generations(retirement_observe_after,generation_key)
    WHERE retirement_observe_after IS NOT NULL;
