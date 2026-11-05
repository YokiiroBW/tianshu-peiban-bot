-- Bounded, restart-safe discovery of blocked Growth transport batches.
-- This is scan scheduling metadata, not a second recovery budget or queue.
CREATE TABLE companion.rbh_growth_transport_scans (
    runtime_generation text NOT NULL,
    actor_id text NOT NULL,
    scope_key text NOT NULL,
    retry_key text NOT NULL,
    retry_revision bigint NOT NULL,
    next_scan_at timestamptz NOT NULL,
    PRIMARY KEY (runtime_generation, actor_id, scope_key, retry_key)
);
CREATE INDEX rbh_growth_transport_scan_due
    ON companion.rbh_growth_transport_scans(runtime_generation, next_scan_at);
