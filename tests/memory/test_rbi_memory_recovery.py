"""RBI Memory stories; execution is reserved for the centralized integration pass."""

import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest
from psycopg.types.json import Jsonb

from tianshu.models.catalog import RoutePlan
from tianshu.models.ledger import AttemptLedger, receipt as ledger_receipt, submission_digest
from tianshu.models.types import ModelReceipt, ModelResult
from tianshu.runtime.recovery_policy import RecoveryPolicy
from tianshu.memory.rbi_recovery import _same_fragment


spec = spec_from_file_location("_rbi_memory_waits", Path(__file__).with_name("test_rbf_wait_recovery.py"))
waits = module_from_spec(spec)
spec.loader.exec_module(waits)
core, ordinary_users, examples = waits.core, waits.ordinary_users, waits.examples


class TwoUnknowns(waits.Model):
    """Use a real disposable Models attempt/submission ledger for the guard."""

    def __init__(self, database):
        super().__init__()
        self.ledger = AttemptLedger(database, "rbi-memory-fixture")

    async def start_request(self, request):
        plan = RoutePlan("fixture", 1, "fixture", "fixture", "openai.chat", "http://localhost",
                         "KEY", "fixture", frozenset({"text"}), {})
        await self.ledger.create(request, plan, {}, frozen=request, data_generation=request.data_generation)
        await self.ledger.started(request.attempt_id, 60, deadline_at=datetime.now(UTC) + timedelta(seconds=60))

    async def submit(self, request, *, completion):
        await self.start_request(request)
        if len(self.submissions) < 2:
            self.submissions.append((request, completion))
            await self.ledger.finish(request.attempt_id, "unknown", error_code="transport_unknown")
            receipt = ledger_receipt(await self.ledger.get(request.attempt_id))
            self.results[request.attempt_id] = ModelResult((), receipt)
            return receipt
        await super().submit(request, completion=completion)
        await self.ledger.finish(request.attempt_id, "succeeded")
        receipt = ledger_receipt(await self.ledger.get(request.attempt_id))
        self.results[request.attempt_id] = replace(self.results[request.attempt_id], receipt=receipt)
        return receipt


async def mature(core, batch):
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET retry_due_at=now()-interval '1 second' WHERE batch_id=%s",
                           (batch["batch_id"],))
        await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s",
                           (batch["manifest"]["recovery"]["lineage_id"],))


async def two_unknown_batch(core, alice, name):
    model = TwoUnknowns(core.database)
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, name)
    partition, _ = await waits.batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, first = await waits.batch_for_episode(core, item.episode_id)
    assert first["status"] == "quarantined"
    await mature(core, first)
    await core.workers.consolidate(partition)
    _, second = await waits.batch_for_episode(core, item.episode_id)
    assert second["status"] == "quarantined" and len(model.submissions) == 2
    return model, item, partition, second


