"""RBJ source graph budgets, original authority semantics, and real consumers."""

import asyncio
from datetime import UTC, datetime, timedelta
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb
import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.sources import rbj_locking
from tianshu.sources.types import EvidenceSetRef, EvidenceSpec
import tianshu.sources.proofs as source_proofs
from tianshu.types import OriginKind, RequestBudget, SourceRef


_spec = spec_from_file_location(
    "rbj_source_core_fixtures", Path(__file__).parents[1] / "core" / "conftest.py"
)
_fixtures = module_from_spec(_spec)
_spec.loader.exec_module(_fixtures)
core = _fixtures.core
ordinary_users = _fixtures.ordinary_users


def _ref(name, locator=None):
    return {"source_id": name, "revision": 1, "locator": locator or {}}


def _node(groups=(), *, scopes=("visible",), forgotten=(), disclosure=False):
    return {"valid": True, "scopes": list(scopes), "length": 8,
            "forgotten": list(forgotten), "disclosure": disclosure, "groups": list(groups)}


async def _evaluate(conn, groups, nodes, *, viewer=("visible",), work=1000000):
    row = await (await conn.execute(
        "SELECT sources._rbj_evaluate(%s::jsonb,%s::text[],%s::jsonb,%s) AS proof",
        (Jsonb(groups), list(viewer), Jsonb(nodes), work),
    )).fetchone()
    return row["proof"]


async def _seed(core, context, prefix, count):
    async with core.database.transaction() as conn:
        scopes, epochs = await core.identity.intake_scope(context, connection=conn)
        await conn.execute(
            "INSERT INTO sources.originals(source_id,source_key,owner_id,person_id,actor_id,head_revision) "
            "SELECT %s||n,%s||n,%s,%s,%s,1 FROM generate_series(0,%s-1) n",
            (prefix, prefix + "-key-", context.principal_id, context.person_id, context.actor_id, count),
        )
        await conn.execute(
            "INSERT INTO sources.versions(source_id,revision,intake_epoch,scope_ids,scope_epochs,audience,"
            "origin_kind,evidence_unit,content,locator,coverage,occurred_at) "
            "SELECT %s||n,1,1,%s::text[],%s::jsonb,%s::text[],'human',"
            "%s||n,'abcdefgh','{}'::jsonb,'{}'::jsonb,now() FROM generate_series(0,%s-1) n",
            (prefix, list(scopes), Jsonb(epochs), list(scopes), prefix + "-event-", count),
        )
    return tuple(SourceRef(f"{prefix}{index}", 1, {}) for index in range(count))


async def _seed_field_graph(core, context):
    refs = await _seed(core, context, "rbj-topology-", 195)
    async with core.database.transaction() as conn:
        # 65 layers of three nodes. Prioritize a 64-edge chain, then fill the
        # remaining edges deterministically to the observed 8,252-edge scale.
        await conn.execute("""
            INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,
                                         support_revision,support_locator,support_key)
            SELECT 'rbj-topology-'||parent,1,'0','rbj-topology-'||child,1,
                   '{}'::jsonb,'edge-'||parent||'-'||child
            FROM (
                SELECT p AS parent, c AS child
                FROM generate_series(1,194) p
                CROSS JOIN generate_series(0,193) c
                WHERE c<p AND c/3<p/3
                ORDER BY CASE WHEN p%3=0 AND c=p-3 THEN 0 ELSE 1 END,p,c
                LIMIT 8252
            ) edges
        """)
        counts = await (await conn.execute(
            "SELECT count(*) AS edges FROM sources.supports WHERE source_id LIKE 'rbj-topology-%'"
        )).fetchone()
        assert counts["edges"] == 8252
    return refs


