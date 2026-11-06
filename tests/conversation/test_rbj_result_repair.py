"""Original-result repair: persisted grant, one consumer and atomic proof."""

from dataclasses import replace
from importlib.resources import files
from unittest.mock import AsyncMock

import pytest
from psycopg.types.json import Jsonb

from test_rbi_result_consumption import fail_native_job, fixture, publish
from tianshu.conversation import rbg_result_ledger as ledger
from tianshu.conversation.codec import receipt as decode_receipt, signature
from tianshu.conversation.rbi_result_wake import EXECUTION_OWNER, MAX_FAILURES, ensure_on, execution_identity
from tianshu.conversation.results import ActionResultEvents
from tianshu.runtime import jobs as runtime_jobs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass


REPAIR_REF = "rbj-source-repair"
REPAIR_REVISION = "verified-revision"
DEFECT_KEY = "source-proof-depth"
CONSUMER = "life_activity"


class AtomicLife:
    def __init__(self, database):
        self.database = database
        self.fail_stage = None
        self.activity_calls = 0
        self._commitment_result = AsyncMock()
        self.detached_result = AsyncMock()

    async def inspect_result_repair_on(self, connection, context, result, result_revision, *,
                                       consumer, owner_id, data_generation):
        if consumer != CONSUMER:
            return {"state": "unknown", "reason": "owner_unconfirmed"}
        row = await (await connection.execute(
            "SELECT id,actor_id,action_id,state,revision,last_result_revision,last_receipt_id "
            "FROM life.activities WHERE id='activity' FOR UPDATE")).fetchone()
        if not row or row["actor_id"] != context.actor_id or row["action_id"] != result.action_id:
            return {"state": "unknown", "reason": "owner_unconfirmed"}
        if owner_id is not None and owner_id != row["id"]:
            return {"state": "superseded", "reason": "owner_changed", "owner_id": owner_id}
        if row["last_result_revision"] >= result_revision:
            return {"state": "unknown", "reason": "local_result_application_unconfirmed"}
        if row["state"] == "cancelled":
            return {"state": "superseded", "reason": "activity_cancelled", "owner_id": row["id"]}
        return {"state": "not_applied", "reason": "original_result_not_applied",
                "owner_id": row["id"], "owner_revision": row["revision"]}

    async def _activity_result(self, context, result, revision, observed_at, capability_id, *,
                               on_complete, expected_owner_id=None):
        self.activity_calls += 1
        async with self.database.transaction() as connection:
            await on_complete.begin(connection)
            await on_complete.fence(connection)
            row = await (await connection.execute(
                "SELECT id,action_id,last_result_revision FROM life.activities WHERE id='activity' FOR UPDATE"
            )).fetchone()
            if not row or row["id"] != expected_owner_id or row["action_id"] != result.action_id:
                await on_complete(connection, "superseded", reason="owner_changed")
                return
            if row["last_result_revision"] >= revision:
                raise AppError("local_result_application_unconfirmed", status=409)
            for stage in ("source", "goal", "memory"):
                await connection.execute(f"INSERT INTO test_rbj_{stage}(receipt_id) VALUES (%s)", (result.receipt_id,))
                if self.fail_stage == stage:
                    raise RuntimeError("fixture_" + stage)
            await connection.execute(
                "UPDATE life.activities SET state='completed',last_result_revision=%s,last_receipt_id=%s "
                "WHERE id='activity'", (revision, result.receipt_id))
            if self.fail_stage == "proof":
                raise RuntimeError("fixture_proof")
            await on_complete(connection, proof={"activity_id": "activity", "last_result_revision": revision,
                                                 "last_receipt_id": result.receipt_id})


async def setup(database):
    value = await fixture(database)
    await migrate(database, (
        Migration("rbj_wake", files("tianshu.runtime").joinpath("rbj_wake.sql").read_text(encoding="utf-8")),
        Migration("rbj_execution_repair", files("tianshu.runtime").joinpath("rbj_execution_repair.sql").read_text(encoding="utf-8")),
        Migration("rbj_result_repair_evidence",
                  files("tianshu.conversation").joinpath("rbj_result_repair_evidence.sql").read_text(encoding="utf-8")),
    ))
    async with database.transaction() as connection:
        for stage in ("source", "goal", "memory"):
            await connection.execute(f"CREATE TABLE test_rbj_{stage}(receipt_id text PRIMARY KEY)")
    value.life = AtomicLife(database)
    value.consumer.life_worker = value.life
    value.recovery.life_worker = value.life
    value.renderer.on_action_result = AsyncMock()
    value.consumer.conversation.on_result = AsyncMock()
    return value


async def exhaust(database, value):
    saved = await publish(value, database)
    job_id = saved["original_job_id"]
    for attempt in range(4):
        async with database.transaction() as connection:
            await fail_native_job(connection, job_id)
        if attempt < 3:
            async with database.transaction() as connection:
                wake = await ensure_on(connection, value.jobs, saved["data_generation"], value.event)
            assert wake["state"] == "accepted"
            job_id = wake["job_id"]
    return saved


