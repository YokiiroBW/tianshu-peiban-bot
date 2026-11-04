"""A durable wake outbox; the Models ledger remains the result authority."""

from datetime import datetime
from collections.abc import Mapping

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass


def completion_record(spec):
    if spec is None:
        return None
    if not isinstance(spec, JobSpec) or not isinstance(spec.task, str) or not spec.task \
            or not isinstance(spec.key, str) or not spec.key or not isinstance(spec.payload, Mapping):
        raise AppError("invalid_model_completion")
    if spec.service_class not in {ServiceClass.INTERACTIVE, ServiceClass.DUE, ServiceClass.ELASTIC}:
        raise AppError("invalid_model_completion")
    if spec.scheduled_at is not None and (spec.scheduled_at.tzinfo is None
                                         or spec.scheduled_at.utcoffset() is None):
        raise AppError("invalid_model_completion")
    return wire(spec)


def completion_spec(value):
    value = dict(value)
    value["service_class"] = ServiceClass(value["service_class"])
    if value.get("scheduled_at") is not None:
        value["scheduled_at"] = datetime.fromisoformat(value["scheduled_at"])
    return JobSpec(**value)


async def enqueue_completion(database, jobs, attempt_id):
    """Commit the existing queue job and the outbox acknowledgement together."""
    async with database.transaction(control=True) as conn:
        row = await (await conn.execute(
            "SELECT s.completion FROM models.submissions s JOIN models.attempts a USING(attempt_id) "
            "WHERE s.attempt_id=%s AND s.completion IS NOT NULL AND s.completion_enqueued_at IS NULL "
            "AND a.status IN ('succeeded','failed','cancelled','unknown') FOR UPDATE OF s SKIP LOCKED",
            (attempt_id,))).fetchone()
        if row is None:
            return False
        await jobs.enqueue(conn, completion_spec(row["completion"]))
        await conn.execute("UPDATE models.submissions SET completion_enqueued_at=now() WHERE attempt_id=%s",
                           (attempt_id,))
        return True


async def pending_completions(database, *, after=""):
    async with database.transaction(control=True, read_only=True) as conn:
        rows = await (await conn.execute(
            "SELECT s.attempt_id FROM models.submissions s JOIN models.attempts a USING(attempt_id) "
            "WHERE s.completion IS NOT NULL AND s.completion_enqueued_at IS NULL "
            "AND a.status IN ('succeeded','failed','cancelled','unknown') "
            "AND s.attempt_id>%s ORDER BY s.attempt_id LIMIT 100", (after,))).fetchall()
    return [row["attempt_id"] for row in rows]
