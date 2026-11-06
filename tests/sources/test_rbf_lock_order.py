"""Source writers share dependencies and prepare a complete ordered lock set."""

import asyncio
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.conversation.types import InputPart
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.sources.archives import lock_selected
from tianshu.sources.module import create_module
from tianshu.sources.service import SourceService
from tianshu.types import OriginKind, SourceRef


_spec = spec_from_file_location(
    "rbf_source_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py"
)
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


def _event(name, support=()):
    return {
        "producer": "rbf-lock-test", "event_id": name, "revision": 1,
        "parts": (InputPart("text", text=name),), "support_refs": tuple(support),
    }


async def _record(core, context, event, connection=None):
    return await core.sources.record_event(
        context, **event, occurred_at=datetime.now(UTC), origin_kind=OriginKind.HUMAN,
        connection=connection,
    )


async def test_different_events_share_support_but_withdrawal_waits(core, ordinary_users):
    alice, _ = ordinary_users
    support = (await _record(core, alice, _event("support")))[0]
    first, second = _event("first", (support,)), _event("second", (support,))
    started = asyncio.Event()

    async def withdraw():
        started.set()
        return await core.sources.invalidate(alice, {
            "source_id": support.source_id, "expected_revision": support.revision,
            "client_request_id": "withdraw-shared-support",
        })

    async with core.database.transaction() as conn:
        await core.sources.prepare_event_write(conn, alice, **first)
        # A different root with the same support can complete while this tx is open.
        second_ref = (await asyncio.wait_for(_record(core, alice, second), 5))[0]
        first_ref = (await _record(core, alice, first, connection=conn))[0]
        pending = asyncio.create_task(withdraw())
        await started.wait()
        assert not (await asyncio.wait({pending}, timeout=0.05))[0]
    await asyncio.wait_for(pending, 5)
    assert await core.sources.check_many(alice, ((first_ref,), (second_ref,))) == (False, False)


async def test_batch_preparation_orders_events_and_reuses_the_caller_connection(core, ordinary_users):
    alice, _ = ordinary_users
    async with core.database.transaction() as conn:
        # This support is uncommitted and is only visible on the supplied connection.
        support = (await _record(core, alice, _event("uncommitted"), connection=conn))[0]
        first, second = _event("batch-a", (support,)), _event("batch-b", (support,))
        aliases = await core.sources.prepare_event_writes(conn, alice, (second, first))
        assert aliases == (None, None)
        first_ref = (await _record(core, alice, first, connection=conn))[0]
        second_ref = (await _record(core, alice, second, connection=conn))[0]
        assert await core.sources.check_many_on(conn, alice, ((first_ref, second_ref),)) == (True,)
    assert await core.sources.check_many(alice, ((first_ref, second_ref),)) == (True,)


async def test_opposite_batch_order_does_not_deadlock(core, ordinary_users):
    alice, _ = ordinary_users
    support = (await _record(core, alice, _event("batch-support")))[0]
    first, second = _event("ordered-a", (support,)), _event("ordered-b", (support,))

    async def write(events):
        async with core.database.transaction() as conn:
            await core.sources.prepare_event_writes(conn, alice, events)
            refs = {}
            for event in events:
                refs[event["event_id"]] = (await _record(core, alice, event, connection=conn))[0]
            return refs

    left, right = await asyncio.wait_for(
        asyncio.gather(write((first, second)), write((second, first))), 5
    )
    assert left == right
    assert await core.sources.check_many(alice, (tuple(left.values()),)) == (True,)


async def test_alias_closure_growth_never_extends_a_partially_locked_set(monkeypatch):
    from tianshu.sources import rbj_locking

    class Cursor:
        def __init__(self, rows):
            self.rows = rows

        async def fetchall(self):
            return self.rows

    class ChangingConnection:
        def __init__(self):
            self.scans = 0
            self.locks = []

        async def execute(self, sql, parameters):
            if "FOR SHARE" in sql:
                self.locks.append(parameters[0])
                return Cursor([])
            raise AssertionError(sql)

    conn = ChangingConnection()

    async def changed(_conn, _refs, _extra, _aliases, _budget):
        conn.scans += 1
        return {"a"} if conn.scans == 1 else {"a", "b"}

    monkeypatch.setattr(rbj_locking, "selected_on", changed)
    with pytest.raises(RecoveryWait) as failure:
        await lock_selected(conn, (SourceRef("a", 1, {}),))
    assert failure.value.reason == "source_lock_set_changed"
    assert failure.value.dependency == "sources:lock_set" and failure.value.retry_at.tzinfo
    assert conn.locks == [["a"]]


