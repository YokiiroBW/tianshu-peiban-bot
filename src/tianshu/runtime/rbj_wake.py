"""Durable identity and short mechanical fencing for two state-wake tasks."""

from datetime import datetime, timedelta
import hashlib
import json
from collections.abc import Mapping

from psycopg.types.json import Jsonb

from .errors import AppError
from .recovery_policy import RecoveryWait


WAKE_TASKS = frozenset({"life.activity_reconcile", "conversation.result"})


def _canonical_json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _same_json(left, right):
    try:
        return _canonical_json(left) == _canonical_json(right)
    except (TypeError, ValueError):
        return False


def payload_digest(payload):
    if not isinstance(payload, Mapping):
        raise ValueError("invalid_wake_payload")
    try:
        raw = _canonical_json(dict(payload))
    except (TypeError, ValueError):
        raise ValueError("invalid_wake_payload") from None
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def event_digest(payload):
    event = payload.get("event") if isinstance(payload, Mapping) else None
    return payload_digest(event) if isinstance(event, Mapping) else None


def redaction_lock_digest(payload):
    # Normal and repair wrappers of the same event must serialize together.
    event_hash = event_digest(payload)
    return "event:" + event_hash if event_hash is not None else "payload:" + payload_digest(payload)


def _redaction_only(old, new, path=()):
    if _same_json(old, new):
        return True
    if new is None:
        return True
    if isinstance(new, (list, dict)) and not new:
        return True
    if path == ("event", "receipt", "result") and _same_json(new, {"source_revoked": True}):
        return True
    if path == ("event", "source_invalidated") and new is True:
        return True
    if isinstance(old, list) and isinstance(new, list):
        return not new
    if isinstance(old, Mapping) and isinstance(new, Mapping):
        for key, value in new.items():
            child = (*path, key)
            if key in old:
                if not _redaction_only(old[key], value, child):
                    return False
            elif child == ("event", "source_invalidated") and value is True:
                continue
            elif child == ("event", "source_invalidation_kind") and value in {
                    "forgotten", "retracted", "fragment_forgotten", "channel_recall"}:
                continue
            elif child == ("event", "receipt", "result", "source_revoked") and value is True:
                continue
            elif child == ("event", "receipt", "error_code") and value == "source_revoked":
                continue
            elif value is None or value == [] or value == {}:
                continue
            else:
                return False
        return True
    return path == ("event", "receipt", "error_code") and new == "source_revoked"


def validate_redaction(expected_payload, redacted_payload):
    """Verify only the frozen result envelope plus irreversible body erasure."""
    if not isinstance(expected_payload, Mapping) or not isinstance(redacted_payload, Mapping):
        raise ValueError("invalid_wake_redaction")
    old, new = dict(expected_payload), dict(redacted_payload)
    old_event, new_event = old.get("event"), new.get("event")
    if not isinstance(old_event, Mapping) or not isinstance(new_event, Mapping):
        raise ValueError("invalid_wake_redaction")
    if not _same_json({key: value for key, value in old.items() if key != "event"},
                      {key: value for key, value in new.items() if key != "event"}):
        raise AppError("wake_redaction_identity_conflict", status=409)
    fields = ("type", "version", "event_id", "action_id", "result_revision", "result_observed_at", "context")
    old_receipt, new_receipt = old_event.get("receipt"), new_event.get("receipt")
    receipt_fields = ("receipt_id", "action_id", "attempt_id", "execution", "effect", "delivery")
    if (not isinstance(old_receipt, Mapping) or not isinstance(new_receipt, Mapping)
            or any(not _same_json(old_event.get(key), new_event.get(key)) for key in fields)
            or any(not _same_json(old_receipt.get(key), new_receipt.get(key)) for key in receipt_fields)
            or new_event.get("source_invalidated") is not True or new_event.get("source_refs") != []
            or not _same_json(new_receipt.get("result"), {"source_revoked": True}) or new_receipt.get("artifacts") != []
            or new_receipt.get("coverage") is not None or new_receipt.get("error_code") != "source_revoked"
            or not _redaction_only(old, new)):
        raise AppError("wake_redaction_identity_conflict", status=409)
    return payload_digest(old), payload_digest(new)


def _key(value):
    if (not isinstance(value, str) or not value or len(value) > 2048
            or any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise AppError("invalid_job_queueing_lock")
    return value


def keys(task, wake_key, object_key=None):
    if task not in WAKE_TASKS:
        raise AppError("wake_task_not_registered", status=409)
    return _key(wake_key), _key(object_key if object_key is not None else wake_key)


def object_lock(task, generation, object_key):
    raw = json.dumps([task, generation, object_key], ensure_ascii=False, separators=(",", ":"))
    return "rbj-wake:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


async def lock_on(connection, task, generation, object_key):
    name = object_lock(task, generation, object_key)
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))", (name,))
    return name


async def lock_variant_on(connection, task, generation, wake_key):
    # A conflicting object key must not let two writers race past identity
    # validation simply because their per-object locks differ.
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             ("rbj-variant:" + object_lock(task, generation, wake_key),))


async def lock_payload_on(connection, task, digest):
    await connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                             ("rbj-payload:" + task + ":" + digest,))


async def _try_redaction_lock_on(connection, name):
    # A source notification can redact several jobs in one transaction. Never
    # wait for a later object while holding an earlier object's transaction lock.
    return await (await connection.execute(
        "SELECT pg_try_advisory_xact_lock(hashtextextended(%s,0)) AS locked, "
        "clock_timestamp()+interval '2 seconds' AS retry_at", (name,))).fetchone()


