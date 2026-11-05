"""Regressions for coalesced wakes and original planning attempts."""
from dataclasses import replace
import asyncio
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
import json
from unittest.mock import AsyncMock

import pytest

from historical_support import planner_setup
from test_planner_execution import ReadPage, mark
from test_rb_diary_experience import Broker
from tianshu.capabilities.catalog import Availability
from tianshu.life import planner
from tianshu.life.planning_engine import decision_fingerprint
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_from_wire
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, TrustedContext


REST = json.dumps({"activity":None,"arrangements":[],"goal_changes":[],"cancel_activity_ids":[],
    "reason":"rest","free_time_reason":"leave space"})


class ReceiptBroker(Broker):
    async def status(self, attempt_id, *, phase="observe"):
        return self.receipts.get(attempt_id)

    async def reusable_result(self, attempt_id, *, context, expected_request_digest=None,
                              expected_data_generation=None):
        return await self.result(attempt_id,context=context)


async def make_life(database):
    value = await planner_setup(database, REST)
    # This isolated port has no Models database; the migration uses only the
    # original ledger's identity as positive legacy-admission evidence.
    async with database.transaction() as connection:
        await connection.execute("CREATE SCHEMA IF NOT EXISTS models")
        await connection.execute("CREATE TABLE IF NOT EXISTS models.attempts(attempt_id text PRIMARY KEY)")
    await migrate(database,(Migration("rbf_life_planning_wake_test",
        files("tianshu.life").joinpath("rbf_life_planning_wake.sql").read_text()),))
    value.worker.life.sources.prepare_event_writes = AsyncMock()
    broker = ReceiptBroker(value.models.generation_profile)
    value.worker.models = value.worker.generations.models = broker
    value.broker = broker
    return value


async def run_plan(value, revision=1):
    await value.worker.plan("actor",revision,wire(value.context),value.data_generation)


def planning_context():
    return TrustedContext("author","actor",None,None,("scope",),("scope",),1,
        OriginKind.ACTOR,frozenset({"life.manage"}))


def test_only_semantic_capability_changes_change_the_fingerprint():
    definition = ReadPage.definition
    available = Availability(True,True,verified_at="2026-11-05T01:00:00Z")
    later = replace(available,verified_at="2026-11-05T02:00:00Z")
    first = [{"definition":definition,"availability":available,"semantic_availability":available,
        "can_execute":True,"freshness":"current"}]
    expired = [{**first[0],"availability":later,"semantic_availability":later,
        "can_execute":False,"freshness":"expired"}]
    changed = [{**first[0],"semantic_availability":replace(later,configured=False)}]
    data = {"routine":None,"capabilities":[],"commitments":[],"interests":[],"goals":[],
        "local_time":"2026-11-05T12:00:00+00:00","day_plan":[],"unfinished":[]}
    context, actor, day = planning_context(), {"persona_id":None,"persona_revision":None}, date(2026,11,5)
    def fingerprint(choices, events=None):
        return decision_fingerprint(context,actor,data,day,events,capability_semantics=planner.capability_semantics(choices))
    original = fingerprint(first)
    assert fingerprint(expired,{"old-observation":{"kind":"capability-observation","watermark":"old"}}) == original
    assert fingerprint(changed) != original
    assert fingerprint(first) == original  # a real A -> B -> A returns to A
    assert fingerprint(first,{"authored":{"kind":"reconsider","scope_ids":["scope"],"watermark":"new"}}) != original
    assert planner.needs_capability_refresh(expired)
    assert not planner.needs_capability_refresh(first)


@pytest.mark.asyncio
async def test_repeated_notifications_coalesce_to_one_pending_actor_wake(database):
    value = await make_life(database)
    for index in range(5):
        await value.worker.life.signal(value.context,f"capability-observation:{index}")
    current = await mark(database)
    async with database.transaction(read_only=True) as connection:
        jobs = await (await connection.execute(
            "SELECT args FROM procrastinate_jobs WHERE task_name='life.plan' AND status='todo'")).fetchall()
    assert current["revision"] == 6 and current["change"] == {}
    assert len(jobs) == 1 and jobs[0]["args"] == {"actor_id":"actor","revision":None,
        "context":None,"data_generation":value.data_generation}


