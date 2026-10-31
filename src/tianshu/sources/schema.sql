CREATE SCHEMA sources;
CREATE TABLE sources.originals (
    source_id text PRIMARY KEY,
    source_key text UNIQUE NOT NULL,
    owner_id text NOT NULL,
    person_id text,
    actor_id text,
    head_revision bigint NOT NULL,
    intake_epoch bigint NOT NULL DEFAULT 1,
    state text NOT NULL DEFAULT 'active',
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sources.versions (
    source_id text NOT NULL REFERENCES sources.originals,
    revision bigint NOT NULL,
    intake_epoch bigint NOT NULL,
    scope_ids text[] NOT NULL,
    scope_epochs jsonb NOT NULL,
    audience text[] NOT NULL,
    origin_kind text NOT NULL,
    evidence_unit text NOT NULL,
    content text,
    media jsonb,
    locator jsonb NOT NULL,
    coverage jsonb NOT NULL,
    occurred_at timestamptz NOT NULL,
    disclosure boolean NOT NULL DEFAULT false,
    state text NOT NULL DEFAULT 'active',
    PRIMARY KEY(source_id,revision)
);
CREATE INDEX sources_scope ON sources.versions USING gin(scope_ids);
CREATE INDEX sources_evidence_unit ON sources.versions(evidence_unit);
CREATE TABLE sources.supports (
    source_id text NOT NULL,
    revision bigint NOT NULL,
    group_id text NOT NULL,
    support_source_id text NOT NULL,
    support_revision bigint NOT NULL,
    support_locator jsonb NOT NULL DEFAULT '{}',
    support_key text NOT NULL,
    PRIMARY KEY(source_id,revision,group_id,support_key),
    FOREIGN KEY(source_id,revision) REFERENCES sources.versions
);
CREATE INDEX supports_reverse ON sources.supports(support_source_id,support_revision);
CREATE TABLE sources.forgotten_fragments (
    source_id text NOT NULL,
    revision bigint NOT NULL,
    start_position bigint NOT NULL,
    end_position bigint NOT NULL,
    CHECK(start_position>=0 AND end_position>start_position),
    PRIMARY KEY(source_id,revision,start_position,end_position)
);
CREATE TABLE sources.suppression (
    source_key text PRIMARY KEY,
    source_id text NOT NULL,
    intake_epoch bigint NOT NULL,
    forgotten_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sources.fragment_barriers (
    source_key text PRIMARY KEY,
    revision bigint NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE sources.changes (
    sequence bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id text NOT NULL,
    revision bigint NOT NULL,
    kind text NOT NULL,
    occurred_at timestamptz NOT NULL DEFAULT now()
);
