"""Shared recovery credits require original Models evidence and preserve fencing."""

from dataclasses import replace
from datetime import UTC, datetime
from importlib.resources import files

import pytest
import pytest_asyncio

from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.rbh_evidence import confirmed_not_sent
from tianshu.runtime.recovery_policy import FailureCategory, RecoveryPolicy, RecoveryWait


@pytest_asyncio.fixture
async def rbh_policy(database):
    await migrate(database, (Migration("rbh_not_sent",
        files("tianshu.runtime").joinpath("rbh_not_sent.sql").read_text(encoding="utf-8")),))
    return RecoveryPolicy()


def receipt(attempt_id, generation, *, kind="legacy", error_class="ClientConnectorError", phase="connect"):
    points = {name: None for name in ("connect_end", "headers_sent", "body_sent", "response_headers",
                                      "first_body_byte", "first_valid_output", "read_complete")}
    points["connect_start"] = 1.0
    diagnostic = {"category": "not_sent", "part_count": 0, "part_kinds": [], "text_bytes": 0,
                  "native_completed": False}
    if kind != "legacy":
        diagnostic["transport"] = {"schema_version": "rbh.transport.v1", "phase": phase,
            "error_class": error_class, "os_errno": None, "tls_reason": None, "host": "example.test",
            "port": 443, "headers_sent": False, "body_sent": False, "evidence": "confirmed_not_sent"}
    return ModelReceipt(attempt_id=attempt_id, intent_id="intent", status="failed", dispatch="not_started",
        route_id="route", route_revision=1, provider_id="provider", model="model", protocol="openai",
        quota_group="quota", finished_at=datetime.now(UTC), error_code="transport_not_sent",
        data_generation=generation, http_timing={"connection_reused": False, "points_ms": points},
        diagnostic=diagnostic)


async def register(policy, conn, name, *, max_attempts=4):
    return await policy.register_on(conn, domain="memory", subject_id=name, owner="memory", material_id=name,
        material_digest=name, dependency="shared-provider", current_version="v1", max_attempts=max_attempts,
        recovery_kind="pure_computation")


async def make_due(conn, identity):
    await conn.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s", (identity,))
    await conn.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second' "
                       "WHERE dependency='shared-provider'")


async def attempt_failure(policy, conn, row, number, *, evidence=None, claim=None):
    attempt_id = f"{row['subject_id']}-{number}"
    reservation = await policy.reserve_attempt_on(conn, row["lineage_id"], owner="memory", expected_version="v1",
        attempt_id=attempt_id, request_digest=attempt_id, route="route",
        original_state="none" if number == 1 else "failed", claim_token=claim)
    await policy.fail_attempt_on(conn, attempt_id, code="transport_not_sent", category=FailureCategory.TRANSPORT,
                                 random_value=0, retry_after=10)
    result = await policy.note_not_sent_on(conn, attempt_id, owner="memory", expected_version="v1",
        model_receipt=evidence or receipt(attempt_id, row["data_generation"]))
    return reservation, result


async def test_separate_budget_idempotency_and_all_claims_stop_at_sixteen(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "bounded")
        identity = row["lineage_id"]
        claim = None
        for number in range(1, 17):
            evidence = receipt(f"bounded-{number}", row["data_generation"])
            reservation, result = await attempt_failure(rbh_policy, conn, row, number, claim=claim, evidence=evidence)
            assert reservation.number == number
            assert (result["total_attempts"], result["computation_attempts"], result["connection_attempts"]) == (number, 0, number)
            if number == 4:
                duplicate = await rbh_policy.note_not_sent_on(conn, reservation.attempt_id, owner="memory",
                    expected_version="v1", model_receipt=evidence)
                assert duplicate == result
            if number < 16:
                assert result["can_retry"]
                await make_due(conn, identity)
                if number % 2:
                    claimed = await rbh_policy.claim_lineage_due_on(conn, identity, owner="memory")
                else:
                    claimed = (await rbh_policy.claim_due_on(conn, owner="memory"))[0]
                claim = claimed["claim_token"]
        assert not result["can_retry"] and result["reason"] == "recovery_connection_budget_exhausted"
        await make_due(conn, identity)  # A stale queue entry cannot bypass the budget.
        assert await rbh_policy.claim_lineage_due_on(conn, identity, owner="memory") is None
        assert await rbh_policy.claim_due_on(conn, owner="memory") == ()
        with pytest.raises(AppError, match="recovery_connection_budget_exhausted"):
            await rbh_policy.inspect_wait_on(conn, identity, owner="memory", expected_version="v1")
        with pytest.raises(AppError, match="recovery_connection_budget_exhausted"):
            await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
                attempt_id="bounded-17", request_digest="next", route="route", original_state="failed")


