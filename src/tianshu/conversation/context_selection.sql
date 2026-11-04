CREATE TABLE IF NOT EXISTS conversation.context_selections (
    attempt_id text PRIMARY KEY,
    turn_id text NOT NULL REFERENCES conversation.turns(turn_id),
    round integer NOT NULL,
    request_digest text NOT NULL,
    selection jsonb NOT NULL,
    token_usage jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    deployment_generation jsonb,
    round_snapshot jsonb NOT NULL,
    model_receipt jsonb,
    model_result jsonb,
    consumed_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS context_selections_turn ON conversation.context_selections(turn_id, round);