async def assert_payload_not_invalidated_on(connection, *, task, generation, digest, event_hash):
    invalidated = await (await connection.execute(
        "SELECT 1 FROM runtime.rbj_wake_redactions WHERE task=%s "
        "AND (blocked_digest=%s OR (%s::text IS NOT NULL AND blocked_event_digest=%s)) "
        "AND (requested_generation IS NULL OR requested_generation=%s) LIMIT 1",
        (task, digest, event_hash, event_hash, generation))).fetchone()
    if invalidated:
        raise AppError("wake_source_invalidated", status=409)


async def assign_key_on(connection, key, *, wake_key, object_key):
    changed = await (await connection.execute(
        "UPDATE runtime.job_keys SET wake_key=%s,wake_object_key=%s WHERE key=%s "
        "AND (wake_key IS NULL OR (wake_key=%s AND wake_object_key=%s)) RETURNING key",
        (wake_key, object_key, key, wake_key, object_key))).fetchone()
    if not changed:
        raise AppError("job_identity_conflict", status=409)


async def _registered_on(connection, generation, task, wake_key, object_key, digest):
    rows = await (await connection.execute(
        "SELECT DISTINCT payload_digest,wake_object_key FROM runtime.job_keys "
        "WHERE data_generation=%s AND task=%s AND wake_key=%s AND cancelled_at IS NULL",
        (generation, task, wake_key))).fetchall()
    if any(row["payload_digest"] != digest or row["wake_object_key"] != object_key for row in rows):
        raise AppError("job_identity_conflict", status=409)


async def assert_object_not_invalidated_on(connection, *, generation, task, object_key):
    if task != "conversation.result":
        return
    invalidated = await (await connection.execute(
        "SELECT 1 FROM runtime.rbj_wake_redaction_scopes WHERE data_generation=%s AND task=%s "
        "AND wake_object_key=%s LIMIT 1", (generation, task, object_key))).fetchone()
    if invalidated:
        raise AppError("wake_source_invalidated", status=409)


async def _pending_on(connection, generation, task, wake_key):
    return await (await connection.execute(
        "SELECT j.id,j.scheduled_at,j.priority,j.queue_name,j.args,now() AS now "
        "FROM procrastinate_jobs j WHERE j.status='todo' AND j.task_name=%s AND EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s "
        "AND k.wake_key=%s AND k.cancelled_at IS NULL AND k.wake_covered_by IS NULL) "
        "ORDER BY j.id FOR UPDATE OF j", (task, generation, wake_key))).fetchall()


async def _not_before_on(connection, generation, task, object_key):
    row = await (await connection.execute(
        "SELECT max(k.wake_not_before) AS due FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
        "WHERE k.data_generation=%s AND k.task=%s AND k.wake_object_key=%s AND k.cancelled_at IS NULL "
        "AND k.wake_covered_by IS NULL AND j.status IN ('todo','doing')",
        (generation, task, object_key))).fetchone()
    return row["due"]


async def _mark_covered_on(connection, job_id, target, coverage_ref):
    # Covered physical rows remain in the native ledger and finish cheaply.
    await connection.execute(
        "INSERT INTO runtime.rbj_wake_coverage_facts(data_generation,job_id,covered_by,coverage_ref) "
        "SELECT DISTINCT data_generation,job_id,%s,%s FROM runtime.job_keys "
        "WHERE job_id=%s AND wake_covered_by IS NULL AND cancelled_at IS NULL",
        (target, coverage_ref, job_id))
    await connection.execute("UPDATE runtime.job_keys SET wake_covered_by=%s,wake_coverage_ref=%s "
                             "WHERE job_id=%s AND wake_covered_by IS NULL", (target, coverage_ref, job_id))
    await connection.execute("UPDATE procrastinate_jobs SET lock=NULL,queueing_lock=NULL "
                             "WHERE id=%s AND status='todo'", (job_id,))


async def _children_on(connection, job_ids):
    rows = await (await connection.execute(
        "SELECT DISTINCT wake_covered_by FROM runtime.job_keys WHERE wake_covered_by=ANY(%s::bigint[])",
        (job_ids,))).fetchall()
    return {row["wake_covered_by"] for row in rows}


async def _retarget_children_on(connection, *, generation, source, target, coverage_ref):
    # Each committed page leaves every reference direct; the parent remains
    # uncovered until all its children have been moved.
    rows = await (await connection.execute(
        "SELECT DISTINCT job_id FROM runtime.job_keys WHERE data_generation=%s AND wake_covered_by=%s "
        "ORDER BY job_id LIMIT 33", (generation, source))).fetchall()
    children = [row["job_id"] for row in rows[:32]]
    if children:
        await connection.execute(
            "INSERT INTO runtime.rbj_wake_coverage_facts(data_generation,job_id,covered_by,previous_covered_by,coverage_ref) "
            "SELECT DISTINCT data_generation,job_id,%s,%s,%s FROM runtime.job_keys "
            "WHERE data_generation=%s AND wake_covered_by=%s AND job_id=ANY(%s::bigint[])",
            (target, source, coverage_ref, generation, source, children))
        await connection.execute(
            "UPDATE runtime.job_keys SET wake_covered_by=%s WHERE data_generation=%s AND wake_covered_by=%s "
            "AND job_id=ANY(%s::bigint[])", (target, generation, source, children))
    return len(rows) <= 32


