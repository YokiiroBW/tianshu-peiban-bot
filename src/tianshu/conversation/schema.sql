CREATE SCHEMA IF NOT EXISTS conversation;
CREATE TABLE conversation.generation_requests (
    material_key text PRIMARY KEY, attempt_id text NOT NULL UNIQUE,
    context_key text NOT NULL, request jsonb, source_refs jsonb NOT NULL,
    state text NOT NULL DEFAULT 'prepared' CHECK(state IN ('prepared','claimed','invalidated','expired')),
    created_at timestamptz NOT NULL DEFAULT now(), claimed_at timestamptz
);
CREATE TABLE conversation.turns (
    turn_id text PRIMARY KEY, actor_id text NOT NULL, conversation_id text NOT NULL,
    producer text NOT NULL, event_id text NOT NULL, event_revision bigint NOT NULL,
    context jsonb NOT NULL, input_parts jsonb NOT NULL, source_refs jsonb NOT NULL,
    original_parts jsonb NOT NULL, original_source_refs jsonb NOT NULL,
    autonomous boolean NOT NULL DEFAULT false,
    participation_state text, participation_policy_revision bigint,
    participation_checked_at timestamptz, participation_deadline timestamptz,
    participation_cutoff bigint, participation_reason text,
    sequence bigint GENERATED ALWAYS AS IDENTITY,
    collector_key text, collection_revision bigint NOT NULL DEFAULT 1,
    collection_messages integer NOT NULL DEFAULT 1, collection_bytes integer NOT NULL DEFAULT 0,
    seal_at timestamptz, merged_into text, message_cutoff bigint,
    state text NOT NULL CHECK(state IN ('collecting','merged','queued','running','waiting_external','waiting_expression','completed','failed','unknown','cancelled','observed')),
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
    background boolean NOT NULL DEFAULT false, consumed boolean NOT NULL DEFAULT false, PRIMARY KEY(turn_id,call_id), UNIQUE(action_id)
);
ALTER TABLE conversation.tool_links ADD COLUMN result_revision bigint NOT NULL DEFAULT -1;
CREATE TABLE conversation.expressions (
    expression_id text PRIMARY KEY, actor_id text NOT NULL, conversation_id text NOT NULL,
    turn_id text, target_ref text NOT NULL, context jsonb NOT NULL, public_parts jsonb NOT NULL,
    source_refs jsonb NOT NULL, artifacts jsonb NOT NULL DEFAULT '[]',
    state text NOT NULL CHECK(state IN ('prepared','sending','waiting_delivery','delivered','failed','unknown')),
    receipt jsonb, fingerprint text NOT NULL, candidate_id text,
    revision bigint NOT NULL DEFAULT 1,
    lease_until timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), completed_at timestamptz
);
CREATE TABLE conversation.result_consumption (
    receipt_id text PRIMARY KEY, action_id text NOT NULL, turn_id text,
    processed_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversation.expression_plans (
    expression_id text PRIMARY KEY, actor_id text NOT NULL, conversation_id text NOT NULL,
    revision bigint NOT NULL DEFAULT 1, fingerprint text NOT NULL, context jsonb NOT NULL,
    plan jsonb NOT NULL, source_refs jsonb NOT NULL, profile jsonb,
    state text NOT NULL, service_class text NOT NULL DEFAULT 'elastic', steps jsonb NOT NULL DEFAULT '[]', rendered jsonb,
    receipt jsonb, reason text, deadline_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE conversation.group_participation (
    actor_id text NOT NULL, conversation_id text NOT NULL,
    enabled boolean NOT NULL DEFAULT false, revision bigint NOT NULL DEFAULT 1,
    PRIMARY KEY(actor_id,conversation_id)
);
CREATE UNIQUE INDEX one_group_participation ON conversation.turns(actor_id,conversation_id)
    WHERE participation_state IN ('waiting','evaluating');
