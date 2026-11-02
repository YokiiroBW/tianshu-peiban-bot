-- A provider-reported queue is distinct from physical submission and execution.
ALTER TABLE models.attempts ADD COLUMN execution_start text NOT NULL DEFAULT 'admitted'
    CHECK(execution_start IN ('admitted','provider_running'));
ALTER TABLE models.attempts ADD COLUMN execution_started_at timestamptz;
ALTER TABLE models.attempts ADD COLUMN provider_state text
    CHECK(provider_state IN ('queued','running','completed'));
ALTER TABLE models.attempts ADD COLUMN provider_observed_at timestamptz;
