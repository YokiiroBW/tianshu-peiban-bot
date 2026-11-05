"""A retryable read stays on its original Activity and Action."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from test_rbf_planning_recovery import make_life
from tianshu.conversation.codec import document
from tianshu.life.continuation import recoverable_action
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Receipt


def failed_read(*, recovery="resume_material_read", phase="acquisition", outcome="failed", **kwargs):
    return Receipt("old-receipt","read-action","old-attempt","failed","none","not_requested",
        result={"phase":phase,"outcome":outcome,"recovery":recovery,
            "retry_at":(datetime.now(UTC)-timedelta(seconds=1)).isoformat(),
            "material_read":{"phase":"acquisition","method":"http","url":"https://example.test/item"}},
        error_code="read_transport_failed", **kwargs)


def test_only_explicit_no_effect_checkpoint_allows_recovery():
    failed = failed_read()
    assert recoverable_action("content.read",failed) == "resume_material_read"
    assert recoverable_action("content.read",failed_read(outcome="wait")) == "resume_material_read"
    assert recoverable_action("content.read",replace(failed,execution="unknown",effect="unknown")) is None
    assert recoverable_action("content.read",replace(failed,execution="cancelled",error_code="explicit_cancel")) is None
    assert recoverable_action("content.read",failed_read(recovery="none")) is None
    assert recoverable_action("content.read",failed_read(recovery="resume_commit",phase="commit")) == "resume_commit"
    assert recoverable_action("content.discover",failed) is None
    video = replace(failed,result={"phase":"model","outcome":"wait","recovery":"resume_model"})
    assert recoverable_action("video.observe",video) == "resume_model"
    assert recoverable_action("video.observe",replace(video,effect="unknown",execution="unknown")) is None


class ActionLedger:
    def __init__(self, database, original, capability_id):
        self.database, self.original, self.current = database, original, original
        self.capability_id = capability_id
        self.revision = 1
        self.resume_calls = []

    async def get(self, action_id, context):
        assert action_id == self.original.action_id
        return {"action":{"capability_id":self.capability_id},"attempt":{"attempt_id":self.current.attempt_id},
            "receipt":wire(self.current),"result_revision":self.revision,"result_observed_at":datetime.now(UTC)}

    async def resume(self, action_id, context, **kwargs):
        self.resume_calls.append(kwargs)
        guard, activity_id, expected = kwargs["recovery_guard"]
        async with self.database.transaction() as connection:
            if not await guard(connection,activity_id,expected):
                raise AppError("recovery_guard_rejected",status=409)
        assert (kwargs["expected_attempt_id"],kwargs["expected_receipt_id"],kwargs["expected_revision"]) == (
            self.original.attempt_id,self.original.receipt_id,1)
        self.current = Receipt("new-receipt",action_id,"new-attempt","pending","none","not_requested")
        self.revision += 1
        return self.current


async def setup_read(database, *, capability_id="content.read", original=None):
    value = await make_life(database)
    deadline = datetime.now(UTC)+timedelta(minutes=15)
    checkpoint = {"capability_id":capability_id,"arguments":{"url":"https://example.test/item"},
        "frozen_action":{"deadline_at":deadline.isoformat()}}
    async with database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,action_id,scope_ids,context,checkpoint_data) "
            "VALUES ('read','actor','read item','reading','running','read-action',ARRAY['actor-scope'],%s,%s)",
            (document(value.context),document(checkpoint)))
    original = original or failed_read()
    ledger = ActionLedger(database,original,capability_id)
    value.worker.actions = ledger
    value.worker.life.memory = type("Memory",(),{"observe_episode":AsyncMock()})()
    await value.worker._activity_result(value.context,original,1,datetime.now(UTC),capability_id)
    return value,ledger,original


async def activity(database):
    async with database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.activities WHERE id='read'")).fetchone()


@pytest.mark.asyncio
async def test_read_recovery_retains_action_and_uses_original_receipt_fences(database):
    value,ledger,original = await setup_read(database)
    waiting = await activity(database)
    assert waiting["waiting_reason"] == "capability_recovery" and waiting["action_id"] == original.action_id
    assert waiting["last_receipt_id"] == original.receipt_id
    await value.worker.reconcile_activity("read",wire(value.context),0)
    current = await activity(database)
    assert current["action_id"] == original.action_id and current["last_receipt_id"] == "new-receipt"
    assert current["waiting_reason"] == "capability_running" and len(ledger.resume_calls) == 1


@pytest.mark.asyncio
async def test_activity_cancellation_wins_the_lock_inside_action_resume(database):
    value,ledger,original = await setup_read(database)
    stale = await activity(database)
    snapshot = await ledger.get(original.action_id,value.context)
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.activities SET state='cancelled',revision=revision+1 WHERE id='read'")
    await value.worker.resume_capability(value.context,stale,snapshot,original,0)
    assert ledger.current == original and (await activity(database))["state"] == "cancelled"


@pytest.mark.asyncio
async def test_paused_recovery_can_be_explicitly_resumed_without_a_new_action(database):
    value,ledger,original = await setup_read(database)
    waiting = await activity(database)
    await value.worker.life.transition_activity(value.context,{"id":"read","expected_revision":waiting["revision"],
        "client_request_id":"pause-read"},"paused")
    paused = await activity(database)
    await value.worker.life.transition_activity(value.context,{"id":"read","expected_revision":paused["revision"],
        "client_request_id":"resume-read"},"running")
    resumed = await activity(database)
    assert resumed["state"] == "dormant" and resumed["waiting_reason"] == "capability_recovery"
    assert resumed["action_id"] == original.action_id and resumed["last_receipt_id"] == original.receipt_id
    await value.worker.reconcile_activity("read",wire(value.context),0)
    assert len(ledger.resume_calls) == 1 and (await activity(database))["action_id"] == original.action_id


@pytest.mark.asyncio
async def test_video_model_wait_uses_the_same_action_and_original_receipt_fences(database):
    original = replace(failed_read(),result={"phase":"model","outcome":"wait","recovery":"resume_model",
        "retry_at":(datetime.now(UTC)-timedelta(seconds=1)).isoformat()})
    value,ledger,_ = await setup_read(database,capability_id="video.observe",original=original)
    await value.worker.reconcile_activity("read",wire(value.context),0)
    assert len(ledger.resume_calls) == 1
    assert (await activity(database))["action_id"] == original.action_id


@pytest.mark.asyncio
async def test_missing_video_checkpoint_stays_unresolved_without_fabricating_recovery(database):
    original = replace(failed_read(),result={"phase":"model","outcome":"wait","recovery":"resume_model",
        "retry_at":(datetime.now(UTC)-timedelta(seconds=1)).isoformat()})
    value,ledger,_ = await setup_read(database,capability_id="video.observe",original=original)
    ledger.resume = AsyncMock(side_effect=AppError("video_checkpoint_unavailable",status=409))
    await value.worker.reconcile_activity("read",wire(value.context),0)
    current = await activity(database)
    assert current["waiting_reason"] == "video_checkpoint_unavailable" and current["last_receipt_id"] == original.receipt_id
    assert current["checkpoint_data"].get("resolved_action_id") is None
