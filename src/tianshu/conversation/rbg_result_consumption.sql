-- The native result job retains the original event/receipt; this index stores
-- only identity, ownership, independent consumer progress and bounded proofs.
CREATE TABLE IF NOT EXISTS conversation.result_deliveries (
    data_generation text NOT NULL,
    receipt_id text NOT NULL,
    action_id text NOT NULL,
    result_revision bigint NOT NULL CHECK (result_revision >= 0),
    original_job_id bigint,
    owner_activity_id text,
    owner_commitment_id text,
    owner_resolved boolean NOT NULL DEFAULT false,
    state text NOT NULL DEFAULT 'pending'
        CHECK (state IN ('pending', 'completed', 'superseded', 'invalidated')),
    reason text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation, receipt_id)
);

CREATE INDEX IF NOT EXISTS result_deliveries_pending
    ON conversation.result_deliveries (data_generation, updated_at, receipt_id)
    WHERE state = 'pending';

CREATE TABLE IF NOT EXISTS conversation.result_consumer_progress (
    data_generation text NOT NULL,
    receipt_id text NOT NULL,
    consumer text NOT NULL CHECK (consumer IN
        ('renderer', 'dialogue', 'life_activity', 'life_commitment', 'detached', 'memory')),
    action_id text NOT NULL,
    result_revision bigint NOT NULL,
    disposition text NOT NULL DEFAULT 'pending' CHECK (disposition IN
        ('pending', 'running', 'done', 'not_applicable', 'superseded', 'invalidated', 'blocked')),
    reason text,
    proof jsonb NOT NULL DEFAULT '{}',
    owner_token text,
    lease_until timestamptz,
    attempts integer NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (data_generation, receipt_id, consumer),
    FOREIGN KEY (data_generation, receipt_id)
        REFERENCES conversation.result_deliveries(data_generation, receipt_id)
);

CREATE INDEX IF NOT EXISTS result_consumer_unfinished
    ON conversation.result_consumer_progress (data_generation, disposition, updated_at)
    WHERE disposition IN ('pending', 'running', 'blocked');
