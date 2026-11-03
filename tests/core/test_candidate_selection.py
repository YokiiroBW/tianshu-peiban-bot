"""Real PG candidate paging, complete authority and interrupted-read stories."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json

from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg import sql
from psycopg.types.json import Jsonb
import pytest

from tianshu.companion.types import ReflectionProposal
from tianshu.memory.candidates import CandidateSelection, families, query_terms
from tianshu.memory.types import MemoryQueryPlan, RecordRef
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, RequestBudget


def budget():
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=10))


async def original(core, context, key, text="候选来源"):
    return await core.sources.record_event(context, producer="web", event_id=key, revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": text},), origin_kind=OriginKind.HUMAN)


async def fact(core, context, key, value="咖啡 coffee", **extra):
    refs = await original(core, context, key, "我说：" + value)
    async with core.database.transaction() as conn:
        return await core.claims.put_on(conn, context, {"subject_id": context.person_id,
            "speaker_id": context.person_id, "predicate": "preference.candidate", "value": value,
            "assertion": "self_confirmed", "source_refs": refs, **extra}, automatic=True)


async def copies(core, template, patches):
    """Bulk fixture for stale scope projections; every source remains real."""
    async with core.database.transaction() as conn:
        columns = await (await conn.execute("SELECT column_name FROM information_schema.columns "
            "WHERE table_schema='memory' AND table_name='claim_revisions' AND is_generated='NEVER' "
            "ORDER BY ordinal_position")).fetchall()
        await conn.execute("INSERT INTO memory.claims(claim_id,current_revision,actor_id,principal_id) "
            "SELECT item->>'claim_id',h.current_revision,h.actor_id,h.principal_id "
            "FROM memory.claims h CROSS JOIN jsonb_array_elements(%s::jsonb) item WHERE h.claim_id=%s",
            (Jsonb(wire(patches)), template.claim_id))
        names = [row["column_name"] for row in columns]
        await conn.execute(sql.SQL("INSERT INTO memory.claim_revisions ({columns}) "
            "SELECT {values} FROM memory.claim_revisions r "
            "CROSS JOIN jsonb_array_elements(%s::jsonb) item "
            "CROSS JOIN LATERAL jsonb_populate_record(NULL::memory.claim_revisions,to_jsonb(r)||item) replica "
            "WHERE r.claim_id=%s AND r.revision=%s").format(
                columns=sql.SQL(",").join(map(sql.Identifier, names)),
                values=sql.SQL(",").join(sql.Identifier("replica", name) for name in names)),
            (Jsonb(wire(patches)), template.claim_id, template.revision))


async def old_authorized_selection(core, context, plan, limit=96):
    """Compare the original full authority query, including its score and order."""
    store = core.retrieval.store
    tsquery, fragments = query_terms(plan.query)
    rows = []
    async with core.database.transaction(budget(), read_only=True) as conn:
        await conn.execute("SELECT set_config('jit','off',true)")
        for family in families(plan.evidence_kinds):
            parts = store.selection(context, plan, family)
            if tsquery:
                relevance = "ts_rank_cd(" + parts["search"] + ",to_tsquery('simple',%s))"
                match = " AND (" + parts["search"] + "@@to_tsquery('simple',%s) OR " + parts["content"] + " ILIKE ANY(%s::text[]))"
                args = (tsquery, *parts["args"], tsquery, fragments, limit)
            else:
                relevance, match, args = "0::real", "", (*parts["args"], limit)
            rows.extend(await (await conn.execute("SELECT " + parts["select"] + "," + relevance +
                " AS relevance FROM " + parts["table"] + " WHERE " + parts["where"] + match +
                " ORDER BY relevance DESC,recorded_at DESC NULLS LAST," + parts["key"] + " LIMIT %s", args)).fetchall())
    return sorted(rows, key=lambda row: (row["relevance"], row.get("recorded_at") or
        datetime.min.replace(tzinfo=UTC)), reverse=True)[:256]


def identities(rows):
    return [(row["_kind"], row["_id"], row["revision"], row["relevance"]) for row in rows]


async def test_lexical_continues_past_unreadable_batches_with_a_stable_cursor(core, ordinary_users, monkeypatch):
    alice, bob = ordinary_users
    value = await fact(core, alice, "candidate_paging")
    private = await original(core, bob, "candidate_unreadable")
    at = datetime.now(UTC) + timedelta(hours=1)
    patches = [{"claim_id": f"page_{index:04d}", "recorded_at": at,
        "source_refs": private if index < 260 else value.source_refs,
        "support_groups": (private,) if index < 260 else (value.source_refs,)} for index in range(262)]
    await copies(core, value, patches)
    check = core.sources.check_many_on
    calls = 0
    async def insert_before_cursor_position(conn, context, groups):
        nonlocal calls
        checked = await check(conn, context, groups)
        calls += 1
        if calls == 1:
            await copies(core, value, [{"claim_id": "page_-001", "recorded_at": at}])
        return checked
    monkeypatch.setattr(core.sources, "check_many_on", insert_before_cursor_position)
    result = await core.retrieval.store.lexical(alice, MemoryQueryPlan(evidence_kinds=("claim",)), budget(), limit=2)
    assert isinstance(result, CandidateSelection) and not result.missing
    assert [row["_id"] for row in result.rows] == ["page_0260", "page_0261"]
    assert calls >= 3
    assert all("_embedding" not in row and "embedding" not in row for row in result.rows)
    following = await core.retrieval.store.lexical(alice, MemoryQueryPlan(evidence_kinds=("claim",)), budget(), limit=1)
    assert [row["_id"] for row in following.rows] == ["page_-001"]


async def test_lexical_matches_full_source_query_for_empty_chinese_and_temporal_plans(core, ordinary_users):
    alice, _ = ordinary_users
    then = datetime.now(UTC) - timedelta(days=2)
    asked = then + timedelta(days=1)
    old = await fact(core, alice, "candidate_time", valid_from=then)
    current = (await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
        "value": "茶 tea", "client_request_id": "candidate_time_correct"})).data
    matter = (await core.claims.save_matter(alice, {"matter_id": "candidate_matter", "title": "咖啡 coffee约定",
        "state": "planned", "expected_revision": 0, "source_refs": current.source_refs,
        "client_request_id": "candidate_matter"})).data
    await core.claims.save_matter(alice, {"matter_id": matter.matter_id, "title": "咖啡 coffee改期",
        "state": "rescheduled", "expected_revision": 1, "client_request_id": "candidate_matter_reschedule"})
    episode = await core.memory.observe_episode(alice, {"event_id": "candidate_episode", "summary": "咖啡 coffee交谈",
        "occurred_at": then, "origin_kind": "human", "source_refs": current.source_refs, "coverage": Coverage(True)})
    await core.memory.record_reflection(alice, ReflectionProposal("candidate_reflection", core.actor,
        (episode.episode_id,), "对咖啡 coffee的主观理解", "interest", datetime.now(UTC),
        current.source_refs, current.scope_ids))
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.claim_revisions SET recorded_at=%s WHERE claim_id=%s AND revision=1",
            (then, old.claim_id))
        await conn.execute("UPDATE memory.matter_revisions SET observed_at=%s,recorded_at=%s "
            "WHERE matter_id=%s AND revision=1", (then, then, matter.matter_id))
    for text in ("", "咖啡", "coffee"):
        base = MemoryQueryPlan(query=text)
        for plan in (base, replace(base, time_mode="known_at", known_at=asked),
                     replace(base, time_mode="valid_at", valid_at=asked)):
            expected = await old_authorized_selection(core, alice, plan)
            selected = await core.retrieval.store.lexical(alice, plan, budget())
            assert not selected.missing
            assert identities(selected.rows) == identities(expected), (text, plan.time_mode)
            if not text and plan.time_mode == "current":
                assert {row["_kind"] for row in selected.rows} == {"claim", "matter", "episode", "reflection"}
            if not text and plan.time_mode == "known_at":
                assert next(row for row in selected.rows if row["_id"] == old.claim_id)["revision"] == 1
            if not text and plan.time_mode == "valid_at":
                assert next(row for row in selected.rows if row["_id"] == matter.matter_id)["revision"] == 1


async def test_about_matter_requires_the_relationships_own_complete_sources(core, ordinary_users):
    alice, bob = ordinary_users
    claim = await fact(core, alice, "candidate_link_claim")
    matter = (await core.claims.save_matter(alice, {"matter_id": "candidate_link_matter", "title": "咖啡约定",
        "state": "planned", "expected_revision": 0, "source_refs": claim.source_refs,
        "client_request_id": "candidate_link_matter"})).data
    refs = await original(core, alice, "candidate_link", "这条认识属于该约定")
    private = await original(core, bob, "candidate_link_private")
    async with core.database.transaction() as conn:
        support = await core.claims.revisions.support_on(conn, alice, (refs,), (*claim.source_refs, *matter.source_refs))
        await core.claims.revisions.links.add_on(conn, alice, link_id="candidate_about_matter",
            from_ref=RecordRef("claim", claim.claim_id, claim.revision),
            to_ref=RecordRef("matter", matter.matter_id, matter.revision), relation_kind="about_matter", support=support)
    plan = MemoryQueryPlan(matter_refs=(matter.matter_id,), evidence_kinds=("claim",))
    await index_claim(core, claim)
    vectors, bounded = await core.retrieval.store.vectors(alice, plan, unit_vector(), "fixture", budget())
    assert not bounded and [row["_id"] for row in vectors] == [claim.claim_id]
    assert identities((await core.retrieval.store.lexical(alice, plan, budget())).rows) == identities(
        await old_authorized_selection(core, alice, plan))
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.semantic_links SET derivation_refs=%s WHERE link_id=%s",
            (Jsonb(wire(private)), "candidate_about_matter"))
    selected = await core.retrieval.store.lexical(alice, plan, budget())
    assert not selected.rows and not selected.missing
    assert not await old_authorized_selection(core, alice, plan)
    vectors, bounded = await core.retrieval.store.vectors(alice, plan, unit_vector(), "fixture", budget())
    assert not bounded and not vectors
    assert not await core.retrieval.store.has_vectors(alice, plan, "fixture", budget())
    async with core.database.transaction() as conn:
        await register_vector_async(conn)
        await conn.execute("UPDATE memory.matters SET embedding=%s,embedding_profile='fixture',state='completed',completion_basis=%s "
            "WHERE matter_id=%s", (Vector(unit_vector()), Jsonb({"source_refs": wire(private)}), matter.matter_id))
    vectors, bounded = await core.retrieval.store.vectors(alice, MemoryQueryPlan(evidence_kinds=("matter",)),
        unit_vector(), "fixture", budget())
    assert not bounded and not vectors


@pytest.mark.parametrize("failure", ("statement_cancel", "timeout"))
async def test_interrupted_family_keeps_verified_batches_and_releases_its_cursor(
        core, ordinary_users, monkeypatch, failure):
    alice, _ = ordinary_users
    value = await fact(core, alice, "candidate_interrupt")
    await copies(core, value, [{"claim_id": f"interrupt_{index:04d}"} for index in range(150)])
    matter = (await core.claims.save_matter(alice, {"matter_id": "candidate_interrupt_matter", "title": "继续可用",
        "state": "planned", "expected_revision": 0, "source_refs": value.source_refs,
        "client_request_id": "candidate_interrupt_matter"})).data
    check, calls = core.sources.check_many_on, 0
    async with core.database.pool.connection() as connection:
        original_jit = (await (await connection.execute("SELECT current_setting('jit') AS jit")).fetchone())["jit"]
        @asynccontextmanager
        async def pinned(*, timeout=None):
            yield connection
        async def fail_second_batch(conn, context, groups):
            nonlocal calls
            calls += 1
            if calls == 2:
                if failure == "timeout":
                    raise TimeoutError("candidate_batch_timed_out")
                await conn.execute("SELECT set_config('statement_timeout','10ms',true)")
                await conn.execute("SELECT pg_sleep(0.1)")
            return await check(conn, context, groups)
        monkeypatch.setattr(core.database.pool, "connection", pinned)
        monkeypatch.setattr(core.sources, "check_many_on", fail_second_batch)
        selected = await core.retrieval.store.lexical(alice,
            MemoryQueryPlan(evidence_kinds=("claim", "matter")), budget(), limit=200)
        assert sum(row["_kind"] == "claim" for row in selected.rows) == 128
        assert any(row["_id"] == matter.matter_id for row in selected.rows)
        assert selected.missing == ("lexical_candidate_timeout:claim",)
        count = await (await connection.execute("SELECT count(*) AS count FROM pg_cursors "
            "WHERE name LIKE 'memory_lexical_%'")).fetchone()
        assert count["count"] == 0
        assert (await (await connection.execute("SELECT 1 AS usable")).fetchone())["usable"] == 1
        assert (await (await connection.execute("SELECT current_setting('jit') AS jit")).fetchone())["jit"] == original_jit


@pytest.mark.parametrize("lane", ("lexical", "vector"))
async def test_real_task_cancellation_and_context_revocation_propagate_cleanly(core, ordinary_users, monkeypatch, lane):
    alice, _ = ordinary_users
    value = await fact(core, alice, "candidate_cancel")
    if lane == "vector":
        await index_claim(core, value)
    async def select(context):
        plan = MemoryQueryPlan(evidence_kinds=("claim",))
        if lane == "vector":
            return await core.retrieval.store.vectors(context, plan, unit_vector(), "fixture", budget())
        return await core.retrieval.store.lexical(context, plan, budget())
    entered = asyncio.Event()
    check = core.sources.check_many_on
    async with core.database.pool.connection() as connection:
        original_jit = (await (await connection.execute("SELECT current_setting('jit') AS jit")).fetchone())["jit"]
        @asynccontextmanager
        async def pinned(*, timeout=None):
            yield connection
        async def wait_in_sql(conn, context, groups):
            entered.set()
            await conn.execute("SELECT pg_sleep(30)")
            return await check(conn, context, groups)
        with monkeypatch.context() as patch:
            patch.setattr(core.database.pool, "connection", pinned)
            patch.setattr(core.sources, "check_many_on", wait_in_sql)
            task = asyncio.create_task(select(alice))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        cursors = await (await connection.execute("SELECT count(*) AS count FROM pg_cursors "
            "WHERE name LIKE 'memory_%'")).fetchone()
        assert cursors["count"] == 0
        assert (await (await connection.execute("SELECT 1 AS usable")).fetchone())["usable"] == 1
        assert (await (await connection.execute("SELECT current_setting('jit') AS jit")).fetchone())["jit"] == original_jit
    with pytest.raises(AppError) as revoked:
        await select(replace(alice, access_epoch=alice.access_epoch + 1))
    assert revoked.value.status in {401, 403, 409}


def unit_vector(first=1.0, second=0.0):
    return [first, second] + [0.0] * 1022


async def index_claim(core, value):
    async with core.database.transaction() as conn:
        await register_vector_async(conn)
        await conn.execute("UPDATE memory.claim_revisions SET embedding=%s,embedding_profile='fixture' "
            "WHERE claim_id=%s AND revision=%s", (Vector(unit_vector()), value.claim_id, value.revision))


async def old_authorized_vectors(core, context, plan, limit=96):
    """Original complete authority + cosine order, independent of batched proofs."""
    p = core.retrieval.store.selection(context, plan, "claim")
    async with core.database.transaction(budget(), read_only=True) as conn:
        await register_vector_async(conn)
        await conn.execute("SELECT set_config('jit','off',true)")
        return await (await conn.execute("SELECT " + p["select"] + ",1-(" + p["embedding"] +
            "<=>%s) AS relevance FROM " + p["table"] + " WHERE " + p["where"] + " AND " + p["profile"] +
            "='fixture' AND " + p["embedding"] + " IS NOT NULL ORDER BY (" + p["embedding"] +
            "<=>%s)+0," + p["key"] + " LIMIT %s", (Vector(unit_vector()), *p["args"], Vector(unit_vector()), limit))).fetchall()


async def test_vectors_and_profile_probe_continue_after_260_unreadable_nearest_rows(core, ordinary_users, monkeypatch):
    alice, bob = ordinary_users
    value = await fact(core, alice, "vector_paging")
    await index_claim(core, value)
    private = await original(core, bob, "vector_paging_private")
    await copies(core, value, [{"claim_id": f"vector_{index:04d}",
        "source_refs": private if index < 260 else value.source_refs,
        "support_groups": (private,) if index < 260 else (value.source_refs,),
        "embedding": json.dumps(unit_vector() if index < 260 else unit_vector(0.8, 0.6))} for index in range(261)])
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.claim_revisions SET embedding_profile='seed_only' WHERE claim_id=%s", (value.claim_id,))
    check, calls = core.sources.check_many_on, []
    async def counted(conn, context, groups):
        calls.append(groups)
        return await check(conn, context, groups)
    monkeypatch.setattr(core.sources, "check_many_on", counted)
    plan, store = MemoryQueryPlan(evidence_kinds=("claim",)), core.retrieval.store
    assert await store.has_vectors(alice, plan, "fixture", budget())
    assert len(calls) >= 3
    calls.clear()
    rows, bounded = await store.vectors(alice, plan, unit_vector(), "fixture", budget(), limit=1)
    assert not bounded and [row["_id"] for row in rows] == ["vector_0260"]
    assert len(calls) >= 3
    assert rows[0]["vector"] == pytest.approx(unit_vector(0.8, 0.6))
    assert not await store.has_vectors(bob, plan, "fixture", budget())
    assert not await store.has_vectors(alice, plan, "other-profile", budget())
    async def unprovable(conn, context, groups):
        raise AppError("source_dependency_budget_exceeded", status=503)
    monkeypatch.setattr(core.sources, "check_many_on", unprovable)
    with pytest.raises(AppError) as unknown:
        await store.has_vectors(alice, plan, "fixture", budget())
    assert unknown.value.code == "memory_vector_candidate_budget"
    rows, bounded = await store.vectors(alice, plan, unit_vector(), "fixture", budget())
    assert not rows and bounded


async def test_exact_vector_batches_match_authorized_distance_order_and_required_sources(core, ordinary_users):
    alice, bob = ordinary_users
    value = await fact(core, alice, "vector_order")
    await index_claim(core, value)
    private = await original(core, bob, "vector_order_private")
    await copies(core, value, [
        {"claim_id": "vector_or", "embedding": json.dumps(unit_vector(0.9, 0.1)),
         "support_groups": (private, value.source_refs)},
        {"claim_id": "vector_required", "embedding": json.dumps(unit_vector()), "derivation_refs": private},
        {"claim_id": "vector_counterexample", "embedding": json.dumps(unit_vector()), "counterexample_refs": private},
        {"claim_id": "vector_further", "embedding": json.dumps(unit_vector(0.4, 0.6))},
    ])
    plan = MemoryQueryPlan(evidence_kinds=("claim",))
    expected = await old_authorized_vectors(core, alice, plan)
    actual, bounded = await core.retrieval.store.vectors(alice, plan, unit_vector(), "fixture", budget())
    assert not bounded and identities(actual) == identities(expected)
    assert {row["_id"] for row in actual} == {value.claim_id, "vector_or", "vector_further"}


async def test_ann_shortfall_uses_exact_batches_only_when_needed_and_preserves_partial_fallback(
        core, ordinary_users, monkeypatch):
    from tianshu.memory import candidates
    alice, _ = ordinary_users
    value = await fact(core, alice, "vector_ann")
    await index_claim(core, value)
    await copies(core, value, [{"claim_id": f"ann_{index}",
        "embedding": json.dumps(unit_vector(1.0, 0.1 * (index + 1)))} for index in range(5)])
    store, plan = core.retrieval.store, MemoryQueryPlan(evidence_kinds=("claim",))
    ann, exact, calls = store._ann_vectors_on, store._exact_vectors_on, []
    fail_exact = False
    async def short_ann(conn, context, parts, values, fingerprint, limit, **kwargs):
        calls.append("ann")
        return await ann(conn, context, parts, values, fingerprint, 1, **kwargs)
    async def exact_fallback(*args, **kwargs):
        calls.append("exact")
        result = await exact(*args, **kwargs)
        if fail_exact:
            raise TimeoutError("late_exact_timeout")
        return result
    monkeypatch.setattr(candidates, "EXACT_VECTOR_CANDIDATES", 2)
    monkeypatch.setattr(store, "_ann_vectors_on", short_ann)
    monkeypatch.setattr(store, "_exact_vectors_on", exact_fallback)
    rows, bounded = await store.vectors(alice, plan, unit_vector(), "fixture", budget(), limit=1)
    assert not bounded and len(rows) == 1 and calls == ["ann"]
    calls.clear()
    expected = await old_authorized_vectors(core, alice, plan, limit=3)
    rows, bounded = await store.vectors(alice, plan, unit_vector(), "fixture", budget(), limit=3)
    assert not bounded and identities(rows) == identities(expected) and calls == ["ann", "exact"]
    fail_exact = True
    rows, bounded = await store.vectors(alice, plan, unit_vector(), "fixture", budget(), limit=3)
    assert bounded and identities(rows) == identities(expected)


@pytest.mark.parametrize("failure", ("statement_cancel", "timeout"))
async def test_vector_late_batch_failure_retains_proved_rows_and_cleans_cursor(core, ordinary_users, monkeypatch, failure):
    alice, _ = ordinary_users
    value = await fact(core, alice, "vector_interrupt")
    await index_claim(core, value)
    await copies(core, value, [{"claim_id": f"vector_interrupt_{index:04d}"} for index in range(150)])
    check, calls = core.sources.check_many_on, 0
    async with core.database.pool.connection() as connection:
        @asynccontextmanager
        async def pinned(*, timeout=None):
            yield connection
        async def fail_second(conn, context, groups):
            nonlocal calls
            calls += 1
            if calls == 2:
                if failure == "timeout":
                    raise TimeoutError("late_vector_timeout")
                await conn.execute("SELECT set_config('statement_timeout','10ms',true)")
                await conn.execute("SELECT pg_sleep(0.1)")
            return await check(conn, context, groups)
        monkeypatch.setattr(core.database.pool, "connection", pinned)
        monkeypatch.setattr(core.sources, "check_many_on", fail_second)
        rows, bounded = await core.retrieval.store.vectors(alice, MemoryQueryPlan(evidence_kinds=("claim",)),
            unit_vector(), "fixture", budget(), limit=200)
        assert bounded and len(rows) == 128 and len({row["_id"] for row in rows}) == 128
        assert all(row["vector"] == pytest.approx(unit_vector()) for row in rows)
        cursors = await (await connection.execute("SELECT count(*) AS count FROM pg_cursors WHERE name LIKE 'memory_%'")).fetchone()
        assert cursors["count"] == 0
        assert (await (await connection.execute("SELECT 1 AS usable")).fetchone())["usable"] == 1