@pytest.mark.parametrize("changes", [
    {"status": "unknown"}, {"dispatch": "submitted"}, {"error_code": "timeout_unknown"},
    {"finished_at": None}, {"first_output_ms": 0.0}, {"provider_handle": "provider-output"},
    {"cancellation_reason": "explicit_cancel"},
    {"http_timing": None},
    {"http_timing": {"connection_reused": False, "points_ms": {"connect_start": 1}}},
    {"diagnostic": {"category": "not_sent", "part_count": 1}},
])
def test_incomplete_legacy_receipts_never_grant_credit(changes):
    assert confirmed_not_sent(replace(receipt("id", "generation"), **changes)) is None


def test_new_evidence_requires_exact_connector_phase_and_consistent_timing():
    base = receipt("id", "generation", kind="new")
    assert confirmed_not_sent(base) == "rbh.transport.v1"
    transport = base.diagnostic["transport"]
    for changes in ({"phase": "close"}, {"phase": "tls"}, {"error_class": "ClientProxyConnectionError"},
                    {"error_class": []}, {"headers_sent": None}, {"body_sent": True},
                    {"evidence": "sent_or_unknown"}, {"host": "user:password@host/path"},
                    {"port": True}, {"tls_reason": "private error body"}):
        modified = dict(base.diagnostic, transport=dict(transport, **changes))
        assert confirmed_not_sent(replace(base, diagnostic=modified)) is None
    points = dict(base.http_timing["points_ms"], headers_sent=0.0)
    assert confirmed_not_sent(replace(base, http_timing={"connection_reused": False, "points_ms": points})) is None


async def test_missing_evidence_identity_fences_and_unknown_are_noops(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "fences")
        identity = row["lineage_id"]
        attempt = await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
            attempt_id="fences-1", request_digest="req", route="route", original_state="none")
        original = receipt(attempt.attempt_id, row["data_generation"])
        while_reserved = await rbh_policy.note_not_sent_on(conn, attempt.attempt_id, owner="memory",
            expected_version="v1", model_receipt=original)
        assert while_reserved["reason"] == "not_confirmed_not_sent" and while_reserved["connection_attempts"] == 0
        await rbh_policy.fail_attempt_on(conn, attempt.attempt_id, code="transport_unknown", category=FailureCategory.UNKNOWN_EFFECT)
        unknown = await rbh_policy.note_not_sent_on(conn, attempt.attempt_id, owner="memory", expected_version="v1",
                                                    model_receipt=replace(original, status="unknown"))
        assert unknown["computation_attempts"] == 1 and unknown["connection_attempts"] == 0
        for wrong, message in ((replace(original, attempt_id="different"), "recovery_model_receipt_mismatch"),
                               (replace(original, data_generation="old"), "job_generation_expired")):
            with pytest.raises(AppError, match=message):
                await rbh_policy.note_not_sent_on(conn, attempt.attempt_id, owner="memory", expected_version="v1", model_receipt=wrong)
        with pytest.raises(AppError, match="recovery_owner_mismatch"):
            await rbh_policy.note_not_sent_on(conn, attempt.attempt_id, owner="different", expected_version="v1", model_receipt=original)
        with pytest.raises(AppError, match="recovery_version_changed"):
            await rbh_policy.note_not_sent_on(conn, attempt.attempt_id, owner="memory", expected_version="v2", model_receipt=original)
        with pytest.raises(AppError, match="recovery_attempt_not_active"):
            await rbh_policy.note_not_sent_on(conn, "missing", owner="memory", expected_version="v1", model_receipt=original)