@pytest.mark.asyncio
async def test_catalog_ready_only_wakes_an_existing_refresh_wait(database):
    value = await make_life(database)
    before = await mark(database)
    assert not await value.worker.life.planning_ready(value.context,("content.read",))
    async with database.transaction() as connection:
        await connection.execute("UPDATE life.plan_watermarks SET reason='planner_capabilities_refresh' WHERE actor_id='actor'")
    assert await value.worker.life.planning_ready(value.context,("content.read",))
    assert await value.worker.life.planning_ready(value.context,("content.read",))
    current = await mark(database)
    async with database.transaction(read_only=True) as connection:
        count = await (await connection.execute(
            "SELECT count(*) AS n FROM procrastinate_jobs WHERE task_name='life.plan' AND status='todo'")).fetchone()
    assert count["n"] == 1 and current["revision"] == before["revision"]
    assert current["change_watermark"] == before["change_watermark"]


@pytest.mark.asyncio
async def test_stale_completion_uses_newest_watermark_and_original_model_result(database):
    value = await make_life(database)
    await run_plan(value)
    original = await mark(database)
    attempt = next(iter(value.broker.requests))
    await value.worker.life.signal(value.context,"capability-observation:second")
    await value.worker.life.signal(value.context,"capability-observation:third")
    await run_plan(value,1)
    assert list(value.broker.requests) == [attempt]
    assert (await mark(database))["generation_key"] == original["generation_key"]
    value.broker.complete(attempt,REST)
    await run_plan(value,1)  # old completion job payload, current row owns the revision
    current = await mark(database)
    assert current["generation_consumed"] and current["generation_key"] == original["generation_key"]
    assert list(value.broker.requests) == [attempt]


@pytest.mark.asyncio
async def test_changed_material_waits_for_original_then_atomically_replaces_it(database):
    value = await make_life(database)
    await run_plan(value)
    original = await mark(database)
    attempt = next(iter(value.broker.requests))
    await value.worker.life.signal(value.context,"reconsider:authored",change={"reason":"new request"})
    await run_plan(value)
    waiting = await mark(database)
    assert waiting["generation_key"] == original["generation_key"]
    assert waiting["reason"] == "planner_refresh_wait" and list(value.broker.requests) == [attempt]
    value.broker.complete(attempt,REST)
    await run_plan(value)
    current = await mark(database)
    assert current["generation_key"] != original["generation_key"] and len(value.broker.requests) == 2
    async with database.transaction(read_only=True) as connection:
        old = await (await connection.execute("SELECT state FROM life.plan_generations WHERE generation_key=%s",
            (original["generation_key"],))).fetchone()
        request = await (await connection.execute("SELECT attempt_id FROM conversation.generation_requests WHERE attempt_id=%s",
            (attempt,))).fetchone()
    assert old["state"] == "superseded" and request["attempt_id"] == attempt


@pytest.mark.asyncio
async def test_claimed_before_rpc_reuses_frozen_identity_even_after_new_material(database):
    value = await make_life(database)
    real_submit = value.broker.submit
    interrupted = {}
    async def stop_before_rpc(request, *, completion=None):
        interrupted.update(request=request,completion=completion)
        raise TimeoutError("before broker admission")
    value.broker.submit = stop_before_rpc
    with pytest.raises(TimeoutError):
        await run_plan(value)
    saved = await mark(database)
    value.broker.submit = real_submit
    await value.worker.life.signal(value.context,"reconsider:after-interruption",change={"reason":"new material"})
    await run_plan(value)
    assert list(value.broker.requests) == [interrupted["request"].attempt_id]
    assert value.broker.requests[interrupted["request"].attempt_id] == interrupted["request"]
    assert value.broker.completions[interrupted["request"].attempt_id] == interrupted["completion"]
    assert (await mark(database))["generation_key"] == saved["generation_key"]


