-- An attempt retains the installation generation in which its owner created it.
ALTER TABLE models.attempts ADD COLUMN data_generation uuid;
ALTER TABLE models.submissions ADD COLUMN completion_state text NOT NULL DEFAULT 'none'
    CHECK (completion_state IN ('none','pending','enqueued','expired'));
ALTER TABLE models.submissions ADD COLUMN completion_error_code text;

UPDATE models.submissions SET completion_state = CASE
    WHEN completion IS NULL THEN 'none'
    WHEN completion_enqueued_at IS NOT NULL THEN 'enqueued'
    ELSE 'pending'
END;

-- Older accepted completions can prove their original generation only when
-- that UUID was actually frozen in their payload. Never invent one.
UPDATE models.attempts a SET data_generation=(s.completion->>'data_generation')::uuid
FROM models.submissions s WHERE s.attempt_id=a.attempt_id
    AND s.completion->>'data_generation' ~* '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$';