@pytest.mark.parametrize("category", [FailureCategory.OTHER, FailureCategory.INVALID_OUTPUT,
                                       FailureCategory.MISSING_INPUT, FailureCategory.STATE_CHANGED])
async def test_not_sent_credit_never_retries_a_nonretryable_failure_category(database, rbh_policy, category):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "permanent")
        identity = row["lineage_id"]
        await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
            attempt_id="permanent-1", request_digest="req", route="route", original_state="none")
        await rbh_policy.fail_attempt_on(conn, "permanent-1", code="transport_not_sent", category=category)
        original = receipt("permanent-1", row["data_generation"], kind="new",
                           error_class="ClientConnectorCertificateError", phase="tls")
        result = await rbh_policy.note_not_sent_on(conn, "permanent-1", owner="memory", expected_version="v1",
                                                  model_receipt=original)
        assert result["connection_attempts"] == 1 and result["computation_attempts"] == 0
        assert result["can_retry"] and result["retry_at"] is None  # The remaining budget is not a retry authorization.
        saved = await (await conn.execute("SELECT due_at,failure_category FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                          (identity,))).fetchone()
        assert saved == {"due_at": None, "failure_category": category.value}
        assert await rbh_policy.claim_lineage_due_on(conn, identity, owner="memory") is None
        assert await rbh_policy.claim_due_on(conn, owner="memory") == ()


async def test_late_history_never_moves_newer_attempt_or_shared_probe(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "late")
        identity = row["lineage_id"]
        first = await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
            attempt_id="late-1", request_digest="one", route="route", original_state="none")
        await rbh_policy.fail_attempt_on(conn, first.attempt_id, code="transport_not_sent", category=FailureCategory.TRANSPORT,
                                         random_value=0)
        await make_due(conn, identity)
        claim = await rbh_policy.claim_lineage_due_on(conn, identity, owner="memory")
        second = await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
            attempt_id="late-2", request_digest="two", route="route", original_state="failed", claim_token=claim["claim_token"])
        await rbh_policy.fail_attempt_on(conn, second.attempt_id, code="transport_unknown", category=FailureCategory.UNKNOWN_EFFECT)
        before = await (await conn.execute("SELECT due_at,claim_token,claim_expires_at FROM runtime.recovery_lineages WHERE lineage_id=%s", (identity,))).fetchone()
        probe_before = await (await conn.execute("SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies WHERE dependency='shared-provider'")).fetchone()
        old = receipt("late-1", row["data_generation"], kind="new", error_class="ClientConnectorCertificateError", phase="tls")
        result = await rbh_policy.note_not_sent_on(conn, first.attempt_id, owner="memory", expected_version="v1", model_receipt=old)
        after = await (await conn.execute("SELECT due_at,claim_token,claim_expires_at FROM runtime.recovery_lineages WHERE lineage_id=%s", (identity,))).fetchone()
        probe_after = await (await conn.execute("SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies WHERE dependency='shared-provider'")).fetchone()
        assert before == after and probe_before == probe_after
        assert result["connection_attempts"] == 1 and result["computation_attempts"] == 1 and result["can_retry"]

async def test_sent_or_evidence_missing_failure_remains_a_computation(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "sent", max_attempts=1)
        identity = row["lineage_id"]
        await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
            attempt_id="sent-1", request_digest="req", route="route", original_state="none")
        await rbh_policy.fail_attempt_on(conn, "sent-1", code="transport_unknown", category=FailureCategory.TRANSPORT)
        sent = replace(receipt("sent-1", row["data_generation"]), dispatch="submitted")
        result = await rbh_policy.note_not_sent_on(conn, "sent-1", owner="memory", expected_version="v1", model_receipt=sent)
        assert result["computation_attempts"] == 1 and result["connection_attempts"] == 0
        assert not result["can_retry"] and result["reason"] == "recovery_budget_exhausted"
        assert await rbh_policy.claim_lineage_due_on(conn, identity, owner="memory") is None


