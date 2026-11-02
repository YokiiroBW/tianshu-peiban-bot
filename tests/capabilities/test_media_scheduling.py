"""Timer author refresh, independent failures and immutable admitted Actions."""

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.media.background import Background
from tianshu.capabilities.media.repository import Repository
from tianshu.capabilities.media.scheduling import author_schedule, scan_deadline, scan_due
from tianshu.capabilities.execution_context import CURRENT_ACTION
from tianshu.capabilities.storage import run_repository
from tianshu.identity.service import IdentityService
from tianshu.runtime.errors import AppError
from tianshu.runtime.migrations import Migration, migrate
from tianshu.runtime.serialization import wire
from tianshu.types import Action, OriginKind, Receipt, SourceRef, TrustedContext


def context():
    return TrustedContext("author", "actor", "person", "conversation", ("private",),
                          ("person",), 3, OriginKind.HUMAN, frozenset({"sources.read"}))


class Documents:
    def __init__(self, rows):
        self.rows, self.now = deepcopy(rows), 100

    def clock(self):
        return self.now

    def list(self, _kind):
        return deepcopy(self.rows)

    def record_schedule(self, _kind, identity, previous, observation, *, begin=False, blocked=False, context=None):
        row = next(item for item in self.rows if identity in item.values())
        if (row.get("schedule") or {}) != (previous.get("schedule") or {}):
            return False
        if begin and (row["revision"] != previous["revision"] or row["next_scan_at"] != previous["next_scan_at"]):
            return False
        if not begin and not (previous.get("schedule") or {}).get("action_key"):
            return False
        row.update(schedule=deepcopy(observation))
        if context is not None:
            row["context"] = context
        if blocked:
            row.update(state="paused", code=observation["code"])
        return True


def media_for(kind="book_feed", field="feed_id", *, rows=None, previous=None):
    previous = previous or context()
    current = replace(previous, access_epoch=5, scope_ids=("private", "new-scope"),
                      audience=("person", "new-audience"), permissions=frozenset({"*"}),
                      origin_kind=OriginKind.TIMER)

    @asynccontextmanager
    async def transaction(**_kwargs):
        yield object()

    identity = IdentityService(SimpleNamespace(transaction=transaction), None)
    identity.context_on = AsyncMock(return_value=current)
    identity.check_active_actor = AsyncMock()
    identity.check = AsyncMock()
    admitted = {}

    async def invoke(action, **_kwargs):
        admitted[action.intent_key] = action
        return Receipt("receipt", action.action_id, "attempt", "pending", "none", "not_requested")

    async def find(_capability, key, _observer):
        if key in admitted:
            return {"action": wire(admitted[key]), "receipt": {"execution": "pending", "effect": "none"}}

    async def local(operation, *args, **kwargs):
        return operation(*args, **kwargs)

    docs = Documents(rows or [{field: "first", "state": "active", "revision": 1,
        "next_scan_at": 1, "interval_seconds": 3600, "context": wire(previous), "source_refs": []}])
    actions = SimpleNamespace(invoke=AsyncMock(side_effect=invoke), find=AsyncMock(side_effect=find))
    media = SimpleNamespace(repo=docs, local=local, capabilities=SimpleNamespace(
        authority=identity, actions=actions, catalog=SimpleNamespace(handler=lambda _key:
            SimpleNamespace(definition=SimpleNamespace(permissions=frozenset())))))
    return media, identity, admitted


