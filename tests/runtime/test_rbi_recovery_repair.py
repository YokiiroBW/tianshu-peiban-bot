"""Same-version repair is a fenced, one-use authorization on the original lineage."""

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files

import pytest
import pytest_asyncio

from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import FailureCategory, RecoveryPolicy, RecoveryWait


@pytest_asyncio.fixture
async def policy(database):
    await migrate(database, (Migration("rbi_recovery_repairs",
        files("tianshu.runtime").joinpath("rbi_recovery_repairs.sql").read_text(encoding="utf-8")),))
    return RecoveryPolicy()


def model_receipt(attempt_id, generation, *, status="failed"):
    return ModelReceipt(attempt_id=attempt_id, intent_id="frozen-intent", status=status,
        dispatch="submitted", route_id="route", route_revision=1, provider_id="provider",
        model="model", protocol="openai", quota_group="quota", finished_at=datetime.now(UTC),
        error_code="invalid_upstream_response", request_digest="a" * 64, data_generation=generation)


async def original(policy, conn, *, name="material", max_attempts=4, state="failed"):
    row = await policy.register_on(conn, domain="growth", subject_id=name, owner="growth",
        material_id=name, material_digest="frozen", dependency="shared", current_version="v1",
        max_attempts=max_attempts, recovery_kind="pure_computation")
    attempt_id = name + "-1"
    await policy.reserve_attempt_on(conn, row["lineage_id"], owner="growth", expected_version="v1",
        attempt_id=attempt_id, request_digest="runtime-request-identity", route="route", original_state="none")
    if state != "reserved":
        await policy.fail_attempt_on(conn, attempt_id, code="invalid_upstream_response",
            category=FailureCategory.UNKNOWN_EFFECT if state == "unknown" else FailureCategory.OTHER)
    receipt = model_receipt(attempt_id, row["data_generation"])
    # The Models guard reads real isolated ledger rows, never an accepting fake.
    await conn.execute(
        "INSERT INTO models.attempts(attempt_id,intent_id,principal_id,function,service_class,route_id,"
        "route_revision,provider_id,model,protocol,quota_group,request_digest,context_fingerprint,"
        "status,dispatch,finished_at,deadline_at,error_code,data_generation) "
        "VALUES(%s,%s,'fixture','growth','elastic','route',1,'provider','model','openai','quota',"
        "'physical-request-digest','fixture','failed','submitted',%s,now()+interval '1 hour',%s,%s)",
        (receipt.attempt_id, receipt.intent_id, receipt.finished_at, receipt.error_code, receipt.data_generation))
    await conn.execute("INSERT INTO models.submissions(attempt_id,request_digest,request,streaming) "
                       "VALUES(%s,%s,'{}'::jsonb,false)", (receipt.attempt_id, receipt.request_digest))
    return row, receipt


def arguments(row, receipt, *, repair_ref="parser-fix:revision-1"):
    return dict(lineage_id=row["lineage_id"], owner="growth", expected_version="v1",
                original_attempt_id=receipt.attempt_id, model_receipt=receipt, repair_ref=repair_ref)