async def test_fresh_second_unknown_supersedes_queued_projection(core, ordinary_users):
    alice, _ = ordinary_users
    model, item, partition, batch = await two_unknown_batch(core, alice, "rbi-two-unknown")
    originals = [model.results[request.attempt_id].receipt for request, _ in model.submissions]
    async with core.database.transaction() as conn:
        before = await (await conn.execute("SELECT step_id,request FROM memory.model_steps WHERE step_id=ANY(%s::text[]) "
            "ORDER BY step_id", (batch["step_ids"],))).fetchall()
        await conn.execute("UPDATE memory.consolidation_attempts SET status='queued',error_code=NULL WHERE step_id=%s",
                           (batch["step_ids"][-1],))
    await mature(core, batch)
    await core.workers.consolidate(partition)
    _, after = await waits.batch_for_episode(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        attempts = await (await conn.execute("SELECT a.number,a.status,a.recovery_policy_state,r.status AS ledger_status "
            "FROM memory.consolidation_attempts a JOIN runtime.recovery_attempts r ON r.attempt_id=a.model_attempt_id "
            "WHERE a.batch_id=%s ORDER BY a.number", (batch["batch_id"],))).fetchall()
        requests = await (await conn.execute("SELECT step_id,request FROM memory.model_steps WHERE step_id=ANY(%s::text[]) "
            "ORDER BY step_id", (batch["step_ids"],))).fetchall()
    assert after["status"] == "applied" and after["error_code"] is None
    assert [row["number"] for row in attempts] == [1, 2, 3]
    assert [(row["status"], row["recovery_policy_state"], row["ledger_status"]) for row in attempts[:2]] == [
        ("unknown", "unknown", "unknown"), ("unknown", "unknown", "unknown")]
    assert requests == before and len(model.submissions) == 3
    assert [model.results[receipt.attempt_id].receipt for receipt in originals] == originals


async def failed_local_batch(core, alice, name):
    model, item, partition, batch = await two_unknown_batch(core, alice, name)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.consolidation_batches SET status='failed',error_code='recovery_original_not_failed',"
            "retry_due_at=NULL WHERE batch_id=%s", (batch["batch_id"],))
        await conn.execute("UPDATE memory.consolidation_attempts SET status='queued',error_code=NULL WHERE step_id=%s",
                           (batch["step_ids"][-1],))
        await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_failed' WHERE episode_id=%s",
                           (item.episode_id,))
    return model, item, partition, batch


async def test_local_failure_reopens_same_batch_and_retains_lineage(core, ordinary_users):
    alice, _ = ordinary_users
    model, item, partition, batch = await failed_local_batch(core, alice, "rbi-local-failure")
    receipts = [model.results[request.attempt_id].receipt for request, _ in model.submissions]
    result = await core.workers.recovery.resume_local_failure(batch["batch_id"], repair_ref="rbi-fixture")
    again = await core.workers.recovery.resume_local_failure(batch["batch_id"], repair_ref="rbi-fixture")
    assert result["reason"] == "reopened" and result["queued"]
    assert again["reason"] == "already_reopened" and not again["queued"]
    assert len(model.submissions) == 2
    async with core.database.transaction(read_only=True) as conn:
        restored = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s",
                                            (batch["batch_id"],))).fetchone()
        attempts = await (await conn.execute("SELECT a.number,a.status,a.recovery_policy_state,r.status AS ledger_status "
            "FROM memory.consolidation_attempts a JOIN runtime.recovery_attempts r ON r.attempt_id=a.model_attempt_id "
            "WHERE a.batch_id=%s ORDER BY a.number", (batch["batch_id"],))).fetchall()
    assert restored["step_ids"] == batch["step_ids"] and restored["status"] == "quarantined"
    assert restored["manifest"]["rbi_local_recovery"]["original_error_code"] == "recovery_original_not_failed"
    assert attempts[-1]["status"] == attempts[-1]["recovery_policy_state"] == attempts[-1]["ledger_status"] == "unknown"
    await mature(core, restored)
    await core.workers.consolidate(partition)
    _, applied = await waits.batch_for_episode(core, item.episode_id)
    assert applied["batch_id"] == batch["batch_id"] and applied["status"] == "applied"
    assert len(model.submissions) == 3
    assert [model.results[receipt.attempt_id].receipt for receipt in receipts] == receipts


@pytest.mark.parametrize("condition,reason", [("lease", "memory_consolidation_busy"),
    ("revision", "memory_episode_changed"), ("paused", "memory_recovery_not_available"),
    ("budget", "recovery_budget_exhausted")])
