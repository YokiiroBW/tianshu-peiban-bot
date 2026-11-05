"""Expired shared probe leases never settle or replay their original attempt."""

import asyncio
from datetime import UTC, datetime

import pytest

from tianshu.models.types import ModelReceipt
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import FailureCategory, RecoveryPolicy, RecoveryWait


async def register(policy, connection, material, *, kind="external_effect"):
    return await policy.register_on(connection, domain="growth", subject_id=material, owner="growth",
        material_id=material, material_digest=material, dependency="quota", current_version="v1",
        max_attempts=3, recovery_kind=kind)


@pytest.mark.parametrize("old_state,late_state,expiry", [
    ("reserved", "failed", "expired"),
    ("reserved", "succeeded", "missing"),
    ("unknown", "failed", "expired"),
    ("unknown", "succeeded", "expired"),
])
async def test_probe_lease_takeover_is_single_owner_and_fences_late_result(database, old_state, late_state, expiry):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        original = await register(policy, connection, "old-material")
        left = await register(policy, connection, "new-left")
        right = await register(policy, connection, "new-right")
        await connection.execute("INSERT INTO runtime.recovery_dependencies(data_generation,dependency,cooldown_until) "
                                 "VALUES(%s,'quota',now()-interval '1 second')", (original["data_generation"],))
        first = await policy.reserve_attempt_on(connection, original["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="old-probe", request_digest="old", route="A", original_state="none")
        assert first.probe_token == "old-probe"
        if old_state == "unknown":
            await policy.fail_attempt_on(connection, "old-probe", code="outcome_unknown",
                                         category=FailureCategory.UNKNOWN_EFFECT)
            with pytest.raises(AppError, match="recovery_original_not_failed"):
                await policy.reserve_attempt_on(connection, original["lineage_id"], owner="growth", expected_version="v1",
                    attempt_id="old-replay", request_digest="old", route="A", original_state="unknown")
        blocked = await policy.inspect_wait_on(connection, left["lineage_id"], owner="growth", expected_version="v1")
        assert blocked.reason == "recovery_dependency_probe_in_progress"
        with pytest.raises(RecoveryWait, match="recovery_dependency_probe_in_progress"):
            await policy.reserve_attempt_on(connection, left["lineage_id"], owner="growth", expected_version="v1",
                attempt_id="new-left", request_digest="left", route="A", original_state="none")
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=NULL WHERE dependency='quota'")
        assert (await policy.inspect_wait_on(connection, left["lineage_id"], owner="growth",
                                             expected_version="v1")).reason == "recovery_dependency_probe_in_progress"
        with pytest.raises(RecoveryWait, match="recovery_dependency_probe_in_progress"):
            await policy.reserve_attempt_on(connection, left["lineage_id"], owner="growth", expected_version="v1",
                attempt_id="new-left", request_digest="left", route="A", original_state="none")
        await connection.execute("UPDATE runtime.recovery_dependencies SET probe_expires_at=" +
            ("NULL" if expiry == "missing" else "now()-interval '1 second'") +
            ",cooldown_until=now()+interval '2 minutes' WHERE dependency='quota'")
        with pytest.raises(RecoveryWait, match="recovery_dependency_cooling"):
            await policy.reserve_attempt_on(connection, left["lineage_id"], owner="growth", expected_version="v1",
                attempt_id="new-left", request_digest="left", route="A", original_state="none")
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second' "
                                 "WHERE dependency='quota'")
        assert await policy.inspect_wait_on(connection, left["lineage_id"], owner="growth", expected_version="v1") is None
        old = await (await connection.execute("SELECT status FROM runtime.recovery_attempts WHERE attempt_id='old-probe'")).fetchone()
        assert old["status"] == old_state

    async def compete(row, attempt_id):
        try:
            async with database.transaction() as connection:
                reservation = await policy.reserve_attempt_on(connection, row["lineage_id"], owner="growth",
                    expected_version="v1", attempt_id=attempt_id, request_digest=attempt_id, route="A", original_state="none")
                return "reserved", reservation
        except RecoveryWait as wait:
            return "waiting", wait

    outcomes = await asyncio.gather(compete(left, "new-left"), compete(right, "new-right"))
    assert sorted(state for state, _ in outcomes) == ["reserved", "waiting"]
    winner = next(value for state, value in outcomes if state == "reserved")
    assert winner.number == 1 and winner.probe_token in {"new-left", "new-right"}
    assert next(value for state, value in outcomes if state == "waiting").reason == "recovery_dependency_probe_in_progress"
    async with database.transaction() as connection:
        before = await (await connection.execute(
            "SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies WHERE dependency='quota'")).fetchone()
        assert before["probe_token"] == winner.probe_token
        if old_state == "unknown":
            await policy.resolve_unknown_on(connection, "old-probe", observation_ref="original-receipt", state=late_state,
                code="http_503" if late_state == "failed" else None,
                category=FailureCategory.TRANSPORT if late_state == "failed" else None, retry_after=3600)
        elif late_state == "failed":
            await policy.fail_attempt_on(connection, "old-probe", code="http_503", category=FailureCategory.TRANSPORT,
                                         retry_after=3600)
        else:
            await policy.succeed_attempt_on(connection, "old-probe")
        after = await (await connection.execute(
            "SELECT cooldown_until,probe_token,probe_expires_at FROM runtime.recovery_dependencies WHERE dependency='quota'")).fetchone()
        assert after == before
        settled = await (await connection.execute("SELECT status FROM runtime.recovery_attempts WHERE attempt_id='old-probe'")).fetchone()
        assert settled["status"] == late_state
        counts = await (await connection.execute("SELECT material_digest,attempts FROM runtime.recovery_lineages "
            "WHERE lineage_id=ANY(%s)", ([original["lineage_id"], left["lineage_id"], right["lineage_id"]],))).fetchall()
        by_material = {item["material_digest"]: item["attempts"] for item in counts}
        assert by_material["old-material"] == 1
        assert sorted((by_material["new-left"], by_material["new-right"])) == [0, 1]


async def test_same_unknown_computation_still_requires_receipt_budget_and_expired_probe(database):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        original = await register(policy, connection, "pure-material", kind="pure_computation")
        identity = original["lineage_id"]
        await connection.execute("INSERT INTO runtime.recovery_dependencies(data_generation,dependency,cooldown_until) "
                                 "VALUES(%s,'quota',now()-interval '1 second')", (original["data_generation"],))
        first = await policy.reserve_attempt_on(connection, identity, owner="growth", expected_version="v1",
            attempt_id="original", request_digest="old", route="A", original_state="none")
        assert first.probe_token == "original"
        receipt = ModelReceipt(attempt_id="original", intent_id="intent", status="unknown", dispatch="unknown",
            route_id="route", route_revision=1, provider_id="provider", model="model", protocol="openai",
            quota_group="quota", finished_at=datetime.now(UTC), data_generation=original["data_generation"])
        await policy.record_computation_unknown_on(connection, "original", owner="growth", expected_version="v1",
                                                   model_receipt=receipt, random_value=0)
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s",
                                 (identity,))
        claim = await policy.claim_lineage_due_on(connection, identity, owner="growth")
        options = dict(owner="growth", expected_version="v1", attempt_id="next", request_digest="new", route="A",
                       original_state="unknown", claim_token=claim["claim_token"])
        with pytest.raises(RecoveryWait, match="recovery_dependency_probe_in_progress"):
            await policy.reserve_attempt_on(connection, identity, **options, model_receipt=receipt)
        await connection.execute("UPDATE runtime.recovery_dependencies SET probe_expires_at=now()-interval '1 second' "
                                 "WHERE dependency='quota'")
        with pytest.raises(AppError, match="recovery_model_unknown_unconfirmed"):
            await policy.reserve_attempt_on(connection, identity, **options)
        second = await policy.reserve_attempt_on(connection, identity, **options, model_receipt=receipt)
        assert second.number == 2 and second.probe_token == "next"
        attempts = await (await connection.execute("SELECT status,supersedes_attempt_id FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number", (identity,))).fetchall()
        assert attempts == [{"status": "unknown", "supersedes_attempt_id": None},
                            {"status": "reserved", "supersedes_attempt_id": "original"}]


