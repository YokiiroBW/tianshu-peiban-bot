"""Recovery observes the exact authorized context accepted for the execution."""
from dataclasses import replace
from importlib.resources import files

import pytest

from tianshu.capabilities.actions import Actions
from tianshu.capabilities.catalog import Catalog
from tianshu.capabilities.contracts import Fault
from tianshu.conversation.codec import document
from tianshu.conversation.types import InputPart
from tianshu.life.worker import LifeWorker
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, Receipt
from test_activity_continuation import NOW, fixed_time
from test_goal_source_transaction import source_life
from test_planner_execution import ReadPage


async def grant_extra_scope(value, identifier):
    async with value.database.transaction() as connection:
        await connection.execute("INSERT INTO identity.scopes(scope_id,actor_id,kind,label) "
            "VALUES (%s,%s,'project','Synthetic newly granted project scope')",
            (identifier, value.context.actor_id))
        await connection.execute("INSERT INTO identity.scope_grants VALUES (%s,'owner',ARRAY['*'])", (identifier,))


async def prepare_resume(value, *, reading):
    await migrate(value.database, (Migration("0035_life_planning_recovery",
        files("tianshu.life").joinpath("planning_recovery.sql").read_text()),))
    original = value.context
    refs = await value.sources.record_event(original, producer="test.life-context", event_id="original-checkpoint",
        revision=1, occurred_at=NOW, origin_kind=OriginKind.TOOL_RESULT,
        parts=(InputPart("text", "An original checkpoint with one unread selected candidate."),))
    async with value.database.transaction() as connection:
        original_intake = await value.identity.intake_scope(original, connection=connection)
    # Expanding read authorization must not also redirect source intake into a
    # second private domain for the same person. The original support stays private.
    await grant_extra_scope(value, "accepted-extra-scope")
    current = await value.identity.context("owner", actor_id=original.actor_id)
    assert set(original.scope_ids) < set(current.scope_ids)
    async with value.database.transaction() as connection:
        assert await value.identity.intake_scope(current, connection=connection) == original_intake
    assert (await value.identity.check_many(current, (refs,)))[0]
    extra_only = replace(current, scope_ids=("accepted-extra-scope",), audience=("accepted-extra-scope",))
    assert not (await value.identity.check_many(extra_only, (refs,)))[0]
    selected = {"capability_id": "content.read", "arguments": {"url": "https://example.test/selected"}}
    remaining = {"capability_id": "content.read", "arguments": {"url": "https://example.test/remaining"},
        "content_key": "remaining"}
    data = {"capability_id": "content.discover" if reading else None, "arguments": {}, "duration_minutes": 20}
    if reading:
        data.update(completed_action_id="original-discovery", last_execution="succeeded", continuation=selected,
            exploration={"next_actions": [{**selected, "content_key": "selected"}, remaining]})
    async with value.database.transaction() as connection:
        await connection.execute("INSERT INTO life.activities(id,actor_id,title,kind,state,waiting_reason,resume_condition,"
            "action_id,checkpoint_data,source_refs,scope_ids,context) VALUES ('resumed-context',%s,'继续原活动',%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (original.actor_id, "research" if reading else "rest", "dormant" if reading else "paused",
             "continuation_available" if reading else "paused_by_user", "continue_from_checkpoint" if reading else None,
             "original-discovery" if reading else None, document(data), document(refs), list(original.scope_ids), document(original)))
    catalog = Catalog()
    catalog.register(ReadPage())
    value.worker.catalog = catalog
    value.life.jobs.register("life.activity_step", value.worker.activity_step)
    value.life.jobs.register("life.activity_reconcile", value.worker.reconcile_activity)
    await value.life.transition_activity(current, {"id": "resumed-context", "expected_revision": 1,
        "client_request_id": "resume-with-authorized-web-scope"}, "running")
    await value.worker.activity_step("resumed-context", 2, wire(current))
    return original, current, catalog


async def activity(value):
    async with value.database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT * FROM life.activities WHERE id='resumed-context'")).fetchone()


async def recovery_job(value):
    async with value.database.transaction(read_only=True) as connection:
        return await (await connection.execute("SELECT k.task,j.args FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
            "WHERE k.key LIKE 'life-recover:activities:resumed-context:%%' "
            "OR (k.task='life.activity_reconcile' AND j.args->>'activity_id'='resumed-context' "
            "AND j.args ? 'data_generation') ORDER BY j.id DESC LIMIT 1")).fetchone()


