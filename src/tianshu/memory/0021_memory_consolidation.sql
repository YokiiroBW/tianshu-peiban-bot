-- Incremental upgrade: existing object IDs and unknown recording times are retained.
ALTER TABLE memory.claim_revisions
    ADD COLUMN recorded_at timestamptz,
    ADD COLUMN change_kind text NOT NULL DEFAULT 'legacy'
        CHECK (change_kind IN ('create','reinforce','correct','transition','retract','legacy')),
    ADD COLUMN memory_kind text NOT NULL DEFAULT 'fact' CHECK (memory_kind IN ('fact','hypothesis')),
    ADD COLUMN current_status text NOT NULL DEFAULT 'current'
        CHECK (current_status IN ('current','history_only','retracted')),
    ADD COLUMN semantic_fingerprint text,
    ADD COLUMN derivation_refs jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN counterexample_refs jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN change_reason text NOT NULL DEFAULT '';
CREATE INDEX claim_revision_recording ON memory.claim_revisions(claim_id,recorded_at,revision);
CREATE INDEX claim_derivations ON memory.claim_revisions USING gin(derivation_refs);

ALTER TABLE memory.episodes
    ADD COLUMN recorded_at timestamptz,
    ADD COLUMN consolidation_partition_id text,
    ADD COLUMN speaker_id text,
    ADD COLUMN lexical text NOT NULL DEFAULT '',
    ADD COLUMN search_vector tsvector GENERATED ALWAYS AS(to_tsvector('simple',lexical)) STORED,
    ADD COLUMN embedding vector(1024),
    ADD COLUMN embedding_profile text,
    ADD COLUMN semantic_fingerprint text;
ALTER TABLE memory.matters
    ADD COLUMN recorded_at timestamptz,
    ADD COLUMN change_kind text NOT NULL DEFAULT 'legacy',
    ADD COLUMN change_reason text NOT NULL DEFAULT '',
    ADD COLUMN completion_basis jsonb,
    ADD COLUMN support_groups jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN scope_groups jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN derivation_refs jsonb NOT NULL DEFAULT '[]',
    ADD COLUMN current_status text NOT NULL DEFAULT 'current',
    ADD COLUMN active boolean NOT NULL DEFAULT true,
    ADD COLUMN lexical text NOT NULL DEFAULT '',
    ADD COLUMN search_vector tsvector GENERATED ALWAYS AS(to_tsvector('simple',lexical)) STORED,
    ADD COLUMN embedding vector(1024),
    ADD COLUMN embedding_profile text,
    ADD COLUMN semantic_fingerprint text;
ALTER TABLE memory.reflections
    ADD COLUMN recorded_at timestamptz,
    ADD COLUMN lexical text NOT NULL DEFAULT '',
    ADD COLUMN search_vector tsvector GENERATED ALWAYS AS(to_tsvector('simple',lexical)) STORED,
    ADD COLUMN embedding vector(1024),
    ADD COLUMN embedding_profile text,
    ADD COLUMN semantic_fingerprint text;
UPDATE memory.matters SET support_groups=jsonb_build_array(source_refs),
    scope_groups=jsonb_build_array(to_jsonb(scope_ids));