@pytest.mark.asyncio
async def test_lost_ack_and_concurrent_wakes_observe_one_original_admission(database):
    value = await make_life(database)
    real_submit = value.broker.submit
    calls = []
    async def lost_ack(request, *, completion=None):
        calls.append(request.attempt_id)
        await real_submit(request,completion=completion)
        raise TimeoutError("ack lost after registration")
    value.broker.submit = lost_ack
    with pytest.raises(TimeoutError):
        await run_plan(value)
    value.broker.submit = real_submit
    await asyncio.gather(run_plan(value),run_plan(value))
    assert len(calls) == 1 and list(value.broker.requests) == calls
    assert (await mark(database))["reason"] in {"planner_queued","planner_running"}


@pytest.mark.asyncio
async def test_missing_latest_receipt_never_applies_an_earlier_success(database):
    value = await make_life(database)
    await run_plan(value)
    attempt = next(iter(value.broker.requests))
    value.broker.complete(attempt,REST)
    value.broker.status = AsyncMock(side_effect=[value.broker.receipts[attempt],None])
    await run_plan(value)
    current = await mark(database)
    assert not current["generation_consumed"] and current["reason"] == "planner_observation_unavailable"
    async with database.transaction(read_only=True) as connection:
        saved = await (await connection.execute("SELECT count(*) AS n FROM life.plan_generations WHERE state='applied'")).fetchone()
    assert saved["n"] == 0 and list(value.broker.requests) == [attempt]


@pytest.mark.asyncio
async def test_replacement_failure_rolls_back_original_pointer_and_recovery_state(database):
    value = await make_life(database)
    await run_plan(value)
    original = await mark(database)
    attempt = next(iter(value.broker.requests))
    value.broker.complete(attempt,REST)
    await value.worker.life.signal(value.context,"reconsider:changed",change={"reason":"new request"})
    value.worker.generations.prepare = AsyncMock(side_effect=RuntimeError("injected before pointer switch"))
    with pytest.raises(RuntimeError,match="injected"):
        await run_plan(value)
    current = await mark(database)
    async with database.transaction(read_only=True) as connection:
        old = await (await connection.execute("SELECT state,recovery_state FROM life.plan_generations WHERE generation_key=%s",
            (original["generation_key"],))).fetchone()
        count = await (await connection.execute("SELECT count(*) AS n FROM life.plan_generations")).fetchone()
    assert current["generation_key"] == original["generation_key"] and not current["generation_consumed"]
    assert old == {"state":"pending","recovery_state":"reserved"} and count["n"] == 1
    assert list(value.broker.requests) == [attempt]


@pytest.mark.asyncio
async def test_dependency_wait_keeps_watermark_and_does_not_spend_attempt(database):
    value = await make_life(database)
    retry_at = datetime.now(UTC)+timedelta(minutes=4)
    value.worker.life.recovery_policy.reserve_attempt_on = AsyncMock(
        side_effect=RecoveryWait("recovery_dependency_cooling",retry_at,dependency="models:quota:quota"))
    await run_plan(value)
    current = await mark(database)
    assert current["reason"] == "recovery_dependency_cooling" and current["next_due_at"] == retry_at
    assert current["generation_key"] is None and not value.broker.requests
    async with database.transaction(read_only=True) as connection:
        attempts = await (await connection.execute("SELECT count(*) AS n FROM runtime.recovery_attempts")).fetchone()
    assert attempts["n"] == 0


@pytest.mark.asyncio
async def test_uncertain_remote_rollback_is_propagated_without_replacing_a_plan(database):
    value = await make_life(database)
    retry_at = datetime.now(UTC)+timedelta(minutes=4)
    wait = recovery_wait_from_wire({"kind":"recovery_wait","version":1,"reason":"source_lock_set_changed",
        "retry_at":retry_at.isoformat(),"dependency":"sources:lock_set","rollback_confirmed":False})
    value.worker.life.recovery_policy.reserve_attempt_on = AsyncMock(side_effect=wait)
    with pytest.raises(RecoveryWait):
        await run_plan(value)
    current = await mark(database)
    assert current["generation_key"] is None and not value.broker.requests
    assert current["reason"] != "source_lock_set_changed"