async def test_shared_probe_takeover_and_late_classification_keep_current_owner(database, rbh_policy):
    async with database.transaction() as conn:
        old = await register(rbh_policy, conn, "probe-old")
        other = await register(rbh_policy, conn, "probe-other")
        await conn.execute("INSERT INTO runtime.recovery_dependencies(data_generation,dependency,cooldown_until) "
                           "VALUES(%s,'shared-provider',now()-interval '1 second')", (old["data_generation"],))
        first = await rbh_policy.reserve_attempt_on(conn, old["lineage_id"], owner="memory", expected_version="v1",
            attempt_id="probe-old-1", request_digest="one", route="route", original_state="none")
        assert first.probe_token == "probe-old-1"
        with pytest.raises(RecoveryWait, match="recovery_dependency_probe_in_progress"):
            await rbh_policy.reserve_attempt_on(conn, other["lineage_id"], owner="memory", expected_version="v1",
                attempt_id="probe-other-1", request_digest="two", route="route", original_state="none")
        await conn.execute("UPDATE runtime.recovery_dependencies SET probe_expires_at=now()-interval '1 second' "
                           "WHERE dependency='shared-provider'")
        second = await rbh_policy.reserve_attempt_on(conn, other["lineage_id"], owner="memory", expected_version="v1",
            attempt_id="probe-other-1", request_digest="two", route="route", original_state="none")
        assert second.probe_token == "probe-other-1"
        before = await (await conn.execute("SELECT cooldown_until,probe_token,probe_expires_at "
            "FROM runtime.recovery_dependencies WHERE dependency='shared-provider'")).fetchone()
        await rbh_policy.fail_attempt_on(conn, first.attempt_id, code="transport_not_sent", category=FailureCategory.TRANSPORT)
        await rbh_policy.note_not_sent_on(conn, first.attempt_id, owner="memory", expected_version="v1",
            model_receipt=receipt(first.attempt_id, old["data_generation"]))
        after = await (await conn.execute("SELECT cooldown_until,probe_token,probe_expires_at "
            "FROM runtime.recovery_dependencies WHERE dependency='shared-provider'")).fetchone()
        assert after == before

async def test_four_historical_failures_restore_only_a_budgeted_original_lineage(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "history")
        identity = row["lineage_id"]
        receipts = []
        claim = None
        for number in range(1, 5):
            attempt_id = f"history-{number}"
            await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
                attempt_id=attempt_id, request_digest=attempt_id, route="route",
                original_state="none" if number == 1 else "failed", claim_token=claim)
            await rbh_policy.fail_attempt_on(conn, attempt_id, code="transport_not_sent", category=FailureCategory.TRANSPORT,
                                            random_value=0, retry_after=30)
            receipts.append(receipt(attempt_id, row["data_generation"]))
            if number < 4:
                await make_due(conn, identity)
                claim = (await rbh_policy.claim_lineage_due_on(conn, identity, owner="memory"))["claim_token"]
        before = await rbh_policy.computation_budget_on(conn, identity, owner="memory", expected_version="v1")
        assert not before["can_retry"] and before["computation_attempts"] == 4 and before["retry_at"] is None
        for index, item in enumerate(receipts):
            result = await rbh_policy.note_not_sent_on(conn, item.attempt_id, owner="memory", expected_version="v1", model_receipt=item)
            if index < 3:
                assert result["retry_at"] is None
        assert result["total_attempts"] == 4 and result["computation_attempts"] == 0
        assert result["connection_attempts"] == 4 and result["can_retry"] and result["retry_at"] is not None
        attempts = await (await conn.execute("SELECT attempt_number,status FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number", (identity,))).fetchall()
        assert [(item["attempt_number"], item["status"]) for item in attempts] == [(i, "failed") for i in range(1, 5)]

