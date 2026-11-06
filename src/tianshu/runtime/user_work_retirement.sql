-- Administrative disposition only. Original jobs, domain states and receipts stay authoritative.
CREATE TABLE runtime.user_work_retirements (
    data_generation text NOT NULL,
    owner text NOT NULL CHECK (owner IN ('runtime','memory','growth','operations')),
    object_key jsonb NOT NULL CHECK (jsonb_typeof(object_key)='object'),
    request_ref text NOT NULL,
    requested_by text NOT NULL,
    original_fingerprint text NOT NULL,
    retired_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation,owner,object_key)
);

CREATE FUNCTION runtime.work_is_retired(owner_name text, object_identity jsonb)
RETURNS boolean LANGUAGE sql STABLE AS $$
    SELECT EXISTS (
        SELECT 1 FROM runtime.user_work_retirements r
        JOIN runtime.installation_generation g ON g.singleton AND g.id::text=r.data_generation
        WHERE r.owner=owner_name AND r.object_key=object_identity
    )
$$;
