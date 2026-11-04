-- An original event revision is immutable even when a later attachment adds
-- a new Episode semantic revision.
ALTER TABLE memory.episodes ADD COLUMN event_revision bigint;
ALTER TABLE memory.episodes ADD COLUMN event_content_hash text;
ALTER TABLE memory.episodes ADD COLUMN supplement_refs jsonb NOT NULL DEFAULT '[]';
ALTER TABLE memory.episodes ADD COLUMN consolidation_authority jsonb NOT NULL DEFAULT '{}';
UPDATE memory.episodes SET event_revision=revision,event_content_hash=content_hash;
UPDATE memory.episodes e SET consolidation_authority=s.authority_bound
    FROM memory.consolidation_state s WHERE e.consolidation_partition_id=s.partition_id;
ALTER TABLE memory.episodes ALTER COLUMN event_revision SET NOT NULL;
ALTER TABLE memory.episodes ALTER COLUMN event_content_hash SET NOT NULL;

-- Semantic snapshots contain no extraction progress or derived index state.
CREATE TABLE memory.episode_revisions (
    episode_id text NOT NULL REFERENCES memory.episodes,
    revision bigint NOT NULL,
    event_id text NOT NULL,
    actor_id text NOT NULL,
    person_id text,
    conversation_id text,
    origin_kind text NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz,
    summary text NOT NULL,
    source_refs jsonb NOT NULL,
    supplement_refs jsonb NOT NULL DEFAULT '[]',
    scope_ids text[] NOT NULL,
    actual_result jsonb,
    coverage jsonb NOT NULL,
    matter_id text,
    speaker_id text,
    semantic_fingerprint text,
    active boolean NOT NULL DEFAULT true,
    PRIMARY KEY (episode_id, revision)
);
CREATE INDEX episode_revisions_known ON memory.episode_revisions(episode_id, recorded_at, revision);
CREATE INDEX episode_revisions_sources ON memory.episode_revisions USING gin(source_refs);
-- Only the current version is provable on upgrade. A NULL recorded_at remains
-- unknown, rather than inventing the date of a historical observation.
INSERT INTO memory.episode_revisions
    (episode_id,revision,event_id,actor_id,person_id,conversation_id,origin_kind,occurred_at,
     recorded_at,summary,source_refs,supplement_refs,scope_ids,actual_result,coverage,matter_id,speaker_id,
     semantic_fingerprint,active)
SELECT episode_id,revision,event_id,actor_id,person_id,conversation_id,origin_kind,occurred_at,
       recorded_at,summary,source_refs,supplement_refs,scope_ids,actual_result,coverage,matter_id,speaker_id,
       semantic_fingerprint,active FROM memory.episodes;

CREATE TABLE memory.episode_supplements (
    episode_id text NOT NULL REFERENCES memory.episodes,
    event_id text NOT NULL,
    event_revision bigint NOT NULL,
    part_index integer NOT NULL CHECK(part_index >= 0),
    supplement_revision bigint NOT NULL CHECK(supplement_revision > 0),
    original_ref jsonb NOT NULL,
    supplement_ref jsonb NOT NULL,
    episode_revision bigint NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(episode_id,event_revision,part_index,supplement_revision)
);
CREATE INDEX episode_supplement_original ON memory.episode_supplements USING gin(original_ref);
CREATE INDEX episode_supplement_source ON memory.episode_supplements USING gin(supplement_ref);

CREATE TABLE memory.group_term_uses (
    actor_id text NOT NULL,
    conversation_id text NOT NULL,
    event_id text NOT NULL,
    claim_id text NOT NULL DEFAULT '',
    revision bigint NOT NULL DEFAULT 0,
    outcome text NOT NULL CHECK(outcome IN ('no_match','matched','explained','used')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(actor_id,conversation_id,event_id,claim_id,revision,outcome)
);

CREATE TABLE memory.consolidation_compilations (
    batch_id text NOT NULL REFERENCES memory.consolidation_batches,
    attempt integer NOT NULL,
    step_id text,
    compiler_version text NOT NULL,
    schema_version text NOT NULL,
    proposal jsonb NOT NULL,
    alias_map jsonb NOT NULL,
    source_snapshot jsonb NOT NULL,
    frozen_input_digest text NOT NULL,
    proposal_digest text NOT NULL,
    selected boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (batch_id, attempt)
);
CREATE UNIQUE INDEX consolidation_compilation_selected
    ON memory.consolidation_compilations(batch_id) WHERE selected;

ALTER TABLE memory.consolidation_batches DROP CONSTRAINT consolidation_batches_status_check;
ALTER TABLE memory.consolidation_batches ADD CONSTRAINT consolidation_batches_status_check
    CHECK(status IN ('prepared','waiting_model','ready','quarantined','applied','stale','failed','revoked','superseded'));
ALTER TABLE memory.consolidation_batches ADD COLUMN observation_revision bigint NOT NULL DEFAULT 0;
ALTER TABLE memory.consolidation_attempts ADD COLUMN recovery_lineage_id text;
ALTER TABLE memory.consolidation_attempts ADD COLUMN recovery_policy_state text;
CREATE TABLE memory.recovery_due_claims (
    lineage_id text PRIMARY KEY,
    claim_token text NOT NULL,
    current_version text NOT NULL,
    expires_at timestamptz NOT NULL
);
CREATE TABLE memory.statistics_outbox (
    batch_id text PRIMARY KEY REFERENCES memory.consolidation_batches,
    revision bigint NOT NULL,
    event jsonb NOT NULL,
    published_revision bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memory.consolidation_reorganizations (
    root_batch_id text PRIMARY KEY REFERENCES memory.consolidation_batches,
    partition_id text NOT NULL,
    context_remaining integer NOT NULL CHECK(context_remaining >= 0),
    repair_remaining integer NOT NULL CHECK(repair_remaining >= 0),
    retry_remaining integer NOT NULL CHECK(retry_remaining >= 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE memory.consolidation_reorganization_parts (
    parent_batch_id text NOT NULL REFERENCES memory.consolidation_batches,
    root_batch_id text NOT NULL REFERENCES memory.consolidation_reorganizations,
    ordinal integer NOT NULL,
    episodes jsonb NOT NULL,
    child_batch_id text UNIQUE REFERENCES memory.consolidation_batches,
    state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','active','applied','superseded','failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY(parent_batch_id,ordinal)
);
CREATE INDEX consolidation_reorganization_pending
    ON memory.consolidation_reorganization_parts(root_batch_id,created_at,ordinal) WHERE state='pending';

-- The new installation's learning history is owned exclusively by Companion.
DELETE FROM memory.semantic_links WHERE from_kind='reflection' OR to_kind='reflection';
ALTER TABLE memory.semantic_links DROP CONSTRAINT semantic_links_from_kind_check;
ALTER TABLE memory.semantic_links DROP CONSTRAINT semantic_links_to_kind_check;
ALTER TABLE memory.semantic_links ADD CONSTRAINT semantic_links_from_kind_check
    CHECK(from_kind IN ('claim','hypothesis','matter','episode'));
ALTER TABLE memory.semantic_links ADD CONSTRAINT semantic_links_to_kind_check
    CHECK(to_kind IN ('claim','hypothesis','matter','episode'));
DELETE FROM memory.index_scan WHERE kind='reflection';
DROP TABLE memory.reflections;
