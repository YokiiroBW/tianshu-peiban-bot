CREATE TABLE IF NOT EXISTS conversation.input_supplements (
    input_id text NOT NULL REFERENCES conversation.turns(turn_id),
    part_index integer NOT NULL,
    supplement_revision bigint NOT NULL,
    original_ref jsonb NOT NULL,
    supplement_ref jsonb NOT NULL,
    media jsonb NOT NULL,
    content_digest text NOT NULL,
    deployment_generation jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    notification_state text NOT NULL DEFAULT 'pending',
    notified_at timestamptz,
    PRIMARY KEY(input_id, part_index, supplement_revision),
    UNIQUE(input_id, part_index, content_digest)
);
ALTER TABLE conversation.turns ADD COLUMN IF NOT EXISTS original_digest text;
ALTER TABLE conversation.turns ADD COLUMN IF NOT EXISTS withdrawn_at timestamptz;
ALTER TABLE conversation.turns ADD COLUMN IF NOT EXISTS deployment_generation text;
ALTER TABLE conversation.expressions ADD COLUMN IF NOT EXISTS result_observed_at timestamptz;
