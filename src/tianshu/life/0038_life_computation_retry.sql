ALTER TABLE life.plan_watermarks ADD COLUMN computation_retry_count integer NOT NULL DEFAULT 0;
ALTER TABLE life.plan_generations ADD COLUMN previous_generation_key text;
ALTER TABLE life.diaries
    ADD COLUMN generation_retry_count integer NOT NULL DEFAULT 0,
    ADD COLUMN generation_retry_due_at timestamptz;
ALTER TABLE life.chapters
    ADD COLUMN generation_retry_count integer NOT NULL DEFAULT 0,
    ADD COLUMN generation_retry_due_at timestamptz;
-- Failure is not semantic review; a permanent failure blocks only its material version.
ALTER TABLE life.diary_days ADD COLUMN blocked_revision bigint NOT NULL DEFAULT 0;
-- Audit only. The existing domain pointers and native queues own all scheduling.
CREATE TABLE life.generation_recipes (
    generation_id text PRIMARY KEY,
    kind text NOT NULL CHECK(kind IN ('diary','chapter')),
    item_id text NOT NULL,
    previous_generation_id text,
    context jsonb NOT NULL,
    recipe jsonb,
    source_refs jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX generation_recipes_sources ON life.generation_recipes USING gin(source_refs);
