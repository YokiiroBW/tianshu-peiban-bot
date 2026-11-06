-- The installation-scoped cursor bounds Memory's maintenance scan.  It stores
-- no business state and cannot confer permission to retry a model attempt.
CREATE TABLE IF NOT EXISTS memory.rbi_reconcile_cursor (
    name text PRIMARY KEY,
    data_generation text NOT NULL,
    after_partition_id text NOT NULL DEFAULT '',
    updated_at timestamptz NOT NULL DEFAULT now()
);