@pytest.mark.asyncio
async def test_global_admin_feed_refreshes_epoch_without_selecting_or_borrowing_a_role():
    previous = replace(context(), actor_id=None, conversation_id=None, person_id=None,
                       permissions=frozenset({"config.publish"}))
    media, identity, admitted = media_for(previous=previous)
    await scan_due(media, "book_feed", "feed_id", None)
    action = next(iter(admitted.values()))
    assert action.actor_id is None and action.context.actor_id is None
    assert action.context.conversation_id is None and action.context.person_id is None
    assert action.context.access_epoch == 5
    assert action.context.scope_ids == previous.scope_ids and action.context.audience == previous.audience
    assert action.context.permissions == previous.permissions
    identity.check_active_actor.assert_not_awaited()
    assert media.repo.rows[0]["context"]["actor_id"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("loss", ["principal", "scope", "permission"])
async def test_global_admin_feed_stops_when_principal_scope_or_required_permission_is_lost(loss):
    previous = replace(context(), actor_id=None, conversation_id=None,
                       permissions=frozenset({"config.publish"}))
    media, identity, admitted = media_for(previous=previous)
    if loss == "principal":
        identity.context_on.side_effect = AppError("unauthorized", status=401)
    elif loss == "scope":
        identity.context_on.return_value = replace(identity.context_on.return_value, scope_ids=())
    else:
        identity.context_on.return_value = replace(identity.context_on.return_value, permissions=frozenset())
        media.capabilities.catalog.handler = lambda _key: SimpleNamespace(
            definition=SimpleNamespace(permissions=frozenset({"config.publish"})))
    result = await scan_due(media, "book_feed", "feed_id", None)
    assert not admitted and media.repo.rows[0]["state"] == "paused"
    assert result["schedule_observation"]["state"] == "blocked"


@pytest.mark.asyncio
async def test_role_timer_default_still_requires_an_actor():
    previous = replace(context(), actor_id=None, conversation_id=None)
    _, identity, _ = media_for(previous=previous)
    with pytest.raises(AppError) as missing:
        await identity.refresh_scheduled_context(previous, permission=None)
    assert missing.value.code == "scheduled_actor_required"


@pytest.mark.asyncio
@pytest.mark.parametrize(("kind", "field", "capability"), [
    ("subscription", "subscription_id", "media.subscriptions.scan"),
    ("book_subscription", "book_id", "books.subscriptions.scan"),
    ("comic_subscription", "comic_id", "comics.subscriptions.scan"),
    ("music_playlist", "playlist_id", "music.playlists.scan"),
    ("book_feed", "feed_id", "books.feeds.refresh"),
])
async def test_timer_epoch_refresh_preserves_original_authority_ceiling(kind, field, capability):
    media, identity, admitted = media_for(kind, field)
    result = await scan_due(media, kind, field, None)
    action = next(iter(admitted.values()))
    assert action.capability_id == capability
    assert action.context.access_epoch == 5
    assert action.context.scope_ids == context().scope_ids
    assert action.context.audience == context().audience
    assert action.context.permissions == context().permissions
    assert (action.actor_id, action.context.conversation_id) == ("actor", "conversation")
    assert action.origin_kind == action.context.origin_kind == OriginKind.TIMER
    assert identity.context_on.await_args.kwargs["permission"] is None
    identity.check.assert_awaited_once_with(action.context, ())
    assert media.repo.rows[0]["context"]["access_epoch"] == 5
    assert result["schedule_observation"]["scan"]["submitted"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("revoked_source", [False, True])
async def test_real_scope_or_source_loss_blocks_only_its_source(revoked_source):
    first, second = context(), context()
    if not revoked_source:
        first = replace(first, scope_ids=("private", "withdrawn"))
    rows = [{"feed_id": key, "state": "active", "revision": 1, "next_scan_at": 1,
        "interval_seconds": 3600, "context": wire(author),
        "source_refs": wire((SourceRef("revoked", 1),)) if revoked_source and key == "first" else []}
        for key, author in (("first", first), ("second", second))]
    media, identity, admitted = media_for(rows=rows)

    async def check(_context, refs):
        if refs:
            raise AppError("source_unavailable", status=403)

    identity.check.side_effect = check
    result = await scan_due(media, "book_feed", "feed_id", None)
    assert media.repo.rows[0]["state"] == "paused"
    assert media.repo.rows[0]["schedule"]["state"] == "blocked"
    assert [action.arguments["feed_id"] for action in admitted.values()] == ["second"]
    assert result["schedule_observation"]["state"] == "partial"
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.capabilities.actions.invoke.await_count == 1


@pytest.mark.asyncio
async def test_transient_failure_has_durable_backoff_and_retains_possible_admission_identity():
    media, identity, _ = media_for()
    identity.context_on.side_effect = OSError("temporary identity storage outage")
    first = await scan_due(media, "book_feed", "feed_id", None)
    assert first["schedule_observation"]["state"] == "retry_wait"
    original = media.repo.rows[0]["schedule"]["action_key"]
    assert media.repo.rows[0]["next_scan_at"] == 1
    assert scan_deadline(media.repo.rows[0]) == 160
    await scan_due(media, "book_feed", "feed_id", None)
    assert identity.context_on.await_count == 1
    media.repo.now = 160
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.repo.rows[0]["next_scan_at"] == 1
    assert scan_deadline(media.repo.rows[0]) == 280
    assert media.repo.rows[0]["schedule"]["action_key"] == original
    assert media.capabilities.actions.invoke.await_count == 0


@pytest.mark.asyncio
async def test_epoch_change_observes_original_pending_action_without_refreshing_or_resubmitting_it():
    media, identity, admitted = media_for()
    await scan_due(media, "book_feed", "feed_id", None)
    original = deepcopy(next(iter(admitted.values())))
    media.repo.now = 160
    identity.context_on.return_value = replace(identity.context_on.return_value, access_epoch=6)
    result = await scan_due(media, "book_feed", "feed_id", None)
    assert media.capabilities.actions.invoke.await_count == 1
    assert next(iter(admitted.values())) == original
    assert media.repo.rows[0]["context"]["access_epoch"] == 6
    assert result["schedule_observation"]["scan"]["pending"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", ["unknown", "failed", "succeeded"])
async def test_unknown_receipt_pauses_timer_and_is_never_automatically_replayed(execution):
    media, _, admitted = media_for()
    await scan_due(media, "book_feed", "feed_id", None)
    original = deepcopy(next(iter(admitted.values())))
    media.capabilities.actions.find.side_effect = None
    media.capabilities.actions.find.return_value = {"receipt": {"execution": execution, "effect": "unknown"}}
    media.repo.now = 160
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.repo.rows[0]["state"] == "paused"
    assert media.repo.rows[0]["code"] == ("scheduled_action_unknown" if execution == "unknown" else "scheduled_effect_unknown")
    assert media.repo.rows[0]["schedule"]["action_key"] == original.action_id
    assert media.repo.rows[0]["schedule"]["retry_generation"] == 0
    assert media.repo.rows[0]["next_scan_at"] == 1
    media.repo.now = 10000
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.capabilities.actions.invoke.await_count == 1
    assert list(admitted.values()) == [original]


@pytest.mark.asyncio
async def test_each_known_terminal_failure_grants_one_new_action_after_backoff():
    media, _, admitted = media_for()
    receipts = {}

    async def find(_capability, key, _context):
        if key in admitted:
            return {"action": wire(admitted[key]), "receipt": receipts.get(key,
                {"execution": "pending", "effect": "none"})}

    media.capabilities.actions.find.side_effect = find
    await scan_due(media, "book_feed", "feed_id", None)
    original = deepcopy(next(iter(admitted.values())))
    for failure_at, retry_at, generation in ((160, 220, 1), (280, 400, 2)):
        failed_key = next(reversed(admitted))
        receipts[failed_key] = {"execution": "failed", "effect": "none", "error_code": "temporary_scan_failure"}
        media.repo.now = failure_at
        await scan_due(media, "book_feed", "feed_id", None)
        row = media.repo.rows[0]
        assert row["revision"] == 1 and row["next_scan_at"] == 1
        assert row["schedule"]["state"] == "retry_wait"
        assert row["schedule"]["action_key"] is None
        assert row["schedule"]["retry_generation"] == generation
        assert scan_deadline(row) == retry_at
        assert media.capabilities.actions.invoke.await_count == generation
        media.repo.now = retry_at - 1
        await scan_due(media, "book_feed", "feed_id", None)
        assert media.capabilities.actions.invoke.await_count == generation
        media.repo.now = retry_at
        await scan_due(media, "book_feed", "feed_id", None)
        assert media.capabilities.actions.invoke.await_count == generation + 1
        assert len(admitted) == generation + 1
        new_key = next(reversed(admitted))
        assert new_key != failed_key
        assert row["schedule"]["action_key"] == new_key
        assert row["schedule"]["retry_generation"] == generation
        await scan_due(media, "book_feed", "feed_id", None)
        assert media.capabilities.actions.invoke.await_count == generation + 1
    assert admitted[original.intent_key] == original
    assert [call.args[0].intent_key for call in media.capabilities.actions.invoke.await_args_list] == list(admitted)
    final_key = next(reversed(admitted))
    receipts[final_key] = {"execution": "unknown", "effect": "unknown"}
    media.repo.now = 460
    await scan_due(media, "book_feed", "feed_id", None)
    media.repo.now = 10000
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.repo.rows[0]["schedule"]["action_key"] == final_key
    assert media.repo.rows[0]["schedule"]["retry_generation"] == 2
    assert media.repo.rows[0]["state"] == "paused"
    assert media.capabilities.actions.invoke.await_count == 3


def test_explicit_resume_reauthors_a_blocked_timer_without_changing_an_old_action():
    author = replace(context(), access_epoch=5)
    action = Action("resume", "resume", "books.feeds.control", "actor", author,
                    {"action": "resume"}, OriginKind.HUMAN)
    old = {"context": wire(context()), "source_refs": wire((SourceRef("authored-basis", 1),)),
           "schedule": {"state": "blocked"}}
    new = author_schedule("book_feed", old, action)
    assert new["context"] == wire(author) and new["schedule"] == {}
    assert new["source_refs"] == old["source_refs"]
    assert old["context"]["access_epoch"] == 3


@pytest.mark.asyncio
async def test_schedule_observation_preserves_configuration_revision_and_concurrent_author_edits(database):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),))
    runtime = SimpleNamespace(database=database, files=None, jobs=SimpleNamespace(sync_tasks={}))
    repo = Repository("unused", runtime=runtime, clock=lambda: 100)
    old = await run_repository(database, repo.put, "book_feed", "feed", {
        "feed_id": "feed", "state": "active", "next_scan_at": 1, "context": wire(context())})
    observation = {"state": "retry_wait", "action_key": "original-key", "code": "outage", "next_check_at": 160}
    assert await run_repository(database, repo.record_schedule, "book_feed", "feed", old, observation, begin=True)
    updated = await run_repository(database, repo.get, "book_feed", "feed")
    assert updated["revision"] == old["revision"] and updated["next_scan_at"] == 1
    assert (await run_repository(database, repo.maintenance_due, "book_sources"))["due"] == 160
    edit = Action("edit", "edit", "books.feeds.control", "actor", context(),
                  {"action": "resume"}, OriginKind.HUMAN)
    token = CURRENT_ACTION.set(edit)
    try:
        edited = await run_repository(database, repo.put, "book_feed", "feed",
            {**updated, "state": "active", "next_scan_at": 500, "title": "new author title"}, updated["revision"])
    finally:
        CURRENT_ACTION.reset(token)
    assert edited["schedule"] == {}
    assert not await run_repository(database, repo.record_schedule, "book_feed", "feed", updated, observation)
    assert not await run_repository(database, repo.record_schedule, "book_feed", "feed", updated, observation, begin=True)
    assert await run_repository(database, repo.get, "book_feed", "feed") == edited


