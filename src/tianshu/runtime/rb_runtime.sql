-- Register after runtime.schema.sql and the native Procrastinate schema.
CREATE TABLE runtime.installation_generation (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO runtime.installation_generation(singleton,id) VALUES(true,gen_random_uuid()::text);

ALTER TABLE runtime.job_keys ADD COLUMN payload_digest text;
ALTER TABLE runtime.job_keys ADD COLUMN payload_version integer NOT NULL DEFAULT 1 CHECK (payload_version > 0);
ALTER TABLE runtime.job_keys ADD COLUMN data_generation text;
-- Coalesced wake identities remain independently replayable/cancellable while
-- pointing at the same native pending job.
ALTER TABLE runtime.job_keys DROP CONSTRAINT job_keys_job_id_key;
CREATE INDEX job_keys_job_id ON runtime.job_keys(job_id);

-- Service accounting is scheduling metadata. The job and its outcome remain
-- owned by Procrastinate and the domain, respectively.
CREATE TABLE runtime.queue_service (
    data_generation text NOT NULL,
    queue_name text NOT NULL,
    domain text NOT NULL,
    subject_id text NOT NULL,
    service_seconds double precision NOT NULL DEFAULT 0,
    measured_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(data_generation, queue_name, domain, subject_id)
);
CREATE TABLE runtime.queue_service_runs (
    job_id bigint PRIMARY KEY,
    data_generation text NOT NULL,
    queue_name text NOT NULL,
    domain text NOT NULL,
    subject_id text NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX queue_service_active_subject ON runtime.queue_service_runs(data_generation,queue_name,domain,subject_id);

CREATE FUNCTION runtime.fair_claim_job(target_queue text, p_worker_id bigint)
RETURNS procrastinate_jobs LANGUAGE plpgsql AS $$
DECLARE
    picked_id bigint;
    picked_domain text;
    picked_subject text;
    picked_generation text;
    result procrastinate_jobs;
BEGIN
    SELECT j.id, split_part(j.task_name,'.',1), COALESCE(NULLIF(k.subject_id,''),'task:' || j.task_name),k.data_generation
    INTO picked_id, picked_domain, picked_subject, picked_generation
    FROM procrastinate_jobs j
    JOIN LATERAL (SELECT key,created_at,subject_id,data_generation FROM runtime.job_keys
        WHERE job_id=j.id AND cancelled_at IS NULL ORDER BY created_at,key LIMIT 1) k ON true
    JOIN runtime.installation_generation generation ON generation.singleton=true AND k.data_generation=generation.id
    LEFT JOIN runtime.queue_service service ON service.data_generation=k.data_generation AND service.queue_name=j.queue_name
      AND service.domain=split_part(j.task_name,'.',1)
      AND service.subject_id=COALESCE(NULLIF(k.subject_id,''),'task:' || j.task_name)
    WHERE j.queue_name=target_queue AND j.status='todo'
      AND (j.scheduled_at IS NULL OR j.scheduled_at<=now())
      AND NOT EXISTS (
        SELECT 1 FROM procrastinate_jobs other WHERE j.lock IS NOT NULL AND other.lock=j.lock
          AND (other.status='doing' OR (other.status='todo' AND
            (other.priority>j.priority OR (other.priority=j.priority AND other.id<j.id))))
      )
    ORDER BY
      COALESCE(GREATEST(0,service.service_seconds-EXTRACT(EPOCH FROM (now()-service.measured_at))),0)
      + COALESCE((SELECT sum(GREATEST(1,EXTRACT(EPOCH FROM (now()-active.started_at))))
          FROM runtime.queue_service_runs active
          JOIN procrastinate_jobs running ON running.id=active.job_id AND running.status='doing'
          JOIN procrastinate_workers owner ON owner.id=running.worker_id
            AND owner.last_heartbeat>now()-interval '30 seconds'
          WHERE active.data_generation=k.data_generation AND active.queue_name=j.queue_name AND active.domain=split_part(j.task_name,'.',1)
            AND active.subject_id=COALESCE(NULLIF(k.subject_id,''),'task:' || j.task_name)),0)
      - GREATEST(0,EXTRACT(EPOCH FROM (now()-GREATEST(k.created_at,COALESCE(j.scheduled_at,k.created_at))))),
      j.priority DESC, j.id
    LIMIT 1 FOR UPDATE OF j SKIP LOCKED;
    IF picked_id IS NULL THEN
        RETURN result;
    END IF;
    UPDATE procrastinate_jobs SET status='doing',worker_id=p_worker_id WHERE id=picked_id RETURNING * INTO result;
    INSERT INTO runtime.queue_service(data_generation,queue_name,domain,subject_id)
      VALUES(picked_generation,target_queue,picked_domain,picked_subject) ON CONFLICT DO NOTHING;
    INSERT INTO runtime.queue_service_runs(job_id,data_generation,queue_name,domain,subject_id)
      VALUES(picked_id,picked_generation,target_queue,picked_domain,picked_subject)
      ON CONFLICT(job_id) DO UPDATE SET data_generation=excluded.data_generation,queue_name=excluded.queue_name,domain=excluded.domain,
        subject_id=excluded.subject_id,started_at=now();
    RETURN result;
END;
$$;

CREATE FUNCTION runtime.finish_fair_job(p_job_id bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE finished runtime.queue_service_runs;
BEGIN
    DELETE FROM runtime.queue_service_runs WHERE job_id=p_job_id RETURNING * INTO finished;
    IF finished.job_id IS NOT NULL THEN
        UPDATE runtime.queue_service SET
          service_seconds=GREATEST(0,service_seconds-EXTRACT(EPOCH FROM (now()-measured_at)))
            + GREATEST(0.01,EXTRACT(EPOCH FROM (now()-finished.started_at))),
          measured_at=now()
        WHERE data_generation=finished.data_generation AND queue_name=finished.queue_name
          AND domain=finished.domain AND subject_id=finished.subject_id;
    END IF;
END;
$$;

-- These rows are coordination metadata for the existing Procrastinate queue,
-- not a second queue or a replacement for the owning domain's receipts.
CREATE TABLE runtime.recompute_work (
    domain text NOT NULL,
    work_key text NOT NULL,
    task text NOT NULL,
    payload jsonb NOT NULL,
    subject_id text,
    matter_id text,
    payload_version integer NOT NULL CHECK (payload_version > 0),
    data_generation text NOT NULL,
    dirty_revision bigint NOT NULL DEFAULT 0,
    processed_revision bigint NOT NULL DEFAULT 0,
    covered_revision bigint NOT NULL DEFAULT 0,
    queue_generation bigint NOT NULL DEFAULT 0,
    queued_job_key text,
    due_at timestamptz,
    claim_token text,
    claim_revision bigint,
    claim_expires_at timestamptz,
    last_error text,
    PRIMARY KEY (data_generation, domain, work_key),
    CHECK (covered_revision <= processed_revision AND processed_revision <= dirty_revision)
);
CREATE INDEX recompute_due ON runtime.recompute_work (due_at)
    WHERE dirty_revision > processed_revision;
CREATE INDEX recompute_claim_expiry ON runtime.recompute_work (claim_expires_at)
    WHERE claim_token IS NOT NULL;

CREATE TABLE runtime.recompute_changes (
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    change_id text NOT NULL,
    revision bigint NOT NULL,
    PRIMARY KEY (data_generation, domain, work_key, change_id),
    FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key)
);

CREATE TABLE runtime.recompute_gaps (
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    material_id text NOT NULL,
    revision bigint NOT NULL,
    reason text NOT NULL,
    opened_at timestamptz NOT NULL DEFAULT now(),
    repaired_at timestamptz,
    PRIMARY KEY (data_generation, domain, work_key, material_id),
    FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key)
);

CREATE TABLE runtime.recompute_attempts (
    claim_token text PRIMARY KEY,
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    revision bigint NOT NULL,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    outcome text NOT NULL DEFAULT 'running',
    error_code text,
    FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key)
);
CREATE TABLE runtime.recompute_gap_history (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data_generation text NOT NULL,
    domain text NOT NULL,
    work_key text NOT NULL,
    material_id text NOT NULL,
    revision bigint NOT NULL,
    action text NOT NULL CHECK (action IN ('isolated', 'repaired')),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (data_generation, domain, work_key) REFERENCES runtime.recompute_work(data_generation, domain, work_key)
);

