"""RBH Memory stories; the coordinator runs these with the integrated Runtime."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from psycopg.types.json import Jsonb
from pathlib import Path
import json

import pytest

from tianshu.models.http_timing import HttpTiming
from tianshu.models.ledger import AttemptLedger, receipt as ledger_receipt
from tianshu.models.types import ModelPart, ModelResult
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryPolicy, RecoveryWait


spec = spec_from_file_location("_rbh_wait_fixtures",Path(__file__).with_name("test_rbf_wait_recovery.py"))
waits = module_from_spec(spec)
spec.loader.exec_module(waits)
core, ordinary_users, examples = waits.core, waits.ordinary_users, waits.examples
rpc_examples = waits.load_helper("_rbh_rpc_examples",Path(__file__).parents[1]/"core"/"test_memory_rpc_recovery.py")


class LegacyFailure(waits.Model):
    def __init__(self, *, database, failures=1):
        super().__init__()
        self.database = database
        self.failures = failures

    async def submit(self, request, *, completion):
        if len(self.submissions) < self.failures:
            self.submissions.append((request,completion))
            timing = HttpTiming(None)
            timing.connection_reused = False
            timing.mark("connect_start")
            receipt = await waits.recorded_failure(self.database, request, timing=timing.record(),
                diagnostic={"category":"not_sent","part_count":0,"part_kinds":[],
                            "text_bytes":0,"native_completed":False})
            self.results[request.attempt_id] = ModelResult((),receipt)
            return receipt
        return await super().submit(request,completion=completion)


@pytest.mark.parametrize("withdrawal,phase",[("source","on_submit"),("identity","on_submit"),
                                               ("source","before_observe")])
async def test_live_failure_archives_without_retry_after_authority_withdrawal(
        core, ordinary_users, monkeypatch, withdrawal, phase):
    alice, _ = ordinary_users
    policy = RecoveryPolicy()
    calls = []
    revoked = False
    item = None

    async def withdraw():
        nonlocal revoked
        if withdrawal == "source":
            ref = item.source_refs[0]
            await core.sources.invalidate(alice,{"source_id":ref.source_id,"expected_revision":ref.revision,
                "client_request_id":"rbh-live-withdraw-"+phase})
        else:
            async with core.database.transaction() as conn:
                await conn.execute("UPDATE identity.principals SET access_epoch=access_epoch+1 WHERE principal_id=%s",
                    (alice.principal_id,))
        revoked = True

    class WithdrawOnSubmit(LegacyFailure):
        async def submit(self, request, *, completion):
            receipt = await super().submit(request,completion=completion)
            if phase == "on_submit":
                await withdraw()
            return receipt

    model = WithdrawOnSubmit(database=core.database)
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=policy)

    async def note_before_withdrawal(*args, **kwargs):
        calls.append(kwargs["model_receipt"].attempt_id)
        if phase == "before_observe" and not revoked:
            # Leave a historical failed attempt unclassified for the later
            # authoritative observation; this is only a disposable fixture.
            return {"can_retry":True,"reason":"not_confirmed_not_sent"}
        raise AssertionError("withdrawn authority reached Runtime note")

    monkeypatch.setattr(policy,"note_not_sent_on",note_before_withdrawal)
    item = await examples.episode(core,alice,"rbh-live-authority-"+withdrawal+"-"+phase)
    partition, _ = await waits.batch_for_episode(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await waits.batch_for_episode(core,item.episode_id)
    if phase == "before_observe":
        assert batch["status"] == "quarantined" and len(calls) == 1
        calls.clear()
        await withdraw()
        await waits.force_batch_due(core,batch["batch_id"])
        await core.workers.consolidate(partition)
        _, batch = await waits.batch_for_episode(core,item.episode_id)

    original = model.submissions[0][0]
    async with core.database.transaction(read_only=True) as conn:
        attempts = await (await conn.execute("SELECT a.number,a.model_attempt_id,a.recovery_policy_state,"
            "r.status,r.error_code,l.cancelled,l.attempts FROM memory.consolidation_attempts a "
            "JOIN runtime.recovery_attempts r ON r.attempt_id=a.model_attempt_id "
            "JOIN runtime.recovery_lineages l ON l.lineage_id=a.recovery_lineage_id "
            "WHERE a.batch_id=%s ORDER BY a.number",(batch["batch_id"],))).fetchall()
        classified = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_not_sent "
            "WHERE attempt_id=%s",(original.attempt_id,))).fetchone()
    assert calls == [] and classified["n"] == 0
    assert batch["status"] == "revoked" and batch["error_code"] == "source_unavailable"
    assert len(attempts) == 1 and attempts[0]["number"] == attempts[0]["attempts"] == 1
    assert attempts[0]["model_attempt_id"] == original.attempt_id and attempts[0]["cancelled"]
    assert attempts[0]["recovery_policy_state"] == attempts[0]["status"] == "failed"
    assert attempts[0]["error_code"] == "transport_not_sent"
    assert model.results[original.attempt_id].receipt.status == "failed" and len(model.submissions) == 1


async def historical_failure(core, context, monkeypatch, name, *, failures=1):
    model, policy = LegacyFailure(database=core.database, failures=failures), RecoveryPolicy()
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=policy)
    note = policy.note_not_sent_on

    async def old_unclassified(*args, **kwargs):
        return {"reason":"not_confirmed_not_sent","can_retry":True}

    monkeypatch.setattr(policy,"note_not_sent_on",old_unclassified)
    item = await examples.episode(core,context,name)
    partition, _ = await waits.batch_for_episode(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await waits.batch_for_episode(core,item.episode_id)
    for _ in range(failures-1):
        await mature(core,batch)
        await core.workers.consolidate(partition)
        _, batch = await waits.batch_for_episode(core,item.episode_id)
    assert batch["status"] == "quarantined" and len(model.submissions) == failures
    monkeypatch.setattr(policy,"note_not_sent_on",note)
    lineage = batch["manifest"]["recovery"]["lineage_id"]
    async with core.database.transaction() as conn:
        # Recreate an exhausted historical lineage in the disposable fixture.
        await conn.execute("UPDATE runtime.recovery_lineages SET max_attempts=1,due_at=NULL WHERE lineage_id=%s",(lineage,))
        await conn.execute("UPDATE memory.consolidation_batches SET status='failed',error_code='recovery_budget_exhausted',"
            "retry_due_at=NULL WHERE batch_id=%s",(batch["batch_id"],))
        await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_failed' WHERE episode_id=%s",
            (item.episode_id,))
        steps = await (await conn.execute("SELECT step_id,request FROM memory.model_steps WHERE step_id=ANY(%s::text[])",
            (batch["step_ids"],))).fetchall()
    return model,policy,item,partition,batch,lineage,steps


async def mature(core, batch):
    lineage = batch["manifest"]["recovery"]["lineage_id"]
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()-interval '1 second' WHERE batch_id=%s",
            (batch["batch_id"],))
        await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s",(lineage,))
        await conn.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second',"
            "probe_token=NULL,probe_expires_at=NULL WHERE (data_generation,dependency) IN "
            "(SELECT data_generation,dependency FROM runtime.recovery_lineages WHERE lineage_id=%s)",(lineage,))


async def test_historical_connection_reconciliation_is_bounded_and_keeps_original(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model,policy,item,partition,batch,lineage,original_steps = await historical_failure(
        core,alice,monkeypatch,"rbh-legacy-original")
    before_receipt = model.results[model.submissions[0][0].attempt_id].receipt
    first = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert first["inspected"] == 1 and first["scheduled"] == 0
    assert first["changes"][0]["reason"] == "confirmed_not_sent"
    second = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert second["inspected"] == 1 and second["scheduled"] == 1
    assert (await core.memory.consolidation.reconcile_transport_waits(limit=1))["scheduled"] == 0
    assert len(model.submissions) == 1 and model.results[before_receipt.attempt_id].receipt == before_receipt
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
            (batch["batch_id"],))).fetchone()
        steps = await (await conn.execute("SELECT step_id,request FROM memory.model_steps WHERE step_id=ANY(%s::text[])",
            (batch["step_ids"],))).fetchall()
        ledger = await (await conn.execute("SELECT attempt_id,attempt_number,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number",(lineage,))).fetchall()
        budget = await policy.computation_budget_on(conn,lineage,owner="memory",
            expected_version=batch["manifest"]["recovery"]["version"])
    assert steps == original_steps and current["step_ids"] == batch["step_ids"]
    assert current["manifest"]["local_wait"]["recovered_from"]["error_code"] == "recovery_budget_exhausted"
    assert [(row["attempt_id"],row["attempt_number"],row["status"]) for row in ledger] == [
        (before_receipt.attempt_id,1,"failed")]
    assert budget["total_attempts"] == 1 and budget["computation_attempts"] == 0 and budget["connection_attempts"] == 1

    # Recovery observes the original before allocating a different attempt.
    await mature(core,current)
    await core.workers.consolidate(partition)
    await mature(core,current)
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        applied = await (await conn.execute("SELECT status,step_ids,commit_refs FROM memory.consolidation_batches "
            "WHERE batch_id=%s",(batch["batch_id"],))).fetchone()
        ledger = await (await conn.execute("SELECT attempt_id,attempt_number,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number",(lineage,))).fetchall()
    assert applied["status"] == "applied" and len(applied["step_ids"]) == 2
    assert [row["attempt_number"] for row in ledger] == [1,2]
    assert ledger[0]["attempt_id"] == before_receipt.attempt_id and ledger[0]["status"] == "failed"
    assert len(model.submissions) == 2 and model.submissions[1][0].attempt_id != before_receipt.attempt_id
    await core.workers.consolidate(partition)
    assert len(model.submissions) == 2


async def test_older_credit_restores_latest_failed_attempt_due(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model,policy,_,_,batch,lineage,_ = await historical_failure(
        core,alice,monkeypatch,"rbh-older-credit",failures=2)
    latest = model.submissions[-1][0].attempt_id
    async with core.database.transaction() as conn:
        result = await policy.note_not_sent_on(conn,latest,owner="memory",
            expected_version=batch["manifest"]["recovery"]["version"],
            model_receipt=model.results[latest].receipt)
        row = await (await conn.execute("SELECT due_at FROM runtime.recovery_lineages WHERE lineage_id=%s",(lineage,))).fetchone()
    assert not result["can_retry"] and row["due_at"] is None
    first = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert first["scheduled"] == 0
    second = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert second["scheduled"] == 0
    third = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert third["scheduled"] == 1
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,step_ids FROM memory.consolidation_batches WHERE batch_id=%s",
            (batch["batch_id"],))).fetchone()
        row = await (await conn.execute("SELECT attempts,due_at FROM runtime.recovery_lineages WHERE lineage_id=%s",
            (lineage,))).fetchone()
    assert current["status"] == "quarantined" and current["step_ids"] == batch["step_ids"]
    assert row["attempts"] == 2 and row["due_at"] is not None and len(model.submissions) == 2


@pytest.mark.parametrize("condition",["paused","withdrawn","unknown","stale","bad_digest"])
async def test_reconciliation_keeps_ineligible_originals(core, ordinary_users, monkeypatch, condition):
    alice, _ = ordinary_users
    model,_,item,_,batch,lineage,steps = await historical_failure(core,alice,monkeypatch,"rbh-ineligible-"+condition)
    attempt_id = model.submissions[0][0].attempt_id
    if condition in {"unknown","bad_digest"}:
        result = model.results[attempt_id]
        receipt = replace(result.receipt,**({"status":"unknown"} if condition == "unknown" else {"request_digest":"wrong"}))
        model.results[attempt_id] = replace(result,receipt=receipt)
    async with core.database.transaction() as conn:
        if condition == "paused":
            await conn.execute("UPDATE runtime.recovery_lineages SET paused=true WHERE lineage_id=%s",(lineage,))
        elif condition == "withdrawn":
            await conn.execute("UPDATE runtime.recovery_lineages SET source_withdrawn=true WHERE lineage_id=%s",(lineage,))
        elif condition == "stale":
            await conn.execute("UPDATE memory.episodes SET revision=revision+1 WHERE episode_id=%s",(item.episode_id,))
    if condition == "bad_digest":
        with pytest.raises(AppError,match="memory_model_result_identity_conflict"):
            await core.memory.consolidation.reconcile_transport_waits(limit=1)
    else:
        result = await core.memory.consolidation.reconcile_transport_waits(limit=1)
        assert result["scheduled"] == 0
        if condition == "unknown":
            # The returned receipt was changed while the locked Models fact
            # still says failed; it cannot authorize a historical credit.
            assert result["changes"][0]["reason"] == "recovery_model_outcome_changed"
        if condition == "stale":
            assert result["changes"][0]["reason"] == "memory_consolidation_episode_changed"
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,step_ids FROM memory.consolidation_batches WHERE batch_id=%s",
            (batch["batch_id"],))).fetchone()
        after = await (await conn.execute("SELECT step_id,request FROM memory.model_steps WHERE step_id=ANY(%s::text[])",
            (batch["step_ids"],))).fetchall()
    assert current["status"] == "failed" and current["step_ids"] == batch["step_ids"] and after == steps
    assert len(model.submissions) == 1


async def test_dependency_waits_share_one_partition_wake_and_do_not_block_new_material(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model,policy = waits.Model(),RecoveryPolicy()
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=policy)
    reserve = policy.reserve_attempt_on
    left = 3

    async def block_first(*args, **kwargs):
        nonlocal left
        if left:
            left -= 1
            raise RecoveryWait("recovery_dependency_probe_in_progress",datetime.now(UTC)+timedelta(minutes=5),
                dependency="models:fixture")
        return await reserve(*args, **kwargs)

    monkeypatch.setattr(policy,"reserve_attempt_on",block_first)
    batch_ids = []
    for index in range(3):
        item = await examples.episode(core,alice,"rbh-wait-"+str(index))
        partition, _ = await waits.batch_for_episode(core,item.episode_id)
        await core.workers.consolidate(partition)
        _, batch = await waits.batch_for_episode(core,item.episode_id)
        assert batch["status"] == "quarantined" and batch["manifest"]["local_wait"]
        batch_ids.append(batch["batch_id"])
    later = await examples.episode(core,alice,"rbh-wait-independent")
    await core.workers.consolidate(partition)
    _, applied = await waits.batch_for_episode(core,later.episode_id)
    assert applied["status"] == "applied" and applied["batch_id"] not in batch_ids
    assert len(model.submissions) == 1
    async with core.database.transaction(read_only=True) as conn:
        generation = await core.memory.jobs.current_generation_on(conn)
        rows = await (await conn.execute("SELECT args,queueing_lock FROM procrastinate_jobs WHERE task_name='memory.consolidate' "
            "AND status='todo' AND (queueing_lock=%s OR args->>'batch_id'=ANY(%s::text[]))",
            ("memory-consolidate:"+partition+":generation:"+generation,batch_ids))).fetchall()
        waiting = await (await conn.execute("SELECT batch_id,status,manifest FROM memory.consolidation_batches "
            "WHERE batch_id=ANY(%s::text[])",(batch_ids,))).fetchall()
    assert len(rows) == 1 and rows[0]["args"] == {"partition_id":partition}
    assert len(waiting) == 3 and all(row["status"] == "quarantined" and row["manifest"]["local_wait"] for row in waiting)


async def test_shared_dependency_allows_only_one_live_probe(core, ordinary_users):
    class PendingProbe(LegacyFailure):
        async def submit(self,request,*,completion):
            if len(self.submissions) == 1:
                receipt = await waits.Model.submit(self,request,completion=completion)
                receipt = replace(receipt,status="queued",dispatch="submitted")
                self.results[request.attempt_id] = replace(self.results[request.attempt_id],receipt=receipt,parts=())
                return receipt
            return await super().submit(request,completion=completion)

    alice, _ = ordinary_users
    model = PendingProbe(database=core.database)
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    first = await examples.episode(core,alice,"rbh-probe-original")
    partition, _ = await waits.batch_for_episode(core,first.episode_id)
    await core.workers.consolidate(partition)
    _, held = await waits.batch_for_episode(core,first.episode_id)
    await mature(core,held)
    await core.workers.consolidate(partition)
    assert len(model.submissions) == 2
    for index in range(2):
        item = await examples.episode(core,alice,"rbh-probe-waiter-"+str(index))
        await core.workers.consolidate(partition)
        _, batch = await waits.batch_for_episode(core,item.episode_id)
        assert batch["status"] == "quarantined"
        assert batch["manifest"]["local_wait"]["reason"] == "recovery_dependency_probe_in_progress"
    async with core.database.transaction(read_only=True) as conn:
        dependency = await (await conn.execute("SELECT d.probe_token,d.probe_expires_at FROM runtime.recovery_dependencies d "
            "JOIN runtime.recovery_lineages l ON (l.data_generation,l.dependency)=(d.data_generation,d.dependency) "
            "WHERE l.lineage_id=%s",(held["manifest"]["recovery"]["lineage_id"],))).fetchone()
        probes = await (await conn.execute("SELECT attempt_id FROM runtime.recovery_attempts "
            "WHERE dependency='models:fixture' AND dependency_probe AND status='reserved'")).fetchall()
    assert dependency["probe_token"] == model.submissions[1][0].attempt_id
    assert dependency["probe_expires_at"] > datetime.now(UTC)
    assert [probe["attempt_id"] for probe in probes] == [dependency["probe_token"]]
    assert len(model.submissions) == 2


async def test_unknown_original_success_is_observed_once_before_future_retry_due(core, ordinary_users):
    class ValidRpcModel(rpc_examples.RpcModels):
        def answer(self,request,status="succeeded",error=None):
            result = super().answer(request,status,error)
            if status != "succeeded":
                return result
            proposal = json.loads(result.parts[0].text)
            for target in proposal["targets"]:
                if target["status"] == "no_change":
                    target["reason"] = "没有新增事实"
            return replace(result,parts=(ModelPart("text",text=json.dumps(proposal,ensure_ascii=False)),))

    alice, _ = ordinary_users
    model = ValidRpcModel()
    rpc_examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    item = await examples.episode(core,alice,"rbh-original-arrives")
    partition, _ = await waits.batch_for_episode(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, held = await waits.batch_for_episode(core,item.episode_id)
    assert held["status"] == "quarantined" and held["retry_due_at"] > datetime.now(UTC)
    model.release()
    await core.workers.consolidate(partition)
    _, applied = await waits.batch_for_episode(core,item.episode_id)
    assert applied["status"] == "applied", applied.get("error_code")
    assert applied["step_ids"] == held["step_ids"]
    await core.workers.consolidate(partition)
    assert len(model.requests) == 1


async def test_runtime_budget_exhaustion_keeps_original_without_auto_requeue(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model,policy = LegacyFailure(database=core.database),RecoveryPolicy()
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=policy)

    async def exhausted(*args, **kwargs):
        return {"can_retry":False,"retry_at":None,"reason":"recovery_connection_budget_exhausted"}

    monkeypatch.setattr(policy,"note_not_sent_on",exhausted)
    monkeypatch.setattr(policy,"computation_budget_on",exhausted)
    item = await examples.episode(core,alice,"rbh-connection-budget-exhausted")
    partition, _ = await waits.batch_for_episode(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await waits.batch_for_episode(core,item.episode_id)
    assert batch["status"] == "quarantined" and batch["retry_due_at"] is None
    assert batch["manifest"]["recovery_budget_blocked"]["reason"] == "recovery_connection_budget_exhausted"
    assert len(model.submissions) == 1
    async with core.database.transaction() as conn:
        generation = await core.memory.jobs.current_generation_on(conn)
        before = await (await conn.execute("SELECT count(*) AS n FROM procrastinate_jobs "
            "WHERE status='todo' AND queueing_lock=%s",
            ("memory-consolidate:"+partition+":generation:"+generation,))).fetchone()
        await core.memory.consolidation.retries.arm_on(conn,partition)
        after = await (await conn.execute("SELECT count(*) AS n FROM procrastinate_jobs "
            "WHERE status='todo' AND queueing_lock=%s",
            ("memory-consolidate:"+partition+":generation:"+generation,))).fetchone()
    assert before == after and len(model.submissions) == 1


async def test_explicit_historical_resume_keeps_targeted_wake(core, ordinary_users, monkeypatch):
    from tianshu.memory import consolidation_wait

    alice, _ = ordinary_users
    model,policy = waits.Model(),RecoveryPolicy()
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=policy)
    freeze = core.memory.consolidation._freeze_compilation
    blocked = True

    async def wait_once(*args, **kwargs):
        nonlocal blocked
        if blocked:
            blocked = False
            raise RecoveryWait("recovery_dependency_probe_in_progress",datetime.now(UTC)+timedelta(minutes=5),
                dependency="models:fixture")
        return await freeze(*args, **kwargs)

    monkeypatch.setattr(core.memory.consolidation,"_freeze_compilation",wait_once)
    item = await examples.episode(core,alice,"rbh-explicit-target")
    partition, _ = await waits.batch_for_episode(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await waits.batch_for_episode(core,item.episode_id)

    class AheadClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz)+timedelta(minutes=5)

    # The scheduler compares retry_due_at to database now(); a skewed app
    # clock must not postpone a user-requested restoration of the original.
    monkeypatch.setattr(consolidation_wait,"datetime",AheadClock)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET status='failed',"
            "error_code='recovery_dependency_probe_in_progress',manifest=manifest-'local_wait',retry_due_at=NULL "
            "WHERE batch_id=%s",(batch["batch_id"],))
        await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_failed' WHERE episode_id=%s",
            (item.episode_id,))
        result = await core.workers.recovery.resume_on(conn,alice,{"episode_id":item.episode_id,
            "expected_revision":item.revision,"client_request_id":"rbh-explicit-target"})
        generation = await core.memory.jobs.current_generation_on(conn)
        jobs = await (await conn.execute("SELECT args FROM procrastinate_jobs WHERE status='todo' AND queueing_lock=%s",
            ("memory-consolidate-batch:"+partition+":"+batch["batch_id"]+":generation:"+generation,))).fetchall()
        timing = await (await conn.execute("SELECT retry_due_at,now() AS db_now FROM memory.consolidation_batches "
            "WHERE batch_id=%s",(batch["batch_id"],))).fetchone()
    assert result.data["queued"] and result.data["action"] == "resume_original_batch"
    assert len(jobs) == 1 and jobs[0]["args"] == {"partition_id":partition,"batch_id":batch["batch_id"]}
    assert timing["retry_due_at"] == timing["db_now"]
    await core.workers.consolidate(partition,batch_id=batch["batch_id"])
    async with core.database.transaction(read_only=True) as conn:
        restored = await (await conn.execute("SELECT status,step_ids,error_code FROM memory.consolidation_batches "
            "WHERE batch_id=%s",(batch["batch_id"],))).fetchone()
    assert restored["status"] == "applied", restored["error_code"]
    assert restored["step_ids"] == batch["step_ids"] and len(model.submissions) == 1


@pytest.mark.parametrize("batch_status,fresh_observation", [("failed",False), ("failed",True),
    ("quarantined",False), ("quarantined",True)])
async def test_historical_not_sent_proof_survives_later_cancel_without_retry(
        core, ordinary_users, monkeypatch, batch_status, fresh_observation):
    alice, _ = ordinary_users
    model, _, _, _, batch, lineage, steps = await historical_failure(
        core, alice, monkeypatch, "rbh-evidence-then-cancel")
    original = model.submissions[0][0]
    prior_receipt = model.results[original.attempt_id].receipt
    if batch_status == "quarantined":
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined' WHERE batch_id=%s",
                               (batch["batch_id"],))
    ledger = AttemptLedger(core.database, "memory-failed-fixture")
    await ledger.request_cancel(original.attempt_id, reason="explicit_cancel")
    if fresh_observation:
        current_receipt = ledger_receipt(await ledger.get(original.attempt_id))
        model.results[original.attempt_id] = replace(model.results[original.attempt_id], receipt=current_receipt)
    first = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert first["scheduled"] == 0 and first["changes"][0]["reason"] == "confirmed_not_sent"
    assert first["changes"][0]["observed_attempt_eligibility"] == "recovery_model_cancelled"
    if batch_status == "quarantined":
        async with core.database.transaction(read_only=True) as conn:
            fenced = await (await conn.execute("SELECT manifest,retry_due_at FROM memory.consolidation_batches "
                "WHERE batch_id=%s", (batch["batch_id"],))).fetchone()
        assert fenced["retry_due_at"] is None
        assert fenced["manifest"]["recovery_budget_blocked"]["reason"] == "recovery_model_cancelled"
    second = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert second["scheduled"] == 0 and second["changes"][0]["reason"] == "recovery_model_cancelled"
    async with core.database.transaction(read_only=True) as conn:
        saved = await (await conn.execute("SELECT status,step_ids,manifest,retry_due_at FROM memory.consolidation_batches "
                                          "WHERE batch_id=%s", (batch["batch_id"],))).fetchone()
        credits = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE lineage_id=%s",
                                            (lineage,))).fetchone()
        after = await (await conn.execute("SELECT step_id,request FROM memory.model_steps WHERE step_id=ANY(%s::text[])",
                                          (batch["step_ids"],))).fetchall()
        old = await (await conn.execute("SELECT first_failure FROM memory.consolidation_attempts WHERE step_id=%s",
                                        (batch["step_ids"][-1],))).fetchone()
    assert saved["status"] == batch_status and saved["step_ids"] == batch["step_ids"] and after == steps
    if batch_status == "quarantined":
        assert saved["retry_due_at"] is None
        assert saved["manifest"]["recovery_budget_blocked"]["reason"] == "recovery_model_cancelled"
    assert credits["n"] == 1 and len(model.submissions) == 1
    assert old["first_failure"]["attempt_id"] == prior_receipt.attempt_id
    assert old["first_failure"].get("cancellation_reason") is None
    assert model.results[original.attempt_id].receipt.cancellation_reason == ("explicit_cancel" if fresh_observation else None)
    assert (await ledger.get(original.attempt_id))["cancellation_reason"] == "explicit_cancel"


async def test_missing_original_submission_never_earns_not_sent_credit(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, _, _, _, batch, lineage, _ = await historical_failure(core, alice, monkeypatch, "rbh-missing-facts")
    attempt_id = model.submissions[0][0].attempt_id
    async with core.database.transaction() as conn:
        await conn.execute("DELETE FROM models.submissions WHERE attempt_id=%s", (attempt_id,))
    result = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert result["scheduled"] == 0 and result["changes"][0]["reason"] == "recovery_model_receipt_unconfirmed"
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,error_code,step_ids FROM memory.consolidation_batches "
            "WHERE batch_id=%s", (batch["batch_id"],))).fetchone()
        credits = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE lineage_id=%s",
                                            (lineage,))).fetchone()
    assert current["status"] == "failed" and current["step_ids"] == batch["step_ids"] and credits["n"] == 0
    assert len(model.submissions) == 1


async def test_rbh_models_facts_are_locked_before_runtime_not_sent_note(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, policy, _, _, batch, _, _ = await historical_failure(core, alice, monkeypatch, "rbh-facts-lock")
    original_note = policy.note_not_sent_on
    ledger = AttemptLedger(core.database, "memory-failed-fixture")
    started = asyncio.Event()
    cancel_task = None

    async def note_while_cancel_waits(*args, **kwargs):
        nonlocal cancel_task
        async def cancel():
            started.set()
            return await ledger.request_cancel(model.submissions[0][0].attempt_id, reason="explicit_cancel")
        cancel_task = asyncio.create_task(cancel())
        await started.wait()
        await asyncio.sleep(0.05)
        assert not cancel_task.done()
        return await original_note(*args, **kwargs)

    monkeypatch.setattr(policy, "note_not_sent_on", note_while_cancel_waits)
    result = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert result["scheduled"] == 0 and cancel_task is not None
    await asyncio.wait_for(cancel_task, timeout=5)
    assert len(model.submissions) == 1 and batch["step_ids"] == [model.submissions[0][0].intent_id]


@pytest.mark.parametrize("archive", ["missing", "different_route"])
async def test_cancel_without_matching_archive_does_not_invent_not_sent_credit(core, ordinary_users, monkeypatch, archive):
    alice, _ = ordinary_users
    model, _, _, _, batch, lineage, _ = await historical_failure(core, alice, monkeypatch, "rbh-cancel-no-archive")
    attempt_id = model.submissions[0][0].attempt_id
    ledger = AttemptLedger(core.database, "memory-failed-fixture")
    await ledger.request_cancel(attempt_id, reason="explicit_cancel")
    model.results[attempt_id] = replace(model.results[attempt_id], receipt=ledger_receipt(await ledger.get(attempt_id)))
    async with core.database.transaction() as conn:
        if archive == "missing":
            await conn.execute("UPDATE memory.consolidation_attempts SET first_failure=NULL WHERE model_attempt_id=%s", (attempt_id,))
        else:
            await conn.execute("UPDATE memory.consolidation_attempts SET first_failure=first_failure || %s "
                "WHERE model_attempt_id=%s", (Jsonb({"route_id":"different"}), attempt_id))
        await conn.execute("UPDATE memory.consolidation_batches SET status='quarantined' WHERE batch_id=%s", (batch["batch_id"],))
    result = await core.memory.consolidation.reconcile_transport_waits(limit=1)
    assert result["scheduled"] == 0 and result["changes"][0]["reason"] == "recovery_model_cancelled"
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT manifest,retry_due_at FROM memory.consolidation_batches WHERE batch_id=%s",
                                            (batch["batch_id"],))).fetchone()
        credits = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE lineage_id=%s",
                                            (lineage,))).fetchone()
    assert credits["n"] == 0 and current["retry_due_at"] is None
    assert current["manifest"]["recovery_budget_blocked"]["reason"] == "recovery_model_cancelled"