async def test_definitive_nontransport_failure_releases_only_its_probe_lease(database):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        original = await register(policy, connection, "invalid-output")
        following = await register(policy, connection, "independent")
        await connection.execute("INSERT INTO runtime.recovery_dependencies(data_generation,dependency,cooldown_until) "
                                 "VALUES(%s,'quota',now()-interval '1 second')", (original["data_generation"],))
        first = await policy.reserve_attempt_on(connection, original["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="invalid-probe", request_digest="one", route="A", original_state="none")
        assert first.probe_token == "invalid-probe"
        before = await (await connection.execute("SELECT cooldown_until FROM runtime.recovery_dependencies "
                                                 "WHERE dependency='quota'")).fetchone()
        await policy.fail_attempt_on(connection, "invalid-probe", code="schema_invalid",
                                     category=FailureCategory.INVALID_OUTPUT)
        released = await (await connection.execute("SELECT cooldown_until,probe_token,probe_expires_at "
                                                   "FROM runtime.recovery_dependencies WHERE dependency='quota'")).fetchone()
        assert released == {"cooldown_until": before["cooldown_until"], "probe_token": None, "probe_expires_at": None}
        second = await policy.reserve_attempt_on(connection, following["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="independent-probe", request_digest="two", route="A", original_state="none")
        assert second.probe_token == "independent-probe"


async def test_retry_after_cooldown_is_not_bypassed_by_an_expired_probe(database):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        original = await register(policy, connection, "rate-limited")
        following = await register(policy, connection, "other-material")
        await connection.execute("INSERT INTO runtime.recovery_dependencies(data_generation,dependency,cooldown_until) "
                                 "VALUES(%s,'quota',now()-interval '1 second')", (original["data_generation"],))
        first = await policy.reserve_attempt_on(connection, original["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="rate-probe", request_digest="one", route="A", original_state="none")
        assert first.probe_token == "rate-probe"
        due = await policy.fail_attempt_on(connection, "rate-probe", code="http_429",
            category=FailureCategory.TRANSPORT, retry_after=180, random_value=0)
        # Simulate a stale expired lease while retaining the authoritative cooldown.
        await connection.execute("UPDATE runtime.recovery_dependencies SET probe_token='rate-probe',"
                                 "probe_expires_at=now()-interval '1 second' WHERE dependency='quota'")
        with pytest.raises(RecoveryWait, match="recovery_dependency_cooling") as captured:
            await policy.reserve_attempt_on(connection, following["lineage_id"], owner="growth", expected_version="v1",
                attempt_id="next-probe", request_digest="two", route="A", original_state="none")
        assert captured.value.retry_at == due