@pytest.mark.asyncio
@pytest.mark.parametrize("execution", ["succeeded", "unknown"])
async def test_original_receipt_is_reconciled_after_business_revision_and_deadline_advance(database, execution):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),))
    previous = replace(context(), actor_id=None, conversation_id=None, person_id=None)
    media, _, admitted = media_for(previous=previous)
    clock = media.repo
    runtime = SimpleNamespace(database=database, files=None, jobs=SimpleNamespace(sync_tasks={}))
    repo = Repository("unused", runtime=runtime, clock=clock.clock)

    async def local(operation, *args, **kwargs):
        return await run_repository(database, operation, *args, **kwargs)

    media.repo, media.local = repo, local
    await local(repo.put, "book_feed", "first", {"feed_id": "first", "state": "active",
        "next_scan_at": 1, "interval_seconds": 86400, "context": wire(previous), "source_refs": []})
    original_invoke = media.capabilities.actions.invoke.side_effect
    business_due = clock.now + 86400

    async def finish_business_before_dispatch_checkpoint(action, **kwargs):
        pending = await original_invoke(action, **kwargs)
        snapshot = await local(repo.get, "book_feed", "first")
        assert snapshot["schedule"]["state"] == "dispatching"
        assert snapshot["schedule"]["action_key"] == action.action_id
        token = CURRENT_ACTION.set(action)
        try:
            await local(repo.put, "book_feed", "first", {**snapshot, "code": "ready",
                "last_scan_at": clock.now, "next_scan_at": business_due}, snapshot["revision"])
        finally:
            CURRENT_ACTION.reset(token)
        return pending

    media.capabilities.actions.invoke.side_effect = finish_business_before_dispatch_checkpoint
    await scan_due(media, "book_feed", "feed_id", None)
    original = deepcopy(next(iter(admitted.values())))
    submitted = await local(repo.get, "book_feed", "first")
    assert submitted["revision"] == 2 and submitted["next_scan_at"] == business_due
    assert submitted["schedule"]["state"] == "submitted"
    assert submitted["schedule"]["configuration_revision"] == 1
    assert (await local(repo.maintenance_due, "book_sources"))["due"] == 160
    assert scan_deadline(submitted) == 160
    clock.now = 161
    media.capabilities.actions.find.side_effect = None
    media.capabilities.actions.find.return_value = {"receipt": {
        "execution": execution, "effect": "confirmed" if execution == "succeeded" else "unknown"}}
    await scan_due(media, "book_feed", "feed_id", None)
    final = await local(repo.get, "book_feed", "first")
    assert final["revision"] == 2 and final["next_scan_at"] == business_due
    assert final["schedule"]["action_key"] == original.action_id
    assert final["schedule"]["next_check_at"] is None
    assert final["context"]["actor_id"] is None
    assert media.capabilities.actions.invoke.await_count == 1
    assert next(iter(admitted.values())) == original
    if execution == "succeeded":
        assert final["schedule"]["state"] == "completed" and final["state"] == "active"
        assert (await local(repo.maintenance_due, "book_sources"))["due"] == business_due
    else:
        assert final["schedule"]["state"] == "blocked" and final["state"] == "paused"
        assert (await local(repo.maintenance_due, "book_sources"))["due"] is None
        clock.now = business_due + 1
        await scan_due(media, "book_feed", "feed_id", None)
        assert media.capabilities.actions.invoke.await_count == 1


