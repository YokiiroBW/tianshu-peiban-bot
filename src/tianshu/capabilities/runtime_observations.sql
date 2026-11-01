-- Runtime observations are not authored connection settings or configuration revisions.
ALTER TABLE capability_connections.connections ADD COLUMN last_test jsonb;
ALTER TABLE capabilities.maintenance
    ADD COLUMN body jsonb NOT NULL DEFAULT '{}',
    ADD COLUMN next_due_at timestamptz,
    ADD COLUMN wake_generation bigint NOT NULL DEFAULT 0;
CREATE TABLE capabilities.runtime_observations (
    name text PRIMARY KEY,
    owner_id text NOT NULL REFERENCES capabilities.execution_owners(id),
    revision bigint NOT NULL DEFAULT 1,
    observed_at timestamptz NOT NULL DEFAULT now(),
    body jsonb NOT NULL
);