async def test_history_credit_can_restore_latest_definitive_failure_without_reclassifying_it(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "mixed")
        identity = row["lineage_id"]
        claim = None
        for number in range(1, 5):
            attempt_id = f"mixed-{number}"
            await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
                attempt_id=attempt_id, request_digest=attempt_id, route="route",
                original_state="none" if number == 1 else "failed", claim_token=claim)
            await rbh_policy.fail_attempt_on(conn, attempt_id,
                code="http_503" if number == 4 else "transport_not_sent", category=FailureCategory.TRANSPORT,
                random_value=0, retry_after=30)
            if number < 4:
                await make_due(conn, identity)
                claim = (await rbh_policy.claim_lineage_due_on(conn, identity, owner="memory"))["claim_token"]
        latest = replace(receipt("mixed-4", row["data_generation"]), dispatch="submitted", error_code="http_503")
        before = await rbh_policy.note_not_sent_on(conn, "mixed-4", owner="memory", expected_version="v1", model_receipt=latest)
        assert not before["can_retry"] and before["retry_at"] is None
        credit = await rbh_policy.note_not_sent_on(conn, "mixed-1", owner="memory", expected_version="v1",
                                                  model_receipt=receipt("mixed-1", row["data_generation"]))
        assert credit["can_retry"] and credit["retry_at"] is None  # The older result cannot move the successor.
        unfinished = await rbh_policy.note_not_sent_on(conn, "mixed-4", owner="memory", expected_version="v1",
                                                        model_receipt=replace(latest, finished_at=None))
        assert unfinished["retry_at"] is None
        after = await rbh_policy.note_not_sent_on(conn, "mixed-4", owner="memory", expected_version="v1", model_receipt=latest)
        assert after["reason"] == "not_confirmed_not_sent" and after["retry_at"] is not None
        assert (after["total_attempts"], after["computation_attempts"], after["connection_attempts"]) == (4, 3, 1)

@pytest.mark.parametrize("blocked", ["paused", "cancelled", "source_withdrawn"])
async def test_lineage_stops_reclassification_when_owner_state_changes(database, rbh_policy, blocked):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "blocked-" + blocked)
        attempt_id = "blocked-" + blocked + "-1"
        await rbh_policy.reserve_attempt_on(conn, row["lineage_id"], owner="memory", expected_version="v1",
            attempt_id=attempt_id, request_digest="req", route="route", original_state="none")
        await rbh_policy.fail_attempt_on(conn, attempt_id, code="transport_not_sent", category=FailureCategory.TRANSPORT)
        await rbh_policy.update_version_on(conn, row["lineage_id"], owner="memory", expected_version="v1",
                                          current_version="v1", **{blocked: True})
        with pytest.raises(AppError, match="recovery_version_changed"):
            await rbh_policy.note_not_sent_on(conn, attempt_id, owner="memory", expected_version="v1",
                model_receipt=receipt(attempt_id, row["data_generation"]))
        with pytest.raises(AppError, match="recovery_version_changed"):
            await rbh_policy.computation_budget_on(conn, row["lineage_id"], owner="memory", expected_version="v1")


async def test_generation_and_attempt_version_are_independent_fences(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "generation")
        identity = row["lineage_id"]
        original = receipt("generation-1", row["data_generation"])
        await rbh_policy.reserve_attempt_on(conn, identity, owner="memory", expected_version="v1",
            attempt_id="generation-1", request_digest="req", route="route", original_state="none")
        await rbh_policy.fail_attempt_on(conn, "generation-1", code="transport_not_sent", category=FailureCategory.TRANSPORT)
        await rbh_policy.update_version_on(conn, identity, owner="memory", expected_version="v1", current_version="v2")
        with pytest.raises(AppError, match="recovery_version_changed"):
            await rbh_policy.note_not_sent_on(conn, "generation-1", owner="memory", expected_version="v2", model_receipt=original)
        await conn.execute("UPDATE runtime.installation_generation SET id='fixture-new-generation' WHERE singleton=true")
        with pytest.raises(AppError, match="job_generation_expired"):
            await rbh_policy.computation_budget_on(conn, identity, owner="memory", expected_version="v2")
        with pytest.raises(AppError, match="job_generation_expired"):
            await rbh_policy.note_not_sent_on(conn, "generation-1", owner="memory", expected_version="v2", model_receipt=original)
        assert await rbh_policy.claim_due_on(conn, owner="memory") == ()