@pytest.mark.asyncio
async def test_restart_after_key_reservation_admits_only_the_original_action():
    previous = replace(context(), actor_id=None, conversation_id=None)
    media, _, _ = media_for(previous=previous)
    media.capabilities.actions.find.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await scan_due(media, "book_feed", "feed_id", None)
    reserved = deepcopy(media.repo.rows)
    assert reserved[0]["schedule"]["state"] == "dispatching"
    media.capabilities.actions.invoke.assert_not_awaited()
    original_key = reserved[0]["schedule"]["action_key"]
    restarted, _, admitted = media_for(rows=reserved, previous=previous)
    restarted.repo.now = 160
    await scan_due(restarted, "book_feed", "feed_id", None)
    assert list(admitted) == [original_key]
    restarted.repo.now = 220
    await scan_due(restarted, "book_feed", "feed_id", None)
    assert restarted.capabilities.actions.invoke.await_count == 1
    assert list(admitted) == [original_key]


@pytest.mark.asyncio
async def test_admitted_action_missing_from_ledger_does_not_get_resubmitted():
    media, _, _ = media_for()
    await scan_due(media, "book_feed", "feed_id", None)
    media.capabilities.actions.find.side_effect = None
    media.capabilities.actions.find.return_value = None
    media.repo.now = 160
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.repo.rows[0]["state"] == "paused"
    assert media.repo.rows[0]["code"] == "scheduled_action_missing"
    assert media.capabilities.actions.invoke.await_count == 1


