"""Dependency waits retain the original budget, claim and retry boundary."""

from datetime import UTC, datetime, timedelta

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import FailureCategory, RecoveryPolicy, RecoveryWait


async def register(policy, connection, material):
    return await policy.register_on(connection, domain="memory", subject_id=material, owner="memory",
        material_id=material, material_digest=material, dependency="quota", current_version="v1", max_attempts=3)


async def test_cooldown_and_probe_wait_do_not_consume_attempts(database):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        first = await register(policy, connection, "first")
        second = await register(policy, connection, "second")
        await connection.execute("INSERT INTO runtime.recovery_dependencies(data_generation,dependency,cooldown_until) "
                                 "VALUES(%s,'quota',now()+interval '2 minutes')", (first["data_generation"],))
        options = dict(owner="memory", expected_version="v1", attempt_id="one", request_digest="req",
                       route="A", original_state="none")
        with pytest.raises(RecoveryWait) as captured:
            await policy.reserve_attempt_on(connection, first["lineage_id"], **options)
        wait = captured.value
        assert isinstance(wait, AppError) and wait.code == wait.reason == "recovery_dependency_cooling"
        assert wait.dependency == "quota" and wait.retry_at > datetime.now(UTC)
        observed = await policy.inspect_wait_on(connection, first["lineage_id"], owner="memory", expected_version="v1")
        assert observed.reason == wait.reason and observed.retry_at == wait.retry_at
        saved = await (await connection.execute("SELECT attempts FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                                (first["lineage_id"],))).fetchone()
        assert saved["attempts"] == 0
        assert not await (await connection.execute("SELECT 1 FROM runtime.recovery_attempts")).fetchone()
        await connection.execute("UPDATE runtime.recovery_dependencies SET cooldown_until=now()-interval '1 second'")
        probe = await policy.reserve_attempt_on(connection, first["lineage_id"], **options)
        assert probe.probe_token == "one"
        with pytest.raises(RecoveryWait) as captured:
            await policy.reserve_attempt_on(connection, second["lineage_id"], **{**options, "attempt_id": "two"})
        wait = captured.value
        assert wait.reason == "recovery_dependency_probe_in_progress"
        assert datetime.now(UTC) < wait.retry_at <= datetime.now(UTC) + timedelta(seconds=31)
        assert (await policy.inspect_wait_on(connection, second["lineage_id"], owner="memory",
                                              expected_version="v1")).reason == wait.reason
        await policy.succeed_attempt_on(connection, "one")
        assert await policy.inspect_wait_on(connection, second["lineage_id"], owner="memory", expected_version="v1") is None
        assert (await policy.reserve_attempt_on(connection, second["lineage_id"],
                                                **{**options, "attempt_id": "two"})).number == 1


async def test_claim_loss_reports_wait_without_giving_old_owner_write_authority(database):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        row = await register(policy, connection, "claim")
        identity = row["lineage_id"]
        await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="original", request_digest="req", route="A", original_state="none")
        await policy.fail_attempt_on(connection, "original", code="http_503", category=FailureCategory.TRANSPORT,
                                     random_value=0)
        await connection.execute("UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' WHERE lineage_id=%s",
                                 (identity,))
        claim = await policy.claim_lineage_due_on(connection, identity, owner="memory")
        assert await policy.claim_lineage_due_on(connection, identity, owner="memory") is None
        observed = await policy.inspect_wait_on(connection, identity, owner="memory", expected_version="v1")
        assert observed.reason == "recovery_claim_in_progress" and observed.retry_at == claim["claim_expires_at"]
        with pytest.raises(RecoveryWait, match="recovery_claim_lost") as captured:
            await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
                attempt_id="stale", request_digest="req", route="A", original_state="failed", claim_token="old")
        assert captured.value.retry_at == claim["claim_expires_at"]
        current = await (await connection.execute("SELECT attempts,claim_token FROM runtime.recovery_lineages WHERE lineage_id=%s",
                                                  (identity,))).fetchone()
        assert current == {"attempts": 1, "claim_token": claim["claim_token"]}
        with pytest.raises(AppError, match="recovery_owner_mismatch"):
            await policy.inspect_wait_on(connection, identity, owner="other", expected_version="v1")
        with pytest.raises(AppError, match="recovery_version_changed"):
            await policy.inspect_wait_on(connection, identity, owner="memory", expected_version="old")


async def test_not_due_reports_persisted_due_and_not_authorization(database):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        row = await register(policy, connection, "due")
        identity = row["lineage_id"]
        # No wait is not a grant to skip the normal reservation checks.
        assert await policy.inspect_wait_on(connection, identity, owner="memory", expected_version="v1") is None
        await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="original", request_digest="req", route="A", original_state="none")
        due = await policy.fail_attempt_on(connection, "original", code="http_503", category=FailureCategory.TRANSPORT,
                                           retry_after=60, random_value=0)
        wait = await policy.inspect_wait_on(connection, identity, owner="memory", expected_version="v1")
        assert wait.reason == "recovery_not_due" and wait.retry_at == due
        assert await policy.claim_lineage_due_on(connection, identity, owner="memory") is None


async def test_exhausted_budget_is_terminal_not_a_claim_wait(database):
    policy = RecoveryPolicy()
    async with database.transaction() as connection:
        row = await policy.register_on(connection, domain="memory", subject_id="one", owner="memory",
            material_id="one", material_digest="one", dependency="quota", current_version="v1", max_attempts=1)
        identity = row["lineage_id"]
        await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
            attempt_id="only", request_digest="req", route="A", original_state="none")
        await policy.fail_attempt_on(connection, "only", code="http_503", category=FailureCategory.TRANSPORT,
                                     random_value=0)
        with pytest.raises(AppError, match="recovery_budget_exhausted") as captured:
            await policy.reserve_attempt_on(connection, identity, owner="memory", expected_version="v1",
                attempt_id="extra", request_digest="req", route="A", original_state="failed")
        assert not isinstance(captured.value, RecoveryWait)
