"""Source withdrawal erases native bodies without rewriting historical identity."""

from copy import deepcopy
from importlib.resources import files
from types import SimpleNamespace

import pytest
import pytest_asyncio
from psycopg.types.json import Jsonb

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue, JobSpec
from tianshu.runtime.migrations import Migration, migrate


@pytest_asyncio.fixture
async def redaction_queue(database):
    await migrate(database, tuple(Migration(name, files("tianshu.runtime").joinpath(name + ".sql").read_text(encoding="utf-8"))
                                  for name in ("rbj_wake", "rbj_execution_repair")))
    queue = JobQueue(database.config.dsn, database=database)
    calls = []

    async def handler(**payload):
        calls.append(payload)

    task = queue.register("conversation.result", handler, resource_recovery=True)
    await queue.open()
    try:
        yield queue, task, calls
    finally:
        await queue.close()


def envelopes():
    event = {"type": "capability.result", "version": 1, "event_id": "receipt", "action_id": "action",
             "result_revision": 2, "result_observed_at": "2026-11-06T00:00:00Z", "context": {"actor_id": "actor"},
             "source_refs": [{"source_id": "private-source", "revision": 1}],
             "receipt": {"receipt_id": "receipt", "action_id": "action", "attempt_id": "attempt",
                 "execution": "succeeded", "effect": "confirmed", "delivery": "not_applicable",
                 "artifacts": [{"content": "private-body"}], "coverage": {"range": [0, 5]},
                 "result": {"content": "private-body", "source_refs": [{"source_id": "private-source", "revision": 1}]},
                 "error_code": None}}
    redacted = {"type": event["type"], "version": event["version"], "event_id": event["event_id"],
        "action_id": event["action_id"], "result_revision": event["result_revision"],
        "result_observed_at": event["result_observed_at"], "context": event["context"],
        "source_refs": [], "source_invalidated": True, "source_invalidation_kind": "forgotten",
        "receipt": {"receipt_id": "receipt", "action_id": "action", "attempt_id": "attempt",
            "execution": "succeeded", "effect": "confirmed", "delivery": "not_applicable",
            "artifacts": [], "coverage": None, "result": {"source_revoked": True}, "error_code": "source_revoked"}}
    return {"event": event}, {"event": redacted}


def context(job_id):
    return SimpleNamespace(job=SimpleNamespace(id=job_id, attempts=0, queueing_lock=None))


async def test_redaction_preserves_original_digest_blocks_recreation_and_exits_without_consumption(database, redaction_queue):
    queue, task, calls = redaction_queue
    old, redacted = envelopes()
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        job_id = await queue.enqueue(conn, JobSpec("conversation.result", "source-original", old,
            data_generation=generation), queueing_lock="result-wake", wake_object_key="original-result")
        prior = await (await conn.execute("SELECT payload_digest FROM runtime.job_keys WHERE job_id=%s", (job_id,))).fetchone()
        result = await queue.redact_wake_on(conn, job_id, expected_task="conversation.result", expected_generation=generation,
            expected_payload=old, redacted_payload=redacted, invalidation_ref="source-change:1", wake_object_key="original-result")
        assert result["state"] == "redacted" and not result["metadata_unverified"]
        again = await queue.redact_wake_on(conn, job_id, expected_task="conversation.result", expected_generation=generation,
            expected_payload=old, redacted_payload=redacted, invalidation_ref="source-change:1", wake_object_key="original-result")
        assert again["state"] == "already_redacted"
        saved = await (await conn.execute("SELECT j.args,k.payload_digest,k.wake_effective_digest FROM procrastinate_jobs j "
            "JOIN runtime.job_keys k ON k.job_id=j.id WHERE j.id=%s", (job_id,))).fetchone()
        assert saved["args"] == redacted and saved["payload_digest"] == prior["payload_digest"]
        assert saved["wake_effective_digest"] == result["effective_digest"]
        with pytest.raises(AppError, match="wake_source_invalidated"):
            await queue.enqueue(conn, JobSpec("conversation.result", "recreate-keyed", old, data_generation=generation),
                                queueing_lock="result-wake", wake_object_key="original-result")
        with pytest.raises(AppError, match="wake_source_invalidated"):
            await queue.enqueue(conn, JobSpec("conversation.result", "recreate-unkeyed", old, data_generation=generation))
        with pytest.raises(AppError, match="wake_source_invalidated"):
            await queue.enqueue(conn, JobSpec("conversation.result", "recreate-repair-wrapper",
                {**old, "repair_ref": "fix", "repair_consumer": "life_activity"}, data_generation=generation))
        adopted = await queue.adopt_wake_on(conn, job_id, expected_task="conversation.result", expected_generation=generation,
                                            expected_payload=redacted, wake_key="result-wake")
        assert adopted["state"] == "invalidated"
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
    stopped = await task(context(job_id), **old)
    assert stopped == {"state": "invalidated", "reason": "source_invalidated"} and calls == []
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (job_id,))
        history = await queue.observe_execution_history_on(conn, job_ids=[job_id], data_generation=generation,
                                                            task_name="conversation.result")
        assert history["complete"] and history["executions"] == []
        marker = await (await conn.execute("SELECT kind,reason FROM runtime.rbj_wake_exits WHERE job_id=%s", (job_id,))).fetchone()
        assert marker == {"kind": "source_invalidated", "reason": "source_invalidated"}


