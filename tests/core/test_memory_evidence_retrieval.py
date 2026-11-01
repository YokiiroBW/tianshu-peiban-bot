"""Multi-type/time/source stories authored for the final integrated validation run."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from tianshu.capabilities.catalog import Catalog
from tianshu.companion.types import ReflectionProposal
from tianshu.memory.indexing import evidence_text, index_text
from tianshu.memory.evidence import build_evidence_unit
from tianshu.memory.source_trace import SourceTrace
from tianshu.memory.tools import register_tools
from tianshu.memory.types import MemoryQueryPlan, RecordRef
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, ContextContribution, Coverage, OriginKind, RequestBudget, SourceRef


def budget():
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=10))


async def original(core, context, key, text):
    return await core.sources.record_event(context, producer="web", event_id=key, revision=1,
        occurred_at=datetime.now(UTC), parts=({"kind": "text", "text": text},), origin_kind=OriginKind.HUMAN)


async def fact(core, context, key, value, **extra):
    refs = await original(core, context, key, "我说：" + value)
    async with core.database.transaction() as conn:
        return await core.claims.put_on(conn, context, {"subject_id": context.person_id,
            "speaker_id": context.person_id, "predicate": "preference.drink", "value": value,
            "assertion": "self_confirmed", "source_refs": refs, **extra}, automatic=True)


async def indexed_vectors(core, records):
    from pgvector import Vector
    from pgvector.psycopg import register_vector_async

    values = [1.0] + [0.0] * 1023
    tables = {"claim": ("claim_revisions", "claim_id"), "hypothesis": ("claim_revisions", "claim_id"),
              "matter": ("matters", "matter_id"), "episode": ("episodes", "episode_id"),
              "reflection": ("reflections", "proposal_id")}
    async with core.database.transaction() as conn:
        await register_vector_async(conn)
        for record in records:
            table, key = tables[record.kind]
            await conn.execute(f"UPDATE memory.{table} SET embedding=%s,embedding_profile='fixture' "
                f"WHERE {key}=%s AND revision=%s", (Vector(values), record.id, record.revision))
    class QueryEmbedding:
        async def vector(self, *args, **kwargs):
            return values, "fixture"
    core.retrieval.embeddings = QueryEmbedding()
    core.retrieval.settings = {"vector": True, "vector_sql_seconds": 5}


async def test_all_five_types_are_real_candidates_and_hypotheses_keep_their_status(core, ordinary_users):
    alice, _ = ordinary_users
    factual = await fact(core, alice, "coffee_fact", "咖啡", conditions={"day": "weekend"})
    hypothesis = await fact(core, alice, "coffee_guess", "咖啡似乎让人振奋", memory_kind="hypothesis", assertion="inferred")
    episode = await core.memory.observe_episode(alice, {"event_id": "coffee_episode", "summary": "咖啡交谈",
        "occurred_at": datetime.now(UTC), "origin_kind": "human", "source_refs": factual.source_refs})
    reflection = await core.memory.record_reflection(alice, ReflectionProposal("coffee_reflection", core.actor,
        (episode.episode_id,), "咖啡是这次交谈的一个主题", "interest", datetime.now(UTC),
        factual.source_refs, factual.scope_ids, conditions={"certainty": "tentative"}))
    matter = (await core.claims.save_matter(alice, {"matter_id": "coffee_matter", "title": "咖啡约定",
        "state": "planned", "expected_revision": 0, "source_refs": factual.source_refs,
        "client_request_id": "coffee_matter"})).data
    page = await core.retrieval.query(alice, MemoryQueryPlan(query="咖啡"), budget())
    assert {(item.kind, item.id) for item in page.items} >= {("claim", factual.claim_id),
        ("hypothesis", hypothesis.claim_id), ("matter", matter.matter_id),
        ("episode", episode.episode_id), ("reflection", reflection.proposal_id)}
    guessed = next(item for item in page.items if item.id == hypothesis.claim_id)
    assert guessed.interpretation and guessed.assertion == "inferred"
    actual = next(item for item in page.items if item.id == factual.claim_id)
    assert actual.conditions == {"day": "weekend"} and actual.speaker_id == alice.person_id
    assert not page.coverage.complete and "ranked_selection" in page.coverage.missing


async def test_vector_exact_keeps_five_types_and_public_or_support_across_owners(core, ordinary_users):
    alice, bob = ordinary_users
    private = await fact(core, alice, "vector_private", "苹果", predicate="preference.ordinary")
    shared = (await core.claims.share_ordinary(alice, {"claim_id": private.claim_id,
        "expected_revision": private.revision, "client_request_id": "vector_publish"})).data
    async with core.database.transaction() as conn:
        alternatives = await core.claims.put_on(conn, alice, {"subject_id": alice.person_id,
            "speaker_id": None, "predicate": "preference.ordinary", "value": "独立公开依据",
            "assertion": "observed", "source_refs": (*private.source_refs, *shared.source_refs),
            "support_groups": (private.source_refs, shared.source_refs)})
    hypothesis = await fact(core, bob, "vector_guess", "周末咖啡", memory_kind="hypothesis", assertion="inferred",
                            conditions={"day": "weekend"})
    episode = await core.memory.observe_episode(bob, {"event_id": "vector_episode", "summary": "周末交谈",
        "occurred_at": datetime.now(UTC), "origin_kind": "human", "source_refs": hypothesis.source_refs,
        "coverage": Coverage(True)})
    reflection = await core.memory.record_reflection(bob, ReflectionProposal("vector_reflection", core.actor,
        (episode.episode_id,), "对周末交谈的主观理解", "interest", datetime.now(UTC),
        hypothesis.source_refs, hypothesis.scope_ids, conditions={"certainty": "tentative"}))
    matter = (await core.claims.save_matter(bob, {"matter_id": "vector_matter", "title": "周末约定",
        "state": "planned", "expected_revision": 0, "source_refs": hypothesis.source_refs,
        "client_request_id": "vector_matter"})).data
    observations = [item for item in (await core.memory.episodes_since(bob, None, 20)).items
                    if item.matter_id == matter.matter_id]
    assert len(observations) == 1
    command_observation = observations[0]
    assert command_observation.event_id.startswith("memory-revision:")
    assert str(command_observation.origin_kind) == "observation" and command_observation.person_id == bob.person_id
    assert command_observation.actual_result == {"internal": True, "event_kind": "memory_revision",
        "changed_records": [{"kind": "matter", "id": matter.matter_id, "revision": matter.revision}],
        "affected_subject_ids": [bob.person_id]}
    assert command_observation.source_refs == matter.source_refs
    assert set(command_observation.scope_ids) <= set(bob.scope_ids)
    assert all(await core.sources.check_many(bob, (command_observation.source_refs,)))
    assert not command_observation.coverage.complete and tuple(command_observation.coverage.missing) == ("coverage_unknown",)
    records = (RecordRef("claim", private.claim_id, private.revision),
        RecordRef("claim", shared.claim_id, shared.revision), RecordRef("claim", alternatives.claim_id, alternatives.revision),
        RecordRef("hypothesis", hypothesis.claim_id, hypothesis.revision), RecordRef("matter", matter.matter_id, matter.revision),
        RecordRef("episode", episode.episode_id, episode.revision), RecordRef("reflection", reflection.proposal_id, reflection.revision))
    await indexed_vectors(core, records)
    page = await core.retrieval.query(bob, MemoryQueryPlan(query="qzxv_unmatched_semantic_probe"), budget(), limit=12)
    assert {(item.kind, item.id) for item in page.items} == {(ref.kind, ref.id) for ref in records[1:]}
    assert page.coverage.missing == ("ranked_selection",)
    public = next(item for item in page.items if item.id == alternatives.claim_id)
    assert public.source_refs == shared.source_refs and public.subject_id == alice.person_id
    guessed = next(item for item in page.items if item.id == hypothesis.claim_id)
    assert guessed.assertion == "inferred" and guessed.conditions == {"day": "weekend"}
    assert next(item for item in page.items if item.kind == "reflection").interpretation
    assert all(ref not in item.source_refs for item in page.items for ref in private.source_refs)
    assert all(await core.sources.check_many(bob, tuple(item.source_refs for item in page.items)))

    link_refs = await original(core, bob, "vector_matter_link", "这条认识属于同一个周末约定")
    async with core.database.transaction() as conn:
        support = await core.claims.revisions.support_on(conn, bob, (link_refs,), (*hypothesis.source_refs, *matter.source_refs))
        await core.claims.revisions.links.add_on(conn, bob, link_id="vector_about_matter",
            from_ref=RecordRef("hypothesis", hypothesis.claim_id, hypothesis.revision),
            to_ref=RecordRef("matter", matter.matter_id, matter.revision), relation_kind="about_matter", support=support)
    related_plan = MemoryQueryPlan(query="qzxv_unmatched_semantic_probe", matter_refs=(matter.matter_id,))
    related = await core.retrieval.query(bob, related_plan, budget())
    assert {(item.kind, item.id) for item in related.items} == {
        ("hypothesis", hypothesis.claim_id), ("matter", matter.matter_id)}
    async with core.database.transaction(read_only=True) as conn:
        command_index = await (await conn.execute(
            "SELECT embedding IS NULL AND embedding_profile IS NULL AS unindexed FROM memory.episodes WHERE episode_id=%s",
            (command_observation.episode_id,))).fetchone()
    assert command_index["unindexed"]  # The fixture indexed only the explicit records above.
    listing = await core.retrieval.query(bob, replace(related_plan, query="", coverage_mode="list"), budget())
    assert {(item.kind, item.id) for item in listing.items} == {("hypothesis", hypothesis.claim_id),
        ("matter", matter.matter_id), ("episode", command_observation.episode_id)}
    command_unit = next(item for item in listing.items if item.kind == "episode")
    assert command_unit.matter_id == matter.matter_id and command_unit.assertion == "observed"
    assert command_unit.actual_result == command_observation.actual_result and command_unit.source_refs == matter.source_refs
    assert listing.next_cursor is None and not listing.coverage.complete
    assert listing.coverage.missing == command_unit.coverage.missing == ("coverage_unknown",)
    await core.sources.invalidate(bob, {"source_id": link_refs[0].source_id, "expected_revision": 1,
        "client_request_id": "withdraw_vector_relation"}, forgotten=True)
    withdrawn = await core.retrieval.query(bob, related_plan, budget())
    assert [(item.kind, item.id) for item in withdrawn.items] == [("matter", matter.matter_id)]
    withdrawn_listing = await core.retrieval.query(bob, replace(related_plan, query="", coverage_mode="list"), budget())
    assert {(item.kind, item.id) for item in withdrawn_listing.items} == {
        ("matter", matter.matter_id), ("episode", command_observation.episode_id)}
    remaining_observation = next(item for item in withdrawn_listing.items if item.kind == "episode")
    assert remaining_observation.actual_result == command_observation.actual_result and remaining_observation.source_refs == matter.source_refs
    assert all(await core.sources.check_many(bob, (remaining_observation.source_refs,)))


async def test_vector_exact_uses_selected_knowledge_revision_before_validity(core, ordinary_users):
    alice, _ = ordinary_users
    then = datetime.now(UTC) - timedelta(days=2)
    asked = then + timedelta(days=1)
    old = await fact(core, alice, "vector_time", "旧错误", valid_from=then)
    corrected = (await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
        "value": "纠正后的认识", "client_request_id": "vector_time_correction"})).data
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.claim_revisions SET recorded_at=%s WHERE claim_id=%s AND revision=1",
                           (then, old.claim_id))
    await indexed_vectors(core, (RecordRef("claim", old.claim_id, old.revision),
                                 RecordRef("claim", corrected.claim_id, corrected.revision)))
    base = MemoryQueryPlan(query="qzxv_unmatched_temporal_probe", evidence_kinds=("claim",))
    for plan, revision, value in ((base, corrected.revision, "纠正后的认识"),
        (replace(base, time_mode="valid_at", valid_at=asked), corrected.revision, "纠正后的认识"),
        (replace(base, time_mode="known_at", known_at=asked), old.revision, "旧错误")):
        page = await core.retrieval.query(alice, plan, budget())
        assert len(page.items) == 1 and page.items[0].revision == revision and page.items[0].value == value
        assert page.coverage.missing == ("ranked_selection",)


async def test_legacy_empty_lexical_remains_readable_before_optional_reindex(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await original(core, alice, "legacy_book", "看书")
    episode = await core.memory.observe_episode(alice, {"event_id": "legacy_book_episode", "summary": "看书",
        "occurred_at": datetime.now(UTC), "origin_kind": "human", "source_refs": refs})
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.episodes SET lexical='',recorded_at=NULL WHERE episode_id=%s", (episode.episode_id,))
    page = await core.retrieval.query(alice, MemoryQueryPlan(query="书", evidence_kinds=("episode",)), budget())
    assert [item.id for item in page.items] == [episode.episode_id]
    assert page.items[0].recorded_at is None and "recorded_at_unknown" in page.coverage.missing


async def test_counterexamples_remain_in_the_full_unit_source_closure(core, ordinary_users):
    alice, _ = ordinary_users
    value = await fact(core, alice, "interpreted_original", "咖啡", memory_kind="hypothesis", assertion="inferred")
    contrary = await original(core, alice, "interpreted_contrary", "但这次没有咖啡")
    complete = build_evidence_unit(replace(value, counterexample_refs=contrary))
    assert set(ref.source_id for ref in contrary) <= set(ref.source_id for ref in complete.source_refs)
    await core.sources.invalidate(alice, {"source_id": contrary[0].source_id, "expected_revision": 1,
        "client_request_id": "forget_interpreted_contrary"}, forgotten=True)
    assert not all(await core.sources.check_many(alice, (complete.source_refs,)))


async def test_withdrawn_counterexample_retires_interpretation_despite_independent_new_or_support(core, ordinary_users):
    alice, _ = ordinary_users
    own = await original(core, alice, "habit_source", "我通常喝咖啡")
    contrary = await original(core, alice, "habit_exception", "周末例外，我喝茶")
    new = await original(core, alice, "habit_independent", "我仍通常喝咖啡")
    async with core.database.transaction() as conn:
        support = await core.claims.revisions.support_on(conn, alice, (own,), contrary)
        value = await core.claims.revisions.write_claim_on(conn, alice, {"subject_id": alice.person_id,
            "speaker_id": alice.person_id, "predicate": "habit.drink", "value": "通常咖啡，周末例外",
            "conditions": {"exception": "weekend"}, "memory_kind": "hypothesis", "assertion": "inferred",
            "counterexample_refs": contrary}, support=support, claim_id="synthetic_interpretation_counterexample", expected_revision=0)
    async with core.database.transaction() as conn:
        support = await core.claims.revisions.support_on(conn, alice, (new,))
        previous = await core.claims.revisions._claim_head_on(conn, alice, value.claim_id, value.revision)
        reinforced = await core.claims.revisions.reinforce_on(conn, alice, previous, support)
    class CaptureIndexDependencies:
        def __init__(self):
            self.groups = []
        async def vector(self, *args, source_refs=(), **kwargs):
            self.groups.append(source_refs)
            raise AppError("synthetic_embedding_unconfigured", status=503)
    capture = CaptureIndexDependencies()
    core.workers.embeddings = capture
    await core.workers.index_object("hypothesis", value.claim_id, reinforced.revision)
    assert capture.groups and contrary[0].source_id in {ref.source_id for ref in capture.groups[0]}
    await core.sources.invalidate(alice, {"source_id": contrary[0].source_id, "expected_revision": 1,
        "client_request_id": "forget_habit_exception"}, forgotten=True)
    assert all(await core.sources.check_many(alice, (new,)))
    assert not (await core.retrieval.query(alice, MemoryQueryPlan(evidence_kinds=("hypothesis",), coverage_mode="list"), budget())).items
    assert not (await core.claims.history(alice, value.claim_id)).items
    with pytest.raises(AppError):
        await core.claims.get(alice, value.claim_id)
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT state,conditions,embedding FROM memory.claim_revisions WHERE claim_id=%s AND revision=%s",
                                       (value.claim_id, reinforced.revision))).fetchone()
    assert row["state"] == "unsupported" and row["conditions"] == {} and row["embedding"] is None


async def test_valid_truth_and_knowledge_time_choose_different_versions_and_unknown_stays_unknown(core, ordinary_users):
    alice, _ = ordinary_users
    then = datetime.now(UTC) - timedelta(days=2)
    asked = then + timedelta(days=1)
    old = await fact(core, alice, "time_original", "误记的咖啡", valid_from=then)
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.claim_revisions SET recorded_at=%s WHERE claim_id=%s", (then, old.claim_id))
    await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
        "value": "正确的是茶", "client_request_id": "time_correct"})
    plan = MemoryQueryPlan(evidence_kinds=("claim",), coverage_mode="list")
    valid = await core.retrieval.query(alice, replace(plan, time_mode="valid_at", valid_at=asked), budget())
    known = await core.retrieval.query(alice, replace(plan, time_mode="known_at", known_at=asked), budget())
    assert valid.items[0].value == "正确的是茶" and valid.items[0].revision == 2
    assert known.items[0].value == "误记的咖啡" and known.items[0].head_revision == 2
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.claim_revisions SET recorded_at=NULL WHERE claim_id=%s", (old.claim_id,))
    unknown = await core.retrieval.query(alice, replace(plan, time_mode="known_at", known_at=asked), budget())
    assert not unknown.items and "recorded_at_unknown:claim" in unknown.coverage.missing


async def test_matter_valid_at_keeps_the_report_before_rescheduling(core, ordinary_users):
    alice, _ = ordinary_users
    then = datetime.now(UTC) - timedelta(days=2)
    old = (await core.claims.save_matter(alice, {"matter_id": "time_trip", "title": "周一旅行",
        "state": "planned", "expected_revision": 0, "client_request_id": "trip_plan"})).data
    await core.claims.save_matter(alice, {"matter_id": old.matter_id, "title": "周末旅行",
        "state": "rescheduled", "expected_revision": 1, "client_request_id": "trip_reschedule"})
    async with core.database.transaction() as conn:
        await conn.execute("UPDATE memory.matter_revisions SET observed_at=%s,recorded_at=%s WHERE matter_id=%s AND revision=1",
                           (then, then, old.matter_id))
    page = await core.retrieval.query(alice, MemoryQueryPlan(time_mode="valid_at", valid_at=then+timedelta(hours=1),
        evidence_kinds=("matter",), coverage_mode="list"), budget())
    assert page.items[0].revision == 1 and page.items[0].content.startswith("周一旅行")
    assert "matter_effective_time_inferred_from_report" in page.coverage.missing


async def test_complete_list_has_a_scoped_plan_bound_cursor_and_does_not_absorb_later_insertions(core, ordinary_users):
    alice, _ = ordinary_users
    ids = {(await fact(core, alice, "page_"+str(i), "苹果"+str(i))).claim_id for i in range(5)}
    plan = MemoryQueryPlan(evidence_kinds=("claim",), coverage_mode="list")
    first = await core.retrieval.query(alice, plan, budget(), limit=2)
    assert first.next_cursor and not first.coverage.complete
    added = await fact(core, alice, "page_later", "后来新增")
    with pytest.raises(AppError, match="memory_query_cursor_stale"):
        await core.retrieval.query(alice, replace(plan, subject_ids=(alice.person_id,)), budget(), cursor=first.next_cursor)
    gathered = list(first.items)
    cursor = first.next_cursor
    while cursor:
        page = await core.retrieval.query(alice, plan, budget(), cursor=cursor, limit=2)
        gathered.extend(page.items)
        cursor = page.next_cursor
    assert {item.id for item in gathered} == ids and added.claim_id not in ids
    assert len(gathered) == 5 and page.coverage.complete


async def test_conflict_has_both_complete_sides_and_whole_unit_budget(core, ordinary_users):
    alice, _ = ordinary_users
    first = await fact(core, alice, "coffee_side_one", "咖啡", conditions={"day": "weekend"})
    second = await fact(core, alice, "coffee_side_two", "咖啡", polarity=False, conditions={"day": "weekend"})
    proof = await original(core, alice, "coffee_conflict", "我刚才关于周末喝不喝咖啡的两种说法冲突")
    async with core.database.transaction() as conn:
        support = await core.claims.revisions.support_on(conn, alice, (proof,), (*first.source_refs, *second.source_refs))
        await core.claims.revisions.links.add_on(conn, alice, link_id="synthetic_coffee_conflict",
            from_ref=RecordRef("claim", first.claim_id, first.revision), to_ref=RecordRef("claim", second.claim_id, second.revision),
            relation_kind="contradicts", support=support)
    plan = MemoryQueryPlan(query="咖啡", evidence_kinds=("claim",))
    page = await core.retrieval.query(alice, plan, budget(), limit=1)
    unit = page.items[0]
    relation = unit.relations[0]
    assert {relation.from_evidence["polarity"], relation.to_evidence["polarity"]} == {True, False}
    assert relation.from_evidence["conditions"] == relation.to_evidence["conditions"] == {"day": "weekend"}
    assert {ref.source_id for ref in unit.source_refs} >= {ref.source_id for ref in (*first.source_refs, *second.source_refs, *proof)}
    assert first.source_refs[0].source_id not in index_text(unit) and "weekend" in index_text(unit)
    assert relation.to_evidence["id"] in evidence_text(unit)
    with pytest.raises(AppError, match="memory_evidence_unit_budget_exceeded"):
        await core.retrieval.query(alice, replace(plan, budget={"max_tokens": 100}), budget(), limit=1)


async def test_rerank_rechecks_sources_and_drops_the_whole_unit(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    old = await fact(core, alice, "rerank_revoke", "咖啡", conditions={"after": "dinner"})
    core.retrieval.settings = {"vector": False, "rerank": True}
    async def rerank(request):
        document = json.loads(request.native_payload["documents"][0])
        assert document["conditions"] == {"after": "dinner"} and document["assertion"] == "self_confirmed"
        await core.sources.invalidate(alice, {"source_id": old.source_refs[0].source_id,
            "expected_revision": 1, "client_request_id": "withdraw_during_rerank"}, forgotten=True)
        return SimpleNamespace(receipt=SimpleNamespace(status="succeeded"), native_response={"results": [{"index": 0}]})
    monkeypatch.setattr(core.models, "generate", rerank)
    page = await core.retrieval.query(alice, MemoryQueryPlan(query="咖啡", evidence_kinds=("claim",)), budget())
    assert not page.items and "memory_revision_changed" in page.coverage.missing
    historical = await core.retrieval.query(alice, MemoryQueryPlan(time_mode="known_at", known_at=datetime.now(UTC),
        evidence_kinds=("claim",), coverage_mode="list"), budget())
    assert not historical.items


async def test_context_rechecks_background_revision_even_when_old_sources_remain_readable(core, ordinary_users, monkeypatch):
    alice, _ = ordinary_users
    old = await fact(core, alice, "background_revision", "咖啡")
    async def changed_during_search(context, query, request_budget, **kwargs):
        await core.claims.correct(alice, {"claim_id": old.claim_id, "expected_revision": old.revision,
            "value": "其实是茶", "client_request_id": "background_revision_change"})
        return (), ()
    monkeypatch.setattr(core.retrieval, "search", changed_during_search)
    pack = await core.assembler.assemble(alice, "chat", "咖啡", budget(),
        contributions=(ContextContribution("input", "", old.source_refs, alice.scope_ids, 100, True),))
    assert not any(item.kind == "memory_evidence" for item in pack.materials)
    assert "memory_revision_changed" in pack.missing
    assert all(await core.sources.check_many(alice, (old.source_refs,)))


async def test_model_catalog_query_uses_time_plan_and_propagates_the_full_source_closure(core, ordinary_users):
    alice, _ = ordinary_users
    actual = await fact(core, alice, "catalog_knowledge", "咖啡", conditions={"day": "weekend"})
    catalog = Catalog()
    register_tools(catalog, core.memory, core.retrieval)
    handler = catalog.handler("memory.search")
    assert {"time_mode", "known_at", "coverage_mode", "subject_ids"} <= set(handler.definition.input_schema["properties"])
    action = Action("synthetic_query_action", "synthetic_query_intent", "memory.search", core.actor, alice,
        {"subject_ids": [alice.person_id], "time_mode": "known_at", "known_at": datetime.now(UTC).isoformat(),
         "evidence_kinds": ["claim"], "coverage_mode": "list"}, OriginKind.HUMAN,
        actual.source_refs, expression_id="turn:synthetic_query", deadline_at=budget().deadline_at)
    receipt = await handler.execute(action, Attempt("synthetic_query_attempt", action.action_id))
    item = receipt.result["items"][0]
    assert item["id"] == actual.claim_id and item["conditions"] == {"day": "weekend"}
    assert {body_hash(ref) for ref in item["source_refs"]} <= {body_hash(ref) for ref in receipt.result["source_refs"]}
    assert "memory.source.trace" in catalog.handlers
    named = replace(action, arguments={"subject_aliases": ["alice"], "evidence_kinds": ["claim"], "coverage_mode": "list"})
    named_receipt = await handler.execute(named, Attempt("synthetic_named_attempt", named.action_id))
    assert {item["subject_id"] for item in named_receipt.result["resolved_subjects"]} == {alice.person_id}
    unresolved = replace(action, arguments={"subject_aliases": ["synthetic_missing_person"], "coverage_mode": "list"})
    unresolved_receipt = await handler.execute(unresolved, Attempt("synthetic_unresolved_attempt", unresolved.action_id))
    assert not unresolved_receipt.result["items"] and "subject_unresolved" in unresolved_receipt.result["coverage"]["missing"]


async def test_original_followup_is_range_bound_once_per_foreground_and_keeps_actual_action_identity(core, ordinary_users):
    alice, _ = ordinary_users
    refs = await original(core, alice, "trace_original", "0123456789")
    selected = SourceRef(refs[0].source_id, refs[0].revision, {"start": 2, "end": 7})
    args = {"selections": [{"source_ref": wire(selected), "position": 3, "max_characters": 3}]}
    action = Action("synthetic_trace_action", "synthetic_trace_intent", "memory.source.trace", core.actor, alice,
        args, OriginKind.HUMAN, (selected,), expression_id="turn:synthetic_trace", deadline_at=budget().deadline_at)
    trace = SourceTrace(core.memory)
    result = await trace.read(action)
    assert result["items"][0]["content"] == "345" and result["lookup_used"]
    assert not result["coverage"].complete
    with pytest.raises(AppError, match="memory_source_lookup_already_used"):
        await trace.read(replace(action, action_id="synthetic_second_trace", intent_key="another_tool_call"))
    with pytest.raises(AppError, match="memory_source_reference_not_selected"):
        trace.selections(replace(action, arguments={"selections": [{"source_ref": wire(refs[0])}]}))
    with pytest.raises(TimeoutError):
        await trace.read(replace(action, deadline_at=datetime.now(UTC)-timedelta(seconds=1)))
    async with core.database.transaction(read_only=True) as conn:
        row = await (await conn.execute("SELECT result FROM runtime.commands WHERE operation='memory.source.trace' AND principal_id=%s",
                                       (alice.principal_id,))).fetchone()
    assert row["result"]["action_id"] == action.action_id and "345" not in json.dumps(row["result"])
