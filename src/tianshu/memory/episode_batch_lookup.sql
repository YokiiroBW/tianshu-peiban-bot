-- A page needs its latest matching batches, never every historical manifest.
-- Index only episode membership; frozen prompts and results stay out of the GIN.
-- The observed historical batch table is 3 GB. Give this one-time index build
-- a local DDL budget, then restore the caller's original statement timeout.
SELECT set_config('tianshu.migration_statement_timeout',current_setting('statement_timeout'),true);
SET LOCAL statement_timeout='120s';
CREATE INDEX consolidation_episode_membership ON memory.consolidation_batches
    USING gin ((manifest->'episodes') jsonb_path_ops);
CREATE INDEX consolidation_recovery_lineage ON memory.consolidation_batches
    ((manifest->'recovery'->>'lineage_id')) WHERE status='quarantined';
SELECT set_config('statement_timeout',current_setting('tianshu.migration_statement_timeout'),true);
