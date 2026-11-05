-- A local claim can precede the RPC. Only a real Models ledger row confirms
-- admission; existing claimed rows without one remain unconfirmed.
ALTER TABLE conversation.generation_requests
    ADD COLUMN IF NOT EXISTS submission_confirmed boolean NOT NULL DEFAULT false;

ALTER TABLE conversation.generation_requests
    ADD COLUMN IF NOT EXISTS recovery_unconfirmed boolean NOT NULL DEFAULT false;

ALTER TABLE conversation.context_selections
    ADD COLUMN IF NOT EXISTS consumption_state text NOT NULL DEFAULT 'idle'
    CHECK (consumption_state IN ('idle', 'running', 'unconfirmed', 'done'));

ALTER TABLE conversation.expression_plans
    ADD COLUMN IF NOT EXISTS continuation_unconfirmed boolean NOT NULL DEFAULT false;

UPDATE conversation.generation_requests AS generation
SET submission_confirmed = true
FROM models.attempts AS attempt
WHERE generation.attempt_id = attempt.attempt_id
  AND generation.state = 'claimed'
  AND NOT generation.submission_confirmed;

UPDATE conversation.context_selections
SET consumption_state = 'done'
WHERE consumed_at IS NOT NULL AND consumption_state = 'idle';

UPDATE conversation.context_selections AS selected
SET consumption_state = 'unconfirmed'
FROM conversation.turns AS turn
WHERE selected.attempt_id = turn.model_attempt_id
  AND selected.turn_id = turn.turn_id
  AND selected.model_result IS NOT NULL AND selected.consumed_at IS NULL
  AND selected.consumption_state = 'idle' AND turn.state = 'running';