CREATE TABLE memory.consolidation_state (
    partition_id text PRIMARY KEY,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    conversation_id text,
    scope_key text NOT NULL,
    authority_bound jsonb NOT NULL,
    dirty_generation bigint NOT NULL DEFAULT 0,
    first_pending_at timestamptz,
    last_activity_at timestamptz,
    wake_due_at timestamptz,
    active_batch_id text,
    lease_owner text,
    lease_until timestamptz,
    last_error text,
    budget_failure jsonb,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX consolidation_due ON memory.consolidation_state(wake_due_at,partition_id);
CREATE INDEX episodes_pending_partition ON memory.episodes(actor_id,consolidation_partition_id,sequence)
    WHERE active AND extraction_state='pending';
CREATE TABLE memory.consolidation_batches (
    batch_id text PRIMARY KEY,
    partition_id text NOT NULL REFERENCES memory.consolidation_state,
    schema_version text NOT NULL,
    status text NOT NULL CHECK(status IN ('prepared','waiting_model','ready','applied','stale','failed','revoked')),
    manifest jsonb NOT NULL,
    read_set jsonb NOT NULL DEFAULT '[]',
    dependency_refs jsonb NOT NULL DEFAULT '[]',
    context_fingerprint text NOT NULL,
    step_ids text[] NOT NULL DEFAULT '{}',
    proposal_digest text,
    commit_refs jsonb NOT NULL DEFAULT '{}',
    started_at timestamptz NOT NULL DEFAULT now(),
    committed_at timestamptz,
    error_code text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX consolidation_one_active ON memory.consolidation_batches(partition_id)
    WHERE status IN ('prepared','waiting_model','ready');
CREATE INDEX consolidation_status ON memory.consolidation_batches(partition_id,status,started_at);
CREATE INDEX consolidation_dependencies ON memory.consolidation_batches USING gin(dependency_refs);

CREATE TABLE memory.semantic_links (
    link_id text PRIMARY KEY,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    from_kind text NOT NULL CHECK(from_kind IN ('claim','hypothesis','matter','episode','reflection')),
    from_id text NOT NULL,
    from_revision bigint NOT NULL,
    to_kind text NOT NULL CHECK(to_kind IN ('claim','hypothesis','matter','episode','reflection')),
    to_id text NOT NULL,
    to_revision bigint NOT NULL,
    relation_kind text NOT NULL CHECK(relation_kind IN
        ('about_matter','same_event','supersedes','contradicts','supports','counterexample')),
    valid_from timestamptz,
    valid_until timestamptz,
    recorded_at timestamptz,
    support_groups jsonb NOT NULL,
    scope_groups jsonb NOT NULL,
    derivation_refs jsonb NOT NULL DEFAULT '[]',
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','unsupported')),
    current_status text NOT NULL DEFAULT 'current' CHECK(current_status IN ('current','history_only')),
    CHECK(valid_from IS NULL OR valid_until IS NULL OR valid_until>valid_from)
);
CREATE INDEX semantic_links_from ON memory.semantic_links(actor_id,from_kind,from_id,from_revision);
CREATE INDEX semantic_links_to ON memory.semantic_links(actor_id,to_kind,to_id,to_revision);
CREATE INDEX semantic_links_sources ON memory.semantic_links USING gin(source_refs);
CREATE INDEX semantic_links_scopes ON memory.semantic_links USING gin(scope_ids);
CREATE INDEX semantic_links_time ON memory.semantic_links(recorded_at,link_id);

CREATE TABLE memory.matter_revisions (
    matter_id text NOT NULL REFERENCES memory.matters,
    revision bigint NOT NULL,
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    subject_id text,
    title text NOT NULL,
    state text NOT NULL,
    due_at timestamptz,
    summary text NOT NULL,
    conditions jsonb NOT NULL,
    completion_basis jsonb,
    observed_at timestamptz NOT NULL,
    recorded_at timestamptz,
    change_kind text NOT NULL,
    change_reason text NOT NULL DEFAULT '',
    source_refs jsonb NOT NULL,
    support_groups jsonb NOT NULL,
    scope_groups jsonb NOT NULL,
    derivation_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    current_status text NOT NULL DEFAULT 'current',
    interpretation boolean NOT NULL,
    active boolean NOT NULL DEFAULT true,
    semantic_fingerprint text,
    PRIMARY KEY(matter_id,revision)
);
INSERT INTO memory.matter_revisions(matter_id,revision,actor_id,principal_id,subject_id,title,
    state,due_at,summary,conditions,completion_basis,observed_at,recorded_at,change_kind,
    change_reason,source_refs,support_groups,scope_groups,derivation_refs,scope_ids,
    current_status,interpretation,active,semantic_fingerprint)
SELECT matter_id,revision,actor_id,principal_id,subject_id,title,state,due_at,summary,
    conditions,completion_basis,observed_at,NULL,'legacy','',source_refs,support_groups,
    scope_groups,derivation_refs,scope_ids,current_status,interpretation,active,semantic_fingerprint
FROM memory.matters;
CREATE INDEX matter_history_time ON memory.matter_revisions(matter_id,recorded_at,revision);
CREATE INDEX matter_history_sources ON memory.matter_revisions USING gin(source_refs);

CREATE INDEX episodes_lexical ON memory.episodes USING gin(search_vector);
CREATE INDEX matters_lexical ON memory.matters USING gin(search_vector);
CREATE INDEX reflections_lexical ON memory.reflections USING gin(search_vector);
CREATE INDEX episodes_unindexed ON memory.episodes(episode_id) WHERE active AND lexical='';
CREATE INDEX matters_unindexed ON memory.matters(matter_id) WHERE active AND lexical='';
CREATE INDEX reflections_unindexed ON memory.reflections(proposal_id) WHERE active AND lexical='';
CREATE INDEX episodes_embedding ON memory.episodes USING hnsw(embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX matters_embedding ON memory.matters USING hnsw(embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
CREATE INDEX reflections_embedding ON memory.reflections USING hnsw(embedding vector_cosine_ops) WHERE embedding IS NOT NULL;
-- Old contradiction arrays remain audit data. No semantic links are inferred from unequal values.
-- Preserve explicit, sourced legacy associations; unequal values are never evidence.
INSERT INTO memory.semantic_links(link_id,actor_id,principal_id,from_kind,from_id,from_revision,
    to_kind,to_id,to_revision,relation_kind,recorded_at,support_groups,scope_groups,
    derivation_refs,source_refs,scope_ids)
SELECT 'legacy_about_'||md5(c.claim_id||':'||r.revision||':'||m.matter_id),
    c.actor_id,c.principal_id,CASE WHEN r.memory_kind='hypothesis' THEN 'hypothesis' ELSE 'claim' END,
    c.claim_id,r.revision,'matter',m.matter_id,m.revision,'about_matter',NULL,
    supports.groups,supports.scopes,'[]',supports.refs,r.scope_ids||m.scope_ids
FROM (SELECT matter_id,claim_id FROM memory.matter_claims UNION
      SELECT matter_id,claim_id FROM memory.claim_revisions WHERE matter_id IS NOT NULL) old
JOIN memory.claims c ON c.claim_id=old.claim_id
JOIN memory.claim_revisions r ON r.claim_id=c.claim_id AND r.revision=c.current_revision
JOIN memory.matters m ON m.matter_id=old.matter_id AND m.actor_id=c.actor_id
CROSS JOIN LATERAL (
    SELECT jsonb_agg(g.refs) AS groups,
           jsonb_agg(to_jsonb(r.scope_ids||m.scope_ids)) AS scopes,
           (SELECT COALESCE(jsonb_agg(DISTINCT element),'[]') FROM
               jsonb_array_elements(r.source_refs||m.source_refs) element) AS refs
    FROM (SELECT cg.value||mg.value AS refs FROM jsonb_array_elements(r.support_groups) cg
          CROSS JOIN jsonb_array_elements(m.support_groups) mg
          WHERE sources.any_support_usable(jsonb_build_array(cg.value||mg.value),r.scope_ids||m.scope_ids)) g
) supports
WHERE r.state='active' AND m.active AND supports.groups IS NOT NULL
ON CONFLICT DO NOTHING;
-- matter_claims is rebuilt exclusively from semantic about_matter links.
TRUNCATE memory.matter_claims;
INSERT INTO memory.matter_claims(matter_id,claim_id)
SELECT to_id,from_id FROM memory.semantic_links WHERE relation_kind='about_matter';