async def test_persistence_validation_propagates_the_typed_wait():
    wait = RecoveryWait(
        "source_lock_set_changed", datetime.now(UTC) + timedelta(seconds=5),
        dependency="sources:lock_set",
    )
    identity = SimpleNamespace(lock_context_on=AsyncMock())
    service = SourceService(None, identity, None, None, None)
    service._lock_selected = AsyncMock(side_effect=wait)
    with pytest.raises(RecoveryWait) as failure:
        await service.validate_persistence(object(), object(), (SourceRef("a", 1, {}),))
    assert failure.value is wait


def test_source_lifecycle_jobs_opt_in_to_rolled_back_resource_recovery():
    calls = {}

    class Queue:
        def register(self, name, handler, **options):
            calls[name] = options

    service = SimpleNamespace(
        propagate=AsyncMock(), dispatch_dependant=AsyncMock(), notify_dependant=AsyncMock()
    )
    create_module(service, None).register_tasks(Queue(), None)
    assert set(calls) == {"sources.propagate", "sources.dispatch", "sources.notify"}
    assert all(options["retryable"] and options["resource_recovery"] for options in calls.values())


async def test_same_event_revisions_serialize_before_selecting_removed_parts(core, ordinary_users):
    alice, _ = ordinary_users
    first = _event("revisions")
    first["parts"] = (InputPart("text", text="old-1"), InputPart("text", text="old-2"))
    original = await _record(core, alice, first)
    ready, release = asyncio.Event(), asyncio.Event()

    async def write_second():
        async with core.database.transaction() as conn:
            refs = await _record(core, alice, {
                **first, "revision": 2, "parts": (InputPart("text", text="second"),),
            }, connection=conn)
            ready.set()
            await release.wait()
            return refs

    async def write_third():
        return await _record(core, alice, {
            **first, "revision": 3, "parts": (InputPart("text", text="third"),),
        })

    task1 = asyncio.create_task(write_second())
    await asyncio.wait_for(ready.wait(), 5)
    task2 = asyncio.create_task(write_third())
    try:
        assert not (await asyncio.wait({task2}, timeout=0.05))[0]
    finally:
        release.set()
    _, latest = await asyncio.wait_for(asyncio.gather(task1, task2), 5)
    async with core.database.transaction(read_only=True) as conn:
        removed = await (await conn.execute(
            "SELECT state,head_revision FROM sources.originals WHERE source_id=%s",
            (original[1].source_id,),
        )).fetchone()
    assert removed == {"state": "retracted", "head_revision": 1}
    assert latest[0].revision == 3
    assert await core.sources.check_many(alice, ((latest[0],), (original[1],))) == (True, False)


async def test_failed_disclosure_does_not_leave_audience_changes(core, ordinary_users):
    alice, _ = ordinary_users
    missing = SourceRef("missing-source", 1, {})
    async with core.database.transaction() as conn:
        before = await (await conn.execute(
            "SELECT count(*) AS n FROM identity.scopes WHERE kind='publication'"
        )).fetchone()
        with pytest.raises(AppError, match="source_unavailable"):
            await core.sources.disclose(
                alice, producer="life.diary", event_id="invalid", revision=1,
                parts=(InputPart("text", text="invalid"),), source_refs=(missing,),
                connection=conn,
            )
        after = await (await conn.execute(
            "SELECT count(*) AS n FROM identity.scopes WHERE kind='publication'"
        )).fetchone()
        assert after == before


async def test_existing_publication_scope_serializes_writers_before_sources(core, ordinary_users):
    alice, _ = ordinary_users
    support = (await _record(core, alice, _event("publication-support")))[0]
    args = {
        "producer": "life.diary", "event_id": "same-publication", "revision": 1,
        "parts": (InputPart("text", text="published"),), "source_refs": (support,),
        "conversation_ids": (alice.conversation_id,),
    }
    first_ref = await core.sources.disclose(alice, **args)
    alice = await core.identity.context(
        alice.principal_id, actor_id=alice.actor_id, conversation_id=alice.conversation_id
    )
    assert any(scope.startswith("publication_") for scope in alice.scope_ids)
    acquired, release = asyncio.Event(), asyncio.Event()

    async def first():
        async with core.database.transaction() as conn:
            ref = await core.sources.disclose(alice, connection=conn, **args)
            acquired.set()
            await release.wait()
            return ref

    async def second():
        async with core.database.transaction() as conn:
            return await core.sources.disclose(alice, connection=conn, **args)

    task1 = asyncio.create_task(first())
    await asyncio.wait_for(acquired.wait(), 5)
    task2 = asyncio.create_task(second())
    try:
        assert not (await asyncio.wait({task2}, timeout=0.05))[0]
    finally:
        release.set()
    result1, result2 = await asyncio.wait_for(asyncio.gather(task1, task2), 5)
    assert result1 == result2 == first_ref
