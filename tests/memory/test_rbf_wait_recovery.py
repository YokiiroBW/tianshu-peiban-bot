"""Disposable-DB stories for original Memory batches and model receipts."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import json

import pytest

from tianshu.memory import consolidation_wait
from tianshu.models.types import ModelPart, ModelReceipt, ModelResult
from tianshu.models.codec import request_from_wire
from tianshu.models.catalog import RoutePlan
from tianshu.models.ledger import AttemptLedger, receipt as ledger_receipt
from tianshu.runtime.database import DatabaseFailure
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryPolicy, RecoveryWait, recovery_wait_from_wire
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind


def load_helper(name, path):
    spec = spec_from_file_location(name, path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core_fixtures = load_helper("_rbf_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py")
examples = load_helper("_rbf_consolidation_examples", Path(__file__).parents[1] / "core" / "test_rb_consolidation_write.py")
core = core_fixtures.core
ordinary_users = core_fixtures.ordinary_users


async def recorded_failure(database, request, *, timing=None, diagnostic=None):
    """Record a real failed Models attempt for cross-domain recovery fixtures."""
    ledger = AttemptLedger(database, "memory-failed-fixture")
    plan = RoutePlan("fixture", 1, "fixture", "fixture", "openai.chat", "http://localhost",
                     "KEY", "fixture", frozenset({"text"}), {})
    await ledger.create(request, plan, {}, frozen=request, data_generation=request.data_generation)
    await ledger.finish_before_start(request.attempt_id, "failed", error_code="transport_not_sent",
                                     timing=timing, diagnostic=diagnostic)
    return ledger_receipt(await ledger.get(request.attempt_id))


class Model(examples.RecordedModel):
    def __init__(self, *, invalid_first=False):
        super().__init__()
        self.invalid_first = invalid_first
        self.phases = []

    async def submit(self, request, *, completion):
        receipt = await super().submit(request, completion=completion)
        receipt = replace(receipt, data_generation=request.data_generation)
        payload = json.loads(request.messages[0].parts[0].text)
        decisions = [{"subject_id": target["subject_id"], "status": "no_change", "operations": [],
                      "unresolved": [], "reason": "没有新增事实"} for target in payload["targets"]]
        body = "{}" if self.invalid_first and len(self.submissions) == 1 else json.dumps(
            {"targets": decisions, "needs_context": []}, ensure_ascii=False)
        self.results[request.attempt_id] = replace(self.results[request.attempt_id], receipt=receipt,
                                                    parts=(ModelPart("text", text=body),))
        return receipt

    async def status(self, attempt_id, *, phase="observe"):
        self.phases.append((attempt_id, phase))
        return await super().status(attempt_id)

    async def reusable_result(self, attempt_id, *, context, expected_request_digest=None,
                              expected_data_generation=None):
        result = self.results.get(attempt_id)
        if result is None:
            raise AppError("model_result_unavailable", status=409)
        assert result.receipt.data_generation == expected_data_generation
        return result


async def batch_for_episode(core, episode_id):
    return await examples.batch_for(core, episode_id)


async def force_batch_due(core, batch_id):
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()-interval '1 second' WHERE batch_id=%s",
                           (batch_id,))


async def test_prepared_wait_keeps_batch_and_lets_later_input_run(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, policy = Model(), RecoveryPolicy()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=policy)
    original = policy.reserve_attempt_on
    blocked = True

    async def reserve(*args, **kwargs):
        nonlocal blocked
        if blocked:
            blocked = False
            raise RecoveryWait("recovery_dependency_probe_in_progress", datetime.now(UTC)+timedelta(minutes=5),
                               dependency="models:fixture")
        return await original(*args, **kwargs)

    monkeypatch.setattr(policy, "reserve_attempt_on", reserve)
    first = await examples.episode(core, alice, "rbf-wait-first")
    partition, _ = await batch_for_episode(core, first.episode_id)
    await core.workers.consolidate(partition)
    _, waiting = await batch_for_episode(core, first.episode_id)
    batch_id, step_id = waiting["batch_id"], waiting["step_ids"][0]
    assert waiting["status"] == "quarantined" and waiting["manifest"]["recovery_pending"]["kind"] == "original"
    assert waiting["manifest"]["local_wait"]["reason"] == "recovery_dependency_probe_in_progress"
    assert not model.submissions
    async with core.database.transaction(read_only=True) as conn:
        episode = await (await conn.execute("SELECT extraction_state FROM memory.episodes WHERE episode_id=%s",
                                           (first.episode_id,))).fetchone()
        state = await (await conn.execute("SELECT active_batch_id,wake_due_at FROM memory.consolidation_state WHERE partition_id=%s",
                                         (partition,))).fetchone()
        generation = await core.memory.jobs.current_generation_on(conn)
        wakes = await (await conn.execute("SELECT id,args,scheduled_at FROM procrastinate_jobs "
            "WHERE task_name='memory.consolidate' AND status='todo' AND queueing_lock=%s",
            ("memory-consolidate:"+partition+":generation:"+generation,))).fetchall()
    assert episode["extraction_state"] == "consolidation_waiting"
    assert state["active_batch_id"] is None and state["wake_due_at"] is None
    assert len(wakes) == 1 and wakes[0]["args"] == {"partition_id":partition}
    assert wakes[0]["scheduled_at"] is None or wakes[0]["scheduled_at"] <= waiting["retry_due_at"]

    second = await examples.episode(core, alice, "rbf-wait-second")
    await core.workers.consolidate(partition)
    _, later = await batch_for_episode(core, second.episode_id)
    assert later["status"] == "applied" and later["batch_id"] != batch_id
    await force_batch_due(core, batch_id)
    await core.workers.consolidate(partition, batch_id=batch_id)
    async with core.database.transaction(read_only=True) as conn:
        restored = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",(batch_id,))).fetchone()
        attempts = await (await conn.execute("SELECT model_attempt_id FROM memory.consolidation_attempts WHERE batch_id=%s",
                                            (batch_id,))).fetchall()
    assert restored["status"] == "applied" and restored["step_ids"] == [step_id] and len(attempts) == 1
    assert len(model.submissions) == 2


async def test_local_wait_reactivation_uses_database_clock(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, policy = Model(), RecoveryPolicy()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=policy)
    reserve = policy.reserve_attempt_on
    blocked = True

    async def wait_once(*args, **kwargs):
        nonlocal blocked
        if blocked:
            blocked = False
            raise RecoveryWait("recovery_dependency_probe_in_progress", datetime.now(UTC) + timedelta(minutes=5),
                               dependency="models:fixture")
        return await reserve(*args, **kwargs)

    monkeypatch.setattr(policy, "reserve_attempt_on", wait_once)
    item = await examples.episode(core, alice, "rbf-wait-database-clock")
    partition, _ = await batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, waiting = await batch_for_episode(core, item.episode_id)
    assert waiting["status"] == "quarantined" and not model.submissions
    batch_id, step_id = waiting["batch_id"], waiting["step_ids"][0]

    class HostClock(datetime):
        offset = timedelta(hours=1)

        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + cls.offset

    monkeypatch.setattr(consolidation_wait, "datetime", HostClock)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()+interval '5 minutes' "
                           "WHERE batch_id=%s", (batch_id,))
    # A fast client clock must not make a real future database deadline due.
    assert await core.memory.consolidation.waits.reactivate(batch_id, owner=None) == "blocked"
    async with core.database.transaction(read_only=True) as conn:
        unchanged = await (await conn.execute("SELECT status,step_ids FROM memory.consolidation_batches "
                                              "WHERE batch_id=%s", (batch_id,))).fetchone()
    assert unchanged["status"] == "quarantined" and unchanged["step_ids"] == [step_id]
    assert not model.submissions

    await force_batch_due(core, batch_id)
    HostClock.offset = -timedelta(hours=1)
    # A slow client clock must not suppress a wait already due in PostgreSQL.
    assert await core.memory.consolidation.waits.reactivate(batch_id, owner=None) == "active"
    monkeypatch.setattr(consolidation_wait, "datetime", datetime)
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        restored = await (await conn.execute("SELECT status,step_ids FROM memory.consolidation_batches "
                                             "WHERE batch_id=%s", (batch_id,))).fetchone()
    assert restored["status"] == "applied" and restored["step_ids"] == [step_id]
    assert len(model.submissions) == 1


@pytest.mark.parametrize("stale_episode", [False, True])
async def test_resume_historical_failed_wait_checks_original_ledger_and_episode(core, ordinary_users, monkeypatch, stale_episode):
    alice, _ = ordinary_users
    model, policy = Model(), RecoveryPolicy()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=policy)
    freeze = core.memory.consolidation._freeze_compilation
    waited = False

    async def wait_once(*args, **kwargs):
        nonlocal waited
        if not waited:
            waited = True
            raise RecoveryWait("recovery_dependency_probe_in_progress", datetime.now(UTC)+timedelta(minutes=1),
                               dependency="models:fixture")
        return await freeze(*args, **kwargs)

    monkeypatch.setattr(core.memory.consolidation, "_freeze_compilation", wait_once)
    item = await examples.episode(core, alice, "rbf-historical")
    partition, _ = await batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, waiting = await batch_for_episode(core, item.episode_id)
    lineage, step = waiting["manifest"]["recovery"]["lineage_id"], waiting["step_ids"][0]
    # Recreate the old misclassification in this disposable fixture only.
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET status='failed',error_code=%s,"
            "manifest=manifest-'local_wait',retry_due_at=NULL WHERE batch_id=%s",
            ("recovery_dependency_probe_in_progress", waiting["batch_id"]))
        await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_failed',revision=revision+%s WHERE episode_id=%s",
                           (1 if stale_episode else 0, item.episode_id))
    body = {"episode_id": item.episode_id, "expected_revision": 2 if stale_episode else 1,
            "client_request_id": "resume-original"}
    if stale_episode:
        with pytest.raises(AppError, match="memory_consolidation_episode_changed"):
            async with core.database.transaction() as conn:
                await core.workers.recovery.resume_on(conn, alice, body)
    else:
        async with core.database.transaction() as conn:
            result = await core.workers.recovery.resume_on(conn, alice, body)
        assert result.data["action"] == "resume_original_batch" and result.data["batch_id"] == waiting["batch_id"]
        await core.workers.consolidate(partition, batch_id=waiting["batch_id"])
    async with core.database.transaction(read_only=True) as conn:
        batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                                         (waiting["batch_id"],))).fetchone()
        ledger = await (await conn.execute("SELECT attempt_id,status FROM runtime.recovery_attempts WHERE lineage_id=%s",
                                          (lineage,))).fetchall()
    assert batch["step_ids"] == [step] and len(ledger) == 1 and len(model.submissions) == 1
    assert batch["status"] == ("failed" if stale_episode else "applied")


async def test_resume_historical_wait_reconciles_queued_domain_attempt_with_failed_original_receipt(core, ordinary_users):
    class NotSentTwice(Model):
        async def submit(self,request,*,completion):
            if len(self.submissions) < 2:
                self.submissions.append((request,completion))
                receipt = await recorded_failure(core.database, request)
                self.results[request.attempt_id] = ModelResult((),receipt)
                return receipt
            return await super().submit(request,completion=completion)

    async def mature_retry(batch):
        lineage = batch["manifest"]["recovery"]["lineage_id"]
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()-interval '1 second' "
                "WHERE batch_id=%s",(batch["batch_id"],))
            await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' "
                "WHERE lineage_id=%s",(lineage,))
            await conn.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second',"
                "probe_token=NULL,probe_expires_at=NULL WHERE (data_generation,dependency) IN "
                "(SELECT data_generation,dependency FROM runtime.recovery_lineages WHERE lineage_id=%s)",(lineage,))

    alice, _ = ordinary_users
    model, policy = NotSentTwice(), RecoveryPolicy()
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=policy)
    first = await examples.episode(core,alice,"rbf-original-two-failures-a")
    second = await examples.episode(core,alice,"rbf-original-two-failures-b")
    partition, _ = await batch_for_episode(core,first.episode_id)
    await core.workers.consolidate(partition)
    _, held = await batch_for_episode(core,first.episode_id)
    await mature_retry(held)
    await core.workers.consolidate(partition)
    _, held = await batch_for_episode(core,first.episode_id)
    assert held["status"] == "quarantined" and len(held["step_ids"]) == 2 and len(model.submissions) == 2
    assert {item["episode_id"] for item in held["manifest"]["episodes"]} == {first.episode_id,second.episode_id}
    lineage = held["manifest"]["recovery"]["lineage_id"]
    async with core.database.transaction() as conn:
        original_steps = await (await conn.execute("SELECT step_id,request FROM memory.model_steps "
            "WHERE step_id=ANY(%s::text[]) ORDER BY step_id",(held["step_ids"],))).fetchall()
        original_ledger = await (await conn.execute("SELECT attempt_id,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number",(lineage,))).fetchall()
        budget = await (await conn.execute("SELECT attempts,max_attempts FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                         (lineage,))).fetchone()
        # Recreate the historical wrapper misclassification in this disposable
        # fixture: Models and policy retain two failed originals; only the
        # domain's last observed status was still queued.
        await conn.execute("UPDATE memory.consolidation_batches SET status='failed',error_code=%s,retry_due_at=NULL "
            "WHERE batch_id=%s",("recovery_dependency_probe_in_progress",held["batch_id"]))
        await conn.execute("UPDATE memory.consolidation_attempts SET status='queued',error_code=NULL "
            "WHERE step_id=%s",(held["step_ids"][-1],))
        await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_failed' "
            "WHERE episode_id=ANY(%s::text[])",([first.episode_id,second.episode_id],))
    assert [item["status"] for item in original_ledger] == ["failed","failed"] and budget["attempts"] == 2
    async with core.database.transaction() as conn:
        result = await core.workers.recovery.resume_on(conn,alice,{"episode_id":first.episode_id,
            "expected_revision":first.revision,"client_request_id":"historical-two-failures"})
    assert result.data["action"] == "resume_original_batch" and result.data["batch_id"] == held["batch_id"]
    await core.workers.consolidate(partition,batch_id=held["batch_id"])
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                                          (held["batch_id"],))).fetchone()
        attempts = await (await conn.execute("SELECT status,recovery_policy_state FROM memory.consolidation_attempts "
            "WHERE batch_id=%s ORDER BY number",(held["batch_id"],))).fetchall()
    assert current["status"] == "quarantined" and current["step_ids"] == held["step_ids"]
    assert attempts[-1]["status"] == attempts[-1]["recovery_policy_state"] == "failed"
    assert len(model.submissions) == 2
    await mature_retry(current)
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        applied = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                                          (held["batch_id"],))).fetchone()
        steps = await (await conn.execute("SELECT step_id,request FROM memory.model_steps "
            "WHERE step_id=ANY(%s::text[]) ORDER BY step_id",(held["step_ids"],))).fetchall()
        ledger = await (await conn.execute("SELECT attempt_id,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number",(lineage,))).fetchall()
        final_budget = await (await conn.execute("SELECT attempts,max_attempts FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                               (lineage,))).fetchone()
        episodes = await (await conn.execute("SELECT episode_id,extraction_state FROM memory.episodes "
            "WHERE episode_id=ANY(%s::text[])",([first.episode_id,second.episode_id],))).fetchall()
    assert applied["status"] == "applied" and len(model.submissions) == 3
    assert steps == original_steps and ledger[:2] == original_ledger and ledger[-1]["status"] == "succeeded"
    assert final_budget == {"attempts":3,"max_attempts":budget["max_attempts"]}
    assert len(episodes) == 2 and all(episode["extraction_state"] == "completed" for episode in episodes)


async def test_lost_claim_leaves_original_batch_to_queue_owner(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, policy = Model(), RecoveryPolicy()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=policy)

    async def lost(*args, **kwargs):
        raise RecoveryWait("recovery_claim_lost", datetime.now(UTC)+timedelta(seconds=30))

    monkeypatch.setattr(policy, "reserve_attempt_on", lost)
    item = await examples.episode(core, alice, "rbf-claim-lost")
    partition, _ = await batch_for_episode(core, item.episode_id)
    with pytest.raises(RecoveryWait, match="recovery_claim_lost"):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    assert batch["status"] in {"prepared", "waiting_model"}
    assert "local_wait" not in batch["manifest"] and batch["manifest"]["recovery_pending"]
    assert not model.submissions


async def test_known_success_commit_failure_reuses_selected_compilation(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model = Model()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    apply = core.memory.consolidation._apply
    failed = False

    async def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            error = OSError("fixture connection lost")
            error.database_failure = DatabaseFailure("database_pool_timeout", "transaction", "fixture", "worker",
                None, True, True, False, datetime.now(UTC)+timedelta(seconds=10))
            raise error
        return await apply(*args, **kwargs)

    monkeypatch.setattr(core.memory.consolidation, "_apply", fail_once)
    item = await examples.episode(core, alice, "rbf-commit-rollback")
    partition, _ = await batch_for_episode(core, item.episode_id)
    with pytest.raises(OSError):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        selected = await (await conn.execute("SELECT step_id FROM memory.consolidation_compilations WHERE batch_id=%s AND selected",
                                            (batch["batch_id"],))).fetchone()
    assert selected and len(model.submissions) == 1
    await core.workers.consolidate(partition)
    _, applied = await batch_for_episode(core, item.episode_id)
    assert applied["status"] == "applied" and len(model.submissions) == 1


async def test_growth_episode_lock_includes_extra_source_closure(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    item = await examples.episode(core, alice, "rbf-episode-lock")
    extra = await core.sources.record_event(alice, producer="web", event_id="rbf-growth-extra", revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind":"text", "text":"extra evidence"},), origin_kind=OriginKind.HUMAN)
    seen = []
    validate = core.sources.validate_persistence

    async def recording(conn, context, refs):
        seen.append(tuple(refs))
        return await validate(conn, context, refs)

    monkeypatch.setattr(core.sources, "validate_persistence", recording)
    async with core.database.transaction() as conn:
        page = await core.memory.lock_episodes(alice, [(item.episode_id, item.revision)], connection=conn,
                                               extra_source_refs=extra)
    assert page.coverage.complete and len(page.items) == 1
    assert {ref.source_id for ref in extra} <= {ref.source_id for ref in seen[-1]}
    assert {ref.source_id for ref in item.source_refs} <= {ref.source_id for ref in seen[-1]}


async def test_repair_wait_resumes_original_result_without_spending_extra_attempt(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, policy = Model(invalid_first=True), RecoveryPolicy()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=policy)
    reserve = policy.reserve_attempt_on
    blocked = False

    async def wait_for_repair(*args, **kwargs):
        nonlocal blocked
        if kwargs.get("original_state") == "succeeded" and not blocked:
            blocked = True
            raise RecoveryWait("recovery_dependency_cooling", datetime.now(UTC)+timedelta(minutes=1),
                               dependency="models:fixture")
        return await reserve(*args, **kwargs)

    monkeypatch.setattr(policy, "reserve_attempt_on", wait_for_repair)
    item = await examples.episode(core, alice, "rbf-repair-wait")
    partition, _ = await batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, waiting = await batch_for_episode(core, item.episode_id)
    assert waiting["status"] == "quarantined" and len(waiting["step_ids"]) == 2 and len(model.submissions) == 1
    assert waiting["manifest"]["recovery_pending"]["step_id"] == waiting["step_ids"][-1]
    lineage = waiting["manifest"]["recovery"]["lineage_id"]
    async with core.database.transaction(read_only=True) as conn:
        before = await (await conn.execute("SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s", (lineage,))).fetchone()
    assert before["attempts"] == 1
    await force_batch_due(core, waiting["batch_id"])
    await core.workers.consolidate(partition, batch_id=waiting["batch_id"])
    _, applied = await batch_for_episode(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        attempts = await (await conn.execute("SELECT * FROM runtime.recovery_attempts WHERE lineage_id=%s ORDER BY attempt_number",
                                           (lineage,))).fetchall()
    assert applied["status"] == "applied" and applied["batch_id"] == waiting["batch_id"]
    assert [entry["attempt_number"] for entry in attempts] == [1, 2]
    assert attempts[1]["continuation_of"] == attempts[0]["attempt_id"] and len(model.submissions) == 2


class TemporarilyInvisibleModel(Model):
    def __init__(self):
        super().__init__()
        self.missing = False

    async def submit(self, request, *, completion):
        receipt = await super().submit(request, completion=completion)
        return replace(receipt, status="queued", dispatch="not_started")

    async def status(self, attempt_id, *, phase="observe"):
        self.phases.append((attempt_id, phase))
        if self.missing:
            return None
        result = self.results.get(attempt_id)
        return replace(result.receipt, status="queued", dispatch="not_started") if result else None

    async def result(self, attempt_id, *, context):
        return None


async def test_dispatched_consolidation_missing_control_is_observed_not_resent(core, ordinary_users):
    alice, _ = ordinary_users
    model = TemporarilyInvisibleModel()
    examples.configure(core, model)
    item = await examples.episode(core, alice, "rbf-no-resubmit")
    partition, _ = await batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    assert batch["status"] == "waiting_model" and len(model.submissions) == 1
    model.missing = True
    await core.workers.consolidate(partition)
    assert len(model.submissions) == 1 and model.phases[-1][1] == "observe"


class IndexModel:
    def __init__(self):
        self.receipts, self.submissions, self.phases = {}, [], []
        self.missing = False

    async def embedding_profile(self):
        return {"route_id": "fixture", "route_revision": 1, "provider_id": "fixture", "model": "fixture",
                "quota_group": "fixture", "dimensions": 1024, "normalization": "none"}

    async def submit(self, request, *, completion):
        self.submissions.append(request)
        receipt = ModelReceipt(request.attempt_id, request.intent_id, "queued", "not_started", "fixture", 1,
                               "fixture", "fixture", "openai.embed", "fixture", data_generation=request.data_generation)
        self.receipts[request.attempt_id] = receipt
        return receipt

    async def status(self, attempt_id, *, phase="observe"):
        self.phases.append((attempt_id, phase))
        return None if self.missing else self.receipts.get(attempt_id)


async def test_index_missing_ack_control_does_not_resubmit(core, ordinary_users):
    alice, _ = ordinary_users
    model = IndexModel()
    core.workers.embeddings.models = model
    core.steps.models = model
    item = await examples.episode(core, alice, "rbf-index-no-resubmit")
    async with core.database.transaction(read_only=True) as conn:
        generation = await core.memory.jobs.current_generation_on(conn)
    await core.workers.index_object("episode", item.episode_id, item.revision, generation=generation)
    async with core.database.transaction(read_only=True) as conn:
        job = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE kind='episode' AND object_id=%s",
                                       (item.episode_id,))).fetchone()
    assert job["state"] == "observing" and len(model.submissions) == 1
    model.missing = True
    await core.workers.index_retry(job["index_id"], completed_attempt_id=model.submissions[0].attempt_id,
                                  generation=generation)
    async with core.database.transaction(read_only=True) as conn:
        job = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE index_id=%s", (job["index_id"],))).fetchone()
    assert job["state"] == "observing" and len(model.submissions) == 1 and model.phases[-1][1] == "observe"


async def test_unknown_commit_observes_existing_applied_batch(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model = Model()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    apply = core.memory.consolidation._apply

    async def commit_then_lose_ack(*args, **kwargs):
        await apply(*args, **kwargs)
        error = OSError("fixture commit acknowledgement lost")
        error.database_failure = DatabaseFailure("database_commit_unknown", "commit", "fixture", "worker",
            None, False, False, True, None)
        raise error

    monkeypatch.setattr(core.memory.consolidation, "_apply", commit_then_lose_ack)
    item = await examples.episode(core, alice, "rbf-commit-unknown")
    partition, _ = await batch_for_episode(core, item.episode_id)
    with pytest.raises(OSError):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    assert batch["status"] == "applied" and batch["commit_refs"] and len(model.submissions) == 1
    await core.workers.consolidate(partition)
    assert len(model.submissions) == 1


async def test_unconfirmed_local_commit_keeps_slot_until_selected_compilation_reconciles(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model = Model()
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    apply = core.memory.consolidation._apply
    failed = False

    async def lose_commit_ack(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            error = OSError("fixture local commit acknowledgement unknown")
            error.database_failure = DatabaseFailure("database_commit_unknown", "commit", "fixture", "worker",
                None, False, False, True, None)
            raise error
        return await apply(*args, **kwargs)

    monkeypatch.setattr(core.memory.consolidation,"_apply",lose_commit_ack)
    item = await examples.episode(core,alice,"rbf-commit-unknown-pending")
    partition, _ = await batch_for_episode(core,item.episode_id)
    with pytest.raises(OSError):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core,item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        state = await (await conn.execute("SELECT active_batch_id FROM memory.consolidation_state WHERE partition_id=%s",
                                         (partition,))).fetchone()
        selected = await (await conn.execute("SELECT step_id FROM memory.consolidation_compilations WHERE batch_id=%s AND selected",
                                            (batch["batch_id"],))).fetchone()
        episode = await (await conn.execute("SELECT extraction_cursor FROM memory.episodes WHERE episode_id=%s",
                                           (item.episode_id,))).fetchone()
    original_cursor = batch["manifest"]["episodes"][0]["before"]
    assert state["active_batch_id"] == batch["batch_id"] and selected
    assert episode["extraction_cursor"] == original_cursor
    assert len(model.submissions) == 1
    await core.workers.consolidate(partition)
    _, applied = await batch_for_episode(core,item.episode_id)
    assert applied["status"] == "applied" and applied["step_ids"] == batch["step_ids"]
    assert len(model.submissions) == 1


class DisconnectBeforeAdmission(Model):
    def __init__(self, *, lose_ack=False):
        super().__init__()
        self.admissions = []
        self.lose_ack = lose_ack

    async def submit(self, request, *, completion):
        self.admissions.append((wire(request), wire(completion)))
        if len(self.admissions) == 1:
            if self.lose_ack:
                await super().submit(request, completion=completion)
            raise TimeoutError("fixture connection interrupted")
        return await super().submit(request, completion=completion)


@pytest.mark.parametrize("lose_ack", [False, True])
async def test_interrupted_admission_keeps_complete_original_identity(core, ordinary_users, lose_ack):
    alice, _ = ordinary_users
    model = DisconnectBeforeAdmission(lose_ack=lose_ack)
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, "rbf-admission-"+str(lose_ack))
    partition, _ = await batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    if not lose_ack:
        assert batch["status"] == "quarantined" and len(model.submissions) == 0
        await force_batch_due(core, batch["batch_id"])
        await core.workers.consolidate(partition, batch_id=batch["batch_id"])
        assert len(model.admissions) == 2 and model.admissions[0] == model.admissions[1]
    else:
        # Original Models success is already visible; it is consumed without re-admission.
        assert len(model.admissions) == 1
    _, batch = await batch_for_episode(core, item.episode_id)
    assert batch["status"] == "applied" and len(model.submissions) == 1


class IndexDisconnectBeforeAdmission(IndexModel):
    def __init__(self):
        super().__init__()
        self.admissions = []

    async def submit(self, request, *, completion):
        self.admissions.append((wire(request), wire(completion)))
        if len(self.admissions) == 1:
            raise TimeoutError("fixture connection interrupted")
        return await super().submit(request, completion=completion)


async def test_index_interrupted_before_broker_admits_identical_original(core, ordinary_users):
    alice, _ = ordinary_users
    model = IndexDisconnectBeforeAdmission()
    core.workers.embeddings.models = model
    core.steps.models = model
    item = await examples.episode(core, alice, "rbf-index-admission")
    async with core.database.transaction(read_only=True) as conn:
        generation = await core.memory.jobs.current_generation_on(conn)
    await core.workers.index_object("episode", item.episode_id, item.revision, generation=generation)
    async with core.database.transaction(read_only=True) as conn:
        job = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE kind='episode' AND object_id=%s",
                                       (item.episode_id,))).fetchone()
    assert job["state"] == "observing" and len(model.submissions) == 0
    await core.workers.index_retry(job["index_id"], completed_attempt_id=model.admissions[0][0]["attempt_id"],
                                  generation=generation)
    assert len(model.admissions) == 2 and model.admissions[0] == model.admissions[1]
    assert len(model.submissions) == 1


async def test_attribution_query_returns_proven_identity_without_body(core, ordinary_users):
    alice, _ = ordinary_users
    model = Model()
    examples.configure(core, model)
    item = await examples.episode(core, alice, "rbf-attribution")
    partition, _ = await batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    result = await core.workers.recovery.attribution.for_episode(alice, item.episode_id)
    assert result["coverage"]["complete"] and result["items"][0]["steps"][0]["attribution"] == "new_material"
    assert "我喜欢苹果" not in str(result)


async def test_changed_episode_during_wait_cannot_spend_model_attempt(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, policy = Model(), RecoveryPolicy()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=policy)

    async def wait(*args, **kwargs):
        raise RecoveryWait("recovery_dependency_cooling", datetime.now(UTC)+timedelta(minutes=1))

    monkeypatch.setattr(policy, "reserve_attempt_on", wait)
    item = await examples.episode(core, alice, "rbf-stale-wait")
    partition, _ = await batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET revision=revision+1,extraction_state='pending' WHERE episode_id=%s",
                           (item.episode_id,))
    await force_batch_due(core, batch["batch_id"])
    await core.workers.consolidate(partition, batch_id=batch["batch_id"])
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status FROM memory.consolidation_batches WHERE batch_id=%s",
                                            (batch["batch_id"],))).fetchone()
        count = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_attempts")).fetchone()
    assert current["status"] == "stale" and count["n"] == 0 and not model.submissions


@pytest.mark.parametrize("with_policy",[False,True])
async def test_control_unavailable_before_admission_keeps_frozen_attempt_for_reobservation(core, ordinary_users, with_policy):
    class OfflineBeforeAdmission(Model):
        def __init__(self):
            super().__init__()
            self.offline = True

        async def status(self,attempt_id,*,phase="observe"):
            if self.offline and phase == "pre_submit":
                raise AppError("internal_error",status=500)
            return await super().status(attempt_id,phase=phase)

    alice, _ = ordinary_users
    model = OfflineBeforeAdmission()
    examples.configure(core,model)
    if with_policy:
        core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    item = await examples.episode(core,alice,"rbf-control-before-admission")
    partition, _ = await batch_for_episode(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, held = await batch_for_episode(core,item.episode_id)
    assert held["status"] == "quarantined" and not model.submissions
    step_id = held["step_ids"][0]
    async with core.database.transaction(read_only=True) as conn:
        step = await (await conn.execute("SELECT request,state FROM memory.model_steps WHERE step_id=%s",(step_id,))).fetchone()
        attempts = await (await conn.execute("SELECT * FROM memory.consolidation_attempts WHERE batch_id=%s",
                                           (held["batch_id"],))).fetchall()
    assert step["state"] == "prepared" and len(attempts) == 1
    assert attempts[0]["first_failure"]["rpc_failure"]["error_code"] == "internal_error"
    await force_batch_due(core,held["batch_id"])
    await core.workers.consolidate(partition)
    assert not model.submissions
    model.offline = False
    await force_batch_due(core,held["batch_id"])
    await core.workers.consolidate(partition)
    _, applied = await batch_for_episode(core,item.episode_id)
    assert applied["status"] == "applied" and applied["step_ids"] == [step_id]
    assert len(model.submissions) == 1 and model.submissions[0][0].attempt_id == step["request"]["attempt_id"]


async def test_permanent_pre_admission_control_error_stays_terminal(core, ordinary_users):
    class MisconfiguredModel(Model):
        async def status(self,attempt_id,*,phase="observe"):
            if phase == "pre_submit":
                raise AppError("model_function_not_configured",status=503)
            return await super().status(attempt_id,phase=phase)

    alice, _ = ordinary_users
    model = MisconfiguredModel()
    examples.configure(core,model)
    item = await examples.episode(core,alice,"rbf-control-permanent")
    partition, _ = await batch_for_episode(core,item.episode_id)
    await core.workers.consolidate(partition)
    _, failed = await batch_for_episode(core,item.episode_id)
    assert failed["status"] == "failed" and failed["error_code"] == "model_function_not_configured"
    assert not model.submissions


def remote_unconfirmed_wait():
    return recovery_wait_from_wire({"kind":"recovery_wait", "version":1, "reason":"source_lock_set_changed",
        "retry_at":(datetime.now(UTC)+timedelta(seconds=30)).isoformat(),
        "dependency":"sources:lock_set", "rollback_confirmed":False})


class UnconfirmedModel(Model):
    def __init__(self):
        super().__init__()
        self.admissions = []
        self.original_attempt_id = None

    async def submit(self, request, *, completion):
        self.admissions.append((wire(request),wire(completion)))
        if self.original_attempt_id is None:
            self.original_attempt_id = request.attempt_id
        if request.attempt_id == self.original_attempt_id:
            raise remote_unconfirmed_wait()
        return await super().submit(request, completion=completion)


def original_admissions(model):
    return [request for request, _ in model.admissions if request["attempt_id"] == model.original_attempt_id]


async def stored_batch(core, batch_id):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                                       (batch_id,))).fetchone()


async def test_remote_rollback_false_isolates_original_and_later_input_advances(core, ordinary_users):
    alice, _ = ordinary_users
    model = UnconfirmedModel()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, "rbf-remote-unconfirmed")
    partition, _ = await batch_for_episode(core, item.episode_id)
    with pytest.raises(RecoveryWait) as error:
        await core.workers.consolidate(partition)
    assert error.value.remote_rollback_confirmed is False
    _, batch = await batch_for_episode(core, item.episode_id)
    batch_id, step_id = batch["batch_id"], batch["step_ids"][0]
    marker = batch["manifest"]["remote_wait_unresolved"]
    assert marker["remote_rollback_confirmed"] is False and marker["reason"] == "source_lock_set_changed"
    assert batch["status"] == "quarantined" and len(original_admissions(model)) == 1
    async with core.database.transaction(read_only=True) as conn:
        state = await (await conn.execute("SELECT active_batch_id FROM memory.consolidation_state WHERE partition_id=%s",
                                         (partition,))).fetchone()
        episode = await (await conn.execute("SELECT extraction_state FROM memory.episodes WHERE episode_id=%s",
                                           (item.episode_id,))).fetchone()
    assert state["active_batch_id"] is None and episode["extraction_state"] == "consolidation_unknown"

    later = await examples.episode(core, alice, "rbf-remote-independent")
    await core.workers.consolidate(partition)
    _, next_batch = await batch_for_episode(core, later.episode_id)
    assert next_batch["status"] == "applied" and next_batch["batch_id"] != batch_id
    assert len(original_admissions(model)) == 1
    with pytest.raises(AppError, match="memory_remote_wait_unresolved"):
        await core.workers.consolidate(partition, batch_id=batch_id)
    assert len(original_admissions(model)) == 1

    # An authoritative result appears independently; it is consumed by the
    # original ID after revalidating its own sources, versions and read set.
    original = request_from_wire(original_admissions(model)[0])
    await Model.submit(model, original, completion=None)
    await core.workers.consolidate(partition, batch_id=batch_id)
    applied = await stored_batch(core, batch_id)
    assert applied["status"] == "applied" and applied["step_ids"] == [step_id]
    assert applied["manifest"]["last_unresolved_wait"]["remote_rollback_confirmed"] is False
    assert applied["manifest"]["original_observation_only"] and len(original_admissions(model)) == 1


async def test_unconfirmed_local_transaction_keeps_original_slot(core, ordinary_users):
    class LocalUnconfirmedModel(UnconfirmedModel):
        async def submit(self, request, *, completion):
            try:
                return await super().submit(request, completion=completion)
            except RecoveryWait as wait:
                wait.transaction_rollback_confirmed = False
                raise

    alice, _ = ordinary_users
    model = LocalUnconfirmedModel()
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    item = await examples.episode(core,alice,"rbf-local-unconfirmed")
    partition, _ = await batch_for_episode(core,item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core,item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        state = await (await conn.execute("SELECT active_batch_id FROM memory.consolidation_state WHERE partition_id=%s",
                                         (partition,))).fetchone()
    assert batch["status"] == "waiting_model" and state["active_batch_id"] == batch["batch_id"]
    assert batch["manifest"]["remote_wait_unresolved"]["transaction_rollback_confirmed"] is False
    assert len(original_admissions(model)) == 1


async def test_unconfirmed_original_unknown_receipt_cannot_spawn_successor(core, ordinary_users):
    alice, _ = ordinary_users
    model = UnconfirmedModel()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, "rbf-remote-terminal-unknown")
    partition, _ = await batch_for_episode(core, item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    original = request_from_wire(original_admissions(model)[0])
    await Model.submit(model, original, completion=None)
    result = model.results[original.attempt_id]
    model.results[original.attempt_id] = replace(result,receipt=replace(result.receipt,status="unknown",
        finished_at=datetime.now(UTC)))
    for _ in range(2):
        with pytest.raises(AppError, match="memory_remote_wait_unresolved"):
            await core.workers.consolidate(partition, batch_id=batch["batch_id"])
    held = await stored_batch(core,batch["batch_id"])
    assert held["status"] == "quarantined" and held["step_ids"] == batch["step_ids"]
    assert held["manifest"]["remote_wait_unresolved"]["remote_rollback_confirmed"] is False
    assert len(original_admissions(model)) == 1
    async with core.database.transaction(read_only=True) as conn:
        attempts = await (await conn.execute("SELECT model_attempt_id FROM memory.consolidation_attempts WHERE batch_id=%s",
                                           (batch["batch_id"],))).fetchall()
    assert len(attempts) == 1 and attempts[0]["model_attempt_id"] == original.attempt_id


async def test_unconfirmed_original_invalid_output_never_creates_repair_attempt(core, ordinary_users):
    alice, _ = ordinary_users
    model = UnconfirmedModel()
    model.invalid_first = True
    examples.configure(core,model)
    core.memory.bind_observability(statistics=None,recovery_policy=RecoveryPolicy())
    item = await examples.episode(core,alice,"rbf-remote-invalid-output")
    partition, _ = await batch_for_episode(core,item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core,item.episode_id)
    original = request_from_wire(original_admissions(model)[0])
    await Model.submit(model,original,completion=None)
    await core.workers.consolidate(partition,batch_id=batch["batch_id"])
    failed = await stored_batch(core,batch["batch_id"])
    assert failed["status"] == "failed" and failed["manifest"]["original_observation_only"]
    assert failed["step_ids"] == batch["step_ids"] and len(original_admissions(model)) == 1
    async with core.database.transaction() as conn:
        attempts = await (await conn.execute("SELECT step_id FROM memory.consolidation_attempts WHERE batch_id=%s",
                                            (batch["batch_id"],))).fetchall()
        resumed = await core.workers.recovery.resume_on(conn,alice,{"episode_id":item.episode_id,
            "expected_revision":item.revision,"client_request_id":"no-repair-unconfirmed"})
    assert len(attempts) == 1 and resumed.data["queued"] is False


async def test_late_original_result_cannot_overwrite_changed_episode(core, ordinary_users):
    alice, _ = ordinary_users
    model = UnconfirmedModel()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, "rbf-remote-stale")
    partition, _ = await batch_for_episode(core, item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core, item.episode_id)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET revision=revision+1,extraction_state='pending' WHERE episode_id=%s",
                           (item.episode_id,))
    original = request_from_wire(original_admissions(model)[0])
    await Model.submit(model,original,completion=None)
    await core.workers.consolidate(partition,batch_id=batch["batch_id"])
    after = await stored_batch(core,batch["batch_id"])
    async with core.database.transaction(read_only=True) as conn:
        episode = await (await conn.execute("SELECT revision,extraction_state,extraction_cursor FROM memory.episodes "
                                          "WHERE episode_id=%s",(item.episode_id,))).fetchone()
    assert after["status"] == "stale" and after["commit_refs"] == {}
    assert after["manifest"]["remote_wait_unresolved"]["remote_rollback_confirmed"] is False
    assert episode["revision"] == item.revision+1 and episode["extraction_state"] == "pending"
    assert episode["extraction_cursor"] == batch["manifest"]["episodes"][0]["before"]
    assert len(original_admissions(model)) == 1


class UnconfirmedIndexModel(IndexModel):
    def __init__(self):
        super().__init__()
        self.admissions = []

    async def submit(self, request, *, completion):
        self.admissions.append((wire(request),wire(completion)))
        raise remote_unconfirmed_wait()


async def test_index_remote_rollback_false_only_observes_original(core, ordinary_users):
    alice, _ = ordinary_users
    model = UnconfirmedIndexModel()
    core.workers.embeddings.models = model
    core.steps.models = model
    item = await examples.episode(core, alice, "rbf-index-remote-unconfirmed")
    async with core.database.transaction(read_only=True) as conn:
        generation = await core.memory.jobs.current_generation_on(conn)
    with pytest.raises(RecoveryWait) as error:
        await core.workers.index_object("episode", item.episode_id, item.revision, generation=generation)
    assert error.value.remote_rollback_confirmed is False
    async with core.database.transaction(read_only=True) as conn:
        job = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE kind='episode' AND object_id=%s",
                                       (item.episode_id,))).fetchone()
    assert '"remote_rollback_confirmed":false' in job["last_error"]
    with pytest.raises(AppError, match="memory_remote_wait_unresolved"):
        await core.workers.index_retry(job["index_id"], completed_attempt_id=model.admissions[0][0]["attempt_id"],
                                      generation=generation)
    assert len(model.admissions) == 1
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT state,last_error,next_due FROM memory.index_jobs WHERE index_id=%s",
                                           (job["index_id"],))).fetchone()
    assert current["next_due"] is None and '"remote_rollback_confirmed":false' in current["last_error"]



class UnconfirmedProfileModel(Model):
    def __init__(self):
        super().__init__()
        self.profile_calls = 0

    async def generation_profile(self, request):
        self.profile_calls += 1
        if self.profile_calls == 1:
            raise remote_unconfirmed_wait()
        return await super().generation_profile(request)


async def test_read_only_profile_reobserves_without_blocking_new_material(core, ordinary_users):
    alice, _ = ordinary_users
    model = UnconfirmedProfileModel()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, "rbf-profile-unconfirmed")
    partition, _ = await batch_for_episode(core, item.episode_id)
    with pytest.raises(RecoveryWait) as error:
        await core.workers.consolidate(partition)
    assert error.value.remote_rollback_confirmed is False
    _, held = await batch_for_episode(core,item.episode_id)
    batch_id, step_id = held["batch_id"], held["step_ids"][0]
    assert held["status"] == "quarantined" and held["manifest"]["profile_pending"]
    assert held["manifest"]["remote_wait_unresolved"]["stage"] == "route_profile"
    async with core.database.transaction(read_only=True) as conn:
        draft = await (await conn.execute("SELECT request FROM memory.model_steps WHERE step_id=%s",(step_id,))).fetchone()
        ledger = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_attempts")).fetchone()
    assert ledger["n"] == 0 and not model.submissions

    later = await examples.episode(core, alice, "rbf-profile-independent")
    await core.workers.consolidate(partition)
    _, next_batch = await batch_for_episode(core,later.episode_id)
    assert next_batch["status"] == "applied" and next_batch["batch_id"] != batch_id
    await force_batch_due(core,batch_id)
    await core.workers.consolidate(partition,batch_id=batch_id)
    applied = await stored_batch(core,batch_id)
    assert applied["status"] == "applied" and applied["step_ids"] == [step_id]
    assert applied["manifest"]["last_unresolved_wait"]["remote_rollback_confirmed"] is False
    assert sum(request.attempt_id == draft["request"]["attempt_id"] for request, _ in model.submissions) == 1


async def test_profile_reobservation_stops_if_local_transaction_becomes_uncertain(core, ordinary_users):
    class ProfileBecomesUncertain(UnconfirmedProfileModel):
        async def generation_profile(self, request):
            self.profile_calls += 1
            wait = remote_unconfirmed_wait()
            if self.profile_calls > 1:
                wait.transaction_rollback_confirmed = False
            raise wait

    alice, _ = ordinary_users
    model = ProfileBecomesUncertain()
    examples.configure(core,model)
    item = await examples.episode(core,alice,"rbf-profile-local-unknown")
    partition, _ = await batch_for_episode(core,item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition)
    _, held = await batch_for_episode(core,item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition,batch_id=held["batch_id"])
    with pytest.raises(AppError,match="memory_remote_wait_unresolved"):
        await core.workers.consolidate(partition,batch_id=held["batch_id"])
    held = await stored_batch(core,held["batch_id"])
    assert held["manifest"]["remote_wait_unresolved"]["last_reobservation"]["transaction_rollback_confirmed"] is False
    assert model.profile_calls == 2 and not model.submissions
    status = await core.memory.extraction_status(alice,item.episode_id)
    assert status["action"] == "wait_for_reconciliation"


async def test_read_only_preparation_wait_reobserves_and_retains_evidence(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model = Model()
    examples.configure(core,model)
    build = core.memory.consolidation.materials.build
    waited = False

    async def wait_once(*args, **kwargs):
        nonlocal waited
        if not waited:
            waited = True
            raise remote_unconfirmed_wait()
        return await build(*args, **kwargs)

    monkeypatch.setattr(core.memory.consolidation.materials,"build",wait_once)
    item = await examples.episode(core,alice,"rbf-preparation-read")
    partition, _ = await batch_for_episode(core,item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition)
    async with core.database.transaction() as conn:
        state = await (await conn.execute("SELECT last_error,wake_due_at FROM memory.consolidation_state "
                                         "WHERE partition_id=%s",(partition,))).fetchone()
        count = await (await conn.execute("SELECT count(*) AS n FROM memory.consolidation_batches WHERE partition_id=%s",
                                         (partition,))).fetchone()
        await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=now()-interval '1 second' WHERE partition_id=%s",
                           (partition,))
    assert state["last_error"].startswith("memory_preparation_read_observation:") and state["wake_due_at"]
    assert count["n"] == 0 and not model.submissions
    await core.workers.consolidate(partition)
    _, batch = await batch_for_episode(core,item.episode_id)
    assert batch["status"] == "applied" and len(model.submissions) == 1
    assert batch["manifest"]["preparation_observation"]["first"]["remote_rollback_confirmed"] is False


async def test_uncertain_candidate_preparation_holds_only_its_exact_input(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model = Model()
    examples.configure(core,model)
    attach = core.memory.consolidation.materials.attach_records
    waited = False

    async def wait_once(*args, **kwargs):
        nonlocal waited
        if not waited:
            waited = True
            raise remote_unconfirmed_wait()
        return await attach(*args, **kwargs)

    monkeypatch.setattr(core.memory.consolidation.materials,"attach_records",wait_once)
    item = await examples.episode(core,alice,"rbf-candidate-uncertain")
    partition, _ = await batch_for_episode(core,item.episode_id)
    with pytest.raises(RecoveryWait):
        await core.workers.consolidate(partition)
    _, held = await batch_for_episode(core,item.episode_id)
    assert held["status"] == "quarantined" and held["step_ids"] == []
    assert held["manifest"]["preparation_hold"]["stage"] == "candidate_recall"
    assert held["manifest"]["preparation_hold"]["closure_complete"] is False and not model.submissions
    async with core.database.transaction() as conn:
        episode = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s",(item.episode_id,))).fetchone()
        status = await core.workers.recovery.status_on(conn,alice,episode)
        result = await core.workers.recovery.resume_on(conn,alice,{"episode_id":item.episode_id,
            "expected_revision":item.revision,"client_request_id":"candidate-no-attempt"})
    assert status["action"] == "wait_for_reconciliation" and status["preparation_stage"] == "candidate_recall"
    assert result.data["action"] == "wait_for_reconciliation" and result.data["queued"] is False
    later = await examples.episode(core,alice,"rbf-candidate-independent")
    await core.workers.consolidate(partition)
    _, next_batch = await batch_for_episode(core,later.episode_id)
    assert next_batch["status"] == "applied" and next_batch["batch_id"] != held["batch_id"]
    assert (await stored_batch(core,held["batch_id"]))["manifest"]["remote_wait_unresolved"]["step_id"] is None
    assert len(model.submissions) == 1
