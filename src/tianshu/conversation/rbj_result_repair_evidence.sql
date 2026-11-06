-- Trace the coordinator-verified defect evidence back to the original result.
-- Runtime remains the authority for the repair registration and execution grant.
CREATE TABLE IF NOT EXISTS conversation.rbj_result_repair_evidence (
    data_generation text NOT NULL,
    action_id text NOT NULL,
    receipt_id text NOT NULL,
    result_revision bigint NOT NULL,
    consumer text NOT NULL CHECK (consumer IN ('life_activity', 'life_commitment')),
    defect_key text NOT NULL,
    repair_ref text NOT NULL,
    repair_revision text NOT NULL,
    defect_evidence_ref text NOT NULL,
    owner_id text NOT NULL,
    owner_revision bigint,
    material_digest text NOT NULL,
    failure_digest text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation, receipt_id, consumer, defect_key),
    UNIQUE (data_generation, receipt_id, consumer, repair_ref)
);

-- A pre-begin stop records only the original ordinal and an already observed
-- domain/eligibility decision. Runtime remains authoritative for whether that
-- ordinal was actually bound and had not begun.
CREATE TABLE IF NOT EXISTS conversation.rbj_result_preflight_stops (
    data_generation text NOT NULL,
    job_id bigint NOT NULL,
    native_attempt integer NOT NULL,
    action_id text NOT NULL,
    receipt_id text NOT NULL,
    result_revision bigint NOT NULL,
    consumer text NOT NULL,
    repair_ref text NOT NULL,
    owner_id text,
    decision text NOT NULL CHECK (decision IN ('closed', 'blocked', 'unknown')),
    reason text NOT NULL,
    material_digest text NOT NULL,
    observation_ref text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation, job_id, native_attempt)
);
