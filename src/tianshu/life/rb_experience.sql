-- RB-15 is installed on the new empty business baseline after Life's schema
-- and earlier Life migrations. Legacy business rows are not migrated.
ALTER TABLE life.diaries DROP CONSTRAINT diaries_actor_id_day_key;
ALTER TABLE life.diaries ADD COLUMN scene_key text NOT NULL;
ALTER TABLE life.diaries ADD COLUMN review_state text;
ALTER TABLE life.diaries ADD COLUMN review_note text;
ALTER TABLE life.diaries ADD COLUMN review_content_revision bigint;
ALTER TABLE life.diaries ADD CONSTRAINT diaries_actor_day_scene_key
    UNIQUE (actor_id, day, scene_key);

-- Link each authored physical request to the shared recovery lineage. The
-- frozen recipe and Models ledger remain the domain's material/result records.
ALTER TABLE life.generation_recipes
    ADD COLUMN recovery_lineage_id text,
    ADD COLUMN recovery_owner text,
    ADD COLUMN recovery_material_id text,
    ADD COLUMN recovery_material_digest text,
    ADD COLUMN recovery_dependency text,
    ADD COLUMN recovery_version text,
    ADD COLUMN recovery_request_digest text,
    ADD COLUMN recovery_route text,
    ADD COLUMN recovery_probe_token text;

-- Delivery pointers only: immutable diary/chapter revisions remain the source.
CREATE TABLE life.experience_statistics_pending (
    kind text NOT NULL CHECK (kind IN ('diary', 'chapter')),
    item_id text NOT NULL,
    content_revision bigint NOT NULL,
    PRIMARY KEY (kind, item_id, content_revision)
);