async def test_repair_wake_is_idempotent_fenced_and_respects_cooldown(database, policy):
    async with database.transaction() as conn:
        row, receipt = await original(policy, conn)
        identity = row["lineage_id"]
        await conn.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()+interval '4 minutes',"
                           "probe_token='other-probe',probe_expires_at=now()+interval '5 minutes' "
                           "WHERE data_generation=%s AND dependency='shared'", (row["data_generation"],))
        before = await (await conn.execute("SELECT * FROM runtime.recovery_dependencies WHERE dependency='shared'")).fetchone()
        args = arguments(row, receipt)
        result = await policy.resume_repaired_on(conn, **args)
        assert result["eligible"] and result["reason"] is None
        assert isinstance(result["retry_at"], datetime) and result["retry_at"].utcoffset() is not None
        assert result["retry_at"] >= before["cooldown_until"] and result["retry_at"] >= before["probe_expires_at"]
        again = await policy.resume_repaired_on(conn, **args)
        assert again == result
        saved = await (await conn.execute("SELECT attempts,max_attempts,due_at,failure_category FROM "
                                          "runtime.recovery_lineages WHERE lineage_id=%s", (identity,))).fetchone()
        assert saved["attempts"] == 1 and saved["max_attempts"] == 4 and saved["failure_category"] == "other"
        assert saved["due_at"] is not None and saved["due_at"] <= result["retry_at"]
        assert await (await conn.execute("SELECT * FROM runtime.recovery_dependencies WHERE dependency='shared'")).fetchone() == before
        assert (await (await conn.execute("SELECT count(*) AS n FROM runtime.recovery_repairs WHERE lineage_id=%s",
                                          (identity,))).fetchone())["n"] == 1
        attempt = await (await conn.execute("SELECT status,error_code FROM runtime.recovery_attempts WHERE attempt_id=%s",
                                            (receipt.attempt_id,))).fetchone()
        assert attempt == {"status": "failed", "error_code": "invalid_upstream_response"}
        with pytest.raises(AppError, match="recovery_repair_ref_conflict"):
            await policy.resume_repaired_on(conn, **dict(args, model_receipt=replace(receipt, request_digest="b" * 64)))


