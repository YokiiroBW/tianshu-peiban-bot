"""Native admission and bounded scans preserve the original result identity."""

import asyncio
from importlib.resources import files
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from psycopg.types.json import Jsonb

from test_rbi_result_consumption import fail_native_job, fixture, publish
from test_rbj_result_repair import (
    CONSUMER, REPAIR_REF, exhaust, register_and_authorize, schedule_repair, setup,
)
from tianshu.conversation import rbi_result_wake
from tianshu.conversation import rbg_result_ledger as ledger
from tianshu.conversation.rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, ensure_on, execution_identity
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass


async def install_admission(database):
    await migrate(database, (Migration("rbj_native_admission",
        files("tianshu.runtime").joinpath("rbj_native_admission.sql").read_text(encoding="utf-8")),))


async def ordinary_fixture(database):
    value = await fixture(database)
    await migrate(database, tuple(Migration(name, files("tianshu.runtime").joinpath(name + ".sql").read_text(encoding="utf-8"))
                                  for name in ("rbj_wake", "rbj_execution_repair", "rbj_native_admission")))
    return value


async def start_native(database, job_id):
    async with database.transaction() as connection:
        worker = await (await connection.execute("INSERT INTO procrastinate_workers DEFAULT VALUES RETURNING id")).fetchone()
        native = await (await connection.execute(
            "UPDATE procrastinate_jobs SET status='doing',worker_id=%s WHERE id=%s RETURNING attempts",
            (worker["id"], job_id))).fetchone()
    return native["attempts"]


def context(job_id, ordinal):
    return SimpleNamespace(job=SimpleNamespace(id=job_id, attempts=ordinal, queueing_lock=None))


async def finish_native(database, job_id):
    async with database.transaction() as connection:
        await connection.execute("SELECT procrastinate_finish_job_v1(%s,'succeeded',false)", (job_id,))


async def retry_native(database, job_id, retry_at):
    async with database.transaction() as connection:
        await connection.execute("SELECT procrastinate_retry_job_v2(%s,%s,NULL,NULL,NULL)", (job_id, retry_at))
        clock = await (await connection.execute("SELECT clock_timestamp() AS now")).fetchone()
    await asyncio.sleep(max(0, (retry_at - clock["now"]).total_seconds()) + 0.05)


async def current_facts(database, generation, job_id):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute(
            "SELECT native_attempt,outcome,evidence_ref,resolved_outcome,observation_ref,recorded_at "
            "FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s ORDER BY native_attempt",
            (generation, job_id))).fetchall()


