-- The logical batch owns application; independent computations retain every
-- original request/receipt and may only compete for one selected result.
ALTER TABLE memory.consolidation_batches
    ADD COLUMN retry_due_at timestamptz,
    ADD COLUMN retry_requested boolean NOT NULL DEFAULT false,
    ADD COLUMN selected_step_id text;
CREATE INDEX consolidation_retry_due ON memory.consolidation_batches(retry_due_at,partition_id)
    WHERE status='quarantined';

CREATE TABLE memory.consolidation_attempts (
    batch_id text NOT NULL REFERENCES memory.consolidation_batches,
    number integer NOT NULL CHECK(number>0),
    step_id text NOT NULL UNIQUE REFERENCES memory.model_steps,
    model_attempt_id text NOT NULL UNIQUE,
    previous_step_id text,
    kind text NOT NULL CHECK(kind IN ('original','context','repair','retry')),
    due_at timestamptz NOT NULL,
    status text NOT NULL DEFAULT 'prepared',
    error_code text,
    first_failure jsonb,
    rejected_error text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(batch_id,number)
);
CREATE INDEX consolidation_attempt_candidates ON memory.consolidation_attempts(batch_id,number)
    WHERE rejected_error IS NULL;