async def test_repair_does_not_move_existing_due_or_recreate_consumed_reference(database, policy):
    async with database.transaction() as conn:
        row, receipt = await original(policy, conn)
        identity = row["lineage_id"]
        existing = await (await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 minute' "
            "WHERE lineage_id=%s RETURNING due_at", (identity,))).fetchone()
        args = arguments(row, receipt)
        result = await policy.resume_repaired_on(conn, **args)
        assert result["eligible"] and result["retry_at"] == existing["due_at"]
        await conn.execute("UPDATE runtime.recovery_lineages SET due_at=NULL WHERE lineage_id=%s", (identity,))
        consumed = await policy.resume_repaired_on(conn, **args)
        assert not consumed["eligible"] and consumed["reason"] == "recovery_repair_already_consumed"
        assert (await (await conn.execute("SELECT due_at FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                          (identity,))).fetchone())["due_at"] is None


@pytest.mark.parametrize("blocked,reason", [("paused", "recovery_paused"), ("cancelled", "recovery_cancelled"),
                                              ("source_withdrawn", "recovery_source_withdrawn")])
async def test_invalid_lineage_state_cannot_issue_repair(database, policy, blocked, reason):
    async with database.transaction() as conn:
        row, receipt = await original(policy, conn)
        await policy.update_version_on(conn, row["lineage_id"], owner="growth", expected_version="v1",
                                       current_version="v1", **{blocked: True})
        result = await policy.resume_repaired_on(conn, **arguments(row, receipt))
        assert not result["eligible"] and result["reason"] == reason and result["retry_at"] is None
        assert not await (await conn.execute("SELECT 1 FROM runtime.recovery_repairs")).fetchone()


async def test_receipt_identity_and_original_status_are_checked(database, policy):
    async with database.transaction() as conn:
        row, receipt = await original(policy, conn, state="reserved")
        args = arguments(row, receipt)
        result = await policy.resume_repaired_on(conn, **args)
        assert not result["eligible"] and result["reason"] == "recovery_original_in_progress"
        for invalid in (replace(receipt, status="unknown"), replace(receipt, status="running")):
            assert (await policy.resume_repaired_on(conn, **dict(args, model_receipt=invalid)))["reason"] == "recovery_model_not_failed"
        for invalid in (replace(receipt, finished_at=None), replace(receipt, request_digest=None)):
            assert (await policy.resume_repaired_on(conn, **dict(args, model_receipt=invalid)))["reason"] == "recovery_model_receipt_unconfirmed"
        with pytest.raises(AppError, match="recovery_model_cancelled"):
            await policy.resume_repaired_on(conn, **dict(args, model_receipt=replace(receipt,
                cancellation_reason="explicit_cancel")))
        with pytest.raises(AppError, match="recovery_model_receipt_mismatch"):
            await policy.resume_repaired_on(conn, **dict(args, model_receipt=replace(receipt, attempt_id="other")))
        with pytest.raises(AppError, match="job_generation_expired"):
            await policy.resume_repaired_on(conn, **dict(args, model_receipt=replace(receipt, data_generation="other")))
        with pytest.raises(AppError, match="recovery_owner_mismatch"):
            await policy.resume_repaired_on(conn, **dict(args, owner="other"))
        with pytest.raises(AppError, match="recovery_version_changed"):
            await policy.resume_repaired_on(conn, **dict(args, expected_version="v2"))
        await policy.fail_attempt_on(conn, receipt.attempt_id, code="transport_unknown", category=FailureCategory.UNKNOWN_EFFECT)
        assert (await policy.resume_repaired_on(conn, **args))["reason"] == "recovery_original_unknown"
        assert not await (await conn.execute("SELECT 1 FROM runtime.recovery_repairs")).fetchone()


async def test_budget_live_claim_and_successor_cannot_be_bypassed(database, policy):
    async with database.transaction() as conn:
        exhausted, receipt = await original(policy, conn, name="exhausted", max_attempts=1)
        result = await policy.resume_repaired_on(conn, **arguments(exhausted, receipt))
        assert not result["eligible"] and result["reason"] == "recovery_budget_exhausted"
        row, receipt = await original(policy, conn, name="successor")
        args = arguments(row, receipt)
        await conn.execute("UPDATE runtime.recovery_lineages SET claim_token='held',claim_expires_at=now()+interval '1 minute' "
                           "WHERE lineage_id=%s", (row["lineage_id"],))
        live = await policy.resume_repaired_on(conn, **args)
        assert not live["eligible"] and live["reason"] == "recovery_claim_in_progress" and live["retry_at"] is not None
        await conn.execute("UPDATE runtime.recovery_lineages SET claim_expires_at=now()-interval '1 second' "
                           "WHERE lineage_id=%s", (row["lineage_id"],))
        assert (await policy.resume_repaired_on(conn, **args))["eligible"]
        claim = await policy.claim_lineage_due_on(conn, row["lineage_id"], owner="growth")
        await policy.reserve_attempt_on(conn, row["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="successor-2", request_digest="successor", route="route", original_state="failed",
            claim_token=claim["claim_token"])
        late = await policy.resume_repaired_on(conn, **args)
        assert not late["eligible"] and late["reason"] == "recovery_attempt_superseded"

async def test_429_keeps_computation_budget_and_shared_cooldown_across_domains_and_version(database, policy):
    async with database.transaction() as conn:
        row = await policy.register_on(conn, domain="memory", subject_id="one", owner="memory",
            material_id="same-business", material_digest="same-input", dependency="shared", current_version="v1",
            max_attempts=4, recovery_kind="pure_computation")
        await policy.reserve_attempt_on(conn, row["lineage_id"], owner="memory", expected_version="v1",
            attempt_id="rate-limit-1", request_digest="frozen", route="route", original_state="none")
        due = await policy.fail_attempt_on(conn, "rate-limit-1", code="upstream_http_429",
            category=FailureCategory.TRANSPORT, retry_after=120, random_value=0)
        received = replace(model_receipt("rate-limit-1", row["data_generation"]), error_code="upstream_http_429")
        recorded = await policy.note_not_sent_on(conn, "rate-limit-1", owner="memory", expected_version="v1",
                                                model_receipt=received)
        assert recorded["connection_attempts"] == 0 and recorded["computation_attempts"] == 1
        before = await (await conn.execute("SELECT cooldown_until FROM runtime.recovery_dependencies "
            "WHERE data_generation=%s AND dependency='shared'", (row["data_generation"],))).fetchone()
        assert before["cooldown_until"] >= due
        same = await policy.register_on(conn, domain="memory", subject_id="one", owner="memory",
            material_id="same-business", material_digest="same-input", dependency="shared", current_version="v1",
            max_attempts=4, recovery_kind="pure_computation")
        assert same["lineage_id"] == row["lineage_id"] and same["attempts"] == 1
        await policy.update_version_on(conn, row["lineage_id"], owner="memory", expected_version="v1", current_version="v2")
        budget = await policy.computation_budget_on(conn, row["lineage_id"], owner="memory", expected_version="v2")
        assert budget["total_attempts"] == 1 and budget["computation_attempts"] == 1
        another = await policy.register_on(conn, domain="life", subject_id="two", owner="life",
            material_id="new-intent", material_digest="new-input", dependency="shared", current_version="v1",
            max_attempts=4, recovery_kind="pure_computation")
        waiting = await policy.inspect_wait_on(conn, another["lineage_id"], owner="life", expected_version="v1")
        assert isinstance(waiting, RecoveryWait) and waiting.reason == "recovery_dependency_cooling"
        with pytest.raises(RecoveryWait, match="recovery_dependency_cooling"):
            await policy.reserve_attempt_on(conn, another["lineage_id"], owner="life", expected_version="v1",
                attempt_id="new-intent-1", request_digest="new", route="other", original_state="none")
        assert await (await conn.execute("SELECT cooldown_until FROM runtime.recovery_dependencies "
            "WHERE data_generation=%s AND dependency='shared'", (row["data_generation"],))).fetchone() == before
        await conn.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second' "
                           "WHERE data_generation=%s AND dependency='shared'", (row["data_generation"],))
        probe = await policy.reserve_attempt_on(conn, another["lineage_id"], owner="life", expected_version="v1",
            attempt_id="life-probe", request_digest="new", route="other", original_state="none")
        assert probe.probe_token == "life-probe"
        third = await policy.register_on(conn, domain="growth", subject_id="three", owner="growth",
            material_id="third-intent", material_digest="third-input", dependency="shared", current_version="v1",
            max_attempts=4, recovery_kind="pure_computation")
        with pytest.raises(RecoveryWait, match="recovery_dependency_probe_in_progress"):
            await policy.reserve_attempt_on(conn, third["lineage_id"], owner="growth", expected_version="v1",
                attempt_id="growth-probe", request_digest="third", route="other", original_state="none")
        await conn.execute("UPDATE runtime.recovery_dependencies SET probe_expires_at=now()-interval '1 second' "
                           "WHERE data_generation=%s AND dependency='shared'", (row["data_generation"],))
        next_probe = await policy.reserve_attempt_on(conn, third["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="growth-probe", request_digest="third", route="other", original_state="none")
        assert next_probe.probe_token == "growth-probe"
        winner = await (await conn.execute("SELECT cooldown_until,probe_token,probe_expires_at "
            "FROM runtime.recovery_dependencies WHERE data_generation=%s AND dependency='shared'",
            (row["data_generation"],))).fetchone()
        await policy.fail_attempt_on(conn, "life-probe", code="upstream_http_429",
            category=FailureCategory.TRANSPORT, retry_after=3600, random_value=0)
        assert await (await conn.execute("SELECT cooldown_until,probe_token,probe_expires_at "
            "FROM runtime.recovery_dependencies WHERE data_generation=%s AND dependency='shared'",
            (row["data_generation"],))).fetchone() == winner


async def test_repair_rejects_installation_generation_change(database, policy):
    async with database.transaction() as conn:
        row, receipt = await original(policy, conn)
        await conn.execute("UPDATE runtime.installation_generation SET id='new-generation' WHERE singleton=true")
        with pytest.raises(AppError, match="job_generation_expired"):
            await policy.resume_repaired_on(conn, **arguments(row, receipt))
        assert not await (await conn.execute("SELECT 1 FROM runtime.recovery_repairs")).fetchone()

async def test_consumer_interruption_uses_bounded_pure_retry_and_raw_cancelled_receipt(database, policy):
    from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
    from tianshu.runtime.recovery_policy import classify_failure

    assert classify_failure("consumer_interrupted") == FailureCategory.INTERRUPTED
    assert classify_failure("cancelled_unknown") == FailureCategory.STATE_CHANGED
    async with database.transaction() as conn:
        row, receipt = await original(policy, conn, name="interrupted", state="reserved")
        receipt = replace(receipt, status="cancelled", dispatch="not_started", error_code="consumer_interrupted",
                          cancellation_reason="consumer_interrupted")
        await conn.execute("UPDATE models.attempts SET status='cancelled',dispatch='not_started',"
                           "error_code='consumer_interrupted' WHERE attempt_id=%s", (receipt.attempt_id,))
        await conn.execute("INSERT INTO models.cancellations(attempt_id,reason) VALUES(%s,'consumer_interrupted')",
                           (receipt.attempt_id,))
        due = await policy.fail_attempt_on(conn, receipt.attempt_id, code="consumer_interrupted",
                                          category=classify_failure("consumer_interrupted"), random_value=0)
        assert due is not None
        dep = await (await conn.execute("SELECT cooldown_until,probe_token FROM runtime.recovery_dependencies "
                                        "WHERE dependency='shared'")).fetchone()
        assert dep == {"cooldown_until": None, "probe_token": None}
        budget = await policy.computation_budget_on(conn, row["lineage_id"], owner="growth", expected_version="v1")
        assert (budget["total_attempts"], budget["computation_attempts"], budget["connection_attempts"]) == (1, 1, 0)
        noted = await policy.note_not_sent_on(conn, receipt.attempt_id, owner="growth", expected_version="v1",
                                             model_receipt=receipt)
        assert noted["can_retry"] and noted["retry_at"] == due and noted["reason"] == "not_confirmed_not_sent"
        assert (noted["computation_attempts"], noted["connection_attempts"]) == (1, 0)
    async with database.transaction() as conn:
        await conn.execute("SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")
        await lock_recovery_facts_on(conn, receipt.attempt_id)
        claim = await policy.claim_lineage_due_on(conn, row["lineage_id"], owner="growth")
        successor = await policy.reserve_attempt_on(conn, row["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="interrupted-2", request_digest="new-independent-attempt", route="route",
            original_state="failed", claim_token=claim["claim_token"], model_receipt=receipt)
        assert successor.number == 2
        model = await (await conn.execute("SELECT status,dispatch,error_code FROM models.attempts WHERE attempt_id=%s",
                                          (receipt.attempt_id,))).fetchone()
        assert model == {"status": "cancelled", "dispatch": "not_started", "error_code": "consumer_interrupted"}


async def test_explicit_cancel_before_successor_reservation_wins(database, policy):
    from tianshu.models.ledger import AttemptLedger
    from tianshu.models.rbi_outcome_guard import lock_recovery_facts_on
    from tianshu.runtime.recovery_policy import classify_failure

    async with database.transaction() as conn:
        row, receipt = await original(policy, conn, name="cancel-wins", state="reserved")
        receipt = replace(receipt, status="cancelled", dispatch="not_started", error_code="consumer_interrupted",
                          cancellation_reason="consumer_interrupted")
        await conn.execute("UPDATE models.attempts SET status='cancelled',dispatch='not_started',"
                           "error_code='consumer_interrupted' WHERE attempt_id=%s", (receipt.attempt_id,))
        await conn.execute("INSERT INTO models.cancellations(attempt_id,reason) VALUES(%s,'consumer_interrupted')",
                           (receipt.attempt_id,))
        await policy.fail_attempt_on(conn, receipt.attempt_id, code="consumer_interrupted",
                                     category=classify_failure("consumer_interrupted"), random_value=0)
    await AttemptLedger(database, "fixture").request_cancel(receipt.attempt_id, reason="explicit_cancel")
    with pytest.raises(AppError, match="recovery_model_cancelled"):
        async with database.transaction() as conn:
            await conn.execute("SELECT id FROM runtime.installation_generation WHERE singleton=true FOR SHARE")
            await lock_recovery_facts_on(conn, receipt.attempt_id)
            claim = await policy.claim_lineage_due_on(conn, row["lineage_id"], owner="growth")
            await policy.reserve_attempt_on(conn, row["lineage_id"], owner="growth", expected_version="v1",
                attempt_id="cancel-wins-2", request_digest="next", route="route", original_state="failed",
                claim_token=claim["claim_token"], model_receipt=receipt)
    async with database.transaction(read_only=True) as conn:
        saved = await (await conn.execute("SELECT attempts FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                           (row["lineage_id"],))).fetchone()
        assert saved["attempts"] == 1
        assert not await (await conn.execute("SELECT 1 FROM runtime.recovery_attempts WHERE attempt_id='cancel-wins-2'")).fetchone()

async def test_repair_rejects_changed_models_outcome_and_explicit_cancel(database, policy):
    async with database.transaction() as conn:
        row, receipt = await original(policy, conn, name="changed-facts")
        args = arguments(row, receipt)
        await conn.execute("UPDATE models.attempts SET status='unknown',dispatch='unknown' WHERE attempt_id=%s",
                           (receipt.attempt_id,))
        with pytest.raises(AppError, match="recovery_model_outcome_changed"):
            await policy.resume_repaired_on(conn, **args)
        await conn.execute("UPDATE models.attempts SET status='failed',dispatch='submitted' WHERE attempt_id=%s",
                           (receipt.attempt_id,))
        await conn.execute("INSERT INTO models.cancellations(attempt_id,reason) VALUES(%s,'explicit_cancel')",
                           (receipt.attempt_id,))
        with pytest.raises(AppError, match="recovery_model_cancelled"):
            await policy.resume_repaired_on(conn, **args)
        assert not await (await conn.execute("SELECT 1 FROM runtime.recovery_repairs WHERE lineage_id=%s",
                                            (row["lineage_id"],))).fetchone()
        assert (await (await conn.execute("SELECT due_at FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                          (row["lineage_id"],))).fetchone())["due_at"] is None


async def test_repair_and_late_cancel_are_linearized_by_models_admission_lock(database, policy):
    import asyncio
    from tianshu.models.ledger import AttemptLedger

    async with database.transaction() as conn:
        row, receipt = await original(policy, conn, name="late-cancel")
    started = asyncio.Event()

    async def cancel_original():
        started.set()
        return await AttemptLedger(database, "fixture").request_cancel(receipt.attempt_id, reason="explicit_cancel")

    cancellation = None
    try:
        async with database.transaction() as conn:
            authorized = await policy.resume_repaired_on(conn, **arguments(row, receipt))
            assert authorized["eligible"]
            cancellation = asyncio.create_task(cancel_original())
            await started.wait()
            await asyncio.sleep(0.05)
            assert not cancellation.done()
        await asyncio.wait_for(cancellation, timeout=2)
        async with database.transaction() as conn:
            saved = await (await conn.execute("SELECT due_at,attempts FROM runtime.recovery_lineages "
                                               "WHERE lineage_id=%s", (row["lineage_id"],))).fetchone()
            assert saved["due_at"] is not None and saved["attempts"] == 1
            with pytest.raises(AppError, match="recovery_model_cancelled"):
                await policy.resume_repaired_on(conn, **arguments(row, receipt))
    finally:
        if cancellation is not None and not cancellation.done():
            cancellation.cancel()
            await asyncio.gather(cancellation, return_exceptions=True)
