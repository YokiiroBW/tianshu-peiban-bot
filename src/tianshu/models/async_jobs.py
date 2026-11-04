"""A durable wake outbox; the Models ledger remains the result authority."""

from datetime import datetime
from collections.abc import Mapping

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from .generation import current_generation_on, generation_id, require_generation


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
    value = wire(spec)
    # JobSpec is supplied by a trusted producer and must carry the producer's
    # previously frozen generation; Models must never replace it with today's.
    value["data_generation"] = str(generation_id(value.get("data_generation")))
    return value


def completion_spec(value):
    value = dict(value)
    value["service_class"] = ServiceClass(value["service_class"])
    if value.get("scheduled_at") is not None:
        value["scheduled_at"] = datetime.fromisoformat(value["scheduled_at"])
    return JobSpec(**value)


async def enqueue_completion(database, jobs, attempt_id):
    """Commit the existing queue job and the outbox acknowledgement together."""
    async with database.transaction(control=True) as conn:
        current = await current_generation_on(conn)
        row = await (await conn.execute(
            "SELECT s.completion,a.data_generation FROM models.submissions s JOIN models.attempts a USING(attempt_id) "
            "WHERE s.attempt_id=%s AND s.completion IS NOT NULL AND s.completion_state='pending' "
            "AND a.status IN ('succeeded','failed','cancelled','unknown') FOR UPDATE OF s SKIP LOCKED",
            (attempt_id,))).fetchone()
        if row is None:
            return False
        try:
            require_generation(row["data_generation"], current)
            require_generation(row["completion"].get("data_generation"), current)
        except AppError as exc:
            if exc.code != "data_generation_expired":
                raise
            await conn.execute("UPDATE models.submissions SET completion_state='expired',"
                "completion_error_code='data_generation_expired' WHERE attempt_id=%s", (attempt_id,))
            return False
        await jobs.enqueue(conn, completion_spec(row["completion"]))
        await conn.execute("UPDATE models.submissions SET completion_enqueued_at=now(),"
                           "completion_state='enqueued',completion_error_code=NULL WHERE attempt_id=%s",
                           (attempt_id,))
        return True


async def pending_completions(database, *, after=""):
    async with database.transaction(control=True, read_only=True) as conn:
        rows = await (await conn.execute(
            "SELECT s.attempt_id FROM models.submissions s JOIN models.attempts a USING(attempt_id) "
            "WHERE s.completion IS NOT NULL AND s.completion_state='pending' "
            "AND a.status IN ('succeeded','failed','cancelled','unknown') "
            "AND s.attempt_id>%s ORDER BY s.attempt_id LIMIT 100", (after,))).fetchall()
    return [row["attempt_id"] for row in rows]
