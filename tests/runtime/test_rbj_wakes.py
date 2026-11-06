"""Durable state wakes keep their identity through native retry and coverage."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace

import pytest
import pytest_asyncio

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue, JobSpec
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait


@pytest_asyncio.fixture
async def wake_queue(database):
    await migrate(database, tuple(Migration(name, files("tianshu.runtime").joinpath(name + ".sql").read_text(encoding="utf-8"))
                                  for name in ("rbj_wake", "rbj_execution_repair")))
    queue = JobQueue(database.config.dsn, database=database)
    calls, state = [], {"wait_until": None}

    async def handler(**payload):
        calls.append(payload)
        if state["wait_until"] is not None:
            raise RecoveryWait("fixture_resource", state["wait_until"])
        return {"state": "observed"}

    task = queue.register("life.activity_reconcile", handler, resource_recovery=True)
    await queue.open()
    try:
        yield queue, task, calls, state
    finally:
        await queue.close()


def context(job_id, attempt=0, queueing_lock=None):
    return SimpleNamespace(job=SimpleNamespace(id=job_id, attempts=attempt, queueing_lock=queueing_lock))


async def test_retry_detach_crash_window_and_native_retry_reuse_one_logical_pending(database, wake_queue):
    queue, task, calls, state = wake_queue
    due = datetime.now(UTC) + timedelta(minutes=3)
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        payload = {"activity_id": "a", "action_id": "action", "data_generation": generation}
        original_spec = JobSpec("life.activity_reconcile", "wake-original", payload, subject_id="actor", data_generation=generation)
        original = await queue.enqueue(conn, original_spec, queueing_lock="activity:a:action")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (original,))
        successor = await queue.enqueue(conn, replace(original_spec, key="wake-successor"),
                                        queueing_lock="activity:a:action")
    state["wait_until"] = due
    with pytest.raises(RecoveryWait):
        await task(context(original, queueing_lock="activity:a:action:generation:" + generation), **payload)
    # The worker may stop after lock release but before native retry persists.
    async with database.transaction() as conn:
        before = await (await conn.execute("SELECT status,queueing_lock FROM procrastinate_jobs WHERE id=%s",
                                           (original,))).fetchone()
        pending = await (await conn.execute("SELECT scheduled_at FROM procrastinate_jobs WHERE id=%s",
                                            (successor,))).fetchone()
        assert before == {"status": "doing", "queueing_lock": None}
        assert pending["scheduled_at"] >= due
        merged = await queue.enqueue(conn, replace(original_spec, key="wake-during-gap"),
                                     queueing_lock="activity:a:action")
        assert merged == successor
    await queue.app.job_manager.retry_job(context(original).job, retry_at=due)
    async with database.transaction() as conn:
        merged = await queue.enqueue(conn, replace(original_spec, key="wake-after-retry"),
                                     queueing_lock="activity:a:action")
        assert merged == original
        rows = await (await conn.execute("SELECT id,status,lock,scheduled_at FROM procrastinate_jobs "
                                         "WHERE id=ANY(%s::bigint[]) ORDER BY id", ([original, successor],))).fetchall()
        covered = await queue.observe_on(conn, successor)
        assert covered["covered_by"] == original and covered["job_state"] == "todo"
        assert rows[0]["scheduled_at"] >= due and rows[0]["lock"] is not None
        assert rows[1]["lock"] is None and rows[1]["status"] == "todo"
        with pytest.raises(AppError, match="job_identity_conflict"):
            await queue.enqueue(conn, replace(original_spec, key="wrong", payload={**payload, "action_id": "other"}),
                                queueing_lock="activity:a:action")


async def test_adoption_and_trusted_cover_preserve_original_and_cheaply_exit(database, wake_queue):
    queue, task, calls, _state = wake_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        now = (await (await conn.execute("SELECT now() AS now")).fetchone())["now"]
        old_payload = {"activity_id": "a", "context": {"actor": "actor"}, "sequence": 3}
        canonical_payload = {"activity_id": "a", "action_id": "action", "data_generation": generation}
        old = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "old-wake", old_payload,
                                               subject_id="actor", data_generation=generation, scheduled_at=now + timedelta(minutes=1)))
        canonical = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "canonical", canonical_payload,
            subject_id="actor", data_generation=generation, scheduled_at=now + timedelta(minutes=5)),
            queueing_lock="activity:a:action")
        with pytest.raises(AppError, match="job_identity_conflict"):
            await queue.adopt_wake_on(conn, old, expected_task="life.activity_reconcile", expected_generation=generation,
                                      expected_payload=old_payload, wake_key="activity:a:action")
        covered = await queue.cover_wake_on(conn, old, expected_task="life.activity_reconcile", expected_generation=generation,
            expected_payload=old_payload, covered_by_job_id=canonical, expected_covering_payload=canonical_payload,
            wake_key="activity:a:action", coverage_ref="life-cas:a:action", not_before=now + timedelta(seconds=30))
        assert covered["state"] == "covered" and covered["due_at"] == now + timedelta(minutes=1)
        assert (await queue.observe_on(conn, old))["covered_by"] == canonical
        new_signal = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "canonical-next", canonical_payload,
            subject_id="actor", data_generation=generation), queueing_lock="activity:a:action")
        assert new_signal == canonical
        saved = await (await conn.execute("SELECT scheduled_at FROM procrastinate_jobs WHERE id=%s", (canonical,))).fetchone()
        assert saved["scheduled_at"] >= now + timedelta(seconds=30)
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (old,))
    result = await task(context(old), **old_payload)
    assert result == {"state": "covered", "covered_by": canonical} and calls == []
    async with database.transaction() as conn:
        await conn.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (old,))
        marker = await (await conn.execute("SELECT covered_by FROM runtime.rbj_wake_exits WHERE data_generation=%s "
                                            "AND job_id=%s AND native_attempt=0", (generation, old))).fetchone()
        assert marker["covered_by"] == canonical
        observed = await queue.observe_execution_history_on(conn, job_ids=[old], data_generation=generation,
                                                             task_name="life.activity_reconcile")
        assert observed["complete"] and observed["executions"] == []
        native = await (await conn.execute("SELECT args,attempts FROM procrastinate_jobs WHERE id=%s", (old,))).fetchone()
        assert native == {"args": old_payload, "attempts": 1}


async def test_cover_does_not_cancel_other_doing_or_override_wait_lower_bound(database, wake_queue):
    queue, _task, _calls, _state = wake_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        now = (await (await conn.execute("SELECT now() AS now")).fetchone())["now"]
        old_payload, new_payload = {"old": "a"}, {"new": "a"}
        old = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "busy-old", old_payload, data_generation=generation))
        target = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "busy-target", new_payload,
            data_generation=generation, scheduled_at=now + timedelta(seconds=10)), queueing_lock="target:a")
        common = dict(expected_task="life.activity_reconcile", expected_generation=generation, expected_payload=old_payload,
                      covered_by_job_id=target, expected_covering_payload=new_payload, wake_key="target:a", coverage_ref="proof:a")
        conflict = await queue.cover_wake_on(conn, old, **common, not_before=now + timedelta(minutes=1))
        assert conflict["state"] == "waiting" and conflict["reason"] == "wake_not_before_conflict"
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (old,))
        waiting = await queue.cover_wake_on(conn, old, **common)
        assert waiting["state"] == "waiting" and waiting["reason"] == "wake_doing"
        assert (await queue.observe_on(conn, old))["job_state"] == "doing"
        with pytest.raises(AppError, match="job_generation_expired"):
            await queue.cover_wake_on(conn, old, **dict(common, expected_generation="older-generation"))


async def test_variants_keep_distinct_payloads_and_share_object_execution_lock(database, wake_queue):
    queue, _task, _calls, _state = wake_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        normal = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "variant-normal", {"kind": "normal"},
                                                   data_generation=generation), queueing_lock="normal", wake_object_key="object")
        repair = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "variant-repair", {"kind": "repair"},
                                                   data_generation=generation), queueing_lock="repair", wake_object_key="object")
        rows = await (await conn.execute("SELECT id,lock,args FROM procrastinate_jobs WHERE id=ANY(%s::bigint[]) ORDER BY id",
                                         ([normal, repair],))).fetchall()
        assert len(rows) == 2 and rows[0]["lock"] == rows[1]["lock"] and rows[0]["lock"] is not None
        assert rows[0]["args"] != rows[1]["args"]


async def test_covered_exit_requires_valid_target_and_no_existing_execution_fact(database, wake_queue):
    queue, task, calls, _state = wake_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        old_payload, new_payload = {"old": "a"}, {"new": "a"}
        old = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "proof-old", old_payload, data_generation=generation))
        target = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "proof-target", new_payload,
            data_generation=generation), queueing_lock="target:a")
        await queue.cover_wake_on(conn, old, expected_task="life.activity_reconcile", expected_generation=generation,
            expected_payload=old_payload, covered_by_job_id=target, expected_covering_payload=new_payload,
            wake_key="target:a", coverage_ref="proof:a")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (old,))
        await conn.execute("INSERT INTO runtime.rbi_execution_facts(data_generation,identity_digest,owner,max_failures,"
            "job_id,native_attempt,outcome) VALUES(%s,'fixture','fixture',4,%s,0,'started')", (generation, old))
    with pytest.raises(AppError, match="wake_execution_already_started"):
        await task(context(old), **old_payload)
    assert calls == []
    async with database.transaction(read_only=True) as conn:
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_wake_exits WHERE job_id=%s", (old,))).fetchone()
        fact = await (await conn.execute("SELECT outcome FROM runtime.rbi_execution_facts WHERE job_id=%s", (old,))).fetchone()
        assert fact["outcome"] == "started"
    async with database.transaction() as conn:
        other = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "invalid-old", old_payload, data_generation=generation))
        replacement = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "invalid-target", new_payload,
            data_generation=generation), queueing_lock="target:b")
        await queue.cover_wake_on(conn, other, expected_task="life.activity_reconcile", expected_generation=generation,
            expected_payload=old_payload, covered_by_job_id=replacement, expected_covering_payload=new_payload,
            wake_key="target:b", coverage_ref="proof:b")
        await conn.execute("UPDATE runtime.job_keys SET cancelled_at=now() WHERE job_id=%s", (replacement,))
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (other,))
    with pytest.raises(AppError, match="wake_cover_target_invalid"):
        await task(context(other), **old_payload)
    async with database.transaction(read_only=True) as conn:
        assert not await (await conn.execute("SELECT 1 FROM runtime.rbj_wake_exits WHERE job_id=%s", (other,))).fetchone()


async def test_retarget_existing_coverage_in_bounded_committed_pages(database, wake_queue):
    queue, _task, _calls, _state = wake_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        parent_payload, target_payload = {"kind": "normal"}, {"kind": "repair"}
        parent = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "parent", parent_payload,
            data_generation=generation), queueing_lock="parent", wake_object_key="same-object")
        target = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "target", target_payload,
            data_generation=generation), queueing_lock="target", wake_object_key="same-object")
        children = []
        for index in range(34):
            child_payload = {"historical": index}
            child = await queue.enqueue(conn, JobSpec("life.activity_reconcile", f"child-{index}", child_payload,
                                                       data_generation=generation))
            result = await queue.cover_wake_on(conn, child, expected_task="life.activity_reconcile",
                expected_generation=generation, expected_payload=child_payload, covered_by_job_id=parent,
                expected_covering_payload=parent_payload, wake_key="parent", coverage_ref=f"old-proof:{index}")
            assert result["state"] == "covered"
            children.append(child)
        first = await queue.cover_wake_on(conn, parent, expected_task="life.activity_reconcile", expected_generation=generation,
            expected_payload=parent_payload, covered_by_job_id=target, expected_covering_payload=target_payload,
            wake_key="target", coverage_ref="parent-to-target")
        assert first["state"] == "waiting" and first["reason"] == "wake_cover_partial"
    async with database.transaction() as conn:
        second = await queue.cover_wake_on(conn, parent, expected_task="life.activity_reconcile", expected_generation=generation,
            expected_payload=parent_payload, covered_by_job_id=target, expected_covering_payload=target_payload,
            wake_key="target", coverage_ref="parent-to-target")
        assert second["state"] == "covered"
        for child in children:
            assert (await queue.observe_on(conn, child))["covered_by"] == target
        facts = await (await conn.execute("SELECT count(*) AS total FROM runtime.rbj_wake_coverage_facts "
                                           "WHERE data_generation=%s", (generation,))).fetchone()
        assert facts["total"] == 69


async def test_saved_cover_exit_survives_later_flattening(database, wake_queue):
    from tianshu.runtime import rbj_wake

    queue, _task, _calls, _state = wake_queue
    async with database.transaction() as conn:
        generation = await queue.current_generation_on(conn)
        old_payload, parent_payload, target_payload = {"kind": "old"}, {"kind": "normal"}, {"kind": "repair"}
        old = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "history-old", old_payload, data_generation=generation))
        parent = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "history-parent", parent_payload,
            data_generation=generation), queueing_lock="history-parent", wake_object_key="history-object")
        target = await queue.enqueue(conn, JobSpec("life.activity_reconcile", "history-target", target_payload,
            data_generation=generation), queueing_lock="history-target", wake_object_key="history-object")
        await queue.cover_wake_on(conn, old, expected_task="life.activity_reconcile", expected_generation=generation,
            expected_payload=old_payload, covered_by_job_id=parent, expected_covering_payload=parent_payload,
            wake_key="history-parent", coverage_ref="original-cover")
        await conn.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (old,))
        assert await rbj_wake.record_exit_on(conn, task="life.activity_reconcile", generation=generation,
                                             job_id=old, native_attempt=0) == parent
    async with database.transaction() as conn:
        changed = await queue.cover_wake_on(conn, parent, expected_task="life.activity_reconcile", expected_generation=generation,
            expected_payload=parent_payload, covered_by_job_id=target, expected_covering_payload=target_payload,
            wake_key="history-target", coverage_ref="flatten-cover")
        assert changed["state"] == "covered"
        assert (await queue.observe_on(conn, old))["covered_by"] == target
        assert await rbj_wake.record_exit_on(conn, task="life.activity_reconcile", generation=generation,
                                             job_id=old, native_attempt=0) == parent
        fact = await (await conn.execute("SELECT covered_by FROM runtime.rbj_wake_exits WHERE job_id=%s", (old,))).fetchone()
        assert fact["covered_by"] == parent
