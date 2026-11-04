-- RB-15 is installed on the new empty business baseline after Life's schema
-- and earlier Life migrations. Legacy business rows are not migrated.
ALTER TABLE life.diaries DROP CONSTRAINT diaries_actor_id_day_key;
ALTER TABLE life.diaries ADD COLUMN scene_key text NOT NULL;
ALTER TABLE life.diaries ADD COLUMN review_state text;
ALTER TABLE life.diaries ADD COLUMN review_note text;
ALTER TABLE life.diaries ADD COLUMN review_content_revision bigint;
ALTER TABLE life.diaries ADD CONSTRAINT diaries_actor_day_scene_key
    UNIQUE (actor_id, day, scene_key);