-- One budget for the original material/dependency lineage, regardless of
-- route, child job, configuration revision, or which layer performs the attempt.
CREATE TABLE runtime.recovery_lineages (
    lineage_id text PRIMARY KEY,
    data_generation text NOT NULL,
    domain text NOT NULL,
    subject_id text NOT NULL,
    owner text NOT NULL,
    material_digest text NOT NULL,
    dependency text NOT NULL,
    current_version text NOT NULL,
    max_attempts integer NOT NULL CHECK (max_attempts > 0),
    attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    due_at timestamptz,
    failure_category text,
    paused boolean NOT NULL DEFAULT false,
    cancelled boolean NOT NULL DEFAULT false,
    claim_token text,
    claim_expires_at timestamptz,
    continuation_attempt_id text,
    continuation_reason text
);
CREATE INDEX recovery_lineages_due ON runtime.recovery_lineages (due_at, domain, subject_id)
    WHERE due_at IS NOT NULL AND NOT paused AND NOT cancelled;
CREATE TABLE runtime.recovery_attempts (
    attempt_id text PRIMARY KEY,
    lineage_id text NOT NULL REFERENCES runtime.recovery_lineages(lineage_id),
    request_digest text NOT NULL,
    route text NOT NULL,
    dependency text NOT NULL,
    version text NOT NULL,
    attempt_number integer NOT NULL CHECK (attempt_number > 0),
    dependency_probe boolean NOT NULL DEFAULT false,
    continuation_of text,
    continuation_reason text,
    status text NOT NULL DEFAULT 'reserved',
    error_code text,
    observation_ref text,
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz
);
CREATE TABLE runtime.recovery_attempt_observations (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    attempt_id text NOT NULL REFERENCES runtime.recovery_attempts(attempt_id),
    status text NOT NULL,
    error_code text,
    observation_ref text,
    observed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime.recovery_dependency_changes (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lineage_id text NOT NULL REFERENCES runtime.recovery_lineages(lineage_id),
    previous_dependency text NOT NULL,
    new_dependency text NOT NULL,
    version text NOT NULL,
    changed_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE runtime.recovery_dependencies (
    data_generation text NOT NULL,
    dependency text NOT NULL,
    cooldown_until timestamptz,
    probe_token text,
    probe_expires_at timestamptz,
    PRIMARY KEY(data_generation, dependency)
);
