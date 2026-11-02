-- Unknown is an observation hold, not a failed or successful consolidation.
ALTER TABLE memory.consolidation_batches DROP CONSTRAINT consolidation_batches_status_check;
ALTER TABLE memory.consolidation_batches ADD CONSTRAINT consolidation_batches_status_check
    CHECK(status IN ('prepared','waiting_model','ready','quarantined','applied','stale','failed','revoked'));
CREATE INDEX consolidation_quarantined ON memory.consolidation_batches(partition_id,updated_at)
    WHERE status='quarantined';
-- Existing requests retain their original 120 second window. New steps freeze
-- their actual window independently of a transport deadline after restart.
ALTER TABLE memory.model_steps ADD COLUMN request_timeout_seconds integer NOT NULL DEFAULT 120
    CHECK(request_timeout_seconds BETWEEN 1 AND 900);