async def test_local_failure_ineligible_does_not_reopen(core, ordinary_users, condition, reason):
    alice, _ = ordinary_users
    model, item, partition, batch = await failed_local_batch(core, alice, "rbi-ineligible-" + condition)
    lineage = batch["manifest"]["recovery"]["lineage_id"]
    async with core.database.transaction() as conn:
        if condition == "lease":
            await conn.execute("UPDATE memory.consolidation_state SET lease_owner='other',lease_until=now()+interval '5 minutes' "
                               "WHERE partition_id=%s", (partition,))
        elif condition == "revision":
            await conn.execute("UPDATE memory.episodes SET revision=revision+1 WHERE episode_id=%s", (item.episode_id,))
        elif condition == "paused":
            await conn.execute("UPDATE runtime.recovery_lineages SET paused=true WHERE lineage_id=%s", (lineage,))
        else:
            await conn.execute("UPDATE runtime.recovery_lineages SET max_attempts=attempts WHERE lineage_id=%s", (lineage,))
    result = await core.workers.recovery.resume_local_failure(batch["batch_id"], repair_ref="rbi-fixture")
    assert result["reason"] == reason and not result["queued"]
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,error_code,step_ids FROM memory.consolidation_batches WHERE batch_id=%s",
                                            (batch["batch_id"],))).fetchone()
        attempt = await (await conn.execute("SELECT status FROM memory.consolidation_attempts WHERE step_id=%s",
                                            (batch["step_ids"][-1],))).fetchone()
    assert current["status"] == "failed" and current["error_code"] == "recovery_original_not_failed"
    assert current["step_ids"] == batch["step_ids"] and attempt["status"] == "queued" and len(model.submissions) == 2


def test_duplicate_coverage_requires_exact_fragment_and_frozen_input():
    episode = {"episode_id": "ep", "revision": 2, "sequence": 7, "before": {"source_index": 0},
        "after": {"source_index": 1}, "material_ids": ["m"], "complete": True, "missing": []}
    frozen = {"materials": {"m": {"source_refs": [{"source_id": "s", "revision": 1}]}}, "read_set": []}
    original = {"step_ids": ["a"], "selected_step_id": None, "manifest": {"steps": {"a": frozen}}}
    applied = {"step_ids": ["b"], "selected_step_id": "b", "manifest": {"steps": {"b": deepcopy(frozen)}},
        "commit_refs": {"coverage": [{"episode_id": "ep", "revision": 2, "sequence": 7,
            "cursor": {"source_index": 1}, "state": "completed_with_missing", "missing": ["media_unavailable"]}]}}
    assert _same_fragment(original, episode, applied, deepcopy(episode))
    other = deepcopy(episode)
    other["before"], other["after"] = {"source_index": 1}, {"source_index": 2}
    assert not _same_fragment(original, episode, applied, other)
    changed = deepcopy(applied)
    changed["manifest"]["steps"]["b"]["materials"]["m"]["source_refs"][0]["revision"] = 2
    assert not _same_fragment(original, episode, changed, episode)
    changed = deepcopy(applied)
    changed["commit_refs"]["coverage"] = []
    assert not _same_fragment(original, episode, changed, episode)

async def test_exact_applied_coverage_preserves_missing_without_reopening(core, ordinary_users):
    alice, _ = ordinary_users
    model, _, _, batch = await failed_local_batch(core, alice, "rbi-covered")
    covered_batch_id = batch["batch_id"] + ":covered-fixture"
    coverage = []
    for episode in batch["manifest"]["episodes"]:
        coverage.append({"episode_id": episode["episode_id"], "revision": episode["revision"],
            "sequence": episode["sequence"], "cursor": episode["after"], "state": "completed_with_missing",
            "missing": ["media_unavailable"]})
    async with core.database.transaction() as conn:
        await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,"
            "read_set,dependency_refs,context_fingerprint,step_ids,selected_step_id,commit_refs,committed_at) "
            "SELECT %s,partition_id,schema_version,'applied',manifest,read_set,dependency_refs,context_fingerprint,"
            "step_ids,step_ids[cardinality(step_ids)],%s,now() FROM memory.consolidation_batches WHERE batch_id=%s",
            (covered_batch_id, Jsonb({"coverage": coverage}), batch["batch_id"]))
        for episode in batch["manifest"]["episodes"]:
            await conn.execute("UPDATE memory.episodes SET extraction_cursor=%s,extraction_state='completed_with_missing',"
                "extraction_missing=ARRAY['media_unavailable'] WHERE episode_id=%s",
                (Jsonb(episode["after"]), episode["episode_id"]))
    result = await core.workers.recovery.resume_local_failure(batch["batch_id"], repair_ref="rbi-fixture")
    assert result["reason"] == "already_covered" and not result["queued"]
    assert all(value["batch_id"] == covered_batch_id and value["coverage"]["missing"] == ["media_unavailable"]
               for value in result["coverage"])
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,error_code,step_ids FROM memory.consolidation_batches WHERE batch_id=%s",
                                            (batch["batch_id"],))).fetchone()
    assert current["status"] == "failed" and current["error_code"] == "recovery_original_not_failed"
    assert current["step_ids"] == batch["step_ids"] and len(model.submissions) == 2


