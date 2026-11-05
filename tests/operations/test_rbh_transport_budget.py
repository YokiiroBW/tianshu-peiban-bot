"""Operations wake controls consume the domain's authoritative recovery budget."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tianshu.models.http_timing import HttpTiming
from tianshu.models.types import ModelReceipt
from tianshu.operations.incidents import IncidentStore
from tianshu.operations.recovery import RecoveryAction, RecoveryExecutor
from tianshu.runtime.recovery_policy import FailureCategory, RecoveryPolicy


@pytest.mark.parametrize(
    "confirmed,cooling,expected",
    [(True, False, "observing"), (False, False, "ineligible"), (True, True, "requested")],
)
async def test_domain_wake_uses_computation_budget_and_dependency_due(
    operations_db, confirmed, cooling, expected,
):
    policy = RecoveryPolicy()
    async with operations_db.transaction() as conn:
        lineage = await policy.register_on(
            conn, domain="memory", subject_id="actor", owner="memory:batch",
            material_id="original-batch", material_digest="digest", dependency="quota",
            current_version="1", max_attempts=1, recovery_kind="pure_computation",
        )
        await policy.reserve_attempt_on(
            conn, lineage["lineage_id"], owner=lineage["owner"], expected_version="1",
            attempt_id="original-model", request_digest="request", route="route",
            original_state="none",
        )
        await policy.fail_attempt_on(
            conn, "original-model", code="transport_not_sent",
            category=FailureCategory.TRANSPORT, random_value=0,
        )
        if confirmed:
            timing = HttpTiming(datetime.now(UTC))
            timing.connection_reused = False
            timing.mark("connect_start")
            receipt = ModelReceipt(
                "original-model", "intent", "failed", "not_started", "route", 1,
                "provider", "model", "openai", "quota", finished_at=datetime.now(UTC),
                error_code="transport_not_sent", request_digest="request",
                data_generation=str(lineage["data_generation"]), http_timing=timing.record(),
                diagnostic={"category": "not_sent", "part_count": 0, "part_kinds": [],
                            "text_bytes": 0, "native_completed": False},
            )
            await policy.note_not_sent_on(
                conn, "original-model", owner=lineage["owner"], expected_version="1",
                model_receipt=receipt,
            )
            await conn.execute(
                "UPDATE runtime.recovery_lineages SET due_at=now()-interval '1 second' "
                "WHERE lineage_id=%s", (lineage["lineage_id"],),
            )
        await conn.execute(
            "UPDATE runtime.recovery_dependencies SET cooldown_until=now()+"
            "(%s * interval '1 second') WHERE dependency='quota'", (300 if cooling else -1,),
        )

    store = IncidentStore(operations_db, None, None)
    incident = await store.ingest({
        "event_id": "transport-wake", "dependency": "quota", "error_family": "memory_failed",
        "version": "1", "material_id": "original-batch",
    })

    async def inspect(_row, _target):
        return {
            "eligible": True, "version": "1", "material_id": "original-batch",
            "material_digest": "digest", "dependency": "quota", "original_state": "failed",
            "lineage_id": lineage["lineage_id"], "policy_domain": "memory",
            "owner": lineage["owner"], "subject_id": "actor", "max_attempts": 1,
        }

    invoke = AsyncMock(return_value={"state": "accepted"})
    action = RecoveryAction(
        "memory_wake", frozenset({"memory_failed"}), inspect, invoke,
        AsyncMock(return_value={"state": "pending"}), domain_managed=True,
    )
    executor = RecoveryExecutor(operations_db, policy, AsyncMock(), (action,))
    await executor.enqueue_automatic(incident, action.name, {"material_id": "original-batch"})
    await executor.process_requested()
    async with operations_db.transaction(read_only=True) as conn:
        wake = await (await conn.execute(
            "SELECT state,due_at>now() AS future FROM operations.recoveries",
        )).fetchone()
        original = await (await conn.execute(
            "SELECT attempts FROM runtime.recovery_lineages WHERE lineage_id=%s",
            (lineage["lineage_id"],),
        )).fetchone()
        attempts = await (await conn.execute(
            "SELECT attempt_id,status,attempt_number FROM runtime.recovery_attempts",
        )).fetchall()
    assert wake["state"] == expected
    assert invoke.await_count == (1 if expected == "observing" else 0)
    if cooling:
        assert wake["future"]
    assert original["attempts"] == 1
    assert attempts == [{"attempt_id": "original-model", "status": "failed", "attempt_number": 1}]
