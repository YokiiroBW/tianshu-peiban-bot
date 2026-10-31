CREATE SCHEMA IF NOT EXISTS conversation;
CREATE TABLE conversation.turns (
    turn_id text PRIMARY KEY, actor_id text NOT NULL, conversation_id text NOT NULL,
    producer text NOT NULL, event_id text NOT NULL, event_revision bigint NOT NULL,
    context jsonb NOT NULL, input_parts jsonb NOT NULL, source_refs jsonb NOT NULL,
    original_parts jsonb NOT NULL, original_source_refs jsonb NOT NULL,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    collector_key text, collection_revision bigint NOT NULL DEFAULT 1,
    collection_messages integer NOT NULL DEFAULT 1, collection_bytes integer NOT NULL DEFAULT 0,
    seal_at timestamptz, merged_into text, message_cutoff bigint,
    state text NOT NULL CHECK(state IN ('collecting','merged','queued','running','waiting_external','completed','failed','unknown','cancelled','observed')),
    reply_owner text NOT NULL, reply_required boolean NOT NULL, command text,
    revision bigint NOT NULL DEFAULT 1, model_attempt_id text, model_parts jsonb,
    model_native jsonb, expression_id text, missing text[] NOT NULL DEFAULT '{}', error_code text,
    occurred_at timestamptz NOT NULL, received_at timestamptz NOT NULL, started_at timestamptz,
    completed_at timestamptz, lease_until timestamptz, deadline_at timestamptz NOT NULL,
    round integer NOT NULL DEFAULT 0, continuation jsonb NOT NULL DEFAULT '[]',
    ordered_parts jsonb NOT NULL DEFAULT '[]', artifacts jsonb NOT NULL DEFAULT '[]',
    tool_dispatch_complete boolean NOT NULL DEFAULT false,
    UNIQUE(actor_id,conversation_id,producer,event_id,event_revision)
);
CREATE INDEX turns_pending ON conversation.turns(actor_id,conversation_id,received_at) WHERE state IN ('queued','running');
CREATE INDEX turns_running ON conversation.turns(actor_id,conversation_id) WHERE state='running';
CREATE TABLE conversation.messages (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, turn_id text REFERENCES conversation.turns(turn_id),
    actor_id text NOT NULL, conversation_id text NOT NULL, speaker text NOT NULL,
    parts jsonb NOT NULL, source_refs jsonb NOT NULL, scope_ids text[] NOT NULL,
    expression_id text UNIQUE, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX messages_conversation ON conversation.messages(conversation_id,id DESC);
CREATE TABLE conversation.tool_links (
    turn_id text NOT NULL REFERENCES conversation.turns(turn_id), call_id text NOT NULL,
    action_id text NOT NULL, capability_id text NOT NULL, state text NOT NULL,
    last_receipt_id text, tool_name text NOT NULL, provider_call_id text NOT NULL, arguments_hash text NOT NULL, result jsonb,
    consumed boolean NOT NULL DEFAULT false, PRIMARY KEY(turn_id,call_id), UNIQUE(action_id)
);
ALTER TABLE conversation.tool_links ADD COLUMN result_revision bigint NOT NULL DEFAULT -1;
CREATE TABLE conversation.expressions (
    expression_id text PRIMARY KEY, actor_id text NOT NULL, conversation_id text NOT NULL,
    turn_id text, target_ref text NOT NULL, context jsonb NOT NULL, public_parts jsonb NOT NULL,
    source_refs jsonb NOT NULL, artifacts jsonb NOT NULL DEFAULT '[]',
    state text NOT NULL CHECK(state IN ('prepared','sending','delivered','failed','unknown')),
    receipt jsonb, fingerprint text NOT NULL, candidate_id text,
    revision bigint NOT NULL DEFAULT 1,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE conversation.result_consumption (
    receipt_id text PRIMARY KEY, action_id text NOT NULL, turn_id text,
    processed_at timestamptz NOT NULL DEFAULT now()
);