async def test_head_change_during_observation_cannot_reserve_successor(core, ordinary_users, monkeypatch):
    from tianshu.memory import consolidation_retry

    alice, _ = ordinary_users
    model, _, partition, batch = await two_unknown_batch(core, alice, "rbi-head-race")
    await mature(core, batch)
    observed = consolidation_retry.observe_original
    changed = False

    async def change_selection_after_observation(models, request):
        nonlocal changed
        outcome = await observed(models, request)
        if request.intent_id == batch["step_ids"][-1] and not changed:
            changed = True
            async with core.database.transaction() as conn:
                await conn.execute("UPDATE memory.consolidation_batches SET selected_step_id=%s WHERE batch_id=%s",
                                   (batch["step_ids"][0], batch["batch_id"]))
        return outcome

    monkeypatch.setattr(consolidation_retry, "observe_original", change_selection_after_observation)
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT step_ids,selected_step_id FROM memory.consolidation_batches WHERE batch_id=%s",
                                            (batch["batch_id"],))).fetchone()
        attempts = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_attempts WHERE lineage_id=%s",
            (batch["manifest"]["recovery"]["lineage_id"],))).fetchone()
    assert changed and current["selected_step_id"] == batch["step_ids"][0]
    assert current["step_ids"] == batch["step_ids"] and attempts["n"] == 2 and len(model.submissions) == 2


async def test_maintenance_rotates_and_reuses_existing_native_wakes(core, ordinary_users):
    alice, _ = ordinary_users
    examples.configure(core, waits.Model())
    await examples.episode(core, alice, "rbi-maintenance")
    async with core.database.transaction() as conn:
        template = await (await conn.execute("SELECT * FROM memory.consolidation_state ORDER BY partition_id LIMIT 1")).fetchone()
        generation = await core.memory.jobs.current_generation_on(conn)
        for number in range(3):
            await conn.execute("INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,conversation_id,"
                "scope_key,authority_bound,wake_due_at) VALUES(%s,%s,%s,%s,%s,%s,now())",
                (f"rbi-maintenance-{number}", template["actor_id"], template["principal_id"],
                 template["conversation_id"], template["scope_key"], Jsonb(template["authority_bound"])))
    pages = []
    for _ in range(20):
        result = await core.workers.reconcile_due(limit=1)
        pages.append(result)
        if result["complete"]:
            break
    assert pages[-1]["complete"] and all(value["scanned"] <= 1 for value in pages)
    locks = [f"memory-consolidate:rbi-maintenance-{number}:generation:{generation}" for number in range(3)]
    async with core.database.transaction(read_only=True) as conn:
        before = await (await conn.execute("SELECT j.queueing_lock,j.id,count(k.key) AS keys FROM procrastinate_jobs j "
            "JOIN runtime.job_keys k ON k.job_id=j.id WHERE j.status='todo' AND j.queueing_lock=ANY(%s::text[]) "
            "GROUP BY j.queueing_lock,j.id ORDER BY j.queueing_lock", (locks,))).fetchall()
    assert len(before) == 3
    for _ in range(len(pages)):
        result = await core.workers.reconcile_due(limit=1)
        assert result["woken"] == 0
        if result["complete"]:
            break
    async with core.database.transaction(read_only=True) as conn:
        after = await (await conn.execute("SELECT j.queueing_lock,j.id,count(k.key) AS keys FROM procrastinate_jobs j "
            "JOIN runtime.job_keys k ON k.job_id=j.id WHERE j.status='todo' AND j.queueing_lock=ANY(%s::text[]) "
            "GROUP BY j.queueing_lock,j.id ORDER BY j.queueing_lock", (locks,))).fetchall()
    assert after == before