async def coalesce_on(connection, *, generation, task, wake_key, object_key, digest, payload,
                      scheduled_at, priority, notify, include_new_due=True):
    """Merge only equal frozen variants; callers hold the object advisory lock."""
    await _registered_on(connection, generation, task, wake_key, object_key, digest)
    rows = await _pending_on(connection, generation, task, wake_key)
    if not rows:
        return None
    if any(row["args"] != dict(payload) for row in rows):
        raise AppError("job_identity_conflict", status=409)
    now = rows[0]["now"]
    requested = [row["scheduled_at"] or now for row in rows]
    if include_new_due:
        requested.append(scheduled_at or now)
    due = min(requested)
    lower = await _not_before_on(connection, generation, task, object_key)
    if lower is not None:
        due = max(due, lower)
    parents = await _children_on(connection, [row["id"] for row in rows])
    if len(parents) > 1:
        raise AppError("wake_coverage_conflict", status=409)
    kept = next((row for row in rows if row["id"] in parents), rows[0])
    effective_due = None if due <= now else due
    effective_priority = max(priority, *(row["priority"] for row in rows))
    await connection.execute("UPDATE procrastinate_jobs SET scheduled_at=%s,priority=%s,lock=%s "
                             "WHERE id=%s AND status='todo'",
                             (effective_due, effective_priority, object_lock(task, generation, object_key), kept["id"]))
    if lower is not None:
        await connection.execute("UPDATE runtime.job_keys SET wake_not_before=GREATEST(wake_not_before,%s) "
                                 "WHERE job_id=%s AND data_generation=%s AND cancelled_at IS NULL",
                                 (lower, kept["id"], generation))
    for duplicate in rows:
        if duplicate["id"] != kept["id"]:
            await _mark_covered_on(connection, duplicate["id"], kept["id"], "equivalent-frozen-wake")
    if effective_due != kept["scheduled_at"] or effective_priority != kept["priority"]:
        await notify(connection, kept["id"], kept["queue_name"])
    return kept["id"]


async def _job_on(connection, job_id, expected_task, generation, payload):
    if (type(job_id) is not int or job_id < 1 or expected_task not in WAKE_TASKS
            or not isinstance(payload, Mapping)):
        raise ValueError("invalid_wake_identity")
    row = await (await connection.execute(
        "SELECT j.id,j.task_name,j.args,j.status,j.scheduled_at,j.priority,j.lock,j.queue_name,j.attempts,now() AS now,"
        "k.key,k.subject_id,k.matter_id,k.payload_version,k.payload_digest,k.wake_key,k.wake_object_key,"
        "k.wake_covered_by,k.wake_coverage_ref,k.wake_not_before,k.wake_invalidated_at FROM procrastinate_jobs j "
        "JOIN LATERAL (SELECT * FROM runtime.job_keys WHERE job_id=j.id AND data_generation=%s "
        "AND task=%s AND cancelled_at IS NULL ORDER BY created_at,key LIMIT 1) k ON true "
        "WHERE j.id=%s FOR UPDATE OF j", (generation, expected_task, job_id))).fetchone()
    if not row or row["task_name"] != expected_task or row["args"] != dict(payload):
        raise AppError("wake_identity_mismatch", status=409)
    return row


async def _check_keys_on(connection, row, *, task, generation, digest):
    aliases = await (await connection.execute(
        "SELECT task,data_generation,payload_digest,subject_id,matter_id,payload_version "
        "FROM runtime.job_keys WHERE job_id=%s AND cancelled_at IS NULL", (row["id"],))).fetchall()
    if not aliases or any(item["task"] != task or item["data_generation"] != generation
            or item["payload_digest"] not in {None, digest} or item["subject_id"] != row["subject_id"]
            or item["matter_id"] != row["matter_id"] or item["payload_version"] != row["payload_version"]
            for item in aliases):
        raise AppError("job_identity_conflict", status=409)


