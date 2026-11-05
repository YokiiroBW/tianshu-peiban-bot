ALTER TABLE models.submissions ADD COLUMN recovery_version bigint NOT NULL DEFAULT 0
    CHECK (recovery_version >= 0);

-- An authorized resolution never erases the prior wait or asserts that an
-- arbitrary external reference is itself a machine-verified rollback receipt.
CREATE TABLE models.prestart_reconciliations (
    attempt_id text NOT NULL REFERENCES models.attempts(attempt_id) ON DELETE CASCADE,
    recovery_version bigint NOT NULL,
    operator_principal_id text NOT NULL,
    client_request_id text NOT NULL,
    request_digest text NOT NULL,
    data_generation uuid NOT NULL,
    evidence_ref text NOT NULL,
    prior_state text NOT NULL CHECK (prior_state IN ('checking', 'observe_only')),
    prior_wait jsonb,
    previous_broker_instance text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (attempt_id, recovery_version),
    UNIQUE (operator_principal_id, client_request_id)
);