async def test_normal_resume_command_enqueues_targeted_original_recovery(core, ordinary_users):
    alice, _ = ordinary_users
    model, item, partition, batch = await failed_local_batch(core, alice, "rbi-user-resume")
    async with core.database.transaction() as conn:
        result = await core.workers.recovery.resume_on(conn, alice, {"episode_id": item.episode_id,
            "expected_revision": item.revision, "client_request_id": "rbi-resume-command"})
    assert result.data["queued"] and result.data["action"] == "resume_original_batch"
    await core.workers.consolidate(partition, batch_id=batch["batch_id"])
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,step_ids,manifest FROM memory.consolidation_batches "
            "WHERE batch_id=%s", (batch["batch_id"],))).fetchone()
    assert current["status"] == "quarantined" and current["step_ids"] == batch["step_ids"]
    assert current["manifest"]["rbi_local_recovery"]["original_attempt_id"] == model.submissions[-1][0].attempt_id
    assert len(model.submissions) == 2

async def test_exhausted_unknown_remains_observable_for_late_original_success(core, ordinary_users):
    alice, _ = ordinary_users
    model, item, partition, batch = await two_unknown_batch(core, alice, "rbi-budget-late-success")
    lineage = batch["manifest"]["recovery"]["lineage_id"]
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE runtime.recovery_lineages SET max_attempts=attempts WHERE lineage_id=%s", (lineage,))
    await mature(core, batch)
    await core.workers.consolidate(partition)
    assert len(model.submissions) == 2
    original = model.submissions[-1][0]
    # The fixture makes the original Models attempt settle late, without a
    # distinct submission; Memory must consume it despite an exhausted budget.
    await waits.Model.submit(model, original, completion=None)
    model.submissions.pop()
    await model.ledger.finish(original.attempt_id, "succeeded")
    model.results[original.attempt_id] = replace(model.results[original.attempt_id],
        receipt=ledger_receipt(await model.ledger.get(original.attempt_id)))
    await waits.force_batch_due(core, batch["batch_id"])
    await core.workers.consolidate(partition)
    _, applied = await waits.batch_for_episode(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        attempts = await (await conn.execute("SELECT attempt_number,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number", (lineage,))).fetchall()
    assert applied["status"] == "applied" and len(model.submissions) == 2
    assert [(row["attempt_number"], row["status"]) for row in attempts] == [(1, "unknown"), (2, "succeeded")]


async def test_original_cancel_after_observation_blocks_successor(core, ordinary_users, monkeypatch):
    from tianshu.memory import consolidation_retry

    alice, _ = ordinary_users
    model, _, partition, batch = await two_unknown_batch(core, alice, "rbi-model-cancel-race")
    await mature(core, batch)
    observe = consolidation_retry.observe_original
    original_attempt = model.submissions[-1][0].attempt_id
    cancelled = False

    async def cancel_after_observation(models, request):
        nonlocal cancelled
        outcome = await observe(models, request)
        if request.attempt_id == original_attempt and not cancelled:
            cancelled = True
            # The returned receipt predates this real ledger cancellation.
            await model.ledger.request_cancel(original_attempt, reason="explicit_cancel")
        return outcome

    monkeypatch.setattr(consolidation_retry, "observe_original", cancel_after_observation)
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,error_code,step_ids FROM memory.consolidation_batches "
            "WHERE batch_id=%s", (batch["batch_id"],))).fetchone()
        lineage = await (await conn.execute("SELECT attempts,cancelled FROM runtime.recovery_lineages WHERE lineage_id=%s",
            (batch["manifest"]["recovery"]["lineage_id"],))).fetchone()
        originals = await (await conn.execute("SELECT attempt_id,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number", (batch["manifest"]["recovery"]["lineage_id"],))).fetchall()
    assert cancelled and current["step_ids"] == batch["step_ids"] and len(model.submissions) == 2
    assert current["error_code"] == "explicit_cancel" and lineage["cancelled"] and lineage["attempts"] == 2
    assert [row["status"] for row in originals] == ["unknown", "unknown"]
    assert [row["attempt_id"] for row in originals] == [request.attempt_id for request, _ in model.submissions]
    assert (await model.ledger.get(original_attempt))["cancellation_reason"] == "explicit_cancel"


