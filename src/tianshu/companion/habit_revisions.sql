-- Companion owns the current rule and its revision history.  The Memory index
-- is a reference to this ledger, never another editable copy of a rule.
ALTER TABLE companion.style_rules
    ADD COLUMN semantic_revision bigint NOT NULL DEFAULT 1,
    ADD COLUMN preference_kind text NOT NULL DEFAULT 'inferred'
        CHECK (preference_kind IN ('inferred','explicit','temporary')),
    ADD COLUMN conversation_id text,
    ADD COLUMN purposes text[] NOT NULL DEFAULT '{conversation}',
    ADD COLUMN valid_from timestamptz,
    ADD COLUMN valid_until timestamptz,
    ADD COLUMN support_count integer NOT NULL DEFAULT 0;

ALTER TABLE companion.growth_inputs ADD COLUMN skip_reason text;
ALTER TABLE companion.organize_state ADD COLUMN runtime_generation text;

CREATE TABLE companion.habit_revisions (
    rule_id text NOT NULL REFERENCES companion.style_rules(id),
    revision bigint NOT NULL,
    semantic_revision bigint NOT NULL,
    action text NOT NULL,
    snapshot jsonb NOT NULL,
    source_refs jsonb NOT NULL,
    proposal_id text,
    attempt_id text,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (rule_id, revision)
);

-- A support is an original independent event, not an output or an exposure.
CREATE TABLE companion.habit_supports (
    rule_id text NOT NULL REFERENCES companion.style_rules(id),
    event_id text NOT NULL,
    source_ids text[] NOT NULL,
    source_refs jsonb NOT NULL,
    proposal_id text NOT NULL,
    PRIMARY KEY (rule_id, event_id)
);
CREATE INDEX habit_support_source ON companion.habit_supports USING gin(source_ids);
CREATE INDEX habit_support_event ON companion.habit_supports(event_id,proposal_id,rule_id);

-- A proposal whose identity cannot be resolved is retained for review and is
-- never silently turned into a second active rule.
CREATE TABLE companion.habit_unresolved (
    proposal_id text PRIMARY KEY, actor_id text NOT NULL,
    person_id text, conversation_id text,
    scope_ids text[] NOT NULL, source_refs jsonb NOT NULL,
    proposal jsonb NOT NULL, reason text NOT NULL,
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','dismissed')),
    revision bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE companion.learning_reflections (
    proposal_id text PRIMARY KEY, actor_id text NOT NULL REFERENCES companion.actors(actor_id),
    principal_id text NOT NULL, person_id text, conversation_id text,
    revision bigint NOT NULL DEFAULT 1, content text NOT NULL,
    episode_ids text[] NOT NULL, support_ids text[] NOT NULL,
    source_refs jsonb NOT NULL, scope_ids text[] NOT NULL,
    conditions jsonb NOT NULL, counterexamples jsonb NOT NULL,
    occurred_at timestamptz NOT NULL, attempt_id text NOT NULL,
    state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','invalidated')),
    reason text, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX learning_reflection_actor ON companion.learning_reflections(actor_id,occurred_at DESC);
CREATE INDEX learning_reflection_sources ON companion.learning_reflections USING gin(source_refs);

CREATE TABLE companion.growth_material_failures (
    actor_id text NOT NULL, scope_key text NOT NULL, episode_id text NOT NULL,
    episode_revision bigint NOT NULL, failure_key text NOT NULL,
    runtime_generation text NOT NULL,
    error_code text NOT NULL, details jsonb NOT NULL, context jsonb NOT NULL,
    state text NOT NULL DEFAULT 'blocked' CHECK(state IN ('blocked','released','superseded')),
    observation_revision bigint NOT NULL DEFAULT 1,
    retry_count integer NOT NULL DEFAULT 1, due_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(runtime_generation,actor_id,scope_key,episode_id,episode_revision)
);

CREATE TABLE companion.expression_examples (
    id text PRIMARY KEY, rule_id text NOT NULL REFERENCES companion.style_rules(id),
    rule_revision bigint NOT NULL, semantic_revision bigint NOT NULL,
    actor_id text NOT NULL, person_id text, conversation_id text NOT NULL,
    turn_id text NOT NULL, expression_id text NOT NULL,
    input_message_ids jsonb NOT NULL, output_message_ids jsonb NOT NULL,
    scope_ids text[] NOT NULL, source_refs jsonb NOT NULL,
    state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','rejected','invalidated')),
    revision bigint NOT NULL DEFAULT 1, reason text,
    created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE(rule_id,expression_id)
);
CREATE INDEX expression_examples_scene ON companion.expression_examples(actor_id,conversation_id,person_id,state);
CREATE INDEX expression_examples_sources ON companion.expression_examples USING gin(source_refs);

CREATE TABLE companion.expression_example_usage (
    example_id text NOT NULL REFERENCES companion.expression_examples(id),
    usage_id text NOT NULL, turn_id text, outcome text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(example_id,usage_id,outcome)
);

-- A delivery buffer, rebuildable from the authoritative Growth rows.
CREATE TABLE companion.growth_statistics_outbox (
    runtime_generation text NOT NULL, event_id text NOT NULL, revision bigint NOT NULL, event jsonb NOT NULL,
    delivered boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(runtime_generation,event_id,revision)
);
CREATE INDEX growth_statistics_pending ON companion.growth_statistics_outbox(created_at,event_id,revision)
    WHERE NOT delivered;