async def test_field_scale_deep_new_receipt_public_read_and_memory_retraction(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await _seed_field_graph(core, alice)
    async with core.database.transaction() as conn:
        assert await core.sources.check_many_on(conn, alice, (refs,)) == (True,)
        receipt = (await core.sources.record_event(
            alice, producer="rbj-proof", event_id="new-receipt", revision=1,
            occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": "rbj receipt"},),
            origin_kind=OriginKind.HUMAN, support_refs=refs, connection=conn,
        ))[0]
        # The extra receipt changes the longest chain from 64 to 65 and is not committed yet.
        assert await core.sources.check_many_on(conn, alice, ((receipt,),)) == (True,)
        fact = await core.claims.put_on(conn, alice, {
            "subject_id": alice.person_id, "predicate": "interest", "value": "rbj apple",
            "assertion": "self_confirmed", "source_refs": (receipt,),
        }, automatic=True)
    assert (await core.sources.read(alice, receipt, {}))["content"] == "rbj receipt"
    material, _ = await core.retrieval.search(
        alice, "rbj apple", RequestBudget(datetime.now(UTC) + timedelta(seconds=30)), limit=10
    )
    assert any("rbj apple" in item.text for item in material)

    await core.sources.invalidate(alice, {
        "source_id": refs[0].source_id, "expected_revision": refs[0].revision,
        "client_request_id": "rbj-withdraw-field-leaf",
    }, forgotten=True)
    with pytest.raises(AppError):
        await core.sources.read(alice, receipt, {})
    with pytest.raises(AppError):
        await core.claims.get(alice, fact.claim_id)
    material, _ = await core.retrieval.search(
        alice, "rbj apple", RequestBudget(datetime.now(UTC) + timedelta(seconds=30)), limit=10
    )
    assert all("rbj apple" not in item.text for item in material)


async def test_deeper_than_65_is_not_a_business_denial(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await _seed(core, alice, "rbj-chain-", 130)
    async with core.database.transaction() as conn:
        await conn.execute("""
            INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,
                                         support_revision,support_locator,support_key)
            SELECT 'rbj-chain-'||n,1,'0','rbj-chain-'||(n+1),1,'{}'::jsonb,'edge-'||n
            FROM generate_series(0,128) n
        """)
        assert await core.sources.check_many_on(conn, alice, ((refs[0], refs[64], refs[65]),)) == (True,)
        assert await core.sources.validate_persistence(conn, alice, (refs[0],))


async def test_cycles_ranges_disclosure_and_short_circuit_preserve_authority(core):
    leaf, a, b = _ref("leaf"), _ref("a"), _ref("b")
    nodes = {
        "leaf:1": _node(),
        "a:1": _node(([b], [leaf])),
        "b:1": _node(([a],)),
        "private:1": _node(scopes=("other",)),
        "published:1": _node(([_ref("private")],), disclosure=True),
        "plain:1": _node(([_ref("private")],)),
        "fragment:1": _node(forgotten=({"start": 0, "end": 2},)),
        "range:1": _node(([_ref("fragment", {"start": 2, "end": 4})],)),
        "bad-range:1": _node(([_ref("fragment", {"start": 0, "end": 1})],)),
        "lazy:1": _node(([leaf], [_ref("leaf", {"start": 10**30, "end": 10**30})])),
    }
    groups = [[a], [b], [_ref("published")], [_ref("plain")],
              [_ref("range")], [_ref("bad-range")], [_ref("lazy")], [_ref("missing")], []]
    async with core.database.transaction() as conn:
        assert (await _evaluate(conn, groups, nodes))["allowed"] == [True, True, True, False, True, False, True, False, True]
        without_exit = dict(nodes)
        without_exit["a:1"] = _node(([b],))
        assert (await _evaluate(conn, [[a], [b]], without_exit))["allowed"] == [False, False]
        # Overflow is preserved only when the exceptional branch is actually reached.
        with pytest.raises(psycopg.Error) as failure:
            async with conn.transaction():
                await _evaluate(conn, [[_ref("leaf", {"start": 10**30, "end": 10**30})]], nodes)
        assert failure.value.sqlstate == "22003"


async def test_scope_epoch_and_fragment_changes_are_fresh(core, ordinary_users):
    alice, bob = ordinary_users
    refs = await _seed(core, alice, "rbj-scope-", 1)
    whole = refs[0]
    safe = SourceRef(whole.source_id, 1, {"start": 2, "end": 4})
    forgotten = SourceRef(whole.source_id, 1, {"start": 0, "end": 2})
    assert await core.sources.check_many(alice, ((whole,),)) == (True,)
    assert await core.sources.check_many(bob, ((whole,),)) == (False,)
    await core.sources.forget_fragment(alice, {
        "source_id": whole.source_id, "expected_revision": 1, "start": 0, "end": 2,
        "client_request_id": "rbj-forget-fragment",
    })
    assert await core.sources.check_many(alice, ((safe,), (forgotten,), (whole,))) == (True, False, False)
    async with core.database.transaction() as conn:
        row = await (await conn.execute(
            "SELECT scope_ids[1] AS scope_id FROM sources.versions WHERE source_id=%s AND revision=1",
            (whole.source_id,),
        )).fetchone()
        await conn.execute(
            "UPDATE identity.scopes SET intake_epoch=intake_epoch+1 WHERE scope_id=%s", (row["scope_id"],)
        )
    assert await core.sources.check_many(alice, ((safe,),)) == (False,)


async def test_node_edge_and_work_limits_report_budget_not_false(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await _seed(core, alice, "rbj-limit-", 363)
    async with core.database.transaction() as conn:
        # Small private limits test collection stages without requiring a huge graph.
        with pytest.raises(psycopg.Error) as failure:
            async with conn.transaction():
                await conn.execute(
                    "SELECT sources._rbj_check_one(%s::jsonb,%s::text[],%s,%s,%s,%s,%s)",
                    (Jsonb([[_ref("rbj-limit-0"), _ref("rbj-limit-1")]]),
                     list(alice.scope_ids), 1, 65536, 1000000, 32768, 33554432),
                )
        assert failure.value.sqlstate == "54000"
        await conn.execute("""
            INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,
                                         support_revision,support_locator,support_key)
            SELECT 'rbj-limit-'||p,1,'0','rbj-limit-'||c,1,'{}'::jsonb,'edge-'||p||'-'||c
            FROM generate_series(0,362) p CROSS JOIN generate_series(0,362) c WHERE c>p
        """)
        with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
            await core.sources.check_many_on(conn, alice, ((refs[0],),))
        simple = {"leaf:1": _node(), "parent:1": _node(([_ref("leaf")],))}
        with pytest.raises(psycopg.Error) as failure:
            async with conn.transaction():
                await _evaluate(conn, [[_ref("parent")]], simple, work=1)
        assert failure.value.sqlstate == "54000"
        # A node with many forgotten intervals also consumes local proof work.
        fragments = tuple({"start": 100 + n, "end": 101 + n} for n in range(30))
        fragmented = {"fragmented:1": _node(forgotten=fragments)}
        with pytest.raises(psycopg.Error) as failure:
            async with conn.transaction():
                await _evaluate(conn, [[_ref("fragmented", {"start": 1, "end": 2})]],
                                fragmented, work=20)
        assert failure.value.sqlstate == "54000"


async def test_multiple_contexts_share_one_preparation_budget(core, ordinary_users):
    alice, bob = ordinary_users
    alice_refs = await _seed(core, alice, "rbj-alice-budget-", 4100)
    bob_refs = await _seed(core, bob, "rbj-bob-budget-", 4100)
    async with core.database.transaction() as conn:
        with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
            await core.sources.prepare_event_writes(
                conn, alice, (), scoped_refs=((alice, alice_refs), (bob, bob_refs))
            )


async def test_prepare_deduplicates_only_exact_context_and_locator(core, ordinary_users, monkeypatch):
    alice, bob = ordinary_users
    ref = (await _seed(core, alice, "rbj-dedupe-", 1))[0]
    span = SourceRef(ref.source_id, ref.revision, {"start": 1, "end": 3})
    observed = []
    check_scoped_on = source_proofs.check_scoped_on

    async def observe(identity, connection, requests, *, budget=None):
        observed.append(requests)
        return await check_scoped_on(identity, connection, requests, budget=budget)

    monkeypatch.setattr(source_proofs, "check_scoped_on", observe)
    async with core.database.transaction() as conn:
        assert await core.sources.prepare_event_writes(
            conn, alice, (), additional_refs=(ref,),
            scoped_refs=((alice, (ref,)), (alice, (ref,)), (alice, (span,)), (bob, (ref,))),
        ) == ()
        assert observed == [(
            (alice, ((ref,),), True),
            (alice, ((span,),), False),
            (bob, ((ref,),), False),
        )]
        assert await core.sources.check_many_on(conn, alice, ((ref,), (span,))) == (True, True)
        assert await core.sources.check_many_on(conn, bob, ((ref,),)) == (False,)


async def test_high_fanout_alias_closure_remains_locked(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await _seed(core, alice, "rbj-alias-", 300)
    async with core.database.transaction() as conn:
        await conn.execute(
            "INSERT INTO sources.source_aliases(alias_key,source_id,revision) "
            "SELECT 'rbj-many-alias','rbj-alias-'||n,1 FROM generate_series(0,299) n"
        )
    started = asyncio.Event()

    async def retract():
        started.set()
        return await core.sources.invalidate(alice, {
            "source_id": refs[-1].source_id, "expected_revision": 1,
            "client_request_id": "rbj-fanout-retract",
        })

    async with core.database.transaction() as conn:
        assert await core.sources.validate_persistence(conn, alice, (refs[0],))
        pending = asyncio.create_task(retract())
        await asyncio.wait_for(started.wait(), 5)
        assert not (await asyncio.wait({pending}, timeout=0.05))[0]
    await asyncio.wait_for(pending, 5)
    assert await core.sources.check_many(alice, ((refs[-1],),)) == (False,)


@pytest.mark.parametrize("kind", ("support", "alias"))
async def test_lock_wait_rechecks_new_closure_and_rolls_back(
    core, ordinary_users, monkeypatch, kind
):
    alice, _ = ordinary_users
    root, added = await _seed(core, alice, "rbj-race-", 2)
    first_scan = asyncio.Event()
    original_select = rbj_locking.selected_on
    scans = 0

    async def observe(conn, refs, extra, aliases, budget):
        nonlocal scans
        result = await original_select(conn, refs, extra, aliases, budget)
        scans += 1
        if scans == 1:
            first_scan.set()
        return result

    monkeypatch.setattr(rbj_locking, "selected_on", observe)
    event = {"producer": "rbj-race", "event_id": kind, "revision": 1,
             "parts": ({"kind": "text", "text": "receipt"},), "support_refs": (root,)}

    async def consume():
        async with core.database.transaction() as conn:
            await core.sources.prepare_event_writes(conn, alice, (event,))
            await core.sources.record_event(
                alice, **event, occurred_at=datetime.now(UTC), origin_kind=OriginKind.HUMAN,
                connection=conn,
            )

    async with core.database.transaction() as changer:
        await changer.execute("SELECT source_id FROM sources.originals WHERE source_id=%s FOR UPDATE", (root.source_id,))
        pending = asyncio.create_task(consume())
        await asyncio.wait_for(first_scan.wait(), 5)
        assert not (await asyncio.wait({pending}, timeout=0.05))[0]
        if kind == "support":
            await changer.execute(
                "INSERT INTO sources.supports(source_id,revision,group_id,support_source_id,"
                "support_revision,support_locator,support_key) VALUES(%s,1,'0',%s,1,'{}'::jsonb,'new')",
                (root.source_id, added.source_id),
            )
        else:
            for ref in (root, added):
                await changer.execute(
                    "INSERT INTO sources.source_aliases(alias_key,source_id,revision) VALUES('rbj-race-key',%s,1)",
                    (ref.source_id,),
                )
    with pytest.raises(RecoveryWait) as failure:
        await asyncio.wait_for(pending, 5)
    assert failure.value.reason == "source_lock_set_changed"
    async with core.database.transaction(read_only=True) as conn:
        count = await (await conn.execute(
            "SELECT count(*) AS n FROM sources.versions WHERE evidence_unit=%s", (f"rbj-race:{kind}",)
        )).fetchone()
    assert count["n"] == 0


async def test_shared_lock_and_proof_budget_exhaustion_cannot_commit_receipt(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await _seed(core, alice, "rbj-atomic-budget-", 3000)
    event = {"producer": "rbj-atomic-budget", "event_id": "receipt", "revision": 1,
             "parts": ({"kind": "text", "text": "receipt"},), "support_refs": refs}
    with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
        async with core.database.transaction() as conn:
            await core.sources.prepare_event_writes(conn, alice, (event,))
            await core.sources.record_event(
                alice, **event, occurred_at=datetime.now(UTC), origin_kind=OriginKind.HUMAN,
                connection=conn,
            )
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute(
            "SELECT count(*) AS n FROM sources.versions WHERE evidence_unit='rbj-atomic-budget:receipt'"
        )).fetchone()
    assert row["n"] == 0


async def test_evidence_expansion_uses_current_authority_and_shared_lock_budget(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await _seed(core, alice, "rbj-evidence-", 3000)
    async with core.database.transaction() as conn:
        first = await core.sources.register_evidence_on(conn, alice, EvidenceSpec(required=refs[:1500]))
        second = await core.sources.register_evidence_on(conn, alice, EvidenceSpec(required=refs[1500:]))
        assert isinstance(first, EvidenceSetRef) and isinstance(second, EvidenceSetRef)
        missing = EvidenceSetRef("evidence-not-present")
        assert await core.sources.prove_many_on(conn, alice, (first, first, missing)) == (True, True, False)
    async with core.database.transaction() as conn:
        assert await core.sources.prove_many_on(conn, alice, (first, second), lock=False) == (True, True)
        with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
            await core.sources.prove_many_on(conn, alice, (first, second), lock=True)
    await core.sources.invalidate(alice, {
        "source_id": refs[0].source_id, "expected_revision": 1,
        "client_request_id": "rbj-evidence-withdraw",
    })
    async with core.database.transaction(read_only=True) as conn:
        assert await core.sources.prove_many_on(conn, alice, (first, second)) == (False, True)


async def test_caught_budget_error_rolls_back_all_parts_of_event(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await _seed(core, alice, "rbj-multipart-budget-", 2000)
    async with core.database.transaction() as conn:
        with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
            await core.sources.record_event(
                alice, producer="rbj-multipart-budget", event_id="event", revision=1,
                occurred_at=datetime.now(UTC),
                parts=({"kind": "text", "text": "first"}, {"kind": "text", "text": "second"}),
                origin_kind=OriginKind.HUMAN, support_refs=refs, connection=conn,
            )
        row = await (await conn.execute(
            "SELECT count(*) AS n FROM sources.versions WHERE evidence_unit='rbj-multipart-budget:event'"
        )).fetchone()
        assert row["n"] == 0


async def test_direct_lock_selected_uses_a_finite_budget_without_a_caller_budget(core, ordinary_users):
    from tianshu.sources.archives import lock_selected

    alice, _ = ordinary_users
    refs = await _seed(core, alice, "rbj-direct-lock-", 4100)
    async with core.database.transaction() as conn:
        with pytest.raises(AppError, match="source_dependency_budget_exceeded"):
            await lock_selected(conn, refs)
