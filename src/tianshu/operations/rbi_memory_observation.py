"""Bounded SQL fragments for the existing read-only Memory chain probe.

The caller owns the connection, timeout, scheduling and any persisted cursor.
Each query uses existing primary keys and examines at most limit + 1 candidate
rows before inspecting JSON or joining another ledger. Pages are live keyset
observations, not a transactionally consistent whole-system snapshot.
"""

MAX_PAGE = 256

CURRENT_SQL = """
WITH candidates AS MATERIALIZED (
    SELECT partition_id, active_batch_id
    FROM memory.consolidation_state
    WHERE partition_id > %s
    ORDER BY partition_id LIMIT %s
)
SELECT c.partition_id, c.active_batch_id, b.status AS batch_status,
       b.error_code, b.retry_due_at, b.observation_revision,
       b.context_fingerprint, b.manifest->>'data_generation' AS data_generation,
       b.manifest ? 'remote_wait_unresolved' AS unresolved,
       b.manifest ? 'original_observation_only' AS original_only,
       b.manifest ? 'preparation_hold' AS preparation_hold,
       b.manifest->'remote_wait_unresolved'->>'reason' AS wait_reason,
       b.manifest->'remote_wait_unresolved'->>'retry_at' AS wait_retry_at,
       b.manifest->'remote_wait_unresolved'->>'dependency' AS wait_dependency,
       b.manifest->'remote_wait_unresolved'->>'stage' AS wait_stage,
       b.manifest->'remote_wait_unresolved'->'remote_rollback_confirmed' AS remote_rollback_confirmed,
       b.manifest->'remote_wait_unresolved'->'transaction_rollback_confirmed' AS transaction_rollback_confirmed
FROM candidates c
LEFT JOIN LATERAL (
    SELECT status, error_code, retry_due_at, observation_revision, context_fingerprint, manifest
    FROM memory.consolidation_batches WHERE batch_id = c.active_batch_id LIMIT 1
) b ON true
ORDER BY c.partition_id
"""

CURRENT_HOLDS_SQL = """
WITH candidates AS MATERIALIZED (
    SELECT batch_id, partition_id, status, error_code, retry_due_at,
           observation_revision, context_fingerprint, manifest
    FROM memory.consolidation_batches
    WHERE batch_id > %s
    ORDER BY batch_id LIMIT %s
)
SELECT batch_id, partition_id, status AS batch_status, error_code, retry_due_at,
       observation_revision, context_fingerprint, manifest->>'data_generation' AS data_generation,
       manifest ? 'remote_wait_unresolved' AS unresolved,
       manifest ? 'original_observation_only' AS original_only,
       manifest ? 'preparation_hold' AS preparation_hold,
       manifest->'remote_wait_unresolved'->>'reason' AS wait_reason,
       manifest->'remote_wait_unresolved'->>'retry_at' AS wait_retry_at,
       manifest->'remote_wait_unresolved'->>'dependency' AS wait_dependency,
       manifest->'remote_wait_unresolved'->>'stage' AS wait_stage,
       manifest->'remote_wait_unresolved'->'remote_rollback_confirmed' AS remote_rollback_confirmed,
       manifest->'remote_wait_unresolved'->'transaction_rollback_confirmed' AS transaction_rollback_confirmed
FROM candidates ORDER BY batch_id
"""

HISTORY_SQL = """
WITH candidates AS MATERIALIZED (
    SELECT batch_id, number, kind, model_attempt_id, status AS local_status,
           error_code AS local_error_code
    FROM memory.consolidation_attempts
    WHERE (batch_id, number) > (%s, %s)
    ORDER BY batch_id, number LIMIT %s
)
SELECT c.*, b.status AS batch_status, b.observation_revision,
       b.context_fingerprint, b.manifest->>'data_generation' AS data_generation,
       m.status AS model_status, m.dispatch AS model_dispatch,
       m.error_code AS model_error_code, m.finished_at AS model_finished_at,
       m.data_generation AS model_data_generation
FROM candidates c
LEFT JOIN LATERAL (
    SELECT status, observation_revision, context_fingerprint, manifest
    FROM memory.consolidation_batches WHERE batch_id = c.batch_id LIMIT 1
) b ON true
LEFT JOIN LATERAL (
    SELECT status, dispatch, error_code, finished_at, data_generation
    FROM models.attempts WHERE attempt_id = c.model_attempt_id LIMIT 1
) m ON true
ORDER BY c.batch_id, c.number
"""

TARGET_SQL = """
SELECT batch_id, partition_id, status AS batch_status, error_code, retry_due_at,
       observation_revision, context_fingerprint, manifest->>'data_generation' AS data_generation,
       manifest ? 'remote_wait_unresolved' AS unresolved,
       manifest ? 'original_observation_only' AS original_only,
       manifest ? 'preparation_hold' AS preparation_hold,
       manifest->'remote_wait_unresolved'->>'reason' AS wait_reason,
       manifest->'remote_wait_unresolved'->>'retry_at' AS wait_retry_at,
       manifest->'remote_wait_unresolved'->>'dependency' AS wait_dependency,
       manifest->'remote_wait_unresolved'->>'stage' AS wait_stage,
       manifest->'remote_wait_unresolved'->'remote_rollback_confirmed' AS remote_rollback_confirmed,
       manifest->'remote_wait_unresolved'->'transaction_rollback_confirmed' AS transaction_rollback_confirmed
FROM memory.consolidation_batches WHERE batch_id = %s
"""

TARGET_EPISODE_SQL = """
SELECT episode_id, revision, event_revision, sequence, active, extraction_state,
       extraction_missing, consolidation_partition_id,
       extraction_cursor->>'source_index' AS cursor_source_index,
       extraction_cursor->>'step' AS cursor_step,
       extraction_cursor->>'position' IS NOT NULL AS cursor_position_recorded
FROM memory.episodes WHERE episode_id = %s
"""