async def register_and_authorize(database, value, saved):
    async with database.transaction() as connection:
        await value.jobs.register_execution_repair_on(connection, owner=EXECUTION_OWNER, repair_ref=REPAIR_REF,
            repair_revision=REPAIR_REVISION, defect_key=DEFECT_KEY, consumer=CONSUMER)
    return await value.recovery.authorize_local_repair(saved["original_job_id"],
        data_generation=saved["data_generation"], action_id="action", receipt_id="receipt", result_revision=1,
        repair_ref=REPAIR_REF, repair_revision=REPAIR_REVISION, defect_key=DEFECT_KEY,
        repair_consumer=CONSUMER, defect_evidence_ref="verified-native-failure-evidence",
        material_digest=signature(value.event))


async def schedule_repair(value, saved):
    return await value.recovery.recover_result(saved["original_job_id"],
        data_generation=saved["data_generation"], action_id="action", receipt_id="receipt", result_revision=1,
        repair_ref=REPAIR_REF, repair_consumer=CONSUMER)


async def run_native(database, value, saved, job_id):
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (job_id,))
        row = await (await connection.execute("SELECT attempts FROM procrastinate_jobs WHERE id=%s", (job_id,))).fetchone()
    generation_token = runtime_jobs._execution_generation.set(saved["data_generation"])
    job_token = runtime_jobs._execution_identity.set((job_id, row["attempts"], saved["data_generation"]))
    try:
        return await value.consumer.consume(value.event, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    finally:
        runtime_jobs._execution_identity.reset(job_token)
        runtime_jobs._execution_generation.reset(generation_token)


@pytest.mark.asyncio
async def test_queued_legacy_wake_does_not_hide_four_exhausted_failures(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    async with database.transaction() as connection:
        legacy = await value.jobs.enqueue(connection, JobSpec("conversation.result", "legacy-unlocked-rbj",
            {"event": value.event}, ServiceClass.INTERACTIVE, subject_id=value.event["context"]["actor_id"],
            data_generation=saved["data_generation"]))
        wake = await ActionResultEvents(value.jobs).ensure_on(connection, value.event)
    assert wake["state"] == "blocked" and wake["reason"] == "execution_failure_budget_exhausted"
    assert wake["budget"]["failed_executions"] == 4
    assert "life_activity" not in wake["consumer_progress"]
    facts = await value.recovery.inspect(None, {"id": str(saved["original_job_id"])})
    candidates = await value.recovery.candidates()
    observed = await value.recovery.observe(None, None, facts, "observer")
    assert not facts["eligible"] and facts["reason"] == "execution_failure_budget_exhausted"
    assert not candidates[0]["eligible"] and candidates[0]["reason"] == facts["reason"]
    assert observed["state"] == "failed" and observed["reason"] == facts["reason"]
    async with database.transaction(read_only=True) as connection:
        assert (await value.jobs.observe_on(connection, legacy))["job_state"] == "todo"


@pytest.mark.asyncio
async def test_legacy_wake_with_different_subject_is_not_adopted(database):
    value = await setup(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        legacy = await value.jobs.enqueue(connection, JobSpec("conversation.result", "legacy-other-subject",
            {"event": value.event}, ServiceClass.INTERACTIVE, data_generation=saved["data_generation"]))
        original = await (await connection.execute(
            "SELECT subject_id,matter_id,payload_version,payload_digest,wake_key,wake_object_key "
            "FROM runtime.job_keys WHERE job_id=%s", (legacy,))).fetchone()
    with pytest.raises(AppError, match="job_identity_conflict"):
        async with database.transaction() as connection:
            await ActionResultEvents(value.jobs).ensure_on(connection, value.event)
    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute(
            "SELECT subject_id,matter_id,payload_version,payload_digest,wake_key,wake_object_key "
            "FROM runtime.job_keys WHERE job_id=%s", (legacy,))).fetchone()
        assert (await value.jobs.observe_on(connection, legacy))["job_state"] == "todo"
    assert original["subject_id"] is None and after == original


@pytest.mark.asyncio
async def test_trusted_target_grant_is_idempotent_and_only_life_runs(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    untrusted = await schedule_repair(value, saved)
    assert untrusted["state"] == "blocked" and untrusted["reason"] == "repair_evidence_missing"
    first = await register_and_authorize(database, value, saved)
    again = await register_and_authorize(database, value, saved)
    assert first["state"] == again["state"] == "accepted"
    assert first["failure_digest"] == again["failure_digest"] and first["owner_id"] == "activity"
    wake = await schedule_repair(value, saved)
    assert wake["state"] == "accepted"
    await run_native(database, value, saved, wake["job_id"])
    async with database.transaction(read_only=True) as connection:
        progress = await (await connection.execute(
            "SELECT disposition,proof FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'")).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(saved["data_generation"], value.event),
            data_generation=saved["data_generation"], max_failures=MAX_FAILURES,
            repair_ref=REPAIR_REF, consumer=CONSUMER)
    assert progress["disposition"] == "done" and progress["proof"]["last_receipt_id"] == "receipt"
    assert budget["failed_executions"] == 4 and not budget["eligible"]
    assert value.life.activity_calls == 1
    value.renderer.on_action_result.assert_not_awaited()
    value.consumer.conversation.on_result.assert_not_awaited()
    value.consumer.memory.observe_episode.assert_not_awaited()
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()
    value.life.detached_result.assert_not_awaited()


@pytest.mark.asyncio
async def test_finished_repair_reobserves_original_proof_without_reapplying(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    await run_native(database, value, saved, wake["job_id"])
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded',attempts=attempts+1 WHERE id=%s",
                                 (wake["job_id"],))
        grant_before = await (await connection.execute(
            "SELECT closed_at,close_observation_ref,failure_digest FROM runtime.rbj_execution_repair_grants "
            "WHERE repair_ref=%s AND consumer=%s", (REPAIR_REF, CONSUMER))).fetchone()
        fact_before = await (await connection.execute(
            "SELECT outcome,resolved_outcome,observation_ref FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
            (saved["data_generation"], wake["job_id"]))).fetchone()
    assert grant_before["close_observation_ref"] == f"execution:{wake['job_id']}:0:completed"
    assert fact_before == {"outcome": "completed", "resolved_outcome": None, "observation_ref": None}
    for _ in range(2):
        observed = await schedule_repair(value, saved)
        assert observed["state"] == "verified" and observed["disposition"] == "done"
        assert observed["proof"] == {"activity_id": "activity", "last_result_revision": 1,
                                     "last_receipt_id": "receipt"}
    async with database.transaction() as connection:
        grant_after = await (await connection.execute(
            "SELECT closed_at,close_observation_ref,failure_digest FROM runtime.rbj_execution_repair_grants "
            "WHERE repair_ref=%s AND consumer=%s", (REPAIR_REF, CONSUMER))).fetchone()
        fact_after = await (await connection.execute(
            "SELECT outcome,resolved_outcome,observation_ref FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
            (saved["data_generation"], wake["job_id"]))).fetchone()
        # Simulate a conflicting persisted proof without changing its original identity.
        await connection.execute(
            "UPDATE conversation.result_consumer_progress SET proof=proof || %s::jsonb "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='life_activity'",
            (Jsonb({"conflicting_evidence": True}), saved["data_generation"]))
    assert grant_after == grant_before
    assert fact_after["outcome"] == "completed" and fact_after["resolved_outcome"] is None
    assert fact_after["observation_ref"].startswith("result-proof:")
    with pytest.raises(AppError, match="execution_repair_close_conflict"):
        await schedule_repair(value, saved)
    async with database.transaction(read_only=True) as connection:
        fact_final = await (await connection.execute(
            "SELECT outcome,resolved_outcome,observation_ref FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
            (saved["data_generation"], wake["job_id"]))).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(saved["data_generation"], value.event),
            data_generation=saved["data_generation"], max_failures=MAX_FAILURES,
            repair_ref=REPAIR_REF, consumer=CONSUMER)
    assert fact_final == fact_after and budget["failed_executions"] == 4
    assert value.life.activity_calls == 1
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["source", "goal", "memory", "proof"])
async def test_repair_local_effects_and_proof_roll_back_together(database, stage):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    value.life.fail_stage = stage
    with pytest.raises(RuntimeError, match="fixture_"):
        await run_native(database, value, saved, wake["job_id"])
    async with database.transaction(read_only=True) as connection:
        for table in ("source", "goal", "memory"):
            row = await (await connection.execute(f"SELECT count(*) AS count FROM test_rbj_{table}")).fetchone()
            assert row["count"] == 0
        activity = await (await connection.execute(
            "SELECT state,last_result_revision,last_receipt_id FROM life.activities WHERE id='activity'")).fetchone()
        progress = await (await connection.execute(
            "SELECT disposition FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'")).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(saved["data_generation"], value.event),
            data_generation=saved["data_generation"], max_failures=MAX_FAILURES,
            repair_ref=REPAIR_REF, consumer=CONSUMER)
    assert activity == {"state": "running", "last_result_revision": -1, "last_receipt_id": None}
    assert progress["disposition"] != "done"
    assert budget["failed_executions"] == 4 and budget["repair"]["remaining"] == 0
    assert not budget["repair"]["eligible"]


