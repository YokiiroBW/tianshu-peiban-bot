-- Notification revisions coalesce by actor; the latest authorised context is
-- retained independently of the semantic planning fingerprint.
ALTER TABLE life.plan_watermarks ADD COLUMN IF NOT EXISTS wake_context jsonb;

-- A confirmed Models receipt belongs to the original generation even when the
-- current actor pointer later moves to another generation.
ALTER TABLE life.plan_generations
    ADD COLUMN IF NOT EXISTS submission_confirmed boolean,
    ADD COLUMN IF NOT EXISTS admission_started boolean;

-- Only the original Models ledger is submission evidence. Legacy rows without
-- such evidence remain NULL and use conservative observation, not pre-submit.
UPDATE life.plan_generations AS generation
SET submission_confirmed=true
FROM conversation.generation_requests AS request
JOIN models.attempts AS attempt ON attempt.attempt_id=request.attempt_id
WHERE request.material_key=generation.generation_key;

-- Diary/chapter retries also retain original admission evidence. NULL is the
-- conservative legacy state; newly frozen, unclaimed requests use false.
ALTER TABLE life.generation_recipes
    ADD COLUMN IF NOT EXISTS model_submission_confirmed boolean;
UPDATE life.generation_recipes AS recipe
SET model_submission_confirmed=true
FROM models.attempts AS attempt
WHERE attempt.attempt_id=recipe.generation_id;

CREATE TABLE IF NOT EXISTS life.plan_statistics_pending (
    generation_key text PRIMARY KEY REFERENCES life.plan_generations(generation_key) ON DELETE CASCADE
);