async def test_missing_models_facts_never_authorize_retry(core, ordinary_users):
    class InMemoryOnly(waits.Model):
        async def submit(self, request, *, completion):
            if self.submissions:
                return await super().submit(request, completion=completion)
            self.submissions.append((request, completion))
            receipt = ModelReceipt(request.attempt_id, request.intent_id, "unknown", "unknown", "fixture", 1,
                "fixture", "fixture", "openai.chat", "fixture", error_code="transport_unknown",
                finished_at=datetime.now(UTC), data_generation=request.data_generation,
                request_digest=submission_digest(request))
            self.results[request.attempt_id] = ModelResult((), receipt)
            return receipt

    alice, _ = ordinary_users
    model = InMemoryOnly()
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, "rbi-no-original-facts")
    partition, _ = await waits.batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await waits.batch_for_episode(core, item.episode_id)
    await waits.force_batch_due(core, batch["batch_id"])
    await core.workers.consolidate(partition)
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,error_code,step_ids FROM memory.consolidation_batches "
            "WHERE batch_id=%s", (batch["batch_id"],))).fetchone()
        local = await (await conn.execute("SELECT status,recovery_policy_state FROM memory.consolidation_attempts "
            "WHERE batch_id=%s ORDER BY number DESC LIMIT 1", (batch["batch_id"],))).fetchone()
        ledger = await (await conn.execute("SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s",
            (model.submissions[0][0].attempt_id,))).fetchone()
    assert current["status"] == "quarantined" and len(current["step_ids"]) == 1 and len(model.submissions) == 1
    assert local["status"] == "observing" and local["recovery_policy_state"] == ledger["status"] == "reserved"
    assert current["error_code"] == "recovery_model_receipt_unconfirmed"
    later = await examples.episode(core, alice, "rbi-independent-after-unconfirmed")
    await core.workers.consolidate(partition)
    _, advanced = await waits.batch_for_episode(core, later.episode_id)
    assert advanced["status"] == "applied" and advanced["batch_id"] != batch["batch_id"]
    assert len(model.submissions) == 2


@pytest.mark.parametrize("change,reason", [("cancel", "recovery_model_cancelled"),
    ("settled", "recovery_model_outcome_changed"), ("digest", "recovery_model_receipt_mismatch")])
async def test_local_failure_requires_current_locked_models_facts(core, ordinary_users, change, reason):
    alice, _ = ordinary_users
    model, _, _, batch = await failed_local_batch(core, alice, "rbi-original-facts-" + change)
    original = model.submissions[-1][0]
    if change == "cancel":
        await model.ledger.request_cancel(original.attempt_id, reason="explicit_cancel")
    elif change == "settled":
        await model.ledger.finish(original.attempt_id, "succeeded")
    else:
        async with core.database.transaction() as conn:
            await conn.execute("UPDATE models.submissions SET request_digest='fixture-mismatch' WHERE attempt_id=%s",
                               (original.attempt_id,))
    result = await core.workers.recovery.resume_local_failure(batch["batch_id"], repair_ref="rbi-facts-fixture")
    assert result["reason"] == reason and not result["queued"] and len(model.submissions) == 2
    async with core.database.transaction(read_only=True) as conn:
        current = await (await conn.execute("SELECT status,error_code,step_ids FROM memory.consolidation_batches "
            "WHERE batch_id=%s", (batch["batch_id"],))).fetchone()
        local = await (await conn.execute("SELECT status,recovery_policy_state FROM memory.consolidation_attempts "
            "WHERE step_id=%s", (batch["step_ids"][-1],))).fetchone()
    assert current["status"] == "failed" and current["error_code"] == "recovery_original_not_failed"
    assert current["step_ids"] == batch["step_ids"] and local["status"] == "queued"
    assert local["recovery_policy_state"] == "unknown"