@pytest.mark.asyncio
async def test_done_proof_reconciles_lost_finish_without_reapplying(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    finish = value.jobs.finish_execution_on
    value.jobs.finish_execution_on = AsyncMock(side_effect=OSError("lost_finish_ack"))
    with pytest.raises(OSError, match="lost_finish_ack"):
        await run_native(database, value, saved, wake["job_id"])
    value.jobs.finish_execution_on = finish
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='failed',attempts=attempts+1 WHERE id=%s",
                                 (wake["job_id"],))
    async with database.transaction() as connection:
        assert await value.identity.validate_persistence(connection, value.action.context, ())
        await value.actions.assert_result_head_on(connection, value.action.context, action_id="action",
            expected_receipt=value.event["receipt"], result_revision=1,
            data_generation=saved["data_generation"], source_refs=())
        stranded = await ensure_on(connection, value.jobs, saved["data_generation"], value.event,
            reconcile_stranded=True, repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    assert stranded["budget"]["unknown_held"] == stranded["budget"]["active_executions"] == 0
    assert stranded["budget"]["failed_executions"] == 4 and stranded["budget"]["repair"]["reason"] == "repair_closed"
    reconciled = await schedule_repair(value, saved)
    assert reconciled["state"] == "verified"
    assert value.life.activity_calls == 1
    async with database.transaction(read_only=True) as connection:
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(saved["data_generation"], value.event),
            data_generation=saved["data_generation"], max_failures=MAX_FAILURES,
            repair_ref=REPAIR_REF, consumer=CONSUMER)
    assert budget["failed_executions"] == 4 and budget["unknown_held"] == 0
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_committed_proof_before_begin_closes_grant_without_spending_attempt(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state='completed',last_result_revision=1,"
                                 "last_receipt_id='receipt' WHERE id='activity'")
        await connection.execute(
            "INSERT INTO conversation.result_consumer_progress(data_generation,receipt_id,consumer,action_id,"
            "result_revision,disposition,proof) VALUES (%s,'receipt','life_activity','action',1,'done',%s)",
            (saved["data_generation"], Jsonb({"activity_id": "activity", "last_result_revision": 1,
                                              "last_receipt_id": "receipt"})))
    await run_native(database, value, saved, wake["job_id"])
    async with database.transaction() as connection:
        stop = await (await connection.execute("SELECT decision FROM conversation.rbj_result_preflight_stops "
            "WHERE data_generation=%s AND job_id=%s", (saved["data_generation"], wake["job_id"]))).fetchone()
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded',attempts=attempts+1 WHERE id=%s",
                                 (wake["job_id"],))
    budget = await value.recovery._budget_for(saved["data_generation"], value.event,
                                              repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    assert stop["decision"] == "closed" and budget["unknown_held"] == 0
    assert budget["failed_executions"] == 4 and value.life.activity_calls == 0
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_revision_different_receipt_is_never_treated_as_new_head(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    value.actions.current = replace(decode_receipt(value.event["receipt"]), receipt_id="different")
    with pytest.raises(AppError, match="result_receipt_revision_conflict"):
        await value.recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
            action_id="action", receipt_id="receipt", result_revision=1)
    with pytest.raises(AppError, match="result_receipt_revision_conflict"):
        await value.consumer._head_changed(value.event, value.action.context, saved["data_generation"])
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_newer_head_supersedes_old_event_and_wakes_only_the_new_receipt(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    value.actions.current = replace(decode_receipt(value.event["receipt"]), receipt_id="receipt-new")
    value.actions.revision = 2
    result = await value.recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
        action_id="action", receipt_id="receipt", result_revision=1)
    assert result["state"] == "accepted" and result["superseded_receipt_id"] == "receipt"
    async with database.transaction(read_only=True) as connection:
        rows = await (await connection.execute(
            "SELECT receipt_id,state,result_revision FROM conversation.result_deliveries ORDER BY receipt_id"
        )).fetchall()
        original = await (await connection.execute("SELECT disposition,proof FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'")).fetchone()
        aggregate = await (await connection.execute("SELECT 1 FROM conversation.result_consumption "
                                                    "WHERE receipt_id='receipt'")).fetchone()
    assert rows == [{"receipt_id": "receipt", "state": "superseded", "result_revision": 1},
                    {"receipt_id": "receipt-new", "state": "pending", "result_revision": 2}]
    assert original == {"disposition": "superseded", "proof": {"activity_id": "activity"}}
    assert aggregate is None  # Other original consumers still have no proof.
    value.actions.invoke.assert_not_awaited()
    value.actions.resume.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_head_does_not_rewrite_old_consumer_unknown(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        await connection.execute("UPDATE conversation.result_consumer_progress SET disposition='blocked',"
            "reason='local_result_application_unconfirmed' WHERE receipt_id='receipt' AND consumer='life_activity'")
        await connection.execute("UPDATE life.activities SET state='completed',last_result_revision=1,"
                                 "last_receipt_id='receipt' WHERE id='activity'")
    value.actions.current = replace(decode_receipt(value.event["receipt"]), receipt_id="receipt-new")
    value.actions.revision = 2
    await value.recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
        action_id="action", receipt_id="receipt", result_revision=1)
    async with database.transaction(read_only=True) as connection:
        part = await (await connection.execute("SELECT disposition,reason FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'")).fetchone()
    assert part == {"disposition": "blocked", "reason": "local_result_application_unconfirmed"}
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_exhausted_unlocked_normal_job_is_covered_by_authorized_repair(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    async with database.transaction() as connection:
        old_job = await value.jobs.enqueue(connection, JobSpec("conversation.result", "old-unlocked-result",
            {"event": value.event}, ServiceClass.INTERACTIVE, subject_id=value.event["context"]["actor_id"],
            data_generation=saved["data_generation"]))
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    repair = await schedule_repair(value, saved)
    async with database.transaction(read_only=True) as connection:
        old = await value.jobs.observe_on(connection, old_job)
        new = await value.jobs.observe_on(connection, repair["job_id"])
    assert repair["state"] == "accepted" and old["covered_by"] == repair["job_id"]
    assert new["covered_by"] is None
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_owner_rebinding_after_grant_does_not_apply_to_another_row(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state='dormant',action_id='different-action' "
                                 "WHERE id='activity'")
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,scope_ids) "
                                 "VALUES ('other-activity','actor','other','reading','running','action',ARRAY['scope'])")
    await run_native(database, value, saved, wake["job_id"])
    assert value.life.activity_calls == 0
    async with database.transaction(read_only=True) as connection:
        proof = await (await connection.execute("SELECT proof FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'")).fetchone()
    assert not proof or not proof["proof"]
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_revoked_source_cannot_authorize_or_apply_original_result(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    value.identity.revoked = True
    with pytest.raises(AppError, match="source_unavailable"):
        await register_and_authorize(database, value, saved)
    assert value.life.activity_calls == 0
    value.actions.invoke.assert_not_awaited()
    async with database.transaction(read_only=True) as connection:
        stored = await (await connection.execute(
            "SELECT count(*) AS count FROM conversation.rbj_result_repair_evidence")).fetchone()
    assert stored["count"] == 0


@pytest.mark.asyncio
async def test_same_receipt_without_proof_stays_unknown_before_begin(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state='completed',last_result_revision=1,"
                                 "last_receipt_id='receipt' WHERE id='activity'")
    await run_native(database, value, saved, wake["job_id"])
    assert value.life.activity_calls == 0
    async with database.transaction() as connection:
        stop = await (await connection.execute("SELECT decision,reason FROM conversation.rbj_result_preflight_stops "
            "WHERE data_generation=%s AND job_id=%s", (saved["data_generation"], wake["job_id"]))).fetchone()
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded',attempts=attempts+1 WHERE id=%s",
                                 (wake["job_id"],))
    assert stop == {"decision": "unknown", "reason": "local_result_application_unconfirmed"}
    budget = await value.recovery._budget_for(saved["data_generation"], value.event,
                                              repair_ref=REPAIR_REF, repair_consumer=CONSUMER)
    assert budget["failed_executions"] == 4 and budget["unknown_held"] == 0
    result = await schedule_repair(value, saved)
    assert result["state"] == "blocked" and result["outcome"] == "unknown"
    assert result["reason"] == "local_result_application_unconfirmed"


