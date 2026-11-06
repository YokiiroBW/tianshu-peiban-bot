-- Same-version repair authorizations augment the existing recovery ledger.
CREATE TABLE runtime.recovery_repairs (
    lineage_id text NOT NULL REFERENCES runtime.recovery_lineages(lineage_id),
    repair_ref text NOT NULL,
    attempt_id text NOT NULL REFERENCES runtime.recovery_attempts(attempt_id),
    attempt_version text NOT NULL,
    model_intent_id text NOT NULL,
    model_request_digest text NOT NULL,
    models_finished_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (lineage_id, repair_ref)
);
