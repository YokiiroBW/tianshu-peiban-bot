"""Regression cases for lane independence, source closure and learning references."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from tianshu.memory.candidates import CandidateSelection, query_terms
from tianshu.memory.embedding import Embeddings
from tianshu.memory.evidence import select_supports_on
from tianshu.memory.learning_index import LearningEvidence
from tianshu.memory.retrieval import MemoryRetrieval, contributions
from tianshu.memory.retrieval_budget import MODEL_EXECUTION_SECONDS, VECTOR_SQL_SECONDS, foreground_seconds
from tianshu.memory.types import MemoryEvidenceUnit, MemoryQueryPlan
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from tianshu.types import Coverage, OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def context():
    return TrustedContext("owner", "actor", "person", "chat", ("scope",), ("person",), 1, OriginKind.HUMAN)


def deadline():
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=10))


def test_defaults_and_chinese_phrase_priority_keep_single_character_fallback():
    assert VECTOR_SQL_SECONDS == 2.0 and MODEL_EXECUTION_SECONDS == 30.0
    assert foreground_seconds({"vector": True, "rerank": True}) == 73.0
    assert foreground_seconds({"vector": True, "vector_sql_seconds": 4, "rerank": False}) == 47.0
    query, fragments = query_terms("智乃喜欢咖啡")
    assert query.split(" | ")[0] == "'智乃喜欢咖啡'"
    assert query.index("'智乃'") < query.index("'智'")
    assert "%智%" in fragments
    assert query_terms("智")[0] == "'智'"


async def test_lexical_and_vector_lanes_run_together_and_keep_the_successful_lane():
    retrieval = MemoryRetrieval(SimpleNamespace(database=None, models=None), None, {"vector": True})
    both_started = {"lexical": asyncio.Event(), "vector": asyncio.Event()}
    candidate = {"_kind": "claim", "_id": "a", "revision": 1, "_content": "智乃喜欢咖啡", "lexical": "智乃 咖啡"}

    async def lexical(_context, _plan, _budget):
        both_started["lexical"].set()
        await both_started["vector"].wait()
        return CandidateSelection((candidate,), ())

    async def vectors(_context, _plan, _budget, _refs, _service_class):
        both_started["vector"].set()
        await both_started["lexical"].wait()
        return [], ("embedding_unavailable",)

    async def checked(_context, _plan, rows, _budget, **_options):
        return tuple((row, row["_id"]) for row in rows), ()

    retrieval._lexical, retrieval._vectors, retrieval._checked = lexical, vectors, checked
    values, missing = await asyncio.wait_for(retrieval._ranked(context(), MemoryQueryPlan(query="智乃"),
        deadline(), limit=1, source_refs=(), service_class=ServiceClass.INTERACTIVE, purpose="conversation"), 1)
    assert values == ("a",) and "embedding_unavailable" in missing


async def test_complete_large_and_support_is_proved_in_chunks_without_shortening_the_result():
    class Connection:
        @asynccontextmanager
        async def transaction(self):
            yield

    class Sources:
        calls = []

        async def check_many_on(self, _connection, _context, groups):
            self.calls.extend(groups)
            assert all(len(group) <= 2048 for group in groups)
            return tuple(all(ref.source_id != "withdrawn" for ref in group) for group in groups)

    refs = tuple(SourceRef("source-" + str(index), 1) for index in range(2050))
    authority = Sources()
    selected = await select_supports_on(authority, Connection(), context(), [{"support_groups": [wire(refs)]}])
    assert len(selected[0]) == 2050 and len(authority.calls) == 2
    invalid = (*refs, SourceRef("withdrawn", 1))
    assert await select_supports_on(authority, Connection(), context(), [{"support_groups": [wire(invalid)]}]) == [None]


def test_equal_dimensions_do_not_allow_a_result_from_another_embedding_route():
    profile = {"dimensions": 1024, "normalization": "none", "route_id": "a", "model": "model-a"}
    result = SimpleNamespace(receipt=SimpleNamespace(status="succeeded", route_id="b", model="model-b"),
        native_response={"data": [{"embedding": [1.0] + [0.0] * 1023}]})
    with pytest.raises(AppError, match="embedding_profile_rebuild_required"):
        Embeddings.decode(result, profile, "fingerprint")


async def test_embedding_space_ignores_quota_metadata_but_changes_with_model_identity():
    class Profiles:
        value = {"dimensions": 1024, "normalization": "none", "model_version": "a", "quota_group": "first",
                 "route_id": "route", "route_revision": 1}

        async def embedding_profile(self):
            return dict(self.value)

    models = Profiles()
    embeddings = Embeddings(None, models, None)
    before = (await embeddings.profile())[1]
    models.value = {**models.value, "quota_group": "second", "route_revision": 2}
    assert (await embeddings.profile())[1] == before
    models.value = {**models.value, "model_version": "b"}
    assert (await embeddings.profile())[1] != before


async def test_learning_reference_is_qualified_by_growth_and_survives_as_typed_contribution():
    source = SourceRef("learning-source", 1)
    row = {"_kind": "learning", "_id": "rule-a", "revision": 3, "semantic_revision": 2,
           "scope_ids": ["scope"], "source_refs": wire((source,)), "recorded_at": datetime.now(UTC)}
    calls = []

    async def resolve(_context, refs, *, purpose):
        calls.append((refs, purpose))
        return ({"rule_id": "rule-a", "revision": 4, "semantic_revision": 2, "state": "active",
                 "eligible": True, "compatible": True, "source_refs": (source,), "scope_ids": ("scope",),
                 "text": "不喜欢咖啡", "conditions": {"when": "早晨"}},)

    retrieval = MemoryRetrieval(SimpleNamespace(database=None, models=None), None, behavior_resolver=resolve)
    selected, missing = await retrieval._learning_units(context(), MemoryQueryPlan(), (row,), "tool", ())
    assert not missing and calls[0][1] == "tool"
    unit = selected[0][1]
    contribution = contributions((unit,))[0]
    assert contribution.behavior_ref == {"rule_id": "rule-a", "revision": 3, "semantic_revision": 2}
    assert contribution.source_refs == (source,) and "不喜欢咖啡" in contribution.text

    async def revoked(_context, _refs, *, purpose):
        return ({"rule_id": "rule-a", "eligible": False, "compatible": False, "state": "retracted"},)

    retrieval.bind_behavior_resolver(revoked)
    assert await retrieval._learning_units(context(), MemoryQueryPlan(), (row,), "tool", ()) == ((), ())


async def test_reflection_uses_growth_learning_records_and_preserves_typed_reference():
    source = SourceRef("reflection-source", 1)
    row = {"_kind": "learning", "kind": "reflection", "_id": "proposal-a", "revision": 2,
           "semantic_revision": 2, "scope_ids": ["scope"], "source_refs": wire((source,)),
           "recorded_at": datetime.now(UTC)}
    calls = []

    async def resolve(_context, refs, *, purpose):
        calls.append((refs, purpose))
        return ({"kind": "reflection", "id": "proposal-a", "revision": 2, "state": "active",
                 "source_refs": (source,), "scope_ids": ("scope",), "content": "这是一次反思",
                 "conditions": {}},)

    retrieval = MemoryRetrieval(SimpleNamespace(database=None, models=None), None, learning_resolver=resolve)
    selected, missing = await retrieval._learning_units(context(), MemoryQueryPlan(), (row,), "tool", ())
    assert not missing and calls == [(({"kind": "reflection", "id": "proposal-a"},), "tool")]
    unit = selected[0][1]
    assert unit.learning_ref == {"kind": "reflection", "id": "proposal-a", "revision": 2}
    assert not unit.behavior_ref and contributions((unit,))[0].semantic_role == "learning_reference"
    changed = {**row, "revision": 1}
    assert (await retrieval._learning_units(context(), MemoryQueryPlan(), (changed,), "history", ()))[1] == (
        "learning_revision_history_unavailable",)


@pytest.mark.parametrize("withdrawn", [False, True])
async def test_rerank_preserves_the_full_input_source_closure_or_discards_invalid_order(withdrawn):
    refs = (SourceRef("query", 1), SourceRef("first", 1), SourceRef("second", 1))
    rows = [{"_kind": "claim", "_id": name, "revision": 1, "_content": name, "lexical": name}
            for name in ("first", "second")]
    units = {row["_id"]: MemoryEvidenceUnit("claim", row["_id"], 1, "actor", "person", "person",
        "reported", row["_id"], {}, None, None, None, datetime.now(UTC), None, "current", "active",
        (refs[index + 1],), ("scope",), Coverage(True)) for index, row in enumerate(rows)}

    class Models:
        requests = []

        async def generate(self, request):
            self.requests.append(request)
            return SimpleNamespace(receipt=SimpleNamespace(status="succeeded"),
                                   native_response={"results": [{"index": 1}, {"index": 0}]})

    models = Models()
    retrieval = MemoryRetrieval(SimpleNamespace(database=None, models=models), None,
                                {"vector": False, "rerank": True, "rerank_seconds": 1})

    async def lexical(_context, _plan, _budget):
        return CandidateSelection(tuple(rows), ())

    async def checked(_context, _plan, candidates, _budget, *, limit, derivation_refs=(), **_options):
        selected = []
        for row in candidates:
            unit = units[row["_id"]]
            selected.append((row, replace(unit, source_refs=unique_refs(unit.source_refs, derivation_refs),
                                          derivation_refs=tuple(derivation_refs))))
            if len(selected) >= limit:
                break
        return tuple(selected), ()

    async def valid(_context, checked_refs, _budget):
        assert {item.source_id for item in checked_refs} == {item.source_id for item in refs}
        return not withdrawn

    retrieval._lexical, retrieval._checked, retrieval._derivation_valid = lexical, checked, valid
    result, missing = await retrieval._ranked(context(), MemoryQueryPlan(query="内容"), deadline(),
        limit=1, source_refs=(refs[0],), service_class=ServiceClass.INTERACTIVE, purpose="conversation")
    assert len(models.requests) == 1
    if withdrawn:
        assert result[0].id == "first" and "rerank_inputs_revoked" in missing
    else:
        assert result[0].id == "second" and {item.source_id for item in result[0].source_refs} == {item.source_id for item in refs}
        assert {item.source_id for item in result[0].derivation_refs} == {item.source_id for item in refs}


@pytest.mark.parametrize("rule_changed", [False, True])
async def test_rerank_behavior_dependencies_follow_even_a_selected_nonlearning_item(rule_changed):
    source = SourceRef("source", 1)
    reference = {"rule_id": "rule-a", "revision": 2, "semantic_revision": 1}
    rows = [{"_kind": kind, "_id": name, "revision": 1, "_content": name, "lexical": name}
            for kind, name in (("learning", "rule-a"), ("claim", "fact"))]
    common = dict(revision=1, actor_id="actor", subject_id="person", speaker_id="person", assertion="reported",
        conditions={}, occurred_at=None, valid_from=None, valid_until=None, recorded_at=datetime.now(UTC),
        matter_id=None, current_status="current", state="active", source_refs=(source,), scope_ids=("scope",),
        coverage=Coverage(True))
    units = {"rule-a": LearningEvidence(kind="learning", id="rule-a", content="语气偏好",
                                       behavior_ref=reference, **common),
             "fact": MemoryEvidenceUnit(kind="claim", id="fact", content="事实", **common)}

    class Models:
        async def generate(self, request):
            assert request.behavior_refs == (reference,) and request.behavior_purpose == "conversation"
            return SimpleNamespace(receipt=SimpleNamespace(status="succeeded"),
                                   native_response={"results": [{"index": 1}, {"index": 0}]})

    async def resolve(_context, selected, *, purpose):
        return ({"rule_id": "rule-a", "eligible": not rule_changed, "compatible": not rule_changed},)

    retrieval = MemoryRetrieval(SimpleNamespace(database=None, models=Models()), None,
        {"vector": False, "rerank": True, "rerank_seconds": 1}, behavior_resolver=resolve)

    async def lexical(_context, _plan, _budget):
        return CandidateSelection(tuple(rows), ())

    checked_calls = 0

    async def checked(_context, _plan, candidates, _budget, *, limit, derivation_refs=(), behavior_refs=(), **_options):
        nonlocal checked_calls
        checked_calls += 1
        selected = []
        for row in candidates:
            if rule_changed and checked_calls > 1 and row["_kind"] == "learning":
                continue
            unit = units[row["_id"]]
            if behavior_refs:
                unit = replace(unit, behavior_refs=behavior_refs)
            selected.append((row, unit))
            if len(selected) >= limit:
                break
        return tuple(selected), ()

    async def valid(*_args):
        return True

    retrieval._lexical, retrieval._checked, retrieval._derivation_valid = lexical, checked, valid
    result, missing = await retrieval._ranked(context(), MemoryQueryPlan(query="语气"), deadline(), limit=1,
        source_refs=(), service_class=ServiceClass.INTERACTIVE, purpose="conversation")
    if rule_changed:
        assert result[0].id == "fact" and "rerank_behaviors_unavailable" in missing
    else:
        assert result[0].id == "fact" and result[0].behavior_refs == (reference,)
        assert contributions(result)[0].behavior_refs == (reference,)
