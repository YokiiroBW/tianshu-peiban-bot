"""Multi-context source preparation preserves authority and lock ordering."""

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from tianshu.conversation.types import InputPart
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
import tianshu.sources.proofs as source_proofs
from tianshu.types import OriginKind, SourceRef


_spec = spec_from_file_location(
    "rbi_source_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py"
)
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


def _event(name, *, revision=1, support=()):
    return {
        "producer": "rbi-scoped-test", "event_id": name, "revision": revision,
        "parts": (InputPart("text", text=f"{name}-{revision}"),),
        "support_refs": tuple(support),
    }


async def _record(core, context, event, *, connection=None):
    return (await core.sources.record_event(
        context, **event, occurred_at=datetime.now(UTC),
        origin_kind=OriginKind.HUMAN, connection=connection,
    ))[0]


async def _shared_goal_source(core, alice, bob):
    common = await core.sources.disclose(
        alice, producer="life.diary", event_id="rbi-shared-goal", revision=1,
        parts=(InputPart("text", text="shared goal"),), source_refs=(),
        reader_ids=(bob.principal_id,),
    )
    # Actor-level contexts retain each user's private scope and the publication.
    alice = await core.identity.context(alice.principal_id, actor_id=alice.actor_id)
    bob = await core.identity.context(bob.principal_id, actor_id=bob.actor_id)
    return alice, bob, common


async def test_scoped_authority_is_independent_and_private_receipt_still_commits(
    core, ordinary_users, monkeypatch
):
    alice, bob, common = await _shared_goal_source(core, *ordinary_users)
    private = await _record(core, alice, _event("private-input"))
    receipt_event = _event("private-receipt", support=(private,))
    checks = []
    check_scoped_on = source_proofs.check_scoped_on

    async def observe_check(identity, connection, requests, *, budget=None):
        results = await check_scoped_on(identity, connection, requests, budget=budget)
        for (context, groups, _), result in zip(requests, results):
            checks.append((context, groups, result))
        return results

    monkeypatch.setattr(source_proofs, "check_scoped_on", observe_check)
    async with core.database.transaction() as conn:
        generation = await core.sources.jobs.current_generation_on(conn)
        await core.sources.jobs.assert_generation_on(conn, generation)
        aliases = await core.sources.prepare_event_writes(
            conn, alice, (receipt_event,), additional_refs=(private, common),
            scoped_refs=((bob, (common,)), (bob, (private,))),
        )
        assert aliases == (None,)
        assert (alice, ((private, common),), (True,)) in checks
        assert (bob, ((common,),), (True,)) in checks
        assert (bob, ((private,),), (False,)) in checks

        # Model the caller's optional Goal decision after a domain-row fence.
        await conn.execute("CREATE TEMP TABLE rbi_goal (updated boolean NOT NULL) ON COMMIT DROP")
        await conn.execute("INSERT INTO rbi_goal VALUES (false)")
        await conn.execute("SELECT updated FROM rbi_goal FOR UPDATE")
        receipt = await _record(core, alice, receipt_event, connection=conn)
        if (await core.sources.check_many_on(conn, bob, ((common, receipt),)))[0]:
            await conn.execute("UPDATE rbi_goal SET updated=true")
        assert (await (await conn.execute("SELECT updated FROM rbi_goal")).fetchone())["updated"] is False
        assert await core.sources.check_many_on(conn, alice, ((receipt,),)) == (True,)
        assert await core.sources.check_many_on(conn, bob, ((receipt,),)) == (False,)
    assert await core.sources.check_many(alice, ((receipt,),)) == (True,)


async def test_scoped_authority_never_satisfies_main_required_inputs(core, ordinary_users):
    alice, bob = ordinary_users
    bob_private = await _record(core, bob, _event("bob-private"))
    async with core.database.transaction() as conn:
        with pytest.raises(AppError, match="source_support_unavailable") as failure:
            await core.sources.prepare_event_writes(
                conn, alice, (), additional_refs=(bob_private,),
                scoped_refs=((bob, (bob_private,)),),
            )
        assert failure.value.status == 403


async def test_empty_scoped_refs_and_empty_events_keep_existing_contract(core, ordinary_users):
    alice, _ = ordinary_users
    support = await _record(core, alice, _event("legacy-support"))
    event = _event("legacy-receipt", support=(support,))
    async with core.database.transaction() as conn:
        assert await core.sources.prepare_event_writes(
            conn, alice, (event,), additional_refs=(support,), scoped_refs=()
        ) == (None,)
        receipt = await _record(core, alice, event, connection=conn)
    async with core.database.transaction() as conn:
        assert await core.sources.prepare_event_writes(
            conn, alice, (), scoped_refs=((alice, (support,)),)
        ) == ()
    assert await core.sources.check_many(alice, ((receipt,),)) == (True,)


