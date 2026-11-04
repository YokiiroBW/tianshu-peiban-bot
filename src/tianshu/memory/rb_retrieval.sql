-- Derived vectors belong to their embedding space and semantic object version.
-- A separate partition and HNSW index for each profile prevents even an ANN
-- traversal from comparing vectors from different models of the same dimension.
CREATE TABLE memory.search_vectors (
    generation text NOT NULL,
    profile_key text NOT NULL CHECK (profile_key ~ '^[0-9a-f]{64}$'),
    kind text NOT NULL CHECK (kind IN ('claim','hypothesis','matter','episode')),
    object_id text NOT NULL,
    revision bigint NOT NULL,
    content_hash text NOT NULL,
    embedding vector(1024) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (profile_key,generation,kind,object_id,revision)
) PARTITION BY LIST (profile_key);

CREATE INDEX search_vectors_hnsw ON memory.search_vectors USING hnsw (embedding vector_cosine_ops);

-- The existing index worker is the only creator. Identifiers are generated
-- from a SHA-256 profile, while the profile itself is quoted as a literal.
CREATE FUNCTION memory.ensure_embedding_space(space_key text) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE partition_name text;
BEGIN
    IF space_key !~ '^[0-9a-f]{64}$' THEN
        RAISE EXCEPTION 'invalid memory embedding profile';
    END IF;
    partition_name := 'search_vectors_' || substr(space_key, 1, 48);
    IF to_regclass(format('memory.%I', partition_name)) IS NOT NULL THEN
        RETURN;
    END IF;
    -- Partition creation locks the shared parent, so serialize creation across
    -- all spaces, not just competing writers of the same space.
    PERFORM pg_advisory_xact_lock(hashtextextended('memory.search_vectors:create', 0));
    IF to_regclass(format('memory.%I', partition_name)) IS NULL THEN
        EXECUTE format('CREATE TABLE memory.%I PARTITION OF memory.search_vectors FOR VALUES IN (%L)',
                       partition_name, space_key);
    END IF;
END $$;

-- Profile changes restart the bounded consistency cursor. They do not change
-- the old space or interrupt keyword and structured queries.
ALTER TABLE memory.index_scan ADD COLUMN profile_key text, ADD COLUMN generation text;
ALTER TABLE memory.index_jobs ADD COLUMN recovery_lineage_id text, ADD COLUMN recovery_dependency text,
    ADD COLUMN generation text NOT NULL,
    ADD COLUMN content_hash text NOT NULL;
ALTER TABLE memory.index_jobs DROP CONSTRAINT IF EXISTS index_jobs_kind_object_id_revision_profile_key_key;
ALTER TABLE memory.index_jobs ADD CONSTRAINT index_jobs_generation_identity
    UNIQUE(generation,kind,object_id,revision,profile_key,content_hash);
ALTER TABLE memory.index_attempts
    ADD COLUMN recovery_reserved boolean NOT NULL DEFAULT false,
    ADD COLUMN recovery_probe_token text,
    ADD COLUMN event_revision bigint NOT NULL DEFAULT 1;
CREATE TABLE memory.index_stats_cursor (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    generation text,
    after_index_id text NOT NULL DEFAULT '',
    after_number integer NOT NULL DEFAULT 0
);
INSERT INTO memory.index_stats_cursor(singleton) VALUES(true);

-- Companion is the sole owner of learning bodies and state. This projection
-- stores immutable references and search lexemes, never a second rule body.
CREATE TABLE memory.learning_index_refs (
    actor_id text NOT NULL,
    principal_id text NOT NULL,
    rule_id text NOT NULL,
    revision bigint NOT NULL CHECK (revision > 0),
    semantic_revision bigint NOT NULL CHECK (semantic_revision > 0),
    kind text NOT NULL,
    source_refs jsonb NOT NULL,
    scope_ids text[] NOT NULL,
    search_vector tsvector NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (actor_id,rule_id,revision)
);
CREATE INDEX learning_refs_search ON memory.learning_index_refs USING gin(search_vector);
CREATE INDEX learning_refs_actor_recorded ON memory.learning_index_refs(actor_id,recorded_at,rule_id,revision);

-- Source invalidation first makes the authoritative rows unreadable. The
-- existing asynchronous purge then clears the corresponding derived vectors.
CREATE FUNCTION memory.purge_search_vectors() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'claim_revisions' THEN
        DELETE FROM memory.search_vectors WHERE kind IN ('claim','hypothesis')
            AND object_id=OLD.claim_id AND revision=OLD.revision;
    ELSIF TG_TABLE_NAME = 'matter_revisions' THEN
        DELETE FROM memory.search_vectors WHERE kind='matter'
            AND object_id=OLD.matter_id AND revision=OLD.revision;
    ELSIF TG_TABLE_NAME = 'episodes' THEN
        DELETE FROM memory.search_vectors WHERE kind='episode' AND object_id=OLD.episode_id;
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER search_vectors_claim_purge AFTER UPDATE OF state ON memory.claim_revisions
    FOR EACH ROW WHEN (OLD.state='active' AND NEW.state<>'active') EXECUTE FUNCTION memory.purge_search_vectors();
CREATE TRIGGER search_vectors_matter_purge AFTER UPDATE OF active ON memory.matter_revisions
    FOR EACH ROW WHEN (OLD.active AND NOT NEW.active) EXECUTE FUNCTION memory.purge_search_vectors();
CREATE TRIGGER search_vectors_episode_purge AFTER UPDATE OF active ON memory.episodes
    FOR EACH ROW WHEN (OLD.active AND NOT NEW.active) EXECUTE FUNCTION memory.purge_search_vectors();

-- Supplement cleanup can revise a head in place. Its previous semantic vector
-- must not become visible again merely because the withdrawn refs were removed.
CREATE FUNCTION memory.invalidate_episode_vector_on_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.revision=NEW.revision AND
       (OLD.summary IS DISTINCT FROM NEW.summary OR OLD.supplement_refs IS DISTINCT FROM NEW.supplement_refs) THEN
        DELETE FROM memory.search_vectors WHERE kind='episode'
            AND object_id=OLD.episode_id AND revision=OLD.revision;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER search_vectors_episode_changed AFTER UPDATE OF summary,supplement_refs ON memory.episodes
    FOR EACH ROW EXECUTE FUNCTION memory.invalidate_episode_vector_on_change();
