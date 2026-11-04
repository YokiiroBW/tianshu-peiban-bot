"""Media coverage and source sharding use the authoritative domain ledgers."""

import json
from importlib.resources import files
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities.actions import encoded
from tianshu.capabilities.media.statistics import backfill, event_for_job
from tianshu.capabilities.service import Capabilities
from tianshu.capabilities.withdrawal import depend
from tianshu.runtime.migrations import Migration, migrate
from tianshu.types import Action, Attempt, OriginKind, Receipt, SourceRef, TrustedContext


async def migrate_capabilities(database):
    await migrate(database, (Migration("capabilities-0001",
        files("tianshu.capabilities").joinpath("schema.sql").read_text()),))


@pytest.mark.asyncio
async def test_media_backfill_uses_original_job_revision_and_marks_coverage_honestly(database):
    await migrate_capabilities(database)
    jobs = [
        {"job_id": "a", "revision": 4, "state": "completed", "media_kind": "video", "updated_at": 10},
        {"job_id": "b", "revision": 7, "state": "unknown", "media_kind": "book", "updated_at": 11},
        {"job_id": "c", "revision": 2, "state": "auth_required", "media_kind": "audio", "updated_at": 12},
    ]
    async with database.transaction() as conn:
        for job in jobs:
            await conn.execute(
                "INSERT INTO capability_media.jobs(id,dedupe,state,created,updated,body) VALUES(%s,%s,%s,%s,%s,%s)",
                (job["job_id"], job["job_id"], job["state"], 1, job["updated_at"], json.dumps(job)))
    statistics = SimpleNamespace(record_safely_on=AsyncMock(return_value=True), mark_producer_on=AsyncMock())
    first = await backfill(database, statistics, limit=2)
    assert first["complete"] is None and first["next_cursor"]["last_id"] == "b"
    assert not statistics.mark_producer_on.await_count
    final = await backfill(database, statistics, cursor=first["next_cursor"], limit=2)
    assert final["complete"] is True and final["scanned"] == 3
    events = [call.args[1] for call in statistics.record_safely_on.await_args_list]
    assert [(event["event_id"], event["revision"]) for event in events] == [("a", 4), ("b", 7), ("c", 2)]
    assert events[0]["metrics"]["media_completed"] == 1
    assert events[1]["metrics"] == {"media_completed": None, "media_failed": None}
    assert events[2]["dimensions"]["state"] == "blocked" and events[2]["metrics"]["media_failed"] == 0
    assert statistics.mark_producer_on.await_args.kwargs["complete"] is True

    statistics.record_safely_on.return_value = False
    incomplete = await backfill(database, statistics, limit=10)
    assert incomplete["complete"] is False and incomplete["failed"] == 3
    assert statistics.mark_producer_on.await_args.kwargs["complete"] is False
    assert event_for_job({"job_id": "invalid", "revision": 1, "state": "failed"}) is None


@pytest.mark.asyncio
async def test_source_withdrawal_only_invalidates_the_matching_actor_and_is_idempotent(database):
    await migrate_capabilities(database)
    source = SourceRef("source", 3, {"unit": "chars", "start": 0, "end": 20})
    forgotten = SourceRef("source", 3, {"unit": "chars", "start": 3, "end": 6})
    async with database.transaction() as conn:
        for identity, actor in (("a", "actor-a"), ("b", "actor-b"), ("c", None)):
            context = TrustedContext("owner", actor, None, None, ("scope",), ("scope",), 1,
                                     OriginKind.HUMAN, frozenset({"*"}))
            action = Action(identity, identity, "example.write", actor, context, {"text": "private"},
                            OriginKind.HUMAN, source_refs=(source,))
            attempt = Attempt("attempt-" + identity, identity)
            receipt = Receipt("receipt-" + identity, identity, attempt.attempt_id, "succeeded",
                              "confirmed", "not_requested", result={"content": "private"})
            await conn.execute(
                "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,action,attempt,receipt,document) "
                "VALUES(%s,%s,%s,%s,%s,0,%s::jsonb,%s::jsonb,%s::jsonb,'{}')",
                (identity, identity, identity, "owner", action.capability_id,
                 encoded(action), encoded(attempt), encoded(receipt)))
            await depend(conn, "action", identity, (source,))
        owner = SimpleNamespace(runtime=SimpleNamespace(jobs=SimpleNamespace(cancel=AsyncMock())),
                                actions=SimpleNamespace(events=None),
                                projects=SimpleNamespace(withdraw=AsyncMock()))
        assert await Capabilities.actors_for_source(owner, conn, (forgotten,), "fragment_forgotten") == (
            None, "actor-a", "actor-b")
        first = await Capabilities.invalidate_actor(owner, conn, (forgotten,), "fragment_forgotten", actor_id="actor-a")
        again = await Capabilities.invalidate_actor(owner, conn, (forgotten,), "fragment_forgotten", actor_id="actor-a")
        assert first["actions"] == 1 and again["actions"] == 0
        rows = await (await conn.execute("SELECT id,action,receipt,result_revision FROM capabilities.actions ORDER BY id")).fetchall()
        assert rows[0]["action"]["arguments"] == {} and rows[0]["receipt"]["result"]["source_revoked"]
        assert rows[0]["result_revision"] == 1
        assert rows[1]["action"]["arguments"] == {"text": "private"}
        assert rows[2]["action"]["arguments"] == {"text": "private"}
