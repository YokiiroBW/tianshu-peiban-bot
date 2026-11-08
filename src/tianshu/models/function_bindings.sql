-- Preserve every original author configuration before collapsing the old UI's
-- three independent selections. Interactive was the default editing tab; its
-- explicit selection wins, then background, then due. Actor scope stays intact.
LOCK TABLE models.bindings IN ACCESS EXCLUSIVE MODE;
CREATE TABLE models.binding_class_history AS
    SELECT bindings.*, now() AS consolidated_at FROM models.bindings;

-- Keep synchronized aliases for existing open pages and previous-image rollback.
-- They no longer represent independent choices. New writes use the same rule.
WITH selected AS (
    SELECT DISTINCT ON (function, actor_id) function, actor_id, route_id, fallback_routes,
           max(revision) OVER (PARTITION BY function, actor_id) + 1 AS revision
    FROM models.binding_class_history
    ORDER BY function, actor_id,
             CASE service_class WHEN 'interactive' THEN 0 WHEN 'elastic' THEN 1 ELSE 2 END
)
INSERT INTO models.bindings(function, service_class, actor_id, route_id, fallback_routes, revision)
SELECT function, kind, actor_id, route_id, fallback_routes, revision
FROM selected CROSS JOIN unnest(ARRAY['interactive','due','elastic']) AS classes(kind)
ON CONFLICT (function, service_class, actor_id) DO UPDATE
SET route_id=excluded.route_id, fallback_routes=excluded.fallback_routes, revision=excluded.revision;

ALTER TABLE models.bindings ALTER COLUMN service_class SET DEFAULT 'interactive';
