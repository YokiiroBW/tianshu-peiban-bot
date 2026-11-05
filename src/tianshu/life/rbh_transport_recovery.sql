-- Support bounded reconciliation of only Life's transport-wait originals.
CREATE INDEX IF NOT EXISTS life_rbh_plan_transport_wait
    ON life.plan_watermarks(actor_id)
    WHERE generation_consumed AND reason IN ('planner_attempts_exhausted','planner_retry_wait')
      AND last_error='transport_not_sent';
CREATE INDEX IF NOT EXISTS life_rbh_plan_failed_lineage
    ON life.plan_generations(recovery_lineage_id,generation_key)
    WHERE recovery_state='failed';
CREATE INDEX IF NOT EXISTS life_rbh_authored_lineage
    ON life.generation_recipes(recovery_lineage_id,kind,generation_id)
    WHERE recovery_lineage_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS life_rbh_diary_transport_wait
    ON life.diaries(id) WHERE state IN ('failed','unknown')
      AND reason='transport_not_sent' AND NOT generation_cancel_requested;
CREATE INDEX IF NOT EXISTS life_rbh_chapter_transport_wait
    ON life.chapters(id) WHERE state IN ('failed','unknown')
      AND reason='transport_not_sent' AND NOT generation_cancel_requested;
