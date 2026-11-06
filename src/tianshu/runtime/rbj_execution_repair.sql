-- These rows are mechanical repair registrations and grants, never business state.
CREATE TABLE runtime.rbj_execution_repairs (
    owner text NOT NULL,
    defect_key text NOT NULL,
    consumer text NOT NULL,
    repair_ref text NOT NULL,
    repair_revision text NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (owner, defect_key, consumer),
    UNIQUE (owner, repair_ref, consumer)
);

CREATE TABLE runtime.rbj_execution_repair_grants (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    data_generation text NOT NULL,
    identity_digest text NOT NULL,
    owner text NOT NULL,
    defect_key text NOT NULL,
    consumer text NOT NULL,
    repair_ref text NOT NULL,
    repair_revision text NOT NULL,
    failure_digest text NOT NULL,
    granted_at timestamptz NOT NULL DEFAULT now(),
    closed_at timestamptz,
    close_observation_ref text,
    CHECK ((closed_at IS NULL AND close_observation_ref IS NULL) OR
           (closed_at IS NOT NULL AND close_observation_ref IS NOT NULL)),
    UNIQUE (data_generation, identity_digest, defect_key, consumer),
    FOREIGN KEY (owner, defect_key, consumer)
        REFERENCES runtime.rbj_execution_repairs(owner, defect_key, consumer)
);

ALTER TABLE runtime.rbi_execution_facts
    ADD COLUMN purpose text NOT NULL DEFAULT 'normal',
    ADD COLUMN repair_grant_id bigint REFERENCES runtime.rbj_execution_repair_grants(id),
    ADD CONSTRAINT rbj_execution_purpose_check CHECK (
        (purpose='normal' AND repair_grant_id IS NULL) OR
        (purpose='repair' AND repair_grant_id IS NOT NULL AND job_id>0)
    );

-- Associate a repair job before it can fail in the worker's own preflight.
CREATE TABLE runtime.rbj_execution_repair_jobs (
    data_generation text NOT NULL,
    job_id bigint NOT NULL,
    identity_digest text NOT NULL,
    grant_id bigint NOT NULL REFERENCES runtime.rbj_execution_repair_grants(id),
    bound_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation, job_id)
);

-- A repair payload cannot become visible before its same-transaction binding.
CREATE FUNCTION runtime.rbj_check_repair_job_bound() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.task='conversation.result' AND NEW.job_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM public.procrastinate_jobs j WHERE j.id=NEW.job_id
        AND (j.args ? 'repair_ref' OR j.args ? 'repair_consumer')
    ) AND NOT EXISTS (
        SELECT 1 FROM runtime.rbj_execution_repair_jobs b
        WHERE b.data_generation=NEW.data_generation AND b.job_id=NEW.job_id
    ) THEN
        RAISE EXCEPTION 'execution_repair_job_unbound' USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$$;

CREATE CONSTRAINT TRIGGER rbj_repair_job_bound
    AFTER INSERT OR UPDATE OF job_id ON runtime.job_keys
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
    EXECUTE FUNCTION runtime.rbj_check_repair_job_bound();
