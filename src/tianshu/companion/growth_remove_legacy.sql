-- Remove the retired pre-rebuild inventory, not current inputs or attempts.
DROP TABLE IF EXISTS companion.growth_legacy_requests;

ALTER TABLE companion.organize_state
    DROP COLUMN IF EXISTS legacy_state,
    DROP COLUMN IF EXISTS legacy_request_cursor,
    DROP COLUMN IF EXISTS legacy_episode_cursor,
    DROP COLUMN IF EXISTS legacy_missing,
    DROP COLUMN IF EXISTS legacy_started_at;

CREATE INDEX IF NOT EXISTS habit_support_event ON companion.habit_supports(event_id,proposal_id,rule_id);
CREATE INDEX IF NOT EXISTS growth_input_episode_status ON companion.growth_inputs(actor_id,episode_id);
