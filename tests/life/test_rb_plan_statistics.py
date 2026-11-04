from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document
from tianshu.life.statistics import backfill, events, record_on
from tianshu.life.types import PlanApplyResult, PlanCandidateResult
from test_planner_execution import setup


NOW = datetime(2026, 11, 5, 10, tzinfo=UTC)


def test_each_stable_candidate_contributes_to_exactly_one_outcome():
    result = PlanApplyResult((PlanCandidateResult("one", "scheduled", "ok"),
        PlanCandidateResult("two", "deferred_no_slot", "busy")))
    first = list(events("generation", "actor", NOW, result))
    second = list(events("generation", "actor", NOW, result))
    assert first == second and len({event["event_id"] for event in first}) == 2
    assert first[0]["metrics"]["life_scheduled"] == 1 and first[0]["metrics"]["life_deferred_no_slot"] == 0
    assert first[1]["metrics"]["life_deferred_no_slot"] == 1 and first[1]["metrics"]["life_intentional_idle"] == 0


@pytest.mark.asyncio
async def test_statistics_failure_does_not_abort_domain_transaction_and_can_be_replayed(database):
    value = await setup(database, "{}")
    statistics = type("Stats", (), {})()
    statistics.record_safely_on = AsyncMock(side_effect=[False, True])
    statistics.mark_producer_on = AsyncMock(return_value=True)
    value.worker.life.bind_statistics(statistics)
    application = {"items": [{"candidate_key": "one", "status": "scheduled", "reason": "ok"}], "next_due_at": None}
    async with database.transaction() as connection:
        await connection.execute("ALTER TABLE life.plan_generations ADD COLUMN IF NOT EXISTS application_results jsonb")
        await connection.execute("INSERT INTO life.plan_generations(generation_key,actor_id,day,material_fingerprint,base_revision,context,state,reviewed_at,application_results) "
            "VALUES ('g','actor',%s,'m',1,%s,'applied',%s,%s)", (NOW.date(), document(value.context), NOW, document(application)))
        assert not await record_on(value.worker.life, connection, "g", "actor", NOW, application, enqueue_replay=True)
    result = await backfill(value.worker.life, source_quiescent=True)
    assert result["complete"] and result["state"] == "scanned"
    assert statistics.record_safely_on.await_count == 2
    assert statistics.mark_producer_on.await_args_list[-1].kwargs["complete"] is True