async def test_repair_observer_does_not_import_the_live_pre_begin_tail(database):
    value = await setup(database)
    await install_admission(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    job_id, generation = wake["job_id"], saved["data_generation"]
    ordinal = await start_native(database, job_id)
    candidates = await value.recovery.candidates(repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    assert candidates[0]["executing"] and not candidates[0]["eligible"]
    budget = await value.recovery._budget_for(generation, value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    assert budget["repair"]["reason"] == "execution_in_progress"
    inflight = next(item for item in budget["native_inflight"] if item["job_id"] == job_id)
    assert inflight["native_attempt"] == ordinal and inflight["purpose"] == "repair"
    assert inflight["verified"] and not inflight["is_current"] and inflight["started_event_id"]
    assert await current_facts(database, generation, job_id) == []

    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        await task(context(job_id, ordinal), event=value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    finally:
        await value.jobs.close()
    await finish_native(database, job_id)
    facts = await current_facts(database, generation, job_id)
    assert [(fact["native_attempt"], fact["outcome"]) for fact in facts] == [(ordinal, "completed")]
    observed = await schedule_repair(value, saved)
    assert observed["state"] == "verified" and observed["proof"]["last_receipt_id"] == "receipt"
    assert value.life.activity_calls == 1
    for external in (value.actions.invoke, value.actions.resume, value.renderer.on_action_result,
                     value.consumer.conversation.on_result, value.consumer.memory.observe_episode):
        external.assert_not_awaited()
    async with database.transaction(read_only=True) as connection:
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(generation, value.event), data_generation=generation,
            max_failures=MAX_FAILURES, repair_ref=REPAIR_REF, consumer=CONSUMER)
        assert budget["failed_executions"] == 4 and budget["repair_failed_executions"] == 0
        assert budget["unknown_held"] == 0


async def test_matching_done_proof_closes_only_from_the_real_current_repair(database):
    value = await setup(database)
    await install_admission(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    job_id, generation = wake["job_id"], saved["data_generation"]
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state='completed',last_result_revision=1,"
                                 "last_receipt_id='receipt' WHERE id='activity'")
        await connection.execute(
            "INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,consumer,action_id,"
            "result_revision,disposition,proof) VALUES (%s,'receipt','life_activity','action',1,'done',%s)",
            (generation, Jsonb({"activity_id": "activity", "last_result_revision": 1, "last_receipt_id": "receipt"})))
    ordinal = await start_native(database, job_id)
    budget = await value.recovery._budget_for(generation, value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    assert not budget["repair"]["eligible"] and budget["repair"]["reason"] == "execution_in_progress"
    async with database.transaction() as connection:
        forged = {"job_id": job_id, "native_attempt": ordinal, "data_generation": generation}
        with pytest.raises(AppError, match="execution_current_job_mismatch"):
            await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
                identity=execution_identity(generation, value.event), data_generation=generation,
                max_failures=MAX_FAILURES, repair_ref=REPAIR_REF, consumer=CONSUMER, current_job=forged)
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        await task(context(job_id, ordinal), event=value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    finally:
        await value.jobs.close()
    await finish_native(database, job_id)
    async with database.transaction(read_only=True) as connection:
        stop = await (await connection.execute(
            "SELECT decision FROM conversation.rbj_result_preflight_stops WHERE data_generation=%s AND job_id=%s",
            (generation, job_id))).fetchone()
    assert stop["decision"] == "closed" and await current_facts(database, generation, job_id) == []
    assert value.life.activity_calls == 0 and (await schedule_repair(value, saved))["state"] == "verified"


async def test_original_imported_unknown_can_be_reconciled_without_replacing_the_repair(database):
    """The caller supplies the separate original-log and domain-CAS proof."""
    value = await setup(database)
    await install_admission(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    job_id, generation = wake["job_id"], saved["data_generation"]
    assert await start_native(database, job_id) == 0
    identity = execution_identity(generation, value.event)
    async with database.transaction() as connection:
        started = await (await connection.execute("SELECT id FROM procrastinate_events "
            "WHERE job_id=%s AND type='started' ORDER BY id DESC LIMIT 1", (job_id,))).fetchone()
        original = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        await value.jobs.seed_execution_history_on(connection, owner=EXECUTION_OWNER, identity=identity,
            data_generation=generation, max_failures=MAX_FAILURES, complete=True,
            history=[{"job_id": job_id, "native_attempt": 0, "outcome": "unknown",
                      "evidence_ref": f"native-job-event:{started['id']}"}])
        await connection.execute("SELECT procrastinate_retry_job_v2(%s,clock_timestamp(),NULL,NULL,NULL)", (job_id,))
    before = (await current_facts(database, generation, job_id))[0]
    assert before["outcome"] == "unknown" and before["resolved_outcome"] is None
    assert await start_native(database, job_id) == 1
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        await task(context(job_id, 1), event=value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
        await finish_native(database, job_id)
        async with database.transaction(read_only=True) as connection:
            later = await (await connection.execute("SELECT decision,reason FROM conversation.rbj_result_preflight_stops "
                "WHERE data_generation=%s AND job_id=%s AND native_attempt=1", (generation, job_id))).fetchone()
        assert later == {"decision": "unknown", "reason": "execution_outcome_unknown"}
        for _ in range(8):
            budget = await value.recovery._budget_for(generation, value.event,
                repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
            if budget.get("history_complete"):
                break
        assert budget["history_complete"] and budget["unknown_held"] == 1
        # This narrow Runtime primitive verifies mechanics, not the separate
        # caller-owned source, head, Life CAS and original-error-log evidence.
        async with database.transaction() as connection:
            resolved = await value.jobs.resolve_execution_on(connection, owner=EXECUTION_OWNER, identity=identity,
                data_generation=generation, job_id=job_id, native_attempt=0, outcome="not_started",
                retry_at=None, observation_ref="fixture:verified-original-ordinal-0")
        assert resolved["recorded"]
        after = (await current_facts(database, generation, job_id))[0]
        assert (after["outcome"], after["evidence_ref"], after["recorded_at"]) == (
            before["outcome"], before["evidence_ref"], before["recorded_at"])
        assert after["resolved_outcome"] == "not_started"
        retry = await schedule_repair(value, saved)
        assert retry["state"] == "accepted" and retry["job_id"] != job_id
        next_ordinal = await start_native(database, retry["job_id"])
        await task(context(retry["job_id"], next_ordinal), event=value.event,
                   repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
        await finish_native(database, retry["job_id"])
    finally:
        await value.jobs.close()
    async with database.transaction(read_only=True) as connection:
        still_original = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER, identity=identity,
            data_generation=generation, max_failures=MAX_FAILURES, repair_ref=REPAIR_REF, consumer=CONSUMER)
    assert still_original == original and budget["failed_executions"] == 4
    assert budget["repair_failed_executions"] == 0 and budget["unknown_held"] == 0
    assert (await schedule_repair(value, saved))["state"] == "verified" and value.life.activity_calls == 1
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


async def test_new_normal_observer_cannot_poison_its_own_admission(database):
    value = await ordinary_fixture(database)
    saved = await publish(value, database)
    job_id, generation = saved["original_job_id"], saved["data_generation"]
    ordinal = await start_native(database, job_id)
    candidates = await value.recovery.candidates()
    assert candidates[0]["executing"] and not candidates[0]["eligible"]
    budget = await value.recovery._budget_for(generation, value.event)
    assert budget["reason"] == "execution_in_progress"
    tail = next(item for item in budget["native_inflight"] if item["job_id"] == job_id)
    assert tail["purpose"] == "normal" and tail["grant_id"] is None and not tail["is_current"]
    assert await current_facts(database, generation, job_id) == []
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        await task(context(job_id, ordinal), event=value.event)
    finally:
        await value.jobs.close()
    await finish_native(database, job_id)
    facts = await current_facts(database, generation, job_id)
    assert [(fact["native_attempt"], fact["outcome"]) for fact in facts] == [(ordinal, "completed")]
    async with database.transaction(read_only=True) as connection:
        delivery = await (await connection.execute(
            "SELECT state,original_job_id FROM conversation.result_deliveries WHERE receipt_id='receipt'")).fetchone()
        effect = await (await connection.execute("SELECT count(*) AS n FROM test_rbi_effects WHERE receipt_id='receipt'")).fetchone()
        original = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
    assert delivery == {"state": "completed", "original_job_id": job_id} and effect["n"] == 1
    assert original["args"]["event"] == value.event
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


async def test_real_domain_repair_commits_once_after_a_foreign_live_observation(empty_database, tmp_path, record_property):
    spec = spec_from_file_location("rbj_native_domain_fixture",
        Path(__file__).parents[1] / "runtime" / "test_rbj_domain_result_repair.py")
    domain = module_from_spec(spec)
    spec.loader.exec_module(domain)
    value = await domain.prepare(empty_database, tmp_path, record_property)
    ordinal = await start_native(empty_database, value.repair_job_id)
    budget = await value.recovery._budget_for(value.generation, value.event,
        repair_ref=domain.REPAIR_REF, repair_consumer=domain.CONSUMER)
    assert budget["repair"]["reason"] == "execution_in_progress"
    assert await current_facts(empty_database, value.generation, value.repair_job_id) == []
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        await task(context(value.repair_job_id, ordinal), event=value.event,
                   repair_ref=domain.REPAIR_REF, repair_consumer=domain.CONSUMER)
    finally:
        await value.jobs.close()
    await finish_native(empty_database, value.repair_job_id)
    async with empty_database.transaction(read_only=True) as connection:
        activity = await (await connection.execute(
            "SELECT state,last_result_revision,last_receipt_id FROM life.activities WHERE id='reading'")).fetchone()
        goal = await (await connection.execute("SELECT progress,state FROM life.goals WHERE id='goal'")).fetchone()
        goal_proofs = await (await connection.execute("SELECT action_id,result_revision FROM life.goal_progress_receipts WHERE goal_id='goal'")).fetchall()
        originals = await (await connection.execute("SELECT revision FROM sources.versions "
            "WHERE evidence_unit='life:action:result:original-action'")).fetchall()
        episodes = await (await connection.execute("SELECT receipt_id,event_revision FROM memory.episodes WHERE event_id='action:original-action'")).fetchall()
        head = await (await connection.execute("SELECT action,attempt,receipt,result_revision "
            "FROM capabilities.actions WHERE id=%s", (value.action.action_id,))).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(value.generation, value.event), data_generation=value.generation,
            max_failures=MAX_FAILURES, repair_ref=domain.REPAIR_REF, consumer=domain.CONSUMER)
    assert activity == {"state": "completed", "last_result_revision": 2, "last_receipt_id": value.result.receipt_id}
    assert goal == {"progress": 1, "state": "completed"}
    assert goal_proofs == [{"action_id": value.action.action_id, "result_revision": 2}]
    assert originals == [{"revision": 2}]
    assert episodes == [{"receipt_id": value.result.receipt_id, "event_revision": 2}]
    assert head == {"action": wire(value.action), "attempt": wire(value.attempt),
                    "receipt": wire(value.result), "result_revision": 2}
    assert budget["failed_executions"] == 4 and budget["repair_failed_executions"] == 0
    assert (await value.recovery.recover_result(value.original_job_id, **value.expected,
        repair_ref=domain.REPAIR_REF, repair_consumer=domain.CONSUMER))["state"] == "verified"
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()
    value.models.submit.assert_not_awaited()


async def test_adopt_and_coalesce_do_not_mark_an_old_normal_job(database):
    value = await ordinary_fixture(database)
    async with database.transaction() as connection:
        generation = await value.jobs.current_generation_on(connection)
        job_id = await value.jobs.enqueue(connection, JobSpec("conversation.result", "capability.result:receipt",
            {"event": value.event}, ServiceClass.INTERACTIVE, data_generation=generation))
        wake = await ensure_on(connection, value.jobs, generation, value.event,
                               initial_key="capability.result:receipt", allow_successor=True)
        assert wake["job_id"] == job_id
        key = f"conversation.result:{generation}:action:1:receipt"
        same = await value.jobs.enqueue(connection, JobSpec("conversation.result", "coalesced-fixture",
            {"event": value.event}, ServiceClass.INTERACTIVE, data_generation=generation,
            execution_identity=execution_identity(generation, value.event)), queueing_lock=key, wake_object_key=key)
        assert same == job_id
    ordinal = await start_native(database, job_id)
    budget = await value.recovery._budget_for(generation, value.event)
    assert not budget["eligible"] and budget["reason"] == "execution_outcome_unknown"
    assert [(fact["native_attempt"], fact["outcome"]) for fact in await current_facts(database, generation, job_id)] == [(ordinal, "unknown")]

    async def attempt_stop(event):
        async with database.transaction() as connection:
            return await value.jobs.record_execution_preflight_stop_on(connection, owner=EXECUTION_OWNER,
                identity=execution_identity(generation, event), data_generation=generation,
                job_id=job_id, native_attempt=ordinal, reason="history_more", observation_ref="legacy-fixture")

    task = value.jobs.register("conversation.result", attempt_stop, resource_recovery=True)
    await value.jobs.open()
    try:
        stopped = await task(context(job_id, ordinal), event=value.event)
    finally:
        await value.jobs.close()
    assert stopped["state"] == "unregistered" and stopped["reason"] == "execution_admission_unregistered"
    assert value.life.activity_calls == 0


@pytest.mark.parametrize("paged", [False, True])
async def test_new_normal_noop_preserves_an_earlier_true_unknown(database, monkeypatch, paged):
    value = await ordinary_fixture(database)
    saved = await publish(value, database)
    generation, old_id = saved["data_generation"], saved["original_job_id"]
    original_consume = value.consumer._consume_result
    value.consumer._consume_result = AsyncMock(side_effect=OSError("uncertain fixture commit"))
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        old_ordinal = await start_native(database, old_id)
        with pytest.raises(OSError, match="uncertain fixture commit"):
            await task(context(old_id, old_ordinal), event=value.event)
        async with database.transaction() as connection:
            await connection.execute("SELECT procrastinate_finish_job_v1(%s,'failed',false)", (old_id,))
        before = await current_facts(database, generation, old_id)
        assert len(before) == 1 and before[0]["outcome"] == "unknown"
        value.consumer._consume_result = original_consume
        key = f"conversation.result:{generation}:action:1:receipt"
        async with database.transaction() as connection:
            new_id = await value.jobs.enqueue(connection, JobSpec("conversation.result", "normal-after-unknown",
                {"event": value.event}, ServiceClass.INTERACTIVE, subject_id=value.event["context"]["actor_id"],
                data_generation=generation,
                execution_identity=execution_identity(generation, value.event)), queueing_lock=key, wake_object_key=key)
            if paged:
                await connection.execute("UPDATE conversation.result_deliveries SET rbi_history_cursor=0,"
                    "rbi_history_upper=NULL,rbi_open_cursor=0,rbi_history_gap=false "
                    "WHERE data_generation=%s AND receipt_id='receipt'", (generation,))
        if paged:
            monkeypatch.setattr(rbi_result_wake, "HISTORY_JOBS_PER_TICK", 1)
        ordinal = await start_native(database, new_id)
        await task(context(new_id, ordinal), event=value.event)
        await finish_native(database, new_id)
    finally:
        await value.jobs.close()
    after = await current_facts(database, generation, old_id)
    assert before == after and await current_facts(database, generation, new_id) == []
    for _ in range(8):
        budget = await value.recovery._budget_for(generation, value.event)
        if budget.get("history_complete"):
            break
    assert budget["history_complete"]
    async with database.transaction(read_only=True) as connection:
        stop = await (await connection.execute(
            "SELECT kind,reason FROM runtime.rbj_wake_exits WHERE data_generation=%s AND job_id=%s AND native_attempt=%s",
            (generation, new_id, ordinal))).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(generation, value.event), data_generation=generation, max_failures=MAX_FAILURES)
    assert stop["reason"] == "unknown" and budget["reason"] == "execution_outcome_unknown"
    assert budget["unknown_held"] == 1 and value.life.activity_calls == 0
    value.actions.invoke.assert_not_awaited()


async def test_missing_history_anchor_is_unknown_not_a_page_to_retry(database):
    value = await setup(database)
    await install_admission(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    job_id, generation = wake["job_id"], saved["data_generation"]
    async with database.transaction() as connection:
        deleted = await (await connection.execute(
            "DELETE FROM procrastinate_events WHERE id=(SELECT event_id FROM runtime.rbi_execution_history_cursors "
            "WHERE job_id=%s) RETURNING id", (saved["original_job_id"],))).fetchone()
        assert deleted is not None
        await connection.execute("UPDATE conversation.result_deliveries SET rbi_history_cursor=0,rbi_history_upper=NULL,"
            "rbi_open_cursor=0,rbi_history_gap=false WHERE data_generation=%s AND receipt_id='receipt'", (generation,))
    ordinal = await start_native(database, job_id)
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        await task(context(job_id, ordinal), event=value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    finally:
        await value.jobs.close()
    async with database.transaction(read_only=True) as connection:
        delivery = await (await connection.execute(
            "SELECT rbi_history_gap FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id='receipt'",
            (generation,))).fetchone()
        stop = await (await connection.execute("SELECT decision,reason FROM conversation.rbj_result_preflight_stops "
            "WHERE data_generation=%s AND job_id=%s AND native_attempt=%s", (generation, job_id, ordinal))).fetchone()
    assert delivery["rbi_history_gap"] and stop == {"decision": "unknown", "reason": "history_unknown"}
    assert await current_facts(database, generation, job_id) == [] and value.life.activity_calls == 0
    value.actions.invoke.assert_not_awaited()


@pytest.mark.parametrize("valid_lease", [True, False])
async def test_repair_page_does_not_hide_consumer_lease_state(database, monkeypatch, valid_lease):
    value = await setup(database)
    await install_admission(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    job_id, generation = wake["job_id"], saved["data_generation"]
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, generation, value.event, wire(value.action), "actor")
        claim = await (await connection.execute(
            "UPDATE conversation.result_consumer_progress SET disposition='running',owner_token=%s,"
            "lease_until=clock_timestamp()+interval '1 minute' WHERE data_generation=%s AND receipt_id='receipt' "
            "AND consumer='renderer' RETURNING owner_token,lease_until",
            ("live-owner" if valid_lease else None, generation))).fetchone()
        await connection.execute("UPDATE conversation.result_deliveries SET rbi_history_cursor=0,rbi_history_upper=NULL,"
            "rbi_open_cursor=0,rbi_history_gap=false WHERE data_generation=%s AND receipt_id='receipt'", (generation,))
    monkeypatch.setattr(rbi_result_wake, "HISTORY_JOBS_PER_TICK", 2)
    ordinal = await start_native(database, job_id)
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    try:
        if valid_lease:
            with pytest.raises(RecoveryWait) as waited:
                await task(context(job_id, ordinal), event=value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
            assert waited.value.reason == "consumer_running" and waited.value.retry_at == claim["lease_until"]
        else:
            await task(context(job_id, ordinal), event=value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    finally:
        await value.jobs.close()
    async with database.transaction(read_only=True) as connection:
        stopped = await (await connection.execute("SELECT decision,reason FROM conversation.rbj_result_preflight_stops "
            "WHERE data_generation=%s AND job_id=%s AND native_attempt=%s", (generation, job_id, ordinal))).fetchone()
        after = await (await connection.execute("SELECT owner_token,lease_until FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='renderer'", (generation,))).fetchone()
    assert after == claim
    assert stopped == ({"decision": "blocked", "reason": "consumer_running"} if valid_lease else
                       {"decision": "unknown", "reason": "result_consumer_lease_unknown"})
    assert await current_facts(database, generation, job_id) == [] and value.life.activity_calls == 0


@pytest.mark.parametrize("repairing", [False, True])
async def test_bounded_history_pages_retry_the_same_native_job(database, monkeypatch, repairing):
    value = await (setup(database) if repairing else ordinary_fixture(database))
    if repairing:
        await install_admission(database)
        saved = await exhaust(database, value)
        assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
        wake = await schedule_repair(value, saved)
        job_id = wake["job_id"]
    else:
        saved = await publish(value, database)
        job_id = saved["original_job_id"]
        for _ in range(3):
            async with database.transaction() as connection:
                await fail_native_job(connection, job_id)
                wake = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
            assert wake["state"] == "accepted"
            job_id = wake["job_id"]
    generation = saved["data_generation"]
    async with database.transaction() as connection:
        original = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        await connection.execute("UPDATE conversation.result_deliveries SET rbi_history_cursor=0,rbi_history_upper=NULL,"
            "rbi_open_cursor=0,rbi_history_gap=false WHERE data_generation=%s AND receipt_id='receipt'", (generation,))
    monkeypatch.setattr(rbi_result_wake, "HISTORY_JOBS_PER_TICK", 2)
    task = value.jobs.register("conversation.result", value.consumer.consume, retryable=True, resource_recovery=True)
    await value.jobs.open()
    waits = []
    try:
        for _ in range(12):
            ordinal = await start_native(database, job_id)
            payload = {"event": value.event}
            if repairing:
                payload.update(repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
            try:
                await task(context(job_id, ordinal), **payload)
            except RecoveryWait as wait:
                assert wait.reason == "execution_history_more" and wait.dependency == "runtime:execution_history"
                assert await current_facts(database, generation, job_id) == []
                waits.append(ordinal)
                await retry_native(database, job_id, wait.retry_at)
            else:
                await finish_native(database, job_id)
                break
        else:
            pytest.fail("the bounded history cursor did not reach its tail")
    finally:
        await value.jobs.close()
    assert waits and ordinal > waits[-1]
    facts = await current_facts(database, generation, job_id)
    assert [(fact["native_attempt"], fact["outcome"]) for fact in facts] == [(ordinal, "completed")]
    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
        exits = await (await connection.execute(
            "SELECT native_attempt FROM runtime.rbj_wake_exits WHERE data_generation=%s AND job_id=%s ORDER BY native_attempt",
            (generation, job_id))).fetchall()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(generation, value.event), data_generation=generation, max_failures=MAX_FAILURES,
            **({"repair_ref": REPAIR_REF, "consumer": CONSUMER} if repairing else {}))
    assert original == after and [row["native_attempt"] for row in exits] == waits
    assert budget["failed_executions"] == (4 if repairing else 3) and budget["unknown_held"] == 0
    assert value.life.activity_calls == 1