async def test_invalid_scoped_context_and_reference_preserve_errors(core, ordinary_users):
    alice, bob = ordinary_users
    stale = replace(bob, access_epoch=bob.access_epoch + 1)
    async with core.database.transaction() as conn:
        with pytest.raises(AppError, match="access_changed") as failure:
            await core.sources.prepare_event_writes(conn, alice, (), scoped_refs=((stale, ()),))
        assert failure.value.status == 409
    async with core.database.transaction() as conn:
        with pytest.raises(AppError, match="invalid_source_reference"):
            await core.sources.prepare_event_writes(
                conn, alice, (), scoped_refs=((bob, (SourceRef("bad", 0, {}),)),)
            )


async def test_scoped_proof_errors_and_typed_wait_are_not_turned_into_denials(
    core, ordinary_users, monkeypatch
):
    alice, bob = ordinary_users
    ref = await _record(core, bob, _event("unavailable-proof"))
    wait = RecoveryWait(
        "source_lock_set_changed", datetime.now(UTC) + timedelta(seconds=5),
        dependency="sources:lock_set",
    )
    for error in (AppError("source_dependency_budget_exceeded", status=503), wait):
        async def fail_proof(_connection, _context, _groups, *, budget=None):
            raise error

        with monkeypatch.context() as patch:
            patch.setattr(source_proofs, "check_scoped_on", fail_proof)
            async with core.database.transaction() as conn:
                with pytest.raises(type(error)) as failure:
                    await core.sources.prepare_event_writes(
                        conn, alice, (), scoped_refs=((bob, (ref,)),)
                    )
                assert failure.value is error


async def test_reversed_scoped_contexts_use_identical_identity_lock_order(
    core, ordinary_users, monkeypatch
):
    alice, bob, common = await _shared_goal_source(core, *ordinary_users)
    private = await _record(core, alice, _event("ordered-private"))
    orders = {}
    ready_count = 0
    both_ready = asyncio.Event()
    lock_context_on = core.identity.lock_context_on

    async def observe_lock(connection, context, **kwargs):
        orders.setdefault(asyncio.current_task(), []).append(context)
        return await lock_context_on(connection, context, **kwargs)

    monkeypatch.setattr(core.identity, "lock_context_on", observe_lock)

    async def prepare(scoped):
        nonlocal ready_count
        async with core.database.transaction() as conn:
            ready_count += 1
            if ready_count == 2:
                both_ready.set()
            await both_ready.wait()
            assert await core.sources.prepare_event_writes(
                conn, alice, (), additional_refs=(private,), scoped_refs=scoped
            ) == ()
            return tuple(orders[asyncio.current_task()])

    scoped = ((alice, (private,)), (bob, (common,)))
    forward, backward = await asyncio.wait_for(
        asyncio.gather(prepare(scoped), prepare(tuple(reversed(scoped)))), 5
    )
    assert forward == backward
    assert set(forward) == {alice, bob}


async def test_scoped_revision_change_waits_until_prepared_transaction_finishes(core, ordinary_users):
    alice, bob = ordinary_users
    original = await _record(core, bob, _event("versioned"))
    started = asyncio.Event()

    async def revise():
        started.set()
        return await _record(core, bob, _event("versioned", revision=2))

    async with core.database.transaction() as conn:
        assert await core.sources.prepare_event_writes(
            conn, alice, (), scoped_refs=((bob, (original,)),)
        ) == ()
        pending = asyncio.create_task(revise())
        await asyncio.wait_for(started.wait(), 5)
        assert not (await asyncio.wait({pending}, timeout=0.05))[0]
        assert await core.sources.check_many_on(conn, bob, ((original,),)) == (True,)
    latest = await asyncio.wait_for(pending, 5)
    assert latest.revision == 2
    assert await core.sources.check_many(bob, ((original,), (latest,))) == (False, True)


async def test_scoped_alias_closure_blocks_concurrent_retraction(core, ordinary_users):
    alice, bob = ordinary_users
    scoped = await _record(core, bob, _event("alias-scoped"))
    related = await _record(core, bob, _event("alias-related"))
    async with core.database.transaction() as conn:
        for ref in (scoped, related):
            await conn.execute(
                "INSERT INTO sources.source_aliases(alias_key,source_id,revision) VALUES(%s,%s,%s)",
                ("rbi-scoped-alias", ref.source_id, ref.revision),
            )
    started = asyncio.Event()

    async def retract():
        started.set()
        return await core.sources.invalidate(bob, {
            "source_id": related.source_id, "expected_revision": related.revision,
            "client_request_id": "rbi-scoped-alias-retract",
        })

    async with core.database.transaction() as conn:
        assert await core.sources.prepare_event_writes(
            conn, alice, (), scoped_refs=((bob, (scoped,)),)
        ) == ()
        pending = asyncio.create_task(retract())
        await asyncio.wait_for(started.wait(), 5)
        assert not (await asyncio.wait({pending}, timeout=0.05))[0]
    await asyncio.wait_for(pending, 5)
    assert await core.sources.check_many(bob, ((related,),)) == (False,)