async def redact_wake_on(connection, job_id, *, expected_task, expected_generation, expected_payload,
                         redacted_payload, invalidation_ref, wake_object_key=None):
    """CAS an owner-verified source withdrawal to its one-way redacted envelope."""
    from .jobs import JobSpec, _identity_digest

    if expected_task != "conversation.result" or type(job_id) is not int or job_id < 1:
        raise ValueError("invalid_wake_redaction")
    if expected_generation is not None and (not isinstance(expected_generation, str) or not expected_generation):
        raise ValueError("invalid_wake_redaction")
    _key(invalidation_ref)
    if wake_object_key is not None:
        _key(wake_object_key)
    old_hash, new_hash = validate_redaction(expected_payload, redacted_payload)
    # Do not require the historical generation to equal today's installation;
    # source withdrawal must still erase old and unbound native envelopes.
    await connection.execute("SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")
    before = await (await connection.execute(
        "SELECT DISTINCT data_generation,wake_object_key FROM runtime.job_keys WHERE job_id=%s "
        "AND task=%s AND wake_object_key IS NOT NULL", (job_id, expected_task))).fetchall()
    scopes = {(item["data_generation"], item["wake_object_key"]) for item in before
              if item["data_generation"] is not None}
    if expected_generation is not None and wake_object_key is not None:
        scopes.add((expected_generation, wake_object_key))
    # A notification can have several historical jobs and conflicting scopes.
    # The caller retries its derived notification transaction on actual lock
    # contention; authoritative source withdrawal has already committed.
    base = {"job_id": job_id, "effective_digest": None}
    names = [object_lock(expected_task, generation, object_key) for generation, object_key in sorted(scopes)]
    names.append("rbj-payload:" + expected_task + ":" + redaction_lock_digest(expected_payload))
    for name in names:
        lock = await _try_redaction_lock_on(connection, name)
        if not lock["locked"]:
            return {**base, "state": "waiting", "reason": "wake_lock_busy", "retry_at": lock["retry_at"]}
    native = await (await connection.execute(
        "SELECT id,task_name,args FROM procrastinate_jobs WHERE id=%s FOR UPDATE", (job_id,))).fetchone()
    if not native:
        return {**base, "state": "missing", "reason": "wake_job_missing"}
    if native["task_name"] != expected_task:
        raise AppError("wake_redaction_task_mismatch", status=409)
    already = _same_json(native["args"], redacted_payload)
    if not already and not _same_json(native["args"], expected_payload):
        return {**base, "state": "changed", "reason": "wake_payload_changed"}
    aliases = await (await connection.execute(
        "SELECT task,data_generation,subject_id,matter_id,payload_version,payload_digest,wake_key,wake_object_key "
        "FROM runtime.job_keys WHERE job_id=%s", (job_id,))).fetchall()
    unverified = not aliases
    for alias in aliases:
        if (alias["task"] != expected_task or alias["data_generation"] != expected_generation
                or alias["payload_digest"] is None or alias["wake_key"] is None or alias["wake_object_key"] is None
                or wake_object_key is not None and alias["wake_object_key"] not in {None, wake_object_key}):
            unverified = True
        if alias["payload_digest"] is not None and not already:
            identity = _identity_digest(JobSpec(expected_task, "redaction", expected_payload,
                subject_id=alias["subject_id"], matter_id=alias["matter_id"], payload_version=alias["payload_version"],
                data_generation=alias["data_generation"]))
            if identity != alias["payload_digest"]:
                unverified = True
        if (alias["data_generation"] is not None and alias["wake_object_key"] is not None
                and (alias["data_generation"], alias["wake_object_key"]) not in scopes):
            unverified = True
    previous = await (await connection.execute(
        "SELECT original_digest,blocked_digest,blocked_event_digest,effective_digest,requested_generation,metadata_unverified "
        "FROM runtime.rbj_wake_redactions "
        "WHERE job_id=%s ORDER BY id DESC LIMIT 1", (job_id,))).fetchone()
    if already and not previous:
        # The body was already gone before this ledger existed; its original
        # digest is unknown and must not be reconstructed from a stale snapshot.
        unverified = True
    same_previous = (previous and previous["effective_digest"] == new_hash
                     and previous["blocked_digest"] == old_hash
                     and previous["blocked_event_digest"] == event_digest(expected_payload)
                     and previous["requested_generation"] == expected_generation)
    if same_previous and (previous["metadata_unverified"] or not unverified):
        unverified = unverified or previous["metadata_unverified"]
    else:
        await connection.execute(
            "INSERT INTO runtime.rbj_wake_redactions(job_id,data_generation,requested_generation,task,wake_object_key,"
            "original_digest,blocked_digest,blocked_event_digest,effective_digest,invalidation_ref,metadata_unverified) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (job_id, expected_generation if not unverified else None, expected_generation, expected_task, wake_object_key,
             None if already else old_hash, old_hash, event_digest(expected_payload), new_hash, invalidation_ref, unverified))
    if not already:
        await connection.execute("UPDATE procrastinate_jobs SET args=%s WHERE id=%s", (Jsonb(dict(redacted_payload)), job_id))
    await connection.execute(
        "UPDATE runtime.job_keys SET wake_effective_digest=%s,wake_invalidated_at=COALESCE(wake_invalidated_at,now()),"
        "wake_invalidation_ref=COALESCE(wake_invalidation_ref,%s) WHERE job_id=%s",
        (new_hash, invalidation_ref, job_id))
    await connection.execute(
        "INSERT INTO runtime.rbj_wake_redaction_scopes(job_id,data_generation,task,wake_object_key) "
        "SELECT DISTINCT job_id,data_generation,%s,wake_object_key FROM runtime.job_keys "
        "WHERE job_id=%s AND task=%s AND data_generation IS NOT NULL AND wake_object_key IS NOT NULL "
        "ON CONFLICT DO NOTHING", (expected_task, job_id, expected_task))
    if expected_generation is not None and wake_object_key is not None:
        await connection.execute(
            "INSERT INTO runtime.rbj_wake_redaction_scopes(job_id,data_generation,task,wake_object_key) "
            "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (job_id, expected_generation, expected_task, wake_object_key))
    return {"state": "already_redacted" if already else "redacted", "reason": "source_invalidated",
            "job_id": job_id, "effective_digest": new_hash, "metadata_unverified": unverified}


async def adopt_on(connection, job_id, *, expected_task, expected_generation, expected_payload, wake_key, notify):
    from .jobs import JobSpec, _identity_digest

    wake_key, object_key = keys(expected_task, wake_key)
    generation = await (await connection.execute(
        "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
    if not generation or generation["id"] != expected_generation:
        raise AppError("job_generation_expired", status=409)
    await lock_variant_on(connection, expected_task, expected_generation, wake_key)
    await lock_on(connection, expected_task, expected_generation, object_key)
    invalidated = await (await connection.execute(
        "SELECT j.id,j.scheduled_at,j.args,r.effective_digest FROM procrastinate_jobs j "
        "JOIN LATERAL (SELECT effective_digest FROM runtime.rbj_wake_redactions "
        "WHERE job_id=j.id AND task=j.task_name ORDER BY id DESC LIMIT 1) r ON true "
        "WHERE j.id=%s AND j.task_name=%s "
        "AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s "
        "AND k.cancelled_at IS NULL)", (job_id, expected_task, expected_generation))).fetchone()
    if invalidated:
        if (not _same_json(invalidated["args"], expected_payload)
                or payload_digest(invalidated["args"]) != invalidated["effective_digest"]):
            raise AppError("wake_identity_mismatch", status=409)
        return {"state": "invalidated", "reason": "source_invalidated", "job_id": job_id,
                "covered_by": None, "due_at": invalidated["scheduled_at"]}
    await assert_object_not_invalidated_on(connection, generation=expected_generation,
                                           task=expected_task, object_key=object_key)
    row = await _job_on(connection, job_id, expected_task, expected_generation, expected_payload)
    if row["wake_covered_by"] is not None:
        return {"state": "covered", "reason": "wake_covered", "job_id": job_id,
                "covered_by": row["wake_covered_by"], "due_at": row["scheduled_at"]}
    if row["status"] not in {"todo", "doing"}:
        return {"state": "terminal", "reason": "wake_terminal", "job_id": job_id,
                "covered_by": None, "due_at": row["scheduled_at"]}
    digest = _identity_digest(JobSpec(expected_task, row["key"], expected_payload,
        subject_id=row["subject_id"], matter_id=row["matter_id"], payload_version=row["payload_version"],
        data_generation=expected_generation))
    if row["payload_digest"] not in {None, digest}:
        raise AppError("job_identity_conflict", status=409)
    await _check_keys_on(connection, row, task=expected_task, generation=expected_generation, digest=digest)
    if row["wake_key"] is not None and (row["wake_key"] != wake_key or row["wake_object_key"] != object_key):
        raise AppError("job_identity_conflict", status=409)
    await _registered_on(connection, expected_generation, expected_task, wake_key, object_key, digest)
    name = object_lock(expected_task, expected_generation, object_key)
    if row["status"] == "doing":
        other = await (await connection.execute("SELECT id FROM procrastinate_jobs WHERE lock=%s AND status='doing' "
                                                 "AND id<>%s LIMIT 1", (name, job_id))).fetchone()
        if other:
            return {"state": "waiting", "reason": "wake_other_doing", "job_id": job_id,
                    "covered_by": None, "due_at": row["scheduled_at"]}
    was_registered = row["wake_key"] is not None
    await connection.execute("UPDATE runtime.job_keys SET payload_digest=COALESCE(payload_digest,%s),"
        "wake_key=%s,wake_object_key=%s WHERE job_id=%s AND data_generation=%s AND cancelled_at IS NULL",
        (digest, wake_key, object_key, job_id, expected_generation))
    await connection.execute("UPDATE procrastinate_jobs SET lock=%s WHERE id=%s", (name, job_id))
    kept = await coalesce_on(connection, generation=expected_generation, task=expected_task, wake_key=wake_key,
        object_key=object_key, digest=digest, payload=expected_payload, scheduled_at=None, priority=row["priority"],
        notify=notify, include_new_due=False)
    if kept is not None and kept != job_id and row["status"] == "todo":
        return {"state": "covered", "reason": "wake_equivalent", "job_id": job_id,
                "covered_by": kept, "due_at": row["scheduled_at"]}
    return {"state": "already_adopted" if was_registered else "adopted", "reason": "wake_registered",
            "job_id": job_id, "covered_by": None, "due_at": row["scheduled_at"]}


async def cover_on(connection, job_id, *, expected_task, expected_generation, expected_payload,
                   covered_by_job_id, expected_covering_payload, wake_key, coverage_ref, not_before, notify):
    """Record only a domain-attested, exact-envelope cover with no chaining."""
    from .jobs import JobSpec, _identity_digest, current_job_execution

    wake_key, _ = keys(expected_task, wake_key)
    _key(coverage_ref)
    if not_before is not None and (not isinstance(not_before, datetime) or not_before.utcoffset() is None):
        raise ValueError("invalid_wake_not_before")
    if job_id == covered_by_job_id:
        raise AppError("wake_coverage_conflict", status=409)
    generation = await (await connection.execute(
        "SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")).fetchone()
    if not generation or generation["id"] != expected_generation:
        raise AppError("job_generation_expired", status=409)
    target_meta = await (await connection.execute(
        "SELECT wake_object_key FROM runtime.job_keys WHERE job_id=%s AND data_generation=%s "
        "AND task=%s AND wake_key=%s AND cancelled_at IS NULL ORDER BY created_at,key LIMIT 1",
        (covered_by_job_id, expected_generation, expected_task, wake_key))).fetchone()
    if not target_meta or not target_meta["wake_object_key"]:
        raise AppError("wake_cover_target_invalid", status=409)
    object_key = _key(target_meta["wake_object_key"])
    await lock_on(connection, expected_task, expected_generation, object_key)
    # Lock in stable physical-ID order, irrespective of caller's source/target.
    values = {}
    for item, payload in sorted(((job_id, expected_payload), (covered_by_job_id, expected_covering_payload))):
        values[item] = await _job_on(connection, item, expected_task, expected_generation, payload)
    source, target = values[job_id], values[covered_by_job_id]
    for row, payload in ((source, expected_payload), (target, expected_covering_payload)):
        digest = _identity_digest(JobSpec(expected_task, row["key"], payload, subject_id=row["subject_id"],
            matter_id=row["matter_id"], payload_version=row["payload_version"], data_generation=expected_generation))
        if row["payload_digest"] is not None and row["payload_digest"] != digest:
            raise AppError("job_identity_conflict", status=409)
        await _check_keys_on(connection, row, task=expected_task, generation=expected_generation, digest=digest)
        if row is target and row["payload_digest"] is None:
            raise AppError("wake_cover_target_invalid", status=409)
    if source["wake_covered_by"] is not None:
        if source["wake_covered_by"] != covered_by_job_id or source["wake_coverage_ref"] != coverage_ref:
            raise AppError("wake_coverage_conflict", status=409)
        return {"state": "covered", "reason": "already_covered", "job_id": job_id,
                "covered_by": covered_by_job_id, "due_at": target["scheduled_at"]}
    if (target["wake_key"] != wake_key or target["wake_object_key"] != object_key
            or target["wake_covered_by"] is not None or target["wake_invalidated_at"] is not None
            or target["status"] not in {"todo", "doing"}):
        raise AppError("wake_cover_target_invalid", status=409)
    if source["wake_object_key"] is not None and source["wake_object_key"] != object_key:
        raise AppError("wake_coverage_conflict", status=409)
    current = current_job_execution()
    own = (current is not None and current["job_id"] == job_id and current["data_generation"] == expected_generation
           and current["native_attempt"] == source["attempts"])
    if source["status"] == "doing" and not own:
        return {"state": "waiting", "reason": "wake_doing", "job_id": job_id,
                "covered_by": None, "due_at": source["scheduled_at"]}
    if source["status"] not in {"todo", "doing"}:
        return {"state": "terminal", "reason": "wake_terminal", "job_id": job_id,
                "covered_by": None, "due_at": source["scheduled_at"]}
    now = source["now"]
    bounds = [value for value in (not_before, source["wake_not_before"], target["wake_not_before"]) if value is not None]
    bound = max(bounds) if bounds else None
    source_due = source["scheduled_at"] or now
    target_due = target["scheduled_at"] or now
    actual_due = target["scheduled_at"]
    if bound is not None and target_due < bound:
        return {"state": "waiting", "reason": "wake_not_before_conflict", "job_id": job_id,
                "covered_by": None, "due_at": bound}
    desired = min(target_due, max(source_due, bound) if bound is not None else source_due)
    if target["status"] == "doing" and (source_due > now or bound is not None and bound > now):
        return {"state": "waiting", "reason": "wake_cover_target_doing", "job_id": job_id,
                "covered_by": None, "due_at": source_due}
    if bound is not None:
        await connection.execute("UPDATE runtime.job_keys SET wake_not_before=GREATEST(wake_not_before,%s) "
                                 "WHERE job_id=%s AND data_generation=%s AND cancelled_at IS NULL",
                                 (bound, covered_by_job_id, expected_generation))
    if target["status"] == "todo" and desired < target_due:
        due = None if desired <= now else desired
        await connection.execute("UPDATE procrastinate_jobs SET scheduled_at=%s WHERE id=%s AND status='todo'",
                                 (due, covered_by_job_id))
        await notify(connection, covered_by_job_id, target["queue_name"])
        actual_due = due
    flattened = await _retarget_children_on(connection, generation=expected_generation, source=job_id,
                                              target=covered_by_job_id, coverage_ref=coverage_ref)
    if not flattened:
        return {"state": "waiting", "reason": "wake_cover_partial", "job_id": job_id,
                "covered_by": None, "due_at": actual_due}
    await _mark_covered_on(connection, job_id, covered_by_job_id, coverage_ref)
    return {"state": "covered", "reason": "wake_covered", "job_id": job_id,
            "covered_by": covered_by_job_id, "due_at": actual_due}


async def record_exit_on(connection, *, task, generation, job_id, native_attempt):
    if task not in WAKE_TASKS or type(native_attempt) is not int or native_attempt < 0:
        raise AppError("wake_execution_changed", status=409)

    # A committed exit is evidence of the cover as it existed at that time.
    # Later flattening or invalidation must not erase that evidence.
    saved = await (await connection.execute(
        "SELECT kind,covered_by FROM runtime.rbj_wake_exits "
        "WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (generation, job_id, native_attempt))).fetchone()
    if saved:
        row = await (await connection.execute(
            "SELECT status,attempts FROM procrastinate_jobs WHERE id=%s AND task_name=%s FOR UPDATE",
            (job_id, task))).fetchone()
        fact = await (await connection.execute(
            "SELECT 1 FROM runtime.rbj_wake_coverage_facts WHERE data_generation=%s AND job_id=%s "
            "AND covered_by=%s LIMIT 1", (generation, job_id, saved["covered_by"]))).fetchone()
        begun = await (await connection.execute(
            "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s "
            "AND native_attempt=%s", (generation, job_id, native_attempt))).fetchone()
        if not row or row["status"] != "doing" or row["attempts"] != native_attempt:
            raise AppError("wake_execution_changed", status=409)
        if begun:
            raise AppError("wake_execution_already_started", status=409)
        if saved["kind"] != "covered" or not fact:
            raise AppError("wake_coverage_conflict", status=409)
        return saved["covered_by"]

    hint = await (await connection.execute(
        "SELECT k.wake_covered_by,t.wake_object_key FROM runtime.job_keys k "
        "JOIN LATERAL (SELECT wake_object_key FROM runtime.job_keys WHERE job_id=k.wake_covered_by "
        "AND task=%s AND data_generation=%s AND cancelled_at IS NULL AND wake_key IS NOT NULL "
        "ORDER BY created_at,key LIMIT 1) t ON true "
        "WHERE k.job_id=%s AND k.task=%s AND k.data_generation=%s AND k.cancelled_at IS NULL "
        "AND k.wake_covered_by IS NOT NULL AND k.wake_coverage_ref IS NOT NULL "
        "ORDER BY k.created_at,k.key LIMIT 1", (task, generation, job_id, task, generation))).fetchone()
    if not hint or not hint["wake_object_key"]:
        raise AppError("wake_cover_target_invalid", status=409)
    await lock_on(connection, task, generation, hint["wake_object_key"])
    # Lock both native rows in the same order as coverage. This makes target
    # validity and the no-begin proof atomic with a concurrent withdrawal.
    await (await connection.execute(
        "SELECT id FROM procrastinate_jobs WHERE id=ANY(%s::bigint[]) ORDER BY id FOR UPDATE",
        ([job_id, hint["wake_covered_by"]],))).fetchall()
    row = await (await connection.execute(
        "SELECT j.status,j.attempts,k.wake_covered_by FROM procrastinate_jobs j "
        "JOIN LATERAL (SELECT wake_covered_by FROM runtime.job_keys WHERE job_id=j.id AND task=%s "
        "AND data_generation=%s AND cancelled_at IS NULL AND wake_covered_by IS NOT NULL "
        "AND wake_coverage_ref IS NOT NULL ORDER BY created_at,key LIMIT 1) k ON true "
        "WHERE j.id=%s AND j.task_name=%s", (task, generation, job_id, task))).fetchone()
    if (not row or row["status"] != "doing" or row["attempts"] != native_attempt
            or row["wake_covered_by"] != hint["wake_covered_by"]):
        raise AppError("wake_execution_changed", status=409)
    target = await (await connection.execute(
        "SELECT t.id FROM procrastinate_jobs t WHERE t.id=%s AND t.task_name=%s "
        "AND t.status IN ('todo','doing','succeeded') AND EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=t.id AND k.task=%s AND k.data_generation=%s "
        "AND k.cancelled_at IS NULL AND k.wake_key IS NOT NULL AND k.wake_object_key=%s "
        "AND k.wake_covered_by IS NULL AND k.wake_invalidated_at IS NULL) AND NOT EXISTS "
        "(SELECT 1 FROM runtime.rbj_wake_redactions r WHERE r.job_id=t.id)",
        (row["wake_covered_by"], task, task, generation, hint["wake_object_key"]))).fetchone()
    if not target:
        raise AppError("wake_cover_target_invalid", status=409)
    begun = await (await connection.execute(
        "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s "
        "AND native_attempt=%s", (generation, job_id, native_attempt))).fetchone()
    if begun:
        raise AppError("wake_execution_already_started", status=409)
    await connection.execute(
        "INSERT INTO runtime.rbj_wake_exits(data_generation,job_id,native_attempt,covered_by) VALUES(%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING", (generation, job_id, native_attempt, row["wake_covered_by"]))
    saved = await (await connection.execute(
        "SELECT kind,covered_by FROM runtime.rbj_wake_exits WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (generation, job_id, native_attempt))).fetchone()
    if not saved or saved["kind"] != "covered" or saved["covered_by"] != row["wake_covered_by"]:
        raise AppError("wake_coverage_conflict", status=409)
    return row["wake_covered_by"]


async def record_invalidated_exit_on(connection, *, task, generation, job_id, native_attempt):
    if task != "conversation.result" or type(native_attempt) is not int or native_attempt < 0:
        raise AppError("wake_execution_changed", status=409)
    row = await (await connection.execute(
        "SELECT j.status,j.attempts,j.args,r.effective_digest,r.invalidation_ref FROM procrastinate_jobs j "
        "JOIN LATERAL (SELECT effective_digest,invalidation_ref FROM runtime.rbj_wake_redactions "
        "WHERE job_id=j.id AND task=%s ORDER BY id DESC LIMIT 1) r ON true "
        "WHERE j.id=%s AND EXISTS (SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id "
        "AND k.data_generation=%s AND k.task=%s AND k.cancelled_at IS NULL) FOR UPDATE OF j",
        (task, job_id, generation, task))).fetchone()
    if (not row or row["status"] != "doing" or row["attempts"] != native_attempt
            or payload_digest(row["args"]) != row["effective_digest"]
            or not (row["args"].get("event") or {}).get("source_invalidated")):
        raise AppError("wake_invalidation_unconfirmed", status=409)
    begun = await (await connection.execute(
        "SELECT 1 FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (generation, job_id, native_attempt))).fetchone()
    if begun:
        raise AppError("wake_execution_already_started", status=409)
    await connection.execute(
        "INSERT INTO runtime.rbj_wake_exits(data_generation,job_id,native_attempt,kind,reason,observation_ref) "
        "VALUES(%s,%s,%s,'source_invalidated','source_invalidated',%s) ON CONFLICT DO NOTHING",
        (generation, job_id, native_attempt, row["invalidation_ref"]))
    saved = await (await connection.execute(
        "SELECT kind,observation_ref FROM runtime.rbj_wake_exits WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (generation, job_id, native_attempt))).fetchone()
    if not saved or saved["kind"] != "source_invalidated" or saved["observation_ref"] != row["invalidation_ref"]:
        raise AppError("wake_invalidation_conflict", status=409)
    return {"state": "invalidated", "reason": "source_invalidated", "job_id": job_id}


async def enter_on(connection, *, task, generation, job_id, native_attempt):
    """Short pre-handler check; release all locks before entering a domain."""
    metadata = await (await connection.execute(
        "SELECT wake_key,wake_object_key,wake_covered_by FROM runtime.job_keys "
        "WHERE job_id=%s AND task=%s AND data_generation=%s AND cancelled_at IS NULL "
        "ORDER BY created_at,key LIMIT 1", (job_id, task, generation))).fetchone()
    if task == "conversation.result":
        redacted = await (await connection.execute(
            "SELECT 1 FROM runtime.rbj_wake_redactions WHERE job_id=%s AND task=%s LIMIT 1", (job_id, task))).fetchone()
        if redacted:
            return await record_invalidated_exit_on(connection, task=task, generation=generation,
                                                    job_id=job_id, native_attempt=native_attempt)
    if metadata and metadata["wake_covered_by"] is not None:
        target = await record_exit_on(connection, task=task, generation=generation, job_id=job_id,
                                      native_attempt=native_attempt)
        return {"state": "covered", "covered_by": target}
    if not metadata or metadata["wake_key"] is None:
        # Historical rows need domain validation; Runtime cannot guess an ID.
        return {"state": "unregistered"}
    object_key = metadata["wake_object_key"]
    await lock_on(connection, task, generation, object_key)
    row = await (await connection.execute(
        "SELECT j.status,clock_timestamp() AS now,k.wake_key,k.wake_object_key,k.wake_covered_by "
        "FROM procrastinate_jobs j JOIN LATERAL (SELECT * FROM runtime.job_keys "
        "WHERE job_id=j.id AND task=%s AND data_generation=%s AND cancelled_at IS NULL "
        "ORDER BY created_at,key LIMIT 1) k ON true WHERE j.id=%s FOR UPDATE OF j",
        (task, generation, job_id))).fetchone()
    if not row or row["status"] != "doing" or row["wake_object_key"] != object_key:
        raise AppError("wake_execution_changed", status=409)
    if row["wake_covered_by"] is not None:
        target = await record_exit_on(connection, task=task, generation=generation, job_id=job_id,
                                      native_attempt=native_attempt)
        return {"state": "covered", "covered_by": target}
    lower = await _not_before_on(connection, generation, task, object_key)
    if lower is not None and lower > row["now"]:
        raise RecoveryWait("wake_dependency_wait", lower)
    return {"state": "ready", "wake_key": row["wake_key"], "object_key": object_key,
            "started_at": row["now"]}


async def complete_on(connection, *, task, generation, job_id, native_attempt, wake_key, object_key, started_at):
    """Cover only equivalent due signals present before this handler began."""
    await lock_on(connection, task, generation, object_key)
    current = await (await connection.execute(
        "SELECT status FROM procrastinate_jobs WHERE id=%s FOR UPDATE", (job_id,))).fetchone()
    if not current or current["status"] != "doing":
        return
    stopped = await (await connection.execute(
        "SELECT 1 FROM runtime.rbj_wake_exits WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
        (generation, job_id, native_attempt))).fetchone()
    if stopped:
        return
    rows = await (await connection.execute(
        "SELECT j.id FROM procrastinate_jobs j WHERE j.status='todo' AND j.task_name=%s "
        "AND (j.scheduled_at IS NULL OR j.scheduled_at<=%s) AND EXISTS "
        "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s "
        "AND k.wake_key=%s AND k.wake_object_key=%s AND k.cancelled_at IS NULL "
        "AND k.wake_covered_by IS NULL AND k.created_at<=%s "
        "AND (k.wake_not_before IS NULL OR k.wake_not_before<=%s)) ORDER BY j.id FOR UPDATE OF j",
        (task, started_at, generation, wake_key, object_key, started_at, started_at))).fetchall()
    children = await _children_on(connection, [row["id"] for row in rows]) if rows else set()
    for row in rows:
        if row["id"] not in children:
            await _mark_covered_on(connection, row["id"], job_id, "equivalent-wake-completed")


async def release_retry_on(connection, *, task, job_id, retry_at=None, retry_delay=None):
    """Persist a dependency lower bound before detaching the native todo lock."""
    meta = await (await connection.execute(
        "SELECT data_generation,wake_object_key FROM runtime.job_keys WHERE job_id=%s AND task=%s "
        "AND cancelled_at IS NULL AND wake_key IS NOT NULL ORDER BY created_at,key LIMIT 1",
        (job_id, task))).fetchone()
    if not meta:
        return False
    generation, object_key = meta["data_generation"], meta["wake_object_key"]
    await lock_on(connection, task, generation, object_key)
    row = await (await connection.execute(
        "SELECT status,now() AS now FROM procrastinate_jobs WHERE id=%s FOR UPDATE", (job_id,))).fetchone()
    if not row or row["status"] != "doing":
        return True
    if retry_delay is not None:
        retry_at = row["now"] + timedelta(seconds=retry_delay)
    if retry_at is not None:
        await connection.execute("UPDATE runtime.job_keys SET wake_not_before=GREATEST(wake_not_before,%s) "
                                 "WHERE job_id=%s AND data_generation=%s AND cancelled_at IS NULL",
                                 (retry_at, job_id, generation))
        await connection.execute(
            "UPDATE procrastinate_jobs j SET scheduled_at=GREATEST(j.scheduled_at,%s) "
            "WHERE j.status='todo' AND (j.scheduled_at IS NULL OR j.scheduled_at<%s) AND EXISTS "
            "(SELECT 1 FROM runtime.job_keys k WHERE k.job_id=j.id AND k.data_generation=%s AND k.task=%s "
            "AND k.wake_object_key=%s AND k.cancelled_at IS NULL AND k.wake_covered_by IS NULL)",
            (retry_at, retry_at, generation, task, object_key))
    await connection.execute("UPDATE procrastinate_jobs SET queueing_lock=NULL WHERE id=%s AND status='doing'", (job_id,))
    return True