async def test_models_facts_lock_precedes_runtime_claim_until_reservation_commits(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    model, _, partition, batch = await two_unknown_batch(core, alice, "rbi-model-lock-order")
    await mature(core, batch)
    policy = core.memory.recovery_policy
    original_claim = policy.claim_lineage_due_on
    started = asyncio.Event()
    cancel_task = None

    async def claim_while_cancel_waits(*args, **kwargs):
        nonlocal cancel_task
        async def request_cancel():
            started.set()
            return await model.ledger.request_cancel(model.submissions[-1][0].attempt_id, reason="explicit_cancel")
        cancel_task = asyncio.create_task(request_cancel())
        await started.wait()
        await asyncio.sleep(0.05)
        assert not cancel_task.done()
        return await original_claim(*args, **kwargs)

    monkeypatch.setattr(policy, "claim_lineage_due_on", claim_while_cancel_waits)
    await core.workers.consolidate(partition)
    assert cancel_task is not None
    await asyncio.wait_for(cancel_task, timeout=5)
    assert len(model.submissions) == 3


async def test_only_confirmed_consumer_interruption_can_retry_pure_computation(core, ordinary_users):
    class InterruptedOnce(TwoUnknowns):
        async def submit(self, request, *, completion):
            await self.start_request(request)
            if not self.submissions:
                self.submissions.append((request, completion))
                await self.ledger.request_cancel(request.attempt_id, reason="consumer_interrupted")
                await self.ledger.finish(request.attempt_id, "cancelled", error_code="consumer_interrupted")
                receipt = ledger_receipt(await self.ledger.get(request.attempt_id))
                self.results[request.attempt_id] = ModelResult((), receipt)
                return receipt
            await waits.Model.submit(self, request, completion=completion)
            await self.ledger.finish(request.attempt_id, "succeeded")
            receipt = ledger_receipt(await self.ledger.get(request.attempt_id))
            self.results[request.attempt_id] = replace(self.results[request.attempt_id], receipt=receipt)
            return receipt

    alice, _ = ordinary_users
    model = InterruptedOnce(core.database)
    examples.configure(core, model)
    core.memory.bind_observability(statistics=None, recovery_policy=RecoveryPolicy())
    item = await examples.episode(core, alice, "rbi-consumer-interruption")
    partition, _ = await waits.batch_for_episode(core, item.episode_id)
    await core.workers.consolidate(partition)
    _, batch = await waits.batch_for_episode(core, item.episode_id)
    async with core.database.transaction(read_only=True) as conn:
        before = await (await conn.execute("SELECT a.status,a.recovery_policy_state,r.status AS ledger_status "
            "FROM memory.consolidation_attempts a JOIN runtime.recovery_attempts r ON r.attempt_id=a.model_attempt_id "
            "WHERE a.batch_id=%s", (batch["batch_id"],))).fetchone()
        credits = await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_not_sent WHERE attempt_id=%s",
            (model.submissions[0][0].attempt_id,))).fetchone()
    assert batch["status"] == "quarantined" and before == {
        "status": "cancelled", "recovery_policy_state": "failed", "ledger_status": "failed"}
    assert credits["n"] == 0
    await mature(core, batch)
    await core.workers.consolidate(partition)
    _, applied = await waits.batch_for_episode(core, item.episode_id)
    assert applied["status"] == "applied" and len(model.submissions) == 2
    assert (await model.ledger.get(model.submissions[0][0].attempt_id))["cancellation_reason"] == "consumer_interrupted"
