CREATE SCHEMA memory;
CREATE SEQUENCE memory.revision_sequence;
CREATE TABLE memory.model_steps (
    step_id text PRIMARY KEY,
    request jsonb NOT NULL,
    state text NOT NULL DEFAULT 'prepared',
    error_code text,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX model_steps_sources ON memory.model_steps USING gin((request->'source_refs'));
CREATE TABLE memory.episodes (
    episode_id text PRIMARY KEY,
    event_id text NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    person_id text,
    conversation_id text,
    revision bigint NOT NULL DEFAULT 1,
    sequence bigint NOT NULL DEFAULT nextval('memory.revision_sequence'),
    origin_kind text NOT NULL,
    occurred_at timestamptz NOT NULL,
    summary text NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    actual_result jsonb,
    coverage jsonb NOT NULL,
    matter_id text,
    action_id text,
    receipt_id text,
    content_hash text NOT NULL,
    extraction_state text NOT NULL DEFAULT 'pending',
    extraction_started_at timestamptz,
    extraction_cursor jsonb NOT NULL DEFAULT '{}',
    extraction_sources jsonb NOT NULL DEFAULT '[]',
    extraction_missing text[] NOT NULL DEFAULT '{}',
    active boolean NOT NULL DEFAULT true,
    UNIQUE(actor_id,event_id)
);
CREATE INDEX episodes_actor_sequence ON memory.episodes(actor_id,sequence);
CREATE INDEX episodes_actor_time ON memory.episodes(actor_id,occurred_at,sequence);
CREATE INDEX episodes_sources ON memory.episodes USING gin(source_refs);
CREATE TABLE memory.claims (
    claim_id text PRIMARY KEY,
    current_revision bigint NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL
);
CREATE TABLE memory.claim_revisions (
    claim_id text NOT NULL REFERENCES memory.claims,
    revision bigint NOT NULL,
    speaker_id text,
    subject_id text NOT NULL,
    predicate text NOT NULL,
    value jsonb NOT NULL,
    assertion text NOT NULL,
    polarity boolean NOT NULL,
    conditions jsonb NOT NULL,
    valid_from timestamptz,
    valid_until timestamptz,
    observed_at timestamptz NOT NULL,
    state text NOT NULL,
    source_refs jsonb NOT NULL,
    support_groups jsonb NOT NULL,
    scope_groups jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    matter_id text,
    supersedes text[] NOT NULL DEFAULT '{}',
    contradictions text[] NOT NULL DEFAULT '{}',
    evidence_units text[] NOT NULL,
    content text NOT NULL,
    lexical text NOT NULL,
    search_vector tsvector GENERATED ALWAYS AS(to_tsvector('simple',lexical)) STORED,
    embedding vector(1024),
    embedding_profile text,
    PRIMARY KEY(claim_id,revision)
);
CREATE INDEX claims_lexical ON memory.claim_revisions USING gin(search_vector);
CREATE INDEX claims_scopes ON memory.claim_revisions USING gin(scope_ids);
CREATE INDEX claims_sources ON memory.claim_revisions USING gin(source_refs);
CREATE INDEX claims_subject_current ON memory.claim_revisions(subject_id,predicate,state,observed_at DESC);
CREATE INDEX claims_embedding ON memory.claim_revisions USING hnsw(embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE TABLE memory.matters (
    matter_id text PRIMARY KEY,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    subject_id text,
    title text NOT NULL,
    state text NOT NULL,
    due_at timestamptz,
    summary text NOT NULL,
    conditions jsonb NOT NULL DEFAULT '{}',
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    observed_at timestamptz NOT NULL,
    revision bigint NOT NULL DEFAULT 1,
    interpretation boolean NOT NULL DEFAULT false
);
CREATE INDEX matters_actor_current ON memory.matters(actor_id,state,due_at);
CREATE INDEX matters_sources ON memory.matters USING gin(source_refs);
CREATE TABLE memory.matter_claims (
    matter_id text NOT NULL REFERENCES memory.matters,
    claim_id text NOT NULL REFERENCES memory.claims,
    PRIMARY KEY(matter_id,claim_id)
);
CREATE TABLE memory.reflections (
    proposal_id text PRIMARY KEY,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    person_id text,
    episode_ids text[] NOT NULL,
    content text NOT NULL,
    kind text NOT NULL,
    occurred_at timestamptz NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    conditions jsonb NOT NULL,
    counterexamples text[] NOT NULL,
    interpretation boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1,
    active boolean NOT NULL DEFAULT true
);
CREATE INDEX reflections_sources ON memory.reflections USING gin(source_refs);
CREATE TABLE memory.relationships (
    actor_id text NOT NULL,
    person_id text NOT NULL,
    relationship_type text NOT NULL DEFAULT 'acquaintance',
    display_label text NOT NULL DEFAULT '认识的人',
    summary text NOT NULL DEFAULT '',
    frozen boolean NOT NULL DEFAULT false,
    freeze_revision bigint NOT NULL DEFAULT 1,
    frozen_at timestamptz,
    scope_ids text[] NOT NULL DEFAULT '{}',
    source_refs jsonb NOT NULL DEFAULT '[]',
    revision bigint NOT NULL DEFAULT 1,
    PRIMARY KEY(actor_id,person_id)
);
CREATE INDEX relationships_sources ON memory.relationships USING gin(source_refs);
CREATE TABLE memory.relationship_details (
    detail_id text PRIMARY KEY,
    actor_id text NOT NULL,
    person_id text NOT NULL,
    kind text NOT NULL CHECK(kind IN ('boundary','shared_experience','preference')),
    content text NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    explicit boolean NOT NULL,
    active boolean NOT NULL DEFAULT true,
    revision bigint NOT NULL DEFAULT 1
);
CREATE INDEX relationship_details_pair ON memory.relationship_details(actor_id,person_id,kind);
CREATE INDEX relationship_details_sources ON memory.relationship_details USING gin(source_refs);
