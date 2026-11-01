-- One fixed subjective-reflection batch survives new events and worker restarts.
-- The immutable model input remains owned by conversation.generation_requests.
ALTER TABLE companion.organize_state
    ADD COLUMN active_batch jsonb,
    ADD COLUMN blocked_material jsonb;
