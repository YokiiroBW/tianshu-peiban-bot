"""Native PG stories for timer reauthorization and failed-clock continuation.

No model, channel, worker consumer or external provider runs. Actual Identity,
Sources and Procrastinate rows decide whether a new planning signal is permitted.
"""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files

import pytest

from tianshu.conversation.types import InputPart
from tianshu.life.clock import LifeClock
from tianshu.runtime.commands import CommandStore
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobQueue
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind


pytestmark = pytest.mark.asyncio


async def build_clock(core):
    await migrate(core.database, (
        Migration("0031_life", files("tianshu.life").joinpath("schema.sql").read_text()),
        Migration("0035_life_planning_recovery", files("tianshu.life").joinpath("planning_recovery.sql").read_text()),
    ))
    jobs = JobQueue(core.database.config.dsn)
    clock = LifeClock(core.database, CommandStore(core.database), jobs, core.identity, core.sources)

    async def unused_plan(**_kwargs):
        raise AssertionError("This clock story never executes a model/planner")

    jobs.register("life.clock", clock.tick, retryable=True)
    jobs.register("life.plan", unused_plan)
    return clock


async def make_clock(core, context):
    clock = await build_clock(core)
    now = datetime.now(UTC)
    await clock.save(context, {
        "client_request_id": "author-routine", "expected_revision": None,
        "enabled": True, "timezone": "UTC",
        "wake_at": (now - timedelta(hours=1)).strftime("%H:%M"),
        "sleep_at": (now + timedelta(hours=1)).strftime("%H:%M"),
        "idle_seconds": 5400,
    })
    return clock


async def test_actor_activation_starts_a_clock_and_revalidates_a_delayed_activation(core):
    clock = await build_clock(core)
    original = await core.identity.context(core.owner.principal_id, actor_id=core.actor)
    await clock.activate(core.actor, wire(original))
    initial = await routine(core)
    assert initial["enabled"] and initial["revision"] == 1
    assert initial["wake_at"] == "08:00" and initial["sleep_at"] == "23:00"
    assert len(await job_rows(core, "life.clock")) == 1
    assert await job_rows(core, "life.plan") == []
    owner = await core.identity.context(core.owner.principal_id)
    async with core.database.transaction() as conn:
        other = await core.identity.register_actor(owner, connection=conn, enabled=False)
        await core.identity.set_actor_enabled(owner, other, True, connection=conn)
    # This activation payload was queued before the unrelated role changed the
    # principal's epoch. It must validate current grants, not fail on old epoch.
    await clock.activate(core.actor, wire(original))
    renewed = await routine(core)
    assert renewed["context"]["access_epoch"] > original.access_epoch
    assert renewed["next_due_at"] == initial["next_due_at"]
    assert renewed["revision"] == initial["revision"]
    assert renewed["context"]["scope_ids"] == list(original.scope_ids)


async def routine(core):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute(
            "SELECT * FROM life.routines WHERE actor_id=%s", (core.actor,),
        )).fetchone()


async def job_rows(core, task):
    async with core.database.transaction(read_only=True) as conn:
        return await (await conn.execute(
            "SELECT j.id,j.status,j.args FROM procrastinate_jobs j "
            "JOIN runtime.job_keys k ON k.job_id=j.id WHERE k.task=%s ORDER BY j.id", (task,),
        )).fetchall()


async def test_password_epoch_change_resumes_failed_clock_without_replaying_a_boundary(core):
    original = await core.identity.context(core.owner.principal_id, actor_id=core.actor)
    clock = await make_clock(core, original)
    before = await routine(core)
    old_job = (await job_rows(core, "life.clock"))[0]
    # The original production failure is a terminal Procrastinate clock job whose
    # runtime.job_keys entry remains. Do not erase that execution history.
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE procrastinate_jobs SET status='failed' WHERE id=%s", (old_job["id"],))
    await core.management.execute(core.owner, "principals.save", {
        "client_request_id": "change-author-password", "expected_revision": 1,
        "principal_id": core.owner.principal_id, "display_name": "新的作者名称",
        "permissions": ["*"], "enabled": True, "password": "new-isolated-password",
    })
    with pytest.raises(AppError, match="access_changed"):
        await core.identity.check(original)

    assert await clock.recover() == {"resumed": 1, "blocked": 0}
    recovered = await routine(core)
    fresh = await core.identity.context(core.owner.principal_id, actor_id=core.actor)
    assert recovered["context"]["access_epoch"] == fresh.access_epoch
    assert recovered["context"]["scope_ids"] == list(original.scope_ids)
    assert recovered["context"]["origin_kind"] == "timer"
    assert recovered["next_due_at"] == before["next_due_at"]
    clocks = await job_rows(core, "life.clock")
    assert len(clocks) == 2 and clocks[0]["status"] == "failed"

    # Two deliveries of the same pure timer boundary can advance/plan only once.
    await asyncio.gather(*(
        clock.tick(core.actor, recovered["revision"], recovered["next_due_at"].isoformat())
        for _ in range(2)
    ))
    after = await routine(core)
    assert after["last_tick_at"] is not None
    assert after["next_due_at"] > after["last_tick_at"]
    plans = await job_rows(core, "life.plan")
    assert len(plans) == 1
    assert plans[0]["args"]["context"]["access_epoch"] == fresh.access_epoch
    assert plans[0]["args"]["context"]["scope_ids"] == list(original.scope_ids)

    # An unrelated role's enable also changes the same author's access epoch.
    # A delayed old boundary remains a no-op, rather than rerunning business work.
    owner = await core.identity.context(core.owner.principal_id)
    async with core.database.transaction() as conn:
        other = await core.identity.register_actor(owner, connection=conn, enabled=False)
        await core.identity.set_actor_enabled(owner, other, True, connection=conn)
    assert await clock.recover() == {"resumed": 1, "blocked": 0}
    latest = await routine(core)
    assert latest["context"]["access_epoch"] > recovered["context"]["access_epoch"]
    assert latest["next_due_at"] == after["next_due_at"]
    await clock.tick(core.actor, recovered["revision"], recovered["next_due_at"].isoformat())
    assert len(await job_rows(core, "life.plan")) == 1


