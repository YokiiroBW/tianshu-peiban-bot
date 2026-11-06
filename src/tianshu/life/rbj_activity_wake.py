"""Prove old activity wake identities before handing them to Runtime."""

from uuid import UUID

from tianshu.conversation.codec import context_from_record, signature
from tianshu.conversation.generation import GenerationRequests
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import current_job_execution

from .rbi_activity_reconciliation import generation_on, recovery_due, timestamp


TASK = "life.activity_reconcile"


def payload(row, generation):
    return {"activity_id": row["id"], "action_id": row["action_id"], "data_generation": generation}


def wake_key(row):
    return "life-activity-observe:" + signature((row["id"], row["action_id"]))


def lower_bound(row):
    saved = (row.get("checkpoint_data") or {}).get("reconciliation") or {}
    same_action = isinstance(saved, dict) and saved.get("action_id") == row["action_id"]
    bound = timestamp(saved.get("not_before")) if same_action else None
    if same_action and saved.get("reason") == "resource_wait":
        bound = max(filter(None, (bound, timestamp(saved.get("due_at")))), default=None)
    return max(filter(None, (bound, recovery_due(row))), default=None)


def is_legacy(row, candidate):
    """The historical sequence key itself binds the payload to the Action."""
    args = candidate["args"]
    if not isinstance(args, dict) or set(args) != {"activity_id", "context", "sequence"}:
        return False
    sequence = args["sequence"]
    if args["activity_id"] != row["id"] or type(sequence) is not int or sequence < 0:
        return False
    key = candidate["key"]
    sequence_key = f"activity-reconcile:{row['action_id']}:{sequence}"
    startup_prefix = f"life-recover:activities:{row['id']}:{row['revision']}:"
    if key != sequence_key:
        if sequence != 0 or not key.startswith(startup_prefix):
            return False
        try:
            UUID(key[len(startup_prefix):])
        except (ValueError, AttributeError):
            return False
    frozen = (row.get("checkpoint_data") or {}).get("frozen_action") or {}
    if (not isinstance(frozen,dict) or frozen.get("action_id") != row["action_id"]
            or not frozen.get("context") or not row.get("context")):
        return False
    try:
        original = context_from_record(frozen["context"])
        GenerationRequests.observer_context(original, context_from_record(row["context"]))
        GenerationRequests.observer_context(original, context_from_record(args["context"]))
    except (AppError, KeyError, TypeError, ValueError):
        return False
    return original.actor_id == row["actor_id"]


async def candidates(connection, row, generation, *, job_id=None, after=0):
    return await (await connection.execute(
        "SELECT j.id,j.args,j.status,j.scheduled_at,k.key FROM procrastinate_jobs j "
        "JOIN runtime.job_keys k ON k.job_id=j.id AND k.cancelled_at IS NULL "
        "WHERE j.task_name=%s AND k.task=%s AND k.data_generation=%s AND k.subject_id=%s "
        "AND j.args->>'activity_id'=%s AND j.status IN ('todo','doing') "
        "AND (%s::bigint IS NULL OR j.id=%s) AND j.id>%s ORDER BY j.id,k.key LIMIT 32",
        (TASK, TASK, generation, row["actor_id"], row["id"], job_id, job_id, after))).fetchall()


async def scan_on(connection, row, generation, saved):
    """Rotate a durable, bounded page so covered far-future jobs cannot starve others."""
    cursor = saved.get("history_cursor", 0)
    cursor = cursor if type(cursor) is int and cursor >= 0 else 0
    found = await candidates(connection, row, generation, after=cursor)
    if not found and cursor:
        found = await candidates(connection, row, generation)
    return found, found[-1]["id"] if len(found) == 32 else 0


async def adopt_canonical_on(life, connection, row, generation, found):
    expected = payload(row, generation)
    seen = set()
    for item in found:
        if item["args"] == expected and item["id"] not in seen:
            seen.add(item["id"])
            await life.jobs.adopt_wake_on(connection, item["id"], expected_task=TASK,
                expected_generation=generation, expected_payload=expected, wake_key=wake_key(row))


async def cover_legacy_on(life, connection, row, generation, found, covering_job_id, *, only_job_id=None):
    """A different payload is covered only after Life checks its full envelope."""
    outcomes = []
    seen = set()
    for item in found:
        if (item["id"] == covering_job_id or (only_job_id is not None and item["id"] != only_job_id)
                or item["id"] in seen or not is_legacy(row, item)):
            continue
        seen.add(item["id"])
        result = await life.jobs.cover_wake_on(connection, item["id"], expected_task=TASK,
            expected_generation=generation, expected_payload=item["args"],
            covered_by_job_id=covering_job_id, expected_covering_payload=payload(row, generation),
            wake_key=wake_key(row), coverage_ref="life.activity:"+signature({
                "activity_id":row["id"], "action_id":row["action_id"],
                "revision":row["revision"], "data_generation":generation,
                "legacy_job_id":item["id"], "legacy_key":item["key"]}),
            not_before=lower_bound(row))
        outcomes.append(result)
    return (outcomes[0] if outcomes else None) if only_job_id is not None else outcomes


async def current_wake(worker, activity_id, generation):
    """Short native-envelope/row check before source and Action observation."""
    execution = current_job_execution()
    if execution is None:
        return None
    from .rbi_activity_reconciliation import eligible, ensure_reconcile_on, generation_matches
    life = worker.life
    async with life.database.transaction() as connection:
        await generation_on(life, connection, generation)
        row = await (await connection.execute(
            "SELECT * FROM life.activities WHERE id=%s FOR UPDATE", (activity_id,))).fetchone()
        if not eligible(row) or not generation_matches(row, generation):
            return {"state":"blocked", "reason":"original_target_changed"}
        found = await candidates(connection, row, generation, job_id=execution["job_id"])
        if not found:
            return {"state":"blocked", "reason":"original_wake_unverified"}
        exact = payload(row, generation)
        if any(item["args"] == exact for item in found):
            return await life.jobs.adopt_wake_on(connection, execution["job_id"], expected_task=TASK,
                expected_generation=generation, expected_payload=exact, wake_key=wake_key(row))
        if not any(is_legacy(row, item) for item in found):
            return {"state":"blocked", "reason":"original_wake_unverified"}
        now = (await (await connection.execute("SELECT now() AS now")).fetchone())["now"]
        target = await ensure_reconcile_on(life, connection, activity_id=row["id"], action_id=row["action_id"],
            data_generation=generation, due_at=now, reason="legacy_wake", not_before=lower_bound(row),
            cover_legacy=False)
        if target is None:
            return {"state":"blocked", "reason":"original_target_changed"}
        return (await cover_legacy_on(life, connection, row, generation, found, target,
            only_job_id=execution["job_id"])) or {"state":"blocked", "reason":"original_wake_unverified"}
