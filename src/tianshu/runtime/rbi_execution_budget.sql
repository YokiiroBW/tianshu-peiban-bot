-- Mechanical execution facts only. Business delivery state stays in its domain.
-- job_id=0 is a history-completeness binding, not a physical job.
CREATE TABLE runtime.rbi_execution_facts (
    data_generation text NOT NULL,
    identity_digest text NOT NULL,
    owner text NOT NULL,
    max_failures integer NOT NULL CHECK (max_failures > 0),
    job_id bigint NOT NULL CHECK (job_id >= 0),
    native_attempt integer NOT NULL CHECK (native_attempt >= 0),
    outcome text NOT NULL CHECK (outcome IN ('history_complete','history_unknown','started','failed','wait','completed','unknown')),
    retry_at timestamptz,
    evidence_ref text,
    resolved_outcome text CHECK (resolved_outcome IN ('failed','wait','completed')),
    resolved_retry_at timestamptz,
    observation_ref text,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    resolved_at timestamptz,
    PRIMARY KEY (data_generation, identity_digest, job_id, native_attempt),
    CHECK ((job_id=0 AND native_attempt=0 AND outcome IN ('history_complete','history_unknown')) OR
           (job_id>0 AND outcome NOT IN ('history_complete','history_unknown')))
);
CREATE UNIQUE INDEX rbi_execution_physical_identity ON runtime.rbi_execution_facts
    (data_generation, job_id, native_attempt) WHERE job_id>0;

-- A cursor is mechanical history-reading state, not a business work queue.
-- The identity lock serializes its updates with imported execution facts.
CREATE TABLE runtime.rbi_execution_history_cursors (
    data_generation text NOT NULL,
    identity_digest text NOT NULL,
    job_id bigint NOT NULL CHECK (job_id > 0),
    task_name text NOT NULL,
    event_id bigint NOT NULL DEFAULT 0 CHECK (event_id >= 0),
    ordinal integer NOT NULL DEFAULT 0 CHECK (ordinal >= 0),
    active_attempt integer,
    active_event_id bigint,
    last_type text,
    PRIMARY KEY (data_generation, identity_digest, job_id),
    UNIQUE (data_generation, job_id),
    CHECK ((active_attempt IS NULL AND active_event_id IS NULL) OR
           (active_attempt = ordinal AND active_event_id IS NOT NULL))
);
-- The native schema indexes job_id alone; the cursor also needs ordered seek.
CREATE INDEX rbi_execution_events_job_id_id ON public.procrastinate_events(job_id, id);