@pytest.mark.parametrize("revocation", ["actor", "principal", "scope", "permission"])
async def test_timer_does_not_refresh_past_actual_revocation(core, ordinary_users, revocation):
    original = ordinary_users[0]
    clock = await make_clock(core, original)
    before = await routine(core)
    queued = len(await job_rows(core, "life.clock"))
    owner = await core.identity.context(core.owner.principal_id)
    if revocation == "actor":
        async with core.database.transaction() as conn:
            await core.identity.set_actor_enabled(owner, core.actor, False, connection=conn)
    elif revocation == "principal":
        await core.management.execute(owner, "principals.save", {
            "client_request_id": "disable-routine-author", "expected_revision": 1,
            "principal_id": original.principal_id, "display_name": "alice",
            "permissions": sorted(original.permissions), "enabled": False,
        })
    elif revocation == "scope":
        async with core.database.transaction(read_only=True) as conn:
            scope = await (await conn.execute(
                "SELECT scope_id,access_epoch FROM identity.scopes WHERE actor_id=%s AND kind='public'",
                (core.actor,),
            )).fetchone()
        await core.management.execute(owner, "scopes.grant", {
            "client_request_id": "revoke-routine-scope", "expected_revision": scope["access_epoch"],
            "scope_id": scope["scope_id"], "principal_id": original.principal_id, "permissions": [],
        })
    else:
        async with core.database.transaction(read_only=True) as conn:
            actor = await (await conn.execute(
                "SELECT access_epoch FROM identity.actors WHERE actor_id=%s", (core.actor,),
            )).fetchone()
        await core.management.execute(owner, "actors.grant", {
            "client_request_id": "remove-life-manage", "expected_revision": actor["access_epoch"],
            "actor_id": core.actor, "principal_id": original.principal_id, "permissions": ["life.read"],
        })

    await clock.tick(core.actor, before["revision"], before["next_due_at"].isoformat())
    assert await clock.recover() == {"resumed": 0, "blocked": 1}
    after = await routine(core)
    assert after["context"] == before["context"]
    assert after["next_due_at"] == before["next_due_at"]
    assert after["last_tick_at"] is None
    assert len(await job_rows(core, "life.clock")) == queued
    assert await job_rows(core, "life.plan") == []
    owner = await core.identity.context(core.owner.principal_id, actor_id=core.actor)
    view = await clock.view(owner)
    assert view["schedule_status"] == "blocked"
    assert view["schedule_reason"]


async def test_timer_refresh_preserves_fixed_scene_and_original_grant_ceiling(core):
    context = await core.identity.context(core.owner.principal_id, actor_id=core.actor)
    opened = await core.identity.open_web_conversation(context, core.actor, client_request_id="clock-web")
    scene = await core.identity.context(
        core.owner.principal_id, actor_id=core.actor, conversation_id=opened.data["conversation_id"],
    )
    original = replace(scene, permissions=frozenset({"life.manage"}), delivery_allowed=False)
    # A newly disclosed author work grants a new publication scope. The old
    # timer must neither acquire this extra scope nor delivery/more permissions.
    await core.sources.disclose(
        scene, producer="clock-test", event_id="author-note", revision=1,
        parts=(InputPart("text", text="隔离测试中的作者原作"),), source_refs=(),
        conversation_ids=(scene.conversation_id,),
    )
    current = await core.identity.context(
        scene.principal_id, actor_id=scene.actor_id, conversation_id=scene.conversation_id,
    )
    assert set(current.scope_ids) > set(original.scope_ids)
    refreshed = await core.identity.refresh_scheduled_context(original, permission="life.manage")
    assert refreshed.principal_id == original.principal_id
    assert refreshed.actor_id == original.actor_id
    assert refreshed.person_id == original.person_id
    assert refreshed.conversation_id == original.conversation_id
    assert refreshed.scope_ids == original.scope_ids
    assert refreshed.audience == original.audience
    assert refreshed.permissions == original.permissions
    assert refreshed.origin_kind == OriginKind.TIMER
    assert refreshed.delivery_allowed is False
