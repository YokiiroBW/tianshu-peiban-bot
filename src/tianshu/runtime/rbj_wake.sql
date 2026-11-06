-- Durable metadata for explicitly registered, recoverable state wakes only.
ALTER TABLE runtime.job_keys
    ADD COLUMN wake_key text,
    ADD COLUMN wake_object_key text,
    ADD COLUMN wake_covered_by bigint,
    ADD COLUMN wake_coverage_ref text,
    ADD COLUMN wake_not_before timestamptz,
    ADD COLUMN wake_effective_digest text,
    ADD COLUMN wake_invalidated_at timestamptz,
    ADD COLUMN wake_invalidation_ref text;

CREATE INDEX rbj_job_keys_wake_lookup ON runtime.job_keys
    (data_generation, task, wake_object_key, wake_key, job_id)
    WHERE wake_key IS NOT NULL AND cancelled_at IS NULL;
CREATE INDEX rbj_job_keys_wake_covered_by ON runtime.job_keys(wake_covered_by)
    WHERE wake_covered_by IS NOT NULL;

-- Preserve both the original cover and later flattened target changes.
CREATE TABLE runtime.rbj_wake_coverage_facts (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data_generation text NOT NULL,
    job_id bigint NOT NULL,
    covered_by bigint NOT NULL,
    previous_covered_by bigint,
    coverage_ref text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    CHECK (job_id<>covered_by)
);

-- Retain digests only; withdrawn payload bodies remain solely in the owner
-- transaction until the native args are replaced by their redacted envelope.
CREATE TABLE runtime.rbj_wake_redactions (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id bigint NOT NULL,
    data_generation text,
    requested_generation text,
    task text NOT NULL,
    wake_object_key text,
    original_digest text,
    blocked_digest text NOT NULL,
    blocked_event_digest text,
    effective_digest text NOT NULL,
    invalidation_ref text NOT NULL,
    metadata_unverified boolean NOT NULL DEFAULT false,
    invalidated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX rbj_wake_redactions_object ON runtime.rbj_wake_redactions
    (data_generation,task,wake_object_key) WHERE wake_object_key IS NOT NULL;
CREATE INDEX rbj_wake_redactions_job ON runtime.rbj_wake_redactions(job_id,id);
CREATE INDEX rbj_wake_redactions_payload ON runtime.rbj_wake_redactions(task,blocked_digest,requested_generation);
CREATE INDEX rbj_wake_redactions_event ON runtime.rbj_wake_redactions(task,blocked_event_digest,requested_generation)
    WHERE blocked_event_digest IS NOT NULL;
CREATE TABLE runtime.rbj_wake_redaction_scopes (
    job_id bigint NOT NULL,
    data_generation text NOT NULL,
    task text NOT NULL,
    wake_object_key text NOT NULL,
    PRIMARY KEY(job_id,data_generation,task,wake_object_key)
);
CREATE INDEX rbj_wake_redaction_scope_lookup ON runtime.rbj_wake_redaction_scopes
    (data_generation,task,wake_object_key);

-- Proof that a particular native invocation exited before any domain work.
CREATE TABLE runtime.rbj_wake_exits (
    data_generation text NOT NULL,
    job_id bigint NOT NULL,
    native_attempt integer NOT NULL CHECK (native_attempt >= 0),
    kind text NOT NULL DEFAULT 'covered' CHECK (kind IN ('covered','repair_preflight','source_invalidated')),
    covered_by bigint,
    repair_grant_id bigint,
    reason text,
    observation_ref text,
    observed_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation, job_id, native_attempt),
    CHECK ((kind='covered' AND covered_by IS NOT NULL AND repair_grant_id IS NULL
            AND reason IS NULL AND observation_ref IS NULL) OR
           (kind='repair_preflight' AND covered_by IS NULL AND repair_grant_id IS NOT NULL
            AND reason IS NOT NULL AND reason IN ('closed','blocked','unknown') AND observation_ref IS NOT NULL) OR
           (kind='source_invalidated' AND covered_by IS NULL AND repair_grant_id IS NULL
            AND reason='source_invalidated' AND observation_ref IS NOT NULL))
);
