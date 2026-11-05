-- Classifications supplement the existing attempt ledger; they never change it.
CREATE TABLE runtime.recovery_not_sent (
    attempt_id text PRIMARY KEY REFERENCES runtime.recovery_attempts(attempt_id),
    lineage_id text NOT NULL REFERENCES runtime.recovery_lineages(lineage_id),
    evidence_kind text NOT NULL CHECK (evidence_kind IN ('legacy', 'rbh.transport.v1')),
    models_finished_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX recovery_not_sent_lineage ON runtime.recovery_not_sent(lineage_id);