async def test_repeated_latest_receipt_wakes_after_older_out_of_order_credits(database, rbh_policy):
    async with database.transaction() as conn:
        row = await register(rbh_policy, conn, "reordered", max_attempts=4)
        identity = row["lineage_id"]
        receipts = []
        # Synthetic legacy state: six physical attempts were already archived
        # before their individual connection failures could be classified.
        for number in range(1, 7):
            attempt_id = f"reordered-{number}"
            await conn.execute(
                "INSERT INTO runtime.recovery_attempts(attempt_id,lineage_id,request_digest,route,dependency,"
                "version,attempt_number,status,error_code,finished_at) "
                "VALUES(%s,%s,%s,'route','shared-provider','v1',%s,'failed','transport_not_sent',now())",
                (attempt_id, identity, attempt_id, number))
            receipts.append(receipt(attempt_id, row["data_generation"]))
        await conn.execute("UPDATE runtime.recovery_lineages SET attempts=6,failure_category='transport',due_at=NULL "
                           "WHERE lineage_id=%s", (identity,))
        await conn.execute("INSERT INTO runtime.recovery_dependencies "
                           "(data_generation,dependency,cooldown_until,probe_token,probe_expires_at) "
                           "VALUES(%s,'shared-provider',now()+interval '5 minutes','other-probe',"
                           "now()+interval '10 minutes')", (row["data_generation"],))
        before_probe = await (await conn.execute(
            "SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies "
            "WHERE data_generation=%s AND dependency='shared-provider'", (row["data_generation"],))).fetchone()

        latest = receipts[-1]
        first = await rbh_policy.note_not_sent_on(conn, latest.attempt_id, owner="memory", expected_version="v1",
                                                 model_receipt=latest)
        assert (first["total_attempts"], first["computation_attempts"], first["connection_attempts"]) == (6, 5, 1)
        assert not first["can_retry"] and first["retry_at"] is None
        for index, older in enumerate(receipts[:2]):
            credited = await rbh_policy.note_not_sent_on(conn, older.attempt_id, owner="memory", expected_version="v1",
                                                        model_receipt=older)
            assert credited["retry_at"] is None
            assert credited["can_retry"] == (index == 1)
            state = await (await conn.execute(
                "SELECT due_at,claim_token FROM runtime.recovery_lineages WHERE lineage_id=%s", (identity,))).fetchone()
            assert state == {"due_at": None, "claim_token": None}

        recovered = await rbh_policy.note_not_sent_on(conn, latest.attempt_id, owner="memory", expected_version="v1",
                                                     model_receipt=latest)
        assert (recovered["total_attempts"], recovered["computation_attempts"], recovered["connection_attempts"]) == (6, 3, 3)
        assert recovered["can_retry"] and recovered["retry_at"] >= before_probe["probe_expires_at"]
        state = await (await conn.execute(
            "SELECT due_at,claim_token FROM runtime.recovery_lineages WHERE lineage_id=%s", (identity,))).fetchone()
        assert state["due_at"] == recovered["retry_at"] and state["claim_token"] is None
        repeated = await rbh_policy.note_not_sent_on(conn, latest.attempt_id, owner="memory", expected_version="v1",
                                                     model_receipt=latest)
        assert repeated == recovered
        assert await (await conn.execute(
            "SELECT due_at,claim_token FROM runtime.recovery_lineages WHERE lineage_id=%s", (identity,))).fetchone() == state
        assert await (await conn.execute(
            "SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies "
            "WHERE data_generation=%s AND dependency='shared-provider'", (row["data_generation"],))).fetchone() == before_probe
        attempts = await (await conn.execute(
            "SELECT attempt_number,status FROM runtime.recovery_attempts WHERE lineage_id=%s ORDER BY attempt_number",
            (identity,))).fetchall()
        assert [(attempt["attempt_number"], attempt["status"]) for attempt in attempts] == [
            (number, "failed") for number in range(1, 7)]
