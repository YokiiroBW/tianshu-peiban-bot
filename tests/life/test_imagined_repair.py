"""Finite repair keeps the original completion and only adds a current ACTOR original."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.codec import document, source_refs
from tianshu.memory.types import Episode
from tianshu.runtime.api import Page
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, SourceRef
from test_planner_execution import setup


class Memory:
    """A transactional public-port fixture; no production Memory tables are read."""
    def __init__(self, database, episode):
        self.database, self.original = database, episode
        self.writes = 0
        self.drift_on_write = False

    def episode(self, body):
        return replace(self.original, revision=body["revision"], event_id=body["event_id"],
            summary=body["summary"], source_refs=source_refs(body["source_refs"]), actual_result=body["actual_result"])

    async def episodes_by_refs(self, _context, refs, *, connection=None):
        assert connection is not None
        row = await (await connection.execute("SELECT body FROM life.repair_fixture_episode")).fetchone()
        item = self.episode(row["body"])
        found = (item,) if (item.episode_id, item.revision) in refs else ()
        return Page(found, None, Coverage(bool(found), missing=() if found else ("episode_revised",)))

    async def observe_episode(self, _context, value, *, connection=None):
        assert connection is not None
        row = await (await connection.execute("SELECT body FROM life.repair_fixture_episode FOR UPDATE")).fetchone()
        if self.drift_on_write or value["revision"] <= row["body"]["revision"]:
            raise AppError("episode_revision_conflict", status=409)
        await connection.execute("UPDATE life.repair_fixture_episode SET body=%s", (document(value),))
        self.writes += 1
        return self.episode(wire(value))


async def fixture(database):
    value = await setup(database, '{}')
    occurred = datetime.now(UTC) - timedelta(days=1)
    original = Episode("legacy-episode", "fiction:completed:1", 1, "actor", OriginKind.ACTOR,
        occurred, "整理书架", (SourceRef("plan", 1),), ("actor-scope",),
        {"representation": "fictional_world", "software_action": False}, Coverage(True), 1, "e:1")
    async with database.transaction() as connection:
        await connection.execute("CREATE TABLE life.repair_fixture_episode(body jsonb NOT NULL)")
        await connection.execute("CREATE TABLE life.repair_fixture_sources(body jsonb NOT NULL,recorded_at timestamptz NOT NULL DEFAULT now())")
        await connection.execute("INSERT INTO life.repair_fixture_episode VALUES(%s)", (document(original),))
        await connection.execute(
            "INSERT INTO life.activities(id,actor_id,title,kind,state,revision,checkpoint_step,source_refs,scope_ids) "
            "VALUES('completed','actor','整理书架','room','completed',2,1,%s,ARRAY['actor-scope'])",
            (document((SourceRef("plan", 1),)),))
    value.worker.life.memory = Memory(database, original)

    async def record_event(_context, **event):
        await event["connection"].execute("INSERT INTO life.repair_fixture_sources(body) VALUES(%s)",
            (document({key: entry for key, entry in event.items() if key != "connection"}),))
        return (SourceRef("actor-completion", 1),)

    value.worker.life.sources.record_event = AsyncMock(side_effect=record_event)
    return value, original


async def counts(database):
    async with database.transaction(read_only=True) as connection:
        sources = (await (await connection.execute("SELECT count(*) AS n FROM life.repair_fixture_sources")).fetchone())["n"]
        activity = await (await connection.execute("SELECT state,revision,checkpoint_step FROM life.activities WHERE id='completed'")).fetchone()
        episode = await (await connection.execute("SELECT body FROM life.repair_fixture_episode")).fetchone()
    return sources, activity, episode["body"]


@pytest.mark.asyncio
async def test_repair_is_currently_recorded_original_time_preserving_and_idempotent(database):
    value, original = await fixture(database)
    life = value.worker.life
    first = await life.repair_completed_imagined_episode(value.context, "completed", original.episode_id, 1, 2)
    again = await life.repair_completed_imagined_episode(value.context, "completed", original.episode_id, 1, 2)
    assert first == again
    assert first.data["episode_id"] == original.episode_id and first.data["event_id"] == original.event_id
    assert first.revision == first.data["episode_revision"] == 2
    assert first.data["occurred_at"] == original.occurred_at.isoformat()
    assert datetime.fromisoformat(first.data["recorded_at"]) > original.occurred_at
    event = life.sources.record_event.call_args.kwargs
    assert event["occurred_at"] == original.occurred_at and event["origin_kind"] == OriginKind.ACTOR
    assert event["support_refs"] == original.source_refs
    n, activity, repaired = await counts(database)
    assert n == life.memory.writes == 1
    assert activity == {"state": "completed", "revision": 2, "checkpoint_step": 1}
    assert repaired["actual_result"]["imagined"] is True
    assert repaired["actual_result"]["software_action"] is False
    assert "execution" not in repaired["actual_result"]
    assert {ref["source_id"] for ref in first.data["source_refs"]} == {"plan", "actor-completion"}


@pytest.mark.asyncio
@pytest.mark.parametrize("change,error", [
    ("activity_revision", "revision_conflict"), ("event_id", "imagined_episode_activity_mismatch"),
    ("action", "imagined_activity_not_repairable"), ("not_imagined", "imagined_episode_not_repairable"),
    ("scope", "activity_not_found"), ("episode_revision", "imagined_episode_unavailable"),
    ("checkpoint_revision", "imagined_activity_checkpoint_changed"),
])
async def test_repair_refuses_unrelated_or_drifting_material_before_a_source_write(database, change, error):
    value, original = await fixture(database)
    async with database.transaction() as connection:
        if change in {"activity_revision", "checkpoint_revision"}:
            await connection.execute("UPDATE life.activities SET revision=3 WHERE id='completed'")
        elif change == "action":
            await connection.execute("UPDATE life.activities SET action_id='real-action' WHERE id='completed'")
        elif change == "scope":
            await connection.execute("UPDATE life.activities SET scope_ids=ARRAY['another-scope'] WHERE id='completed'")
        else:
            key, body = (("event_id", "fiction:unrelated:1") if change == "event_id" else
                         ("revision", 2) if change == "episode_revision" else ("actual_result", {"software_action": True}))
            await connection.execute("UPDATE life.repair_fixture_episode SET body=body||%s", (document({key: body}),))
    with pytest.raises(AppError) as refused:
        await value.worker.life.repair_completed_imagined_episode(value.context, "completed", original.episode_id, 1,
            3 if change == "checkpoint_revision" else 2)
    assert refused.value.code == error
    assert (await counts(database))[0] == 0
    value.worker.life.sources.record_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_episode_race_rolls_back_new_source_and_command(database):
    value, original = await fixture(database)
    value.worker.life.memory.drift_on_write = True
    with pytest.raises(AppError, match="episode_revision_conflict"):
        await value.worker.life.repair_completed_imagined_episode(value.context, "completed", original.episode_id, 1, 2)
    n, _activity, episode = await counts(database)
    assert n == 0 and episode["revision"] == 1
    async with database.transaction(read_only=True) as connection:
        assert not await (await connection.execute("SELECT 1 FROM runtime.commands WHERE operation='life.imagined.repair'")).fetchone()


@pytest.mark.asyncio
async def test_repair_replay_rechecks_current_source_authorization(database):
    value, original = await fixture(database)
    life = value.worker.life
    await life.repair_completed_imagined_episode(value.context, "completed", original.episode_id, 1, 2)
    life.sources.validate_persistence = AsyncMock(return_value=False)
    with pytest.raises(AppError, match="source_unavailable"):
        await life.repair_completed_imagined_episode(value.context, "completed", original.episode_id, 1, 2)
    assert (await counts(database))[0] == 1


@pytest.mark.asyncio
async def test_repair_requires_the_existing_life_management_permission(database):
    value, original = await fixture(database)
    with pytest.raises(AppError, match="forbidden"):
        await value.worker.life.repair_completed_imagined_episode(
            replace(value.context, permissions=frozenset({"life.read"})), "completed", original.episode_id, 1, 2)
    value.worker.life.sources.record_event.assert_not_awaited()
    assert value.worker.life.memory.writes == 0
