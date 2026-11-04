-- Source withdrawal must remain distinct from a repairable configuration error.
ALTER TABLE runtime.recovery_lineages
    ADD COLUMN source_withdrawn boolean NOT NULL DEFAULT false;

-- Preserve a withdrawal already recorded before this migration. Other source
-- validity changes still come from the owning domain's authoritative event.
UPDATE runtime.recovery_lineages lineage SET source_withdrawn=true
WHERE lineage.failure_category='source_invalid' AND EXISTS (
    SELECT 1 FROM runtime.recovery_attempts attempt
    WHERE attempt.lineage_id=lineage.lineage_id AND attempt.attempt_number=lineage.attempts
      AND attempt.error_code ~* '(withdraw|retract|revoked|forgotten)'
);
