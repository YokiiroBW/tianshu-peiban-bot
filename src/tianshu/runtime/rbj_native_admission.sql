-- Preserve the original unknown fact while recording a separately verified
-- pre-business-begin outcome for one bound native repair ordinal.
ALTER TABLE runtime.rbi_execution_facts
    DROP CONSTRAINT rbi_execution_facts_resolved_outcome_check;
ALTER TABLE runtime.rbi_execution_facts
    ADD CONSTRAINT rbi_execution_facts_resolved_outcome_check
    CHECK (resolved_outcome IN ('failed','wait','completed','not_started'));

-- Only a trusted Python publisher can register the original physical job.
-- Reusing/adopting a legacy job never creates this begin-required policy.
CREATE TABLE runtime.rbj_execution_admissions (
    data_generation text NOT NULL,
    job_id bigint NOT NULL,
    owner text NOT NULL CHECK (owner='conversation.result'),
    identity_digest text NOT NULL,
    payload_digest text NOT NULL,
    policy text NOT NULL CHECK (policy='normal_begin_required'),
    registered_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation,job_id)
);
CREATE INDEX rbj_execution_admissions_identity ON runtime.rbj_execution_admissions
    (data_generation,identity_digest,job_id);

-- Extend the same ordinal exit ledger; never erase earlier exit evidence.
ALTER TABLE runtime.rbj_wake_exits DROP CONSTRAINT rbj_wake_exits_kind_check;
ALTER TABLE runtime.rbj_wake_exits DROP CONSTRAINT rbj_wake_exits_check;
ALTER TABLE runtime.rbj_wake_exits ADD CONSTRAINT rbj_wake_exits_kind_check
    CHECK (kind IN ('covered','repair_preflight','source_invalidated','normal_preflight'));
ALTER TABLE runtime.rbj_wake_exits ADD CONSTRAINT rbj_wake_exits_check CHECK (
    (kind='covered' AND covered_by IS NOT NULL AND repair_grant_id IS NULL
        AND reason IS NULL AND observation_ref IS NULL) OR
    (kind='repair_preflight' AND covered_by IS NULL AND repair_grant_id IS NOT NULL
        AND reason IS NOT NULL AND reason IN ('closed','blocked','unknown') AND observation_ref IS NOT NULL) OR
    (kind='source_invalidated' AND covered_by IS NULL AND repair_grant_id IS NULL
        AND reason IS NOT NULL AND reason='source_invalidated' AND observation_ref IS NOT NULL) OR
    (kind='normal_preflight' AND covered_by IS NULL AND repair_grant_id IS NULL
        AND reason IS NOT NULL AND reason IN ('closed','blocked','unknown','history_more','execution_wait','execution_in_progress') AND observation_ref IS NOT NULL)
);
