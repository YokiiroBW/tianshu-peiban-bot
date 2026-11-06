"""Pure authored generation preserves interrupted physical receipts and budgets."""
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from test_pure_unknown_recovery import setup_generation
from test_rbh_transport_recovery import advance_retry, budget_for


@pytest.mark.parametrize("kind",["diary","chapter"])
async def test_interrupted_original_is_not_treated_as_user_cancellation(database,kind):
    value,attempt,generate,current = await setup_generation(database,kind)
    await generate(attempt)
    value.models.receipts[attempt] = replace(value.models.receipts[attempt],status="cancelled",dispatch="submitted",
        error_code="consumer_interrupted",cancellation_reason="consumer_interrupted",finished_at=datetime.now(UTC))
    await generate(attempt)
    row = await current()
    budget = await budget_for(database,value.diaries.recovery.policy,attempt)
    assert row["state"] == "unknown" and row["generation_retry_due_at"] is not None
    assert budget["computation_attempts"] == 1 and budget["connection_attempts"] == 0
    assert value.models.receipts[attempt].status == "cancelled"
    assert value.models.receipts[attempt].cancellation_reason == "consumer_interrupted"
    table = "life.diaries" if kind == "diary" else "life.chapters"
    await advance_retry(database,table,row["id"])
    await generate(attempt)
    assert (await current())["generation_id"] != attempt


@pytest.mark.parametrize("kind",["diary","chapter"])
async def test_explicit_cancel_still_stops_authored_generation(database,kind):
    value,attempt,generate,current = await setup_generation(database,kind)
    await generate(attempt)
    value.models.receipts[attempt] = replace(value.models.receipts[attempt],status="cancelled",dispatch="submitted",
        error_code="consumer_interrupted",cancellation_reason="explicit_cancel",finished_at=datetime.now(UTC))
    await generate(attempt)
    row = await current()
    assert row["state"] == "cancelled" and row["generation_retry_due_at"] is None
    assert value.models.receipts[attempt].cancellation_reason == "explicit_cancel"
