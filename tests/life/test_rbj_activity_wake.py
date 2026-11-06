"""Historical activity wakes retain a proven owner and physical attempt."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock
from uuid import uuid4

from test_rbf_planning_recovery import make_life
from test_rbi_activity_reconciliation import activity, add_waiting
from tianshu.conversation.codec import document
from tianshu.life import rbj_activity_wake
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire


async def test_old_envelopes_require_original_action_context_and_revision(database):
    value = await make_life(database)
    action,_,_ = await add_waiting(value)
    row = await activity(value)
    args = {"activity_id":"watch","context":wire(value.context),"sequence":1}
    candidate = {"key":f"activity-reconcile:{action}:1","args":args}
    assert rbj_activity_wake.is_legacy(row,candidate)
    assert not rbj_activity_wake.is_legacy(row,{**candidate,"key":"activity-reconcile:other:1"})
    assert not rbj_activity_wake.is_legacy(row,{**candidate,"args":{**args,"sequence":2}})
    assert not rbj_activity_wake.is_legacy(row,{**candidate,"args":{**args,"unexpected":True}})
    wrong_context = {**args["context"],"actor_id":"another"}
    assert not rbj_activity_wake.is_legacy(row,{**candidate,"args":{**args,"context":wrong_context}})
    startup = {"key":f"life-recover:activities:watch:{row['revision']}:{uuid4()}",
        "args":{**args,"sequence":0}}
    assert rbj_activity_wake.is_legacy(row,startup)
    assert not rbj_activity_wake.is_legacy({**row,"revision":row["revision"]+1},startup)


async def test_current_legacy_wake_returns_verified_coverage_before_external_observation(database,monkeypatch):
    value = await make_life(database)
    action,_,_ = await add_waiting(value)
    original_payload = {"activity_id":"watch","context":wire(value.context),"sequence":1}
    lower = datetime.now(UTC)+timedelta(minutes=3)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET checkpoint_data=checkpoint_data||%s WHERE id='watch'",
            (document({"reconciliation":{"action_id":action,"data_generation":value.data_generation,
                "reason":"resource_wait","due_at":lower.isoformat(),"observations":1}}),))
        old = await value.jobs.enqueue(connection,JobSpec("life.activity_reconcile",
            f"activity-reconcile:{action}:1",original_payload,subject_id="actor",data_generation=value.data_generation))
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s",(old,))
    monkeypatch.setattr(rbj_activity_wake,"current_job_execution",lambda:{"job_id":old,"native_attempt":1,
        "data_generation":value.data_generation})
    value.jobs.adopt_wake_on = AsyncMock()
    async def cover(_connection,job_id,**kwargs):
        assert job_id == old and kwargs["expected_payload"] == original_payload
        assert kwargs["expected_covering_payload"] == {"activity_id":"watch","action_id":action,
            "data_generation":value.data_generation}
        assert kwargs["not_before"] == lower
        return {"state":"covered","reason":"domain_covered","job_id":old,
            "covered_by":kwargs["covered_by_job_id"],"due_at":lower}
    value.jobs.cover_wake_on = AsyncMock(side_effect=cover)
    value.actions.get = AsyncMock(side_effect=AssertionError("must exit before Action lookup"))
    result = await value.worker.reconcile_activity(**original_payload)
    assert result["state"] == "covered" and result["job_id"] == old and result["covered_by"] != old
    value.jobs.cover_wake_on.assert_awaited_once()
    value.actions.get.assert_not_awaited()
    current = await activity(value)
    assert current["action_id"] == action and current["checkpoint_data"]["reconciliation"]["not_before"] == lower.isoformat()


async def test_unverified_native_payload_cannot_cover_a_different_original(database,monkeypatch):
    value = await make_life(database)
    action,_,_ = await add_waiting(value)
    original_payload = {"activity_id":"watch","context":wire(value.context),"sequence":1}
    async with database.transaction() as connection:
        old = await value.jobs.enqueue(connection,JobSpec("life.activity_reconcile",
            "activity-reconcile:other-action:1",original_payload,subject_id="actor",data_generation=value.data_generation))
        await connection.execute("UPDATE procrastinate_jobs SET status='doing' WHERE id=%s",(old,))
    monkeypatch.setattr(rbj_activity_wake,"current_job_execution",lambda:{"job_id":old,"native_attempt":1,
        "data_generation":value.data_generation})
    value.jobs.cover_wake_on = AsyncMock()
    value.actions.get = AsyncMock()
    outcome = await value.worker.reconcile_activity(**original_payload)
    assert outcome == {"state":"blocked","reason":"original_wake_unverified"}
    value.jobs.cover_wake_on.assert_not_awaited()
    value.actions.get.assert_not_awaited()
    assert (await activity(value))["action_id"] == action


async def test_historical_scan_rotates_past_a_full_page(database):
    value = await make_life(database)
    action,_,_ = await add_waiting(value)
    async with database.transaction() as connection:
        for sequence in range(35):
            original_payload = {"activity_id":"watch","context":wire(value.context),"sequence":sequence}
            await value.jobs.enqueue(connection,JobSpec("life.activity_reconcile",
                f"activity-reconcile:{action}:{sequence}",original_payload,
                subject_id="actor",data_generation=value.data_generation))
        row = await (await connection.execute("SELECT * FROM life.activities WHERE id='watch'")).fetchone()
        first,cursor = await rbj_activity_wake.scan_on(connection,row,value.data_generation,{})
        second,next_cursor = await rbj_activity_wake.scan_on(connection,row,value.data_generation,
            {"history_cursor":cursor})
    assert len(first) == 32 and len(second) == 3 and cursor == first[-1]["id"] and next_cursor == 0
    assert {item["id"] for item in first}.isdisjoint({item["id"] for item in second})