@pytest.mark.asyncio
async def test_explicit_resume_freezes_its_authorized_context_for_restart_reconciliation_without_replaying(source_life):
    value = source_life
    await migrate(value.database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),))
    catalog = Catalog()
    catalog.register(ReadPage())
    actions = Actions(value.database, value.life.jobs, catalog, value.identity)
    actions.sources = value.sources
    actions.register_tasks()
    value.worker.actions = actions
    original, current, catalog = await prepare_resume(value, reading=True)
    started = await activity(value)
    frozen = started["checkpoint_data"]["frozen_action"]
    saved = await actions.get(started["action_id"], current)
    assert started["context"] == frozen["context"] == saved["action"]["context"]
    assert set(started["scope_ids"]) == set(original.scope_ids)
    assert set(started["context"]["scope_ids"]) == set(current.scope_ids)
    with pytest.raises(Fault, match="forbidden"):
        await actions.get(started["action_id"], original)

    failure = Receipt("stored-timeout", started["action_id"], saved["attempt"]["attempt_id"],
        "cancelled", "none", "not_requested", result={"source_refs": []}, error_code="capability_timeout")
    # The original terminal result was already observed before this restart.
    async with value.database.transaction() as connection:
        await connection.execute("UPDATE capabilities.actions SET receipt=%s,result_revision=1,result_observed_at=%s WHERE id=%s",
            (document(failure), NOW, started["action_id"]))
        await connection.execute("UPDATE life.activities SET state='dormant',waiting_reason='capability_timeout',"
            "resume_condition='review_failure',last_result_revision=1,last_receipt_id=%s WHERE id='resumed-context'",
            (failure.receipt_id,))
    await grant_extra_scope(value, "later-unaccepted-scope")
    observed = []
    async def observe(current_context, episode, *, connection):
        assert await value.sources.validate_persistence(connection, current_context, episode["source_refs"])
        observed.append(episode)
    value.life.memory.observe_episode = observe
    restarted = LifeWorker(value.life, None, None, catalog, actions, None)
    await restarted.recover()
    recovered = await recovery_job(value)
    assert recovered["task"] == "life.activity_reconcile"
    assert recovered["args"]["action_id"] == started["action_id"]
    assert set(recovered["args"]) == {"activity_id","action_id","data_generation"}
    assert set(started["context"]["scope_ids"]) == set(current.scope_ids)
    await restarted.reconcile_activity(**recovered["args"])
    await restarted.reconcile_activity(**recovered["args"])
    settled = await activity(value)
    assert settled["checkpoint_data"]["resolved_action_id"] == started["action_id"]
    assert settled["checkpoint_data"]["completed_action_id"] == "original-discovery"
    assert settled["last_result_revision"] == 1 and settled["last_receipt_id"] == failure.receipt_id
    assert len(observed) == 1 and observed[0]["actual_result"]["derived_kind"] == "activity_read_skip"
    assert (await actions.get(started["action_id"], current))["attempt"] == saved["attempt"]
    async with value.database.transaction(read_only=True) as connection:
        count = await (await connection.execute("SELECT count(*) AS actions FROM capabilities.actions")).fetchone()
        executions = await (await connection.execute("SELECT count(*) AS jobs FROM runtime.job_keys WHERE task='capabilities.execute'")).fetchone()
    assert count["actions"] == executions["jobs"] == 1
    await actions.close()


@pytest.mark.asyncio
async def test_imagined_resume_persists_the_same_context_as_its_completion_timer_and_recovery(source_life):
    value = source_life
    original, current, catalog = await prepare_resume(value, reading=False)
    started = await activity(value)
    async with value.database.transaction(read_only=True) as connection:
        completion = await (await connection.execute("SELECT j.args FROM runtime.job_keys k JOIN procrastinate_jobs j ON j.id=k.job_id "
            "WHERE k.key='activity-fiction:resumed-context:2'")).fetchone()
    assert started["state"] == "running" and started["waiting_reason"] == "fictional_activity"
    assert started["context"] == completion["args"]["context"]
    assert set(started["context"]["scope_ids"]) == set(current.scope_ids)
    assert set(started["scope_ids"]) == set(original.scope_ids)
    restarted = LifeWorker(value.life, None, None, catalog, None, None)
    await restarted.recover()
    recovered = await recovery_job(value)
    assert recovered["task"] == "life.activity_step" and recovered["args"]["context"] == started["context"]
    assert started["action_id"] is None and not started["checkpoint_data"].get("frozen_action")
