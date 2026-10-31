CREATE SCHEMA IF NOT EXISTS companion;
CREATE TABLE companion.persona_profiles (
    persona_id text PRIMARY KEY, name text NOT NULL, revision bigint NOT NULL DEFAULT 1,
    current_revision bigint NOT NULL DEFAULT 1, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE companion.persona_revisions (
    persona_id text NOT NULL REFERENCES companion.persona_profiles(persona_id),
    revision bigint NOT NULL, author_text text NOT NULL, tone text NOT NULL DEFAULT '',
    style text NOT NULL DEFAULT '', address text NOT NULL DEFAULT '', world text NOT NULL DEFAULT '',
    note text NOT NULL DEFAULT '', created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (persona_id, revision)
);
CREATE TABLE companion.actors (
    actor_id text PRIMARY KEY, name text NOT NULL, enabled boolean NOT NULL DEFAULT false,
    revision bigint NOT NULL DEFAULT 1, persona_id text REFERENCES companion.persona_profiles(persona_id),
    persona_revision bigint, model_function text NOT NULL DEFAULT 'chat',
    capabilities text[] NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(), deleted_at timestamptz,
    FOREIGN KEY(persona_id,persona_revision) REFERENCES companion.persona_revisions(persona_id,revision)
);
CREATE TABLE companion.affect_events (
    actor_id text NOT NULL REFERENCES companion.actors(actor_id), event_id text NOT NULL,
    event_revision bigint NOT NULL, scope_ids text[] NOT NULL, appraisal text NOT NULL,
    valence double precision NOT NULL CHECK(valence BETWEEN -1 AND 1),
    arousal double precision NOT NULL CHECK(arousal BETWEEN 0 AND 1),
    stress double precision NOT NULL CHECK(stress BETWEEN 0 AND 1),
    social_need double precision NOT NULL CHECK(social_need BETWEEN 0 AND 1),
    confidence double precision NOT NULL CHECK(confidence BETWEEN 0 AND 1),
    half_life_seconds integer NOT NULL CHECK(half_life_seconds BETWEEN 60 AND 604800),
    occurred_at timestamptz NOT NULL, source_refs jsonb NOT NULL,
    PRIMARY KEY(actor_id,event_id)
);
CREATE INDEX affect_scope_time ON companion.affect_events(actor_id,occurred_at DESC);
CREATE TABLE companion.style_rules (
    id text PRIMARY KEY, actor_id text NOT NULL REFERENCES companion.actors(actor_id),
    person_id text, revision bigint NOT NULL DEFAULT 1, kind text NOT NULL,
    title text NOT NULL, content text NOT NULL, conditions jsonb NOT NULL DEFAULT '{}',
    counterexamples jsonb NOT NULL DEFAULT '[]', episode_ids text[] NOT NULL,
    source_refs jsonb NOT NULL, scope_ids text[] NOT NULL,
    state text NOT NULL CHECK(state IN ('active','revoked','invalidated')),
    reason text, created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX style_actor_scope ON companion.style_rules(actor_id,person_id,state);
