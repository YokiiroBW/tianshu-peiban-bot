"""Recovery budgets are shared across routes and preserve unknown outcomes."""

from datetime import UTC, datetime
from importlib.resources import files

import pytest
import pytest_asyncio

from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import (
    FailureCategory, RecoveryPolicy, classify_failure, closed_partitions,
    compatible_routes, retry_due,
)


@pytest_asyncio.fixture
async def policy(database):
    await migrate(database, (Migration("0007_rb_runtime",
        files("tianshu.runtime").joinpath("rb_runtime.sql").read_text()),
        Migration("0008_rb_recovery_change",
        files("tianshu.runtime").joinpath("rb_recovery_change.sql").read_text())))
    return RecoveryPolicy()


async def test_route_switch_and_revision_do_not_reset_attempt_budget(database, policy):
    async with database.transaction() as connection:
        row = await policy.register_on(connection, domain="memory", subject_id="actor", owner="memory",
            material_id="batch-1", material_digest="digest-1", dependency="provider:quota",
            current_version="v1", max_attempts=3)
        identity = row["lineage_id"]
        first = await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="attempt-1", request_digest="req", route="A", original_state="none")
        assert first.number == 1
        await policy.fail_attempt_on(connection, first.attempt_id, code="http_429",
            category=FailureCategory.TRANSPORT, retry_after=5, random_value=0)
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second'")
        claim = (await policy.claim_due_on(connection, owner="memory"))[0]
        second = await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="attempt-2", request_digest="req", route="B", original_state="failed",
            claim_token=claim["claim_token"])
        assert second.number == 2 and second.probe_token == "attempt-2"
        await policy.fail_attempt_on(connection, second.attempt_id, code="http_429",
            category=FailureCategory.TRANSPORT, retry_after=5, probe_token=second.probe_token, random_value=0)
        await policy.update_version_on(connection, identity, owner="memory", expected_version="v1", current_version="v2")
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second'")
        claim = (await policy.claim_due_on(connection, owner="memory"))[0]
        with pytest.raises(AppError, match="recovery_version_changed"):
            await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
                attempt_id="stale", request_digest="req", route="A", original_state="failed",
                claim_token=claim["claim_token"])
        third = await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v2",
            attempt_id="attempt-3", request_digest="req", route="A", original_state="failed",
            claim_token=claim["claim_token"])
        assert third.number == 3
        assert await policy.fail_attempt_on(connection, third.attempt_id, code="http_429",
            category=FailureCategory.TRANSPORT, probe_token=third.probe_token, random_value=0) is None
        record = await (await connection.execute("SELECT attempts,due_at FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                                   (identity,))).fetchone()
        assert record == {"attempts": 3, "due_at": None}
        replay = await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v2",
            attempt_id="attempt-1", request_digest="req", route="A", original_state="failed")
        assert replay.number == 1 and replay.status == "failed"


async def test_unknown_original_cannot_be_replayed_until_definitive_observation(database, policy):
    async with database.transaction() as connection:
        row = await policy.register_on(connection, domain="growth", subject_id="a", owner="growth",
            material_id="original", material_digest="frozen", dependency="remote", current_version="v1", max_attempts=2)
        attempt = await policy.reserve_attempt_on(connection, row["lineage_id"], owner="growth", expected_version="v1",
            attempt_id="unknown-1", request_digest="req", route="A", original_state="none")
        assert await policy.fail_attempt_on(connection, attempt.attempt_id, code="transport_unknown",
            category=FailureCategory.UNKNOWN_EFFECT) is None
        with pytest.raises(AppError, match="recovery_original_not_failed"):
            await policy.reserve_attempt_on(connection, row["lineage_id"], owner="growth", expected_version="v1",
                attempt_id="illegal", request_digest="req", route="B", original_state="unknown")
        await policy.resolve_unknown_on(connection, attempt.attempt_id, observation_ref="receipt-1",
            state="failed", code="http_503", category=FailureCategory.TRANSPORT)
        saved = await (await connection.execute("SELECT status,observation_ref FROM runtime.recovery_attempts WHERE attempt_id=%s",
                                                  (attempt.attempt_id,))).fetchone()
        assert saved == {"status": "failed", "observation_ref": "receipt-1"}
        history = await (await connection.execute(
            "SELECT status FROM runtime.recovery_attempt_observations WHERE attempt_id=%s ORDER BY id",
            (attempt.attempt_id,))).fetchall()
        assert [item["status"] for item in history] == ["unknown", "failed"]


async def test_successful_original_can_continue_without_rewriting_outcome_or_budget(database, policy):
    async with database.transaction() as connection:
        row = await policy.register_on(connection, domain="memory", subject_id="actor", owner="memory",
            material_id="batch", material_digest="frozen", dependency="provider", current_version="v1", max_attempts=2)
        identity = row["lineage_id"]
        first = await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="first", request_digest="req-1", route="A", original_state="none")
        await policy.succeed_attempt_on(connection, first.attempt_id)
        with pytest.raises(AppError, match="recovery_claim_lost"):
            await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
                attempt_id="unclaimed", request_digest="req-2", route="A", original_state="succeeded")
        claim = await policy.claim_continuation_on(connection, identity, owner="memory", expected_version="v1",
            prior_attempt_id="first", reason="need_context")
        assert (await policy.claim_continuation_on(connection, identity, owner="memory", expected_version="v1",
            prior_attempt_id="first", reason="need_context"))["claim_token"] == claim["claim_token"]
        second = await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="second", request_digest="req-2", route="A", original_state="succeeded",
            claim_token=claim["claim_token"])
        assert second.number == 2
        attempts = await (await connection.execute(
            "SELECT status,continuation_of,continuation_reason FROM runtime.recovery_attempts "
            "WHERE lineage_id=%s ORDER BY attempt_number", (identity,))).fetchall()
        assert attempts == [{"status": "succeeded", "continuation_of": None, "continuation_reason": None},
                            {"status": "reserved", "continuation_of": "first", "continuation_reason": "need_context"}]


