"""Timer author refresh, independent failures and immutable admitted Actions."""

from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.media.background import Background
from tianshu.capabilities.media.repository import Repository
from tianshu.capabilities.media.scheduling import author_schedule, scan_due
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

    def record_schedule(self, _kind, identity, previous, observation, *, blocked, context=None):
        row = next(item for item in self.rows if identity in item.values())
        if row["revision"] != previous["revision"] or row["next_scan_at"] != previous["next_scan_at"]:
            return False
        row.update(schedule=observation, next_scan_at=observation["next_check_at"])
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
    assert media.repo.rows[0]["next_scan_at"] == 160
    await scan_due(media, "book_feed", "feed_id", None)
    assert identity.context_on.await_count == 1
    media.repo.now = 160
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.repo.rows[0]["next_scan_at"] == 280
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
async def test_unknown_receipt_pauses_timer_and_is_never_automatically_replayed():
    media, _, _ = media_for()
    await scan_due(media, "book_feed", "feed_id", None)
    media.capabilities.actions.find.side_effect = None
    media.capabilities.actions.find.return_value = {"receipt": {"execution": "unknown", "effect": "unknown"}}
    media.repo.now = 160
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.repo.rows[0]["state"] == "paused"
    assert media.repo.rows[0]["code"] == "scheduled_action_unknown"
    media.repo.now = 10000
    await scan_due(media, "book_feed", "feed_id", None)
    assert media.capabilities.actions.invoke.await_count == 1


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
    observation = {"state": "retry_wait", "code": "outage", "next_check_at": 160}
    assert await run_repository(database, repo.record_schedule, "book_feed", "feed", old, observation)
    updated = await run_repository(database, repo.get, "book_feed", "feed")
    assert updated["revision"] == old["revision"] and updated["next_scan_at"] == 160
    edited = await run_repository(database, repo.put, "book_feed", "feed",
        {**updated, "state": "paused", "title": "new author title"}, updated["revision"])
    assert not await run_repository(database, repo.record_schedule, "book_feed", "feed", updated, observation)
    assert await run_repository(database, repo.get, "book_feed", "feed") == edited


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