@pytest.mark.asyncio
async def test_business_snapshot_cannot_restore_a_newer_blocked_schedule(database):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),))
    runtime = SimpleNamespace(database=database, files=None, jobs=SimpleNamespace(sync_tasks={}))
    repo = Repository("unused", runtime=runtime, clock=lambda: 100)
    old = await run_repository(database, repo.put, "book_feed", "feed", {
        "feed_id": "feed", "state": "active", "next_scan_at": 1, "context": wire(context())})
    pending = {"state": "pending", "action_key": "original-key", "code": None, "next_check_at": 160}
    assert await run_repository(database, repo.record_schedule, "book_feed", "feed", old, pending, begin=True)
    snapshot = await run_repository(database, repo.get, "book_feed", "feed")
    blocked = {**pending, "state": "blocked", "code": "scheduled_scope_unavailable", "next_check_at": None}
    assert await run_repository(database, repo.record_schedule, "book_feed", "feed", snapshot, blocked,
                                blocked=True, context=wire(replace(context(), access_epoch=5)))
    action = Action("original-key", "original-key", "books.feeds.refresh", "actor", context(),
                    {"feed_id": "feed"}, OriginKind.TIMER)
    token = CURRENT_ACTION.set(action)
    try:
        final = await run_repository(database, repo.put, "book_feed", "feed",
            {**snapshot, "state": "active", "code": "ready", "next_scan_at": 86500}, snapshot["revision"])
    finally:
        CURRENT_ACTION.reset(token)
    assert final["schedule"] == blocked and final["state"] == "paused"
    assert final["code"] == "scheduled_scope_unavailable" and final["context"]["access_epoch"] == 5