async def test_dependency_switch_keeps_budget_and_exact_claim_does_not_take_other_work(database, policy):
    async with database.transaction() as connection:
        common = dict(domain="memory", owner="memory", dependency="quota-a", current_version="v1", max_attempts=3)
        first = await policy.register_on(connection, subject_id="a", material_id="material-a", material_digest="a", **common)
        other = await policy.register_on(connection, subject_id="b", material_id="material-b", material_digest="b", **common)
        for row, attempt_id in ((first, "a1"), (other, "b1")):
            await policy.reserve_attempt_on(connection, row["lineage_id"], owner="memory", expected_version="v1",
                attempt_id=attempt_id, request_digest="request", route="A", original_state="none")
            await policy.fail_attempt_on(connection, attempt_id, code="http_503",
                category=FailureCategory.TRANSPORT, random_value=0)
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second'")
        changed = await policy.update_dependency_on(connection, first["lineage_id"], owner="memory",
            expected_version="v1", expected_dependency="quota-a", new_dependency="quota-b")
        assert changed["attempts"] == 1 and changed["dependency"] == "quota-b"
        claim = await policy.claim_lineage_due_on(connection, first["lineage_id"], owner="memory")
        untouched = await (await connection.execute("SELECT claim_token FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                                     (other["lineage_id"],))).fetchone()
        assert untouched["claim_token"] is None
        next_attempt = await policy.reserve_attempt_on(connection, first["lineage_id"], owner="memory", expected_version="v1",
            attempt_id="a2", request_digest="request", route="B", original_state="failed", claim_token=claim["claim_token"])
        assert next_attempt.number == 2 and next_attempt.dependency == "quota-b"
        same = await policy.register_on(connection, domain="memory", owner="memory", subject_id="a", material_id="material-a",
            material_digest="a", dependency="quota-b", current_version="v1", max_attempts=3)
        assert same["lineage_id"] == first["lineage_id"] and same["attempts"] == 2
        with pytest.raises(AppError, match="recovery_original_not_settled"):
            await policy.update_dependency_on(connection, first["lineage_id"], owner="memory", expected_version="v1",
                expected_dependency="quota-b", new_dependency="quota-a")


async def test_configuration_change_resumes_original_failure_without_reclassifying(database, policy):
    async with database.transaction() as connection:
        row = await policy.register_on(connection, domain="memory", subject_id="actor", owner="memory",
            material_id="batch-config", material_digest="frozen", dependency="provider", current_version="v1", max_attempts=3)
        identity = row["lineage_id"]
        await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="config-1", request_digest="original", route="A", original_state="none")
        assert await policy.fail_attempt_on(connection, "config-1", code="mapping_not_configured",
            category=FailureCategory.OTHER) is None
        with pytest.raises(AppError, match="recovery_version_unchanged"):
            await policy.update_version_on(connection, identity, owner="memory", expected_version="v1",
                current_version="v1", resume_due=True)
        with pytest.raises(AppError, match="recovery_version_changed"):
            await policy.update_version_on(connection, identity, owner="memory", expected_version="stale",
                current_version="v2", resume_due=True)
        resumed = await policy.update_version_on(connection, identity, owner="memory", expected_version="v1",
            current_version="v2", resume_due=True)
        assert resumed["lineage_id"] == identity and resumed["attempts"] == 1
        assert resumed["failure_category"] == FailureCategory.OTHER.value and resumed["due_at"] is not None
        old = await (await connection.execute(
            "SELECT status,error_code FROM runtime.recovery_attempts WHERE attempt_id='config-1'")).fetchone()
        assert old == {"status": "failed", "error_code": "mapping_not_configured"}
        claim = await policy.claim_lineage_due_on(connection, identity, owner="memory")
        attempt = await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v2",
            attempt_id="config-2", request_digest="corrected", route="A", original_state="failed",
            claim_token=claim["claim_token"])
        assert attempt.number == 2


