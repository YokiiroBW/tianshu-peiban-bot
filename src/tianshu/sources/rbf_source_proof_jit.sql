-- Limit this setting to source proof execution. PostgreSQL restores the caller's
-- setting on function exit; authority and dependency logic remain unchanged.
ALTER FUNCTION sources.check_groups(jsonb, text[]) SET jit = off;