@pytest.mark.asyncio
async def test_uncertain_commit_race_after_begin_holds_original_attempt(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    wake = await schedule_repair(value, saved)
    value.life._activity_result = AsyncMock(side_effect=AppError("local_result_application_unconfirmed", status=409))
    with pytest.raises(AppError, match="local_result_application_unconfirmed"):
        await run_native(database, value, saved, wake["job_id"])
    async with database.transaction(read_only=True) as connection:
        progress = await (await connection.execute("SELECT disposition,reason FROM conversation.result_consumer_progress "
            "WHERE receipt_id='receipt' AND consumer='life_activity'")).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(saved["data_generation"], value.event),
            data_generation=saved["data_generation"], max_failures=MAX_FAILURES,
            repair_ref=REPAIR_REF, consumer=CONSUMER)
    assert progress == {"disposition": "blocked", "reason": "local_result_application_unconfirmed"}
    assert budget["unknown_held"] == 1 and budget["failed_executions"] == 4
    assert not budget["repair"]["eligible"]
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("pending", [False, True])
@pytest.mark.parametrize("allow_successor", [False, True])
async def test_live_consumer_lease_precedes_pending_and_successor_wakes(database, pending, allow_successor):
    value = await setup(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        if not pending:
            await fail_native_job(connection, saved["original_job_id"])
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        claim = await (await connection.execute(
            "UPDATE conversation.result_consumer_progress SET disposition='running',owner_token='live-owner',"
            "lease_until=now()+interval '1 minute' WHERE data_generation=%s AND receipt_id='receipt' "
            "AND consumer='renderer' RETURNING owner_token,lease_until,proof",
            (saved["data_generation"],))).fetchone()
        due = await (await connection.execute(
            "UPDATE conversation.result_deliveries SET rbi_reconcile_due_at=now()+interval '2 hours',"
            "rbi_reconcile_reason='original-due' WHERE data_generation=%s AND receipt_id='receipt' "
            "RETURNING rbi_reconcile_due_at,rbi_reconcile_reason", (saved["data_generation"],))).fetchone()
        original = await (await connection.execute(
            "SELECT status,args,scheduled_at,queueing_lock FROM procrastinate_jobs WHERE id=%s",
            (saved["original_job_id"],))).fetchone()
        count = await (await connection.execute(
            "SELECT count(*) AS n FROM runtime.job_keys WHERE task='conversation.result'")).fetchone()
        wake = await ensure_on(connection, value.jobs, saved["data_generation"], value.event,
                               allow_successor=allow_successor, reconcile_stranded=True)
    assert wake["state"] == "waiting" and wake["reason"] == "consumer_running"
    assert wake["due_at"] == claim["lease_until"]
    assert wake["budget"]["failed_executions"] == (0 if pending else 1)
    assert wake["budget"]["unknown_held"] == 0
    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute(
            "SELECT owner_token,lease_until,proof FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='renderer'",
            (saved["data_generation"],))).fetchone()
        same = await (await connection.execute(
            "SELECT status,args,scheduled_at,queueing_lock FROM procrastinate_jobs WHERE id=%s",
            (saved["original_job_id"],))).fetchone()
        after_count = await (await connection.execute(
            "SELECT count(*) AS n FROM runtime.job_keys WHERE task='conversation.result'")).fetchone()
        after_due = await (await connection.execute(
            "SELECT rbi_reconcile_due_at,rbi_reconcile_reason FROM conversation.result_deliveries "
            "WHERE data_generation=%s AND receipt_id='receipt'", (saved["data_generation"],))).fetchone()
    assert after == claim and same == original and after_count == count and after_due == due
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_multiple_live_consumers_wait_for_the_earliest_database_lease(database):
    value = await setup(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        await connection.execute("UPDATE conversation.result_consumer_progress SET disposition='running',"
            "owner_token='render-owner',lease_until=now()+interval '2 minutes' "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='renderer'", (saved["data_generation"],))
        first = await (await connection.execute("UPDATE conversation.result_consumer_progress SET disposition='running',"
            "owner_token='dialogue-owner',lease_until=now()+interval '1 minute' "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='dialogue' RETURNING lease_until",
            (saved["data_generation"],))).fetchone()
        wake = await ensure_on(connection, value.jobs, saved["data_generation"], value.event,
                               allow_successor=True, reconcile_stranded=True)
    assert wake["state"] == "waiting" and wake["reason"] == "consumer_running"
    assert wake["due_at"] == first["lease_until"]
    candidates = await value.recovery.candidates()
    facts = await value.recovery.inspect(None, {"id": str(saved["original_job_id"])})
    observed = await value.recovery.observe(None, None, facts, "observer")
    assert candidates[0]["executing"] and candidates[0]["due_at"] == first["lease_until"]
    assert not facts["eligible"] and facts["reason"] == "consumer_running" and facts["due_at"] == first["lease_until"]
    assert observed["state"] == "pending" and observed["reason"] == "consumer_running"
    assert observed["due_at"] == first["lease_until"]


@pytest.mark.asyncio
async def test_dispatched_normal_worker_records_real_consumer_lease_wait(database):
    value = await setup(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        await connection.execute("UPDATE conversation.result_consumer_progress SET disposition='not_applicable',"
            "reason='fixture_not_applicable' WHERE data_generation=%s AND receipt_id='receipt' AND consumer<>'renderer'",
            (saved["data_generation"],))
        claim = await (await connection.execute("UPDATE conversation.result_consumer_progress SET disposition='running',"
            "owner_token='live-owner',lease_until=now()+interval '1 minute' "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='renderer' RETURNING owner_token,lease_until",
            (saved["data_generation"],))).fetchone()
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (saved["original_job_id"],))
    generation_token = runtime_jobs._execution_generation.set(saved["data_generation"])
    job_token = runtime_jobs._execution_identity.set((saved["original_job_id"], 0, saved["data_generation"]))
    try:
        with pytest.raises(RecoveryWait) as wait:
            await value.consumer.consume(value.event)
    finally:
        runtime_jobs._execution_identity.reset(job_token)
        runtime_jobs._execution_generation.reset(generation_token)
    assert wait.value.reason == "result_consumer_busy" and wait.value.retry_at == claim["lease_until"]
    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute("SELECT owner_token,lease_until FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='renderer'", (saved["data_generation"],))).fetchone()
        fact = await (await connection.execute("SELECT outcome,retry_at FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND job_id=%s AND native_attempt=0", (saved["data_generation"], saved["original_job_id"]))).fetchone()
    assert after == claim and fact == {"outcome": "wait", "retry_at": claim["lease_until"]}
    value.renderer.on_action_result.assert_not_awaited()
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer,disposition,reason", [
    ("life_activity", "pending", "execution_interrupted"),
    ("renderer", "blocked", "database_outcome_unconfirmed"),
    ("dialogue", "blocked", "database_outcome_unconfirmed"),
])
async def test_expired_lease_then_reconciles_only_under_consumer_semantics(database, consumer, disposition, reason):
    value = await setup(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (saved["original_job_id"],))
    first = await value.recovery._budget_for(saved["data_generation"], value.event)
    assert first["unknown_held"] == 1
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='failed',attempts=attempts+1 WHERE id=%s",
                                 (saved["original_job_id"],))
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        claim = await (await connection.execute(
            "UPDATE conversation.result_consumer_progress SET disposition='running',owner_token='later-owner',"
            "lease_until=now()+interval '1 minute' WHERE data_generation=%s AND receipt_id='receipt' AND consumer=%s "
            "RETURNING lease_until", (saved["data_generation"], consumer))).fetchone()
    waiting = await value.recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
        action_id="action", receipt_id="receipt", result_revision=1)
    assert waiting["state"] == "waiting" and waiting["reason"] == "consumer_running"
    assert waiting["due_at"] == claim["lease_until"]
    assert waiting["budget"]["failed_executions"] == 0 and waiting["budget"]["unknown_held"] == 1
    async with database.transaction() as connection:
        proof = await (await connection.execute(
            "SELECT rbi_failed_native FROM conversation.result_deliveries WHERE data_generation=%s AND receipt_id='receipt'",
            (saved["data_generation"],))).fetchone()
        assert f"{saved['original_job_id']}:0" in proof["rbi_failed_native"]
        await connection.execute("UPDATE conversation.result_consumer_progress SET lease_until=now()-interval '1 second' "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer=%s", (saved["data_generation"], consumer))
    recovered = await value.recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
        action_id="action", receipt_id="receipt", result_revision=1)
    assert recovered["budget"]["failed_executions"] == 1 and recovered["budget"]["unknown_held"] == 0
    async with database.transaction(read_only=True) as connection:
        progress = await (await connection.execute(
            "SELECT disposition,reason,owner_token,lease_until FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer=%s",
            (saved["data_generation"], consumer))).fetchone()
    assert progress == {"disposition": disposition, "reason": reason, "owner_token": None, "lease_until": None}
    value.renderer.on_action_result.assert_not_awaited()
    value.consumer.conversation.on_result.assert_not_awaited()
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_atomic_claim_can_reacquire_and_commit_with_its_own_proof(database):
    value = await setup(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        old_token, _ = await ledger.claim_on(connection, saved["data_generation"], "receipt", "life_activity")
        await connection.execute("UPDATE conversation.result_consumer_progress SET lease_until=now()-interval '1 second' "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='life_activity'", (saved["data_generation"],))
        new_token, _ = await ledger.claim_on(connection, saved["data_generation"], "receipt", "life_activity")
        await connection.execute("INSERT INTO test_rbj_memory(receipt_id) VALUES ('receipt')")
        await ledger.complete_on(connection, saved["data_generation"], "receipt", "life_activity", new_token,
                                 proof={"activity_id": "activity", "last_result_revision": 1,
                                        "last_receipt_id": "receipt"})
    assert old_token != new_token
    async with database.transaction(read_only=True) as connection:
        progress = await (await connection.execute("SELECT disposition,proof,attempts FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='life_activity'", (saved["data_generation"],))).fetchone()
        effect = await (await connection.execute("SELECT receipt_id FROM test_rbj_memory WHERE receipt_id='receipt' ")).fetchone()
    assert progress["disposition"] == "done" and progress["proof"]["last_receipt_id"] == "receipt"
    assert progress["attempts"] == 2 and effect == {"receipt_id": "receipt"}


@pytest.mark.asyncio
@pytest.mark.parametrize("native", [False, True])
@pytest.mark.parametrize("consumer,lease_kind,error_code", [
    ("renderer", "expired", "result_consumer_outcome_unknown"),
    ("dialogue", "expired", "result_consumer_outcome_unknown"),
    ("life_activity", "missing_due", "result_consumer_lease_unknown"),
    ("life_activity", "missing_owner", "result_consumer_lease_unknown"),
])
async def test_unproven_consumer_claim_is_not_replayed_by_direct_or_native_entry(
        database, native, consumer, lease_kind, error_code):
    value = await setup(database)
    saved = await publish(value, database)
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        await connection.execute("UPDATE conversation.result_consumer_progress SET disposition='not_applicable',"
            "reason='fixture_not_applicable' WHERE data_generation=%s AND receipt_id='receipt' AND consumer<>%s",
            (saved["data_generation"], consumer))
        await connection.execute("UPDATE conversation.result_consumer_progress SET proof=%s "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer='renderer' AND consumer<>%s",
            (Jsonb({"rendered": False}), saved["data_generation"], consumer))
        await connection.execute("UPDATE conversation.result_consumer_progress SET disposition='running',"
            "owner_token=%s,lease_until=CASE WHEN %s='expired' THEN now()-interval '1 second' "
            "WHEN %s='missing_owner' THEN now()+interval '1 minute' ELSE NULL END "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer=%s",
            (None if lease_kind == "missing_owner" else "original-owner", lease_kind, lease_kind,
             saved["data_generation"], consumer))
        before = await (await connection.execute(
            "SELECT disposition,owner_token,lease_until,proof FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer=%s",
            (saved["data_generation"], consumer))).fetchone()
    blocked = await value.recovery.recover_result(saved["original_job_id"], data_generation=saved["data_generation"],
        action_id="action", receipt_id="receipt", result_revision=1)
    assert blocked["state"] == "blocked"
    assert blocked["reason"] == ("result_consumer_outcome_unknown" if lease_kind == "expired" else "result_consumer_lease_unknown")
    assert blocked["outcome"] == "unknown"
    facts = await value.recovery.inspect(None, {"id": str(saved["original_job_id"])})
    observed = await value.recovery.observe(None, None, facts, "observer")
    assert not facts["eligible"] and facts["reason"] == blocked["reason"]
    assert observed["state"] == "unknown" and observed["reason"] == blocked["reason"]
    generation_token = job_token = None
    if native:
        async with database.transaction() as connection:
            await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s", (saved["original_job_id"],))
        generation_token = runtime_jobs._execution_generation.set(saved["data_generation"])
        job_token = runtime_jobs._execution_identity.set((saved["original_job_id"], 0, saved["data_generation"]))
    try:
        with pytest.raises(AppError, match=error_code):
            await value.consumer.consume(value.event)
    finally:
        if native:
            runtime_jobs._execution_identity.reset(job_token)
            runtime_jobs._execution_generation.reset(generation_token)
    async with database.transaction(read_only=True) as connection:
        after = await (await connection.execute(
            "SELECT disposition,owner_token,lease_until,proof FROM conversation.result_consumer_progress "
            "WHERE data_generation=%s AND receipt_id='receipt' AND consumer=%s",
            (saved["data_generation"], consumer))).fetchone()
        original = await (await connection.execute("SELECT args FROM procrastinate_jobs WHERE id=%s",
            (saved["original_job_id"],))).fetchone()
        if native:
            fact = await (await connection.execute(
                "SELECT outcome FROM runtime.rbi_execution_facts WHERE data_generation=%s AND job_id=%s AND native_attempt=0",
                (saved["data_generation"], saved["original_job_id"]))).fetchone()
            assert fact == {"outcome": "unknown"}
    assert after == before and original["args"]["event"] == value.event
    assert (await value.actions.get("action", value.action.context))["receipt"] == value.event["receipt"]
    value.renderer.on_action_result.assert_not_awaited()
    value.consumer.conversation.on_result.assert_not_awaited()
    assert value.life.activity_calls == 0
    value.actions.invoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_authorized_repair_waits_for_another_consumer_without_spending_grant(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        claim = await (await connection.execute(
            "UPDATE conversation.result_consumer_progress SET disposition='running',owner_token='live-owner',"
            "lease_until=now()+interval '1 minute' WHERE data_generation=%s AND receipt_id='receipt' "
            "AND consumer='renderer' RETURNING lease_until", (saved["data_generation"],))).fetchone()
        count = await (await connection.execute(
            "SELECT count(*) AS n FROM runtime.job_keys WHERE task='conversation.result'")).fetchone()
    result = await schedule_repair(value, saved)
    assert result["state"] == "waiting" and result["reason"] == "consumer_running"
    assert result["due_at"] == claim["lease_until"]
    assert result["budget"]["failed_executions"] == 4 and result["budget"]["repair"]["remaining"] == 1
    async with database.transaction(read_only=True) as connection:
        after_count = await (await connection.execute(
            "SELECT count(*) AS n FROM runtime.job_keys WHERE task='conversation.result'")).fetchone()
        grant = await (await connection.execute("SELECT closed_at FROM runtime.rbj_execution_repair_grants "
            "WHERE repair_ref=%s AND consumer=%s", (REPAIR_REF, CONSUMER))).fetchone()
    assert after_count == count and grant["closed_at"] is None and value.life.activity_calls == 0


@pytest.mark.asyncio
async def test_repair_authorization_waits_for_other_consumer_without_granting(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    async with database.transaction() as connection:
        await value.jobs.register_execution_repair_on(connection, owner=EXECUTION_OWNER, repair_ref=REPAIR_REF,
            repair_revision=REPAIR_REVISION, defect_key=DEFECT_KEY, consumer=CONSUMER)
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        claim = await (await connection.execute(
            "UPDATE conversation.result_consumer_progress SET disposition='running',owner_token='live-owner',"
            "lease_until=now()+interval '1 minute' WHERE data_generation=%s AND receipt_id='receipt' "
            "AND consumer='renderer' RETURNING lease_until", (saved["data_generation"],))).fetchone()
    result = await value.recovery.authorize_local_repair(saved["original_job_id"],
        data_generation=saved["data_generation"], action_id="action", receipt_id="receipt", result_revision=1,
        repair_ref=REPAIR_REF, repair_revision=REPAIR_REVISION, defect_key=DEFECT_KEY,
        repair_consumer=CONSUMER, defect_evidence_ref="verified-native-failure-evidence",
        material_digest=signature(value.event))
    assert result["state"] == "waiting" and result["reason"] == "consumer_running"
    assert result["due_at"] == claim["lease_until"] and result["budget"]["failed_executions"] == 4
    async with database.transaction(read_only=True) as connection:
        grant = await (await connection.execute("SELECT 1 FROM runtime.rbj_execution_repair_grants "
            "WHERE repair_ref=%s AND consumer=%s", (REPAIR_REF, CONSUMER))).fetchone()
    assert grant is None and value.life.activity_calls == 0


@pytest.mark.asyncio
async def test_dispatched_repair_waits_for_other_consumer_without_beginning(database):
    value = await setup(database)
    saved = await exhaust(database, value)
    assert (await register_and_authorize(database, value, saved))["state"] == "accepted"
    repair = await schedule_repair(value, saved)
    async with database.transaction() as connection:
        await ledger.initialize_on(connection, saved["data_generation"], value.event, wire(value.action), "actor")
        claim = await (await connection.execute(
            "UPDATE conversation.result_consumer_progress SET disposition='running',owner_token='live-owner',"
            "lease_until=now()+interval '1 minute' WHERE data_generation=%s AND receipt_id='receipt' "
            "AND consumer='renderer' RETURNING lease_until", (saved["data_generation"],))).fetchone()
    with pytest.raises(RecoveryWait) as wait:
        await run_native(database, value, saved, repair["job_id"])
    assert wait.value.reason == "consumer_running" and wait.value.retry_at == claim["lease_until"]
    async with database.transaction(read_only=True) as connection:
        stopped = await (await connection.execute(
            "SELECT decision,reason FROM conversation.rbj_result_preflight_stops WHERE data_generation=%s AND job_id=%s",
            (saved["data_generation"], repair["job_id"]))).fetchone()
        fact = await (await connection.execute("SELECT 1 FROM runtime.rbi_execution_facts "
            "WHERE data_generation=%s AND job_id=%s", (saved["data_generation"], repair["job_id"]))).fetchone()
        budget = await value.jobs.inspect_execution_on(connection, owner=EXECUTION_OWNER,
            identity=execution_identity(saved["data_generation"], value.event),
            data_generation=saved["data_generation"], max_failures=MAX_FAILURES,
            repair_ref=REPAIR_REF, consumer=CONSUMER)
    assert stopped == {"decision": "blocked", "reason": "consumer_running"} and fact is None
    assert budget["failed_executions"] == 4 and budget["repair"]["remaining"] == 1
    assert value.life.activity_calls == 0