async def test_configuration_change_cannot_resume_cancelled_withdrawn_active_or_exhausted_work(database, policy):
    async with database.transaction() as connection:
        for state in ("paused", "cancelled", "withdrawn", "withdrawn_after_failure", "reserved", "unknown", "succeeded", "exhausted"):
            row = await policy.register_on(connection, domain="growth", subject_id=state, owner="growth",
                material_id="material-" + state, material_digest="frozen", dependency="provider",
                current_version="v1", max_attempts=1 if state == "exhausted" else 3)
            identity = row["lineage_id"]
            attempt_id = "attempt-" + state
            await policy.reserve_attempt_on(connection, identity, owner="growth", expected_version="v1",
                attempt_id=attempt_id, request_digest="request", route="A", original_state="none")
            if state == "unknown":
                await policy.fail_attempt_on(connection, attempt_id, code="transport_unknown",
                    category=FailureCategory.UNKNOWN_EFFECT)
            elif state == "succeeded":
                await policy.succeed_attempt_on(connection, attempt_id)
            elif state != "reserved":
                code = "source_retracted" if state == "withdrawn" else "mapping_not_configured"
                category = FailureCategory.SOURCE_INVALID if state == "withdrawn" else FailureCategory.OTHER
                await policy.fail_attempt_on(connection, attempt_id, code=code, category=category)
            if state in {"paused", "cancelled"}:
                await policy.update_version_on(connection, identity, owner="growth", expected_version="v1",
                    current_version="v1", **{state: True})
            if state == "withdrawn_after_failure":
                await policy.update_version_on(connection, identity, owner="growth", expected_version="v1",
                    current_version="v1", source_withdrawn=True)
            with pytest.raises(AppError, match="recovery_resume_not_eligible"):
                await policy.update_version_on(connection, identity, owner="growth", expected_version="v1",
                    current_version="v2", paused=False, cancelled=False, source_withdrawn=False, resume_due=True)
            current = await (await connection.execute(
                "SELECT current_version,attempts,source_withdrawn FROM runtime.recovery_lineages WHERE lineage_id=%s",
                (identity,))).fetchone()
            assert current["current_version"] == "v1" and current["attempts"] == 1
            if state in {"withdrawn", "withdrawn_after_failure"}:
                assert current["source_withdrawn"] is True


def test_failure_split_and_retry_after_use_explicit_evidence():
    assert classify_failure("input_media_required") == FailureCategory.MISSING_INPUT
    assert classify_failure("http_429") == FailureCategory.TRANSPORT
    assert classify_failure("whatever", external_outcome_unknown=True) == FailureCategory.UNKNOWN_EFFECT
    now = datetime(2026, 11, 5, tzinfo=UTC)
    assert (retry_due(1, retry_after=50, now=now, random_value=0) - now).total_seconds() == 50
    graph = {"a": {"a", "b"}, "b": {"a", "b"}, "c": {"c"}}
    assert closed_partitions([{"a", "b"}, {"c"}], graph)
    assert not closed_partitions([{"a"}, {"b", "c"}], graph)
    routes = [{"id": "one", "purposes": ["embed"], "embedding_space": "x"},
              {"id": "two", "purposes": ["embed"], "embedding_space": "y"}]
    assert [item["id"] for item in compatible_routes(routes, purpose="embed", embedding_space="x")] == ["one"]