async def test_old_generation_missing_metadata_and_collision_never_block_confirmed_erasure(database, redaction_queue):
    queue, _task, _calls = redaction_queue
    old, redacted = envelopes()
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        job_id = await queue.enqueue(conn, JobSpec("conversation.result", "legacy-key", old, data_generation=generation))
        await conn.execute("UPDATE runtime.job_keys SET payload_digest='conflicting-history' WHERE job_id=%s", (job_id,))
        legacy = await (await conn.execute("INSERT INTO procrastinate_jobs(queue_name,task_name,args) "
            "VALUES('maintenance','conversation.result',%s) RETURNING id", (Jsonb(old),))).fetchone()
        await conn.execute("UPDATE runtime.installation_generation SET id='new-generation' WHERE singleton=true")
        result = await queue.redact_wake_on(conn, job_id, expected_task="conversation.result", expected_generation=generation,
            expected_payload=old, redacted_payload=redacted, invalidation_ref="source-change:old", wake_object_key="original-result")
        assert result["state"] == "redacted" and result["metadata_unverified"]
        untouched = await (await conn.execute("SELECT payload_digest FROM runtime.job_keys WHERE job_id=%s", (job_id,))).fetchone()
        assert untouched["payload_digest"] == "conflicting-history"
        unbound = await queue.redact_wake_on(conn, legacy["id"], expected_task="conversation.result", expected_generation=None,
            expected_payload=old, redacted_payload=redacted, invalidation_ref="source-change:legacy")
        assert unbound["state"] == "redacted" and unbound["metadata_unverified"]
        saved = await (await conn.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (legacy["id"],))).fetchone()
        assert saved["args"] == redacted


async def test_redaction_cas_and_fixed_tombstone_refuse_identity_or_body_changes(database, redaction_queue):
    queue, _task, _calls = redaction_queue
    old, redacted = envelopes()
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        job_id = await queue.enqueue(conn, JobSpec("conversation.result", "cas-job", old, data_generation=generation))
        forbidden = deepcopy(redacted)
        forbidden["event"]["context"]["actor_id"] = "other"
        with pytest.raises(AppError, match="wake_redaction_identity_conflict"):
            await queue.redact_wake_on(conn, job_id, expected_task="conversation.result", expected_generation=generation,
                expected_payload=old, redacted_payload=forbidden, invalidation_ref="source-change:1")
        changed = deepcopy(old)
        changed["event"]["result_revision"] = 3
        await conn.execute("UPDATE procrastinate_jobs SET args=%s WHERE id=%s", (Jsonb(changed), job_id))
        result = await queue.redact_wake_on(conn, job_id, expected_task="conversation.result", expected_generation=generation,
            expected_payload=old, redacted_payload=redacted, invalidation_ref="source-change:1")
        assert result["state"] == "changed" and result["reason"] == "wake_payload_changed"
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_wake_redactions WHERE job_id=%s", (job_id,))).fetchone()


async def test_redaction_never_erases_existing_started_or_unknown_execution_fact(database, redaction_queue):
    queue, task, calls = redaction_queue
    old, redacted = envelopes()
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        job_id = await queue.enqueue(conn, JobSpec("conversation.result", "active-source", old, data_generation=generation),
                                     queueing_lock="active-wake", wake_object_key="active-result")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        await conn.execute("INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,"
            "job_id,native_attempt,outcome) VALUES(%s,'fixture','conversation.result',4,%s,0,'unknown')", (generation, job_id))
        await queue.redact_wake_on(conn, job_id, expected_task="conversation.result", expected_generation=generation,
            expected_payload=old, redacted_payload=redacted, invalidation_ref="source-change:active", wake_object_key="active-result")
    with pytest.raises(AppError, match="wake_execution_already_started"):
        await task(context(job_id), **old)
    assert calls == []
    async with database.transaction(read_only=True) as conn:
        saved = await (await conn.execute("SELECT outcome FROM runtime.rbi_execution_facts WHERE job_id=%s", (job_id,))).fetchone()
        assert saved["outcome"] == "unknown"
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_wake_exits WHERE job_id=%s", (job_id,))).fetchone()


@pytest.mark.parametrize("lock_kind", ["object", "payload"])
async def test_redaction_lock_contention_returns_database_timed_wait_and_preserves_body(database, redaction_queue, lock_kind):
    from tianshu.runtime import rbj_wake

    queue, _task, _calls = redaction_queue
    old, redacted = envelopes()
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        job_id = await queue.enqueue(conn, JobSpec("conversation.result", "contended-" + lock_kind, old,
            data_generation=generation), queueing_lock="contended-wake", wake_object_key="contended-object")
    async with database.transaction() as holder:
        if lock_kind == "object":
            await rbj_wake.lock_on(holder, "conversation.result", generation, "contended-object")
        else:
            await rbj_wake.lock_payload_on(holder, "conversation.result", rbj_wake.redaction_lock_digest(old))
        async with database.transaction() as contender:
            before = (await (await contender.execute("SELECT clock_timestamp() AS now")).fetchone())["now"]
            waiting = await queue.redact_wake_on(contender, job_id, expected_task="conversation.result",
                expected_generation=generation, expected_payload=old, redacted_payload=redacted,
                invalidation_ref="source-change:contended", wake_object_key="contended-object")
            assert waiting["state"] == "waiting" and waiting["reason"] == "wake_lock_busy"
            assert waiting["retry_at"].utcoffset() is not None and waiting["retry_at"] > before
            saved = await (await contender.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
            assert saved["args"] == old
            assert not await (await contender.execute("SELECT 1 FROM runtime.rbj_wake_redactions WHERE job_id=%s", (job_id,))).fetchone()
    async with database.transaction() as conn:
        erased = await queue.redact_wake_on(conn, job_id, expected_task="conversation.result",
            expected_generation=generation, expected_payload=old, redacted_payload=redacted,
            invalidation_ref="source-change:contended", wake_object_key="contended-object")
        assert erased["state"] == "redacted"
