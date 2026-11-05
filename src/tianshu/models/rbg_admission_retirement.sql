-- Business retirement fences future admission without inventing an old receipt.
CREATE TABLE IF NOT EXISTS models.admission_retirements (
    attempt_id text PRIMARY KEY,
    data_generation uuid NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    lineage_id text NOT NULL,
    reason text NOT NULL CHECK (reason IN ('superseded','expired')),
    sealed_at timestamptz NOT NULL DEFAULT now()
);