@pytest.mark.asyncio
async def test_maintenance_and_business_wakes_share_the_original_observation_deadline(database, monkeypatch):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),))
    wakes = []
    monkeypatch.setattr("tianshu.capabilities.media.background.wake_sync",
        lambda _runtime, _conn, _name, due=None: wakes.append(due))
    runtime = SimpleNamespace(database=database, files=None)
    repo = Repository("unused", runtime=runtime, clock=lambda: 100)
    cases = [
        ("active", 86500, {"state": "pending", "action_key": "original", "next_check_at": 160}, 160),
        ("paused", 86500, {"state": "pending", "action_key": "original", "next_check_at": 160}, 160),
        ("active", 86500, {"state": "retry_wait", "action_key": None, "next_check_at": 160}, 86500),
        ("active", 1, {"state": "retry_wait", "action_key": None, "next_check_at": 160}, 160),
        ("active", 86500, {"state": "blocked", "action_key": "original", "next_check_at": None}, None),
    ]
    for state, business_due, schedule, expected in cases:
        wakes.clear()
        row = await run_repository(database, repo.put, "book_feed", "feed", {
            "feed_id": "feed", "state": state, "next_scan_at": business_due,
            "context": wire(context()), "schedule": schedule})
        assert scan_deadline(row) == expected
        assert (await run_repository(database, repo.maintenance_due, "book_sources"))["due"] == expected
        assert wakes == ([] if expected is None else [datetime.fromtimestamp(expected, UTC)])


@pytest.mark.asyncio
async def test_startup_recovers_original_receipt_wake_before_saved_future_business_wake(database):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),
        Migration("capabilities-0002-runtime-observations",
        files("tianshu.capabilities").joinpath("runtime_observations.sql").read_text())))
    jobs = SimpleNamespace(tasks={}, sync_tasks={},
        register=lambda key, function: jobs.tasks.update({key: function}), enqueue=AsyncMock())
    runtime = SimpleNamespace(database=database, jobs=jobs, files=None)
    now = datetime.now(UTC)
    observation_due = now.timestamp() - 1
    business_due = now.timestamp() + 86400
    repo = Repository("unused", runtime=runtime)
    source = await run_repository(database, repo.put, "book_feed", "feed", {
        "feed_id": "feed", "state": "active", "next_scan_at": business_due,
        "context": wire(replace(context(), actor_id=None, conversation_id=None)),
        "schedule": {"state": "submitted", "action_key": "original", "next_check_at": observation_due}})
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO capabilities.maintenance(name,owner,lease_until,next_due_at,wake_generation) "
            "VALUES('book_sources','',to_timestamp(0),%s,7)", (now + timedelta(days=1),))

    async def next_due(name):
        return await run_repository(database, repo.maintenance_due, name)

    background = Background(runtime, next_due=next_due)
    background.start("book_sources", AsyncMock())
    await background.activate()
    async with database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT *,now() AS database_now FROM capabilities.maintenance "
            "WHERE name='book_sources'")).fetchone()
    assert row["next_due_at"] == datetime.fromtimestamp(observation_due, UTC)
    assert row["wake_generation"] == 8 and row["next_due_at"] < row["database_now"]
    assert jobs.enqueue.await_count == 1
    assert jobs.enqueue.await_args.args[1].scheduled_at <= row["database_now"]
    assert await run_repository(database, repo.get, "book_feed", "feed") == source
    await background.close()


@pytest.mark.asyncio
async def test_background_persists_partial_source_failure_instead_of_reporting_empty_success(database):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),
        Migration("capabilities-0002-runtime-observations",
        files("tianshu.capabilities").joinpath("runtime_observations.sql").read_text())))
    jobs = SimpleNamespace(tasks={}, register=lambda key, function: jobs.tasks.update({key: function}),
                           enqueue=AsyncMock())
    runtime = SimpleNamespace(database=database, jobs=jobs)
    expected = {"state": "partial", "code": "scheduled_scope_unavailable",
                "scan": {"submitted": 1, "blocked": 1}}
    background = Background(runtime, next_due=AsyncMock(return_value={"known": True, "due": None}))
    background.start("book_sources", AsyncMock(return_value={"schedule_observation": expected}))
    await background.activate()
    await background.run("book_sources", wake_generation=1)
    async with database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT body FROM capabilities.maintenance WHERE name='book_sources'")).fetchone()
    assert row["body"]["state"] == "partial" and row["body"]["scan"] == expected["scan"]
    await background.close()
