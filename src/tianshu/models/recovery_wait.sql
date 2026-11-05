-- Waiting belongs to the original submission, not a new model attempt.
ALTER TABLE models.submissions ADD COLUMN recovery_state text
    CHECK (recovery_state IN ('checking', 'scheduled', 'observe_only'));
ALTER TABLE models.submissions ADD COLUMN recovery_due_at timestamptz;
ALTER TABLE models.submissions ADD COLUMN recovery_wait jsonb;

ALTER TABLE models.submissions ADD CONSTRAINT submissions_recovery_wait_shape CHECK (
    CASE
        WHEN recovery_state IS NULL OR recovery_state = 'checking'
            THEN recovery_due_at IS NULL AND recovery_wait IS NULL
        WHEN recovery_state = 'scheduled'
            THEN recovery_due_at IS NOT NULL AND recovery_wait IS NOT NULL
        WHEN recovery_state = 'observe_only'
            THEN (recovery_due_at IS NULL AND recovery_wait IS NULL)
                 OR (recovery_due_at IS NOT NULL AND recovery_wait IS NOT NULL)
        ELSE false
    END
);

CREATE INDEX models_submissions_recovery_due
    ON models.submissions(recovery_due_at, attempt_id) WHERE recovery_state = 'scheduled';
CREATE INDEX models_attempts_queued_handoff
    ON models.attempts(queued_at, attempt_id) INCLUDE (broker_instance)
    WHERE status = 'queued' AND dispatch = 'not_started' AND NOT cancel_requested;