TARGET_BATCH_EPISODE_SQL = """
SELECT batch_id, status AS batch_status, context_fingerprint, observation_revision,
       manifest->>'data_generation' AS data_generation,
       COALESCE((manifest->'episodes') @> jsonb_build_array(jsonb_build_object(
           'episode_id', %s::text, 'revision', %s::bigint, 'sequence', %s::bigint)), false)
           AS exact_version_in_batch
FROM memory.consolidation_batches WHERE batch_id = %s
"""

TARGET_ATTEMPTS_SQL = """
WITH candidates AS MATERIALIZED (
    SELECT batch_id, number, kind, model_attempt_id, status AS local_status,
           error_code AS local_error_code
    FROM memory.consolidation_attempts
    WHERE batch_id = %s AND number > %s
    ORDER BY number LIMIT %s
)
SELECT c.*, m.status AS model_status, m.dispatch AS model_dispatch,
       m.error_code AS model_error_code, m.finished_at AS model_finished_at,
       m.data_generation AS model_data_generation
FROM candidates c
LEFT JOIN LATERAL (
    SELECT status, dispatch, error_code, finished_at, data_generation
    FROM models.attempts WHERE attempt_id = c.model_attempt_id LIMIT 1
) m ON true
ORDER BY c.number
"""


def _text(value, *, empty=False):
    if not isinstance(value, str) or len(value) > 200 or (not empty and not value):
        raise ValueError("invalid_memory_probe_cursor")
    if any(ord(char) < 32 for char in value):
        raise ValueError("invalid_memory_probe_cursor")
    return value


def _limit(value):
    if type(value) is not int or not 1 <= value <= MAX_PAGE:
        raise ValueError("invalid_memory_probe_limit")
    return value


def current_query(*, after="", limit=64):
    return CURRENT_SQL, (_text(after, empty=True), _limit(limit) + 1)


def current_holds_query(*, after="", limit=64):
    """Page batches too: isolated holds can have no active pointer or attempt."""
    return CURRENT_HOLDS_SQL, (_text(after, empty=True), _limit(limit) + 1)


def history_query(*, after=None, limit=64):
    after = after if after is not None else {"batch_id": "", "number": 0}
    if not isinstance(after, dict) or set(after) != {"batch_id", "number"}:
        raise ValueError("invalid_memory_probe_cursor")
    batch = _text(after["batch_id"], empty=True)
    number = after["number"]
    if type(number) is not int or number < 0:
        raise ValueError("invalid_memory_probe_cursor")
    return HISTORY_SQL, (batch, number, _limit(limit) + 1)


def target_query(batch_id):
    return TARGET_SQL, (_text(batch_id),)


def target_episode_query(episode_id):
    return TARGET_EPISODE_SQL, (_text(episode_id),)


def target_batch_episode_query(batch_id, episode_id, *, revision, sequence):
    if type(revision) is not int or revision < 1 or type(sequence) is not int or sequence < 1:
        raise ValueError("invalid_memory_probe_version")
    return TARGET_BATCH_EPISODE_SQL, (_text(episode_id), revision, sequence, _text(batch_id))


def target_attempts_query(batch_id, *, after=0, limit=64):
    if type(after) is not int or after < 0:
        raise ValueError("invalid_memory_probe_cursor")
    return TARGET_ATTEMPTS_SQL, (_text(batch_id), after, _limit(limit) + 1)


def page_result(scope, rows, *, after=None, limit=64, error_type=None, sqlstate=None):
    """Describe one candidate page without promoting it to global coverage."""
    _limit(limit)
    if scope not in {"current", "current_holds", "history", "target_attempts"}:
        raise ValueError("invalid_memory_probe_scope")
    if error_type is not None or rows is None:
        return {"items": None, "next_cursor": after,
                "coverage": {"scope": scope, "status": "unknown", "error_type": error_type,
                             "sqlstate": sqlstate,
                             "page_complete": None, "overall_complete": None}}
    visible = list(rows[:limit])
    has_more = len(rows) > limit
    if scope == "current":
        items = [dict(row) for row in visible if row.get("active_batch_id") is not None]
        last = visible[-1]["partition_id"] if visible else after
    elif scope == "current_holds":
        items = [dict(row) for row in visible if row.get("batch_status") == "quarantined"]
        last = visible[-1]["batch_id"] if visible else after
    elif scope == "history":
        # Missing Models rows remain unavailable; they are not proof that an
        # original physical attempt failed, succeeded, or was never submitted.
        items = [dict(row) for row in visible if row.get("model_status") in {"unknown", None}]
        last = ({"batch_id": visible[-1]["batch_id"], "number": visible[-1]["number"]}
                if visible else after)
    else:
        items = [dict(row) for row in visible]
        last = visible[-1]["number"] if visible else after
    return {"items": items, "next_cursor": last if has_more else None, "last_scanned": last,
            "coverage": {"scope": scope, "status": "sampled", "scanned": len(visible),
                         "returned": len(items), "page_complete": not has_more,
                         "overall_complete": None, "wrap_required": not has_more,
                         "consistency": "live_keyset_not_snapshot"}}


def target_result(row, *, error_type=None, sqlstate=None):
    if error_type is not None:
        return {"item": None, "coverage": {"scope": "target", "status": "unknown",
                         "error_type": error_type, "sqlstate": sqlstate, "overall_complete": None}}
    return {"item": dict(row) if row is not None else None,
            "coverage": {"scope": "target", "status": "found" if row is not None else "absent",
                         "overall_complete": None}}
