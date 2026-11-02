ALTER TABLE life.plan_watermarks
    ADD COLUMN last_error text,
    ADD COLUMN failed_generation_key text,
    ADD COLUMN repair_count integer NOT NULL DEFAULT 0,
    ADD COLUMN repair_due_at timestamptz,
    ADD COLUMN review_after timestamptz;
