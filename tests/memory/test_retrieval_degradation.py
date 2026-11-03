"""One retrieval path preserves verified branches without weakening final authority."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import psycopg
import pytest

from tianshu.memory.candidates import CandidateSelection
from tianshu.memory.indexing import evidence_text, index_text, rerank_text
import tianshu.memory.retrieval as retrieval_module
from tianshu.memory.retrieval import MemoryRetrieval
from tianshu.memory.types import MemoryEvidenceUnit, MemoryQueryPlan, RecordRef, SemanticLink
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage, OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def context():
    return TrustedContext("owner", "actor", "person", "chat", ("scope",), ("person",), 1,
                          OriginKind.HUMAN)


def budget(seconds=90):
    return RequestBudget(datetime.now(UTC) + timedelta(seconds=seconds))


def evidence(identifier):
    return MemoryEvidenceUnit("claim", identifier, 1, "actor", "person", "person", "self_confirmed",
        "我周末不喝咖啡 " + identifier, {"day": "weekend"}, None, None, None, datetime.now(UTC), None,
        "current", "active", (SourceRef("source:" + identifier, 1),), ("scope",), Coverage(True), polarity=False)


def pipeline(count=4, *, settings=None):
    values = {"r%02d" % index: evidence("r%02d" % index) for index in range(count)}
    rows = [{"_kind": "claim", "_id": value.id, "revision": 1, "_content": value.content,
             "lexical": "shared", "scope_ids": ["scope"]} for value in values.values()]
    calls = SimpleNamespace(local=[], reread=[], hydrated=[], models=[], embeddings=[], source_groups=[], sql=[])
    revoked, changed, errors = set(), set(), {}

    class Connection:
        async def execute(self, sql):
            calls.sql.append(sql)

    class Database:
        @asynccontextmanager
        async def transaction(self, phase_budget, *, read_only=False):
            calls.local.append(phase_budget)
            assert read_only
            yield Connection()

    class Authority:
        async def check(self, *_args):
            if "identity" in errors:
                raise errors["identity"]

        async def resolve_memory_subjects_on(self, _conn, _context, ids, **_kwargs):
            if "display" in errors:
                raise errors["display"]
            return tuple({"subject_id": identifier, "display_name": "本人", "kind": "person"} for identifier in ids)

    class Sources:
        async def check_many_on(self, _conn, _context, groups):
            calls.source_groups.extend(groups)
            if "sources" in errors:
                raise errors["sources"]
            return tuple(not any(ref.source_id in revoked for ref in refs) for refs in groups)

    class Store:
        settings = {}
        lexical_rows = tuple(rows)
        lexical_missing = ()
        vector_rows = ()
        before_reread = None

        async def coverage_gaps(self, *_args):
            if "coverage" in errors:
                raise errors["coverage"]
            return ()

        async def lexical(self, _context, _plan, phase_budget):
            calls.local.append(phase_budget)
            if "lexical" in errors:
                raise errors["lexical"]
            return CandidateSelection(self.lexical_rows, self.lexical_missing)

        async def has_vectors(self, *_args):
            if "vector_exists" in errors:
                raise errors["vector_exists"]
            return True

        async def vectors(self, *_args):
            if "vector" in errors:
                raise errors["vector"]
            return self.vector_rows, False

        async def reread(self, conn, _context, _plan, selected, *, snapshot=None):
            calls.reread.append((tuple(row["_id"] for row in selected), conn, snapshot))
            if self.before_reread:
                await self.before_reread(selected)
            if "final" in errors:
                raise errors["final"]
            return [None if row["_id"] in changed else row for row in selected]

        async def page(self, _context, _plan, phase_budget, *, after, snapshot, limit):
            calls.local.append(phase_budget)
            if "page" in errors:
                raise errors["page"]
            return [row for row in rows if after[0] is None or (row["_kind"], row["_id"]) > after][:limit + 1]

    class Embeddings:
        async def profile(self):
            if "profile" in errors:
                raise errors["profile"]
            return {}, "fixture"

        async def vector(self, _context, _query, phase_budget, **kwargs):
            calls.embeddings.append((phase_budget, kwargs))
            if "embedding" in errors:
                raise errors["embedding"]
            return [1.0, 0.0], "fixture"

    class Models:
        after_rerank = None

        async def generate(self, request):
            calls.models.append(request)
            if self.after_rerank:
                await self.after_rerank(request)
            if "rerank" in errors:
                raise errors["rerank"]
            documents = [json.loads(value) for value in request.native_payload["documents"]]
            order = sorted(range(len(documents)), key=lambda index: documents[index]["id"])
            return SimpleNamespace(receipt=SimpleNamespace(status="succeeded"),
                native_response={"results": [{"index": index} for index in order]})

    models, store = Models(), Store()
    memory = SimpleNamespace(database=Database(), models=models, identity=Authority(), sources=Sources())
    retrieval = MemoryRetrieval(memory, Embeddings(), {"vector": False, "rerank": False, **(settings or {})})
    retrieval.store = store

    async def hydrate(_conn, _context, _plan, selected):
        calls.hydrated.append(tuple(row["_id"] for row in selected))
        return [(row, values[row["_id"]]) for row in selected]

    retrieval._hydrate_on = hydrate
    return SimpleNamespace(retrieval=retrieval, store=store, models=models, values=values, rows=rows,
                           calls=calls, revoked=revoked, changed=changed, errors=errors)


async def test_partial_lexical_missing_keeps_completed_rows_and_runs_the_vector_lane():
    setup = pipeline(settings={"vector": True})
    setup.store.lexical_rows = tuple(setup.rows[:1])
    setup.store.lexical_missing = ("lexical_candidate_timeout:reflection",)
    setup.store.vector_rows = tuple(setup.rows[1:2])
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert {item.id for item in page.items} == {"r00", "r01"}
    assert "lexical_candidate_timeout:reflection" in page.coverage.missing and not page.coverage.complete
    assert len(setup.calls.embeddings) == 1 and len(setup.calls.reread) == 1
    assert setup.calls.source_groups


@pytest.mark.parametrize("empty_lexical", [False, True])
async def test_vector_existence_budget_unknown_keeps_lexical_with_missing_without_embedding(empty_lexical):
    setup = pipeline(settings={"vector": True})
    if empty_lexical:
        setup.store.lexical_rows = ()
    setup.errors["vector_exists"] = AppError("memory_vector_candidate_budget", status=503)
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert [item.id for item in page.items] == ([] if empty_lexical else ["r00", "r01"])
    assert page.coverage.missing == ("vector_candidate_budget", "ranked_selection")
    assert not page.coverage.complete and not setup.calls.embeddings and not setup.calls.models
    assert bool(setup.calls.source_groups) == (not empty_lexical)


async def test_vector_existence_budget_unknown_still_obeys_the_parent_deadline():
    setup = pipeline(settings={"vector": True})
    parent = budget()
    async def exhausted(*_args):
        object.__setattr__(parent, "_monotonic_deadline", 0)
        raise AppError("memory_vector_candidate_budget", status=503)
    setup.store.has_vectors = exhausted
    with pytest.raises(TimeoutError, match="request_deadline_exceeded"):
        await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), parent, limit=2)
    assert not setup.calls.embeddings and not setup.calls.reread


@pytest.mark.parametrize("phase,error", [
    ("vector_exists", AppError("permission_denied", status=403)),
    ("vector_exists", AppError("invalid_source_context", status=400)),
    ("vector_exists", asyncio.CancelledError()),
    ("vector_exists", AppError("memory_support_budget_exceeded", status=503)),
    ("vector_exists", AppError("memory_vector_candidate_budget", status=403)),
    ("vector", AppError("memory_vector_candidate_budget", status=503)),
])
async def test_vector_existence_budget_mapping_does_not_swallow_other_authority_or_phase_errors(phase, error):
    setup = pipeline(settings={"vector": True})
    setup.errors[phase] = error
    with pytest.raises(type(error)) as raised:
        await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert raised.value is error and not setup.calls.reread
    if phase == "vector_exists":
        assert not setup.calls.embeddings


@pytest.mark.parametrize("error", [TimeoutError(), psycopg.errors.QueryCanceled("statement timeout")])
async def test_lexical_lane_timeout_does_not_discard_vector_evidence(error):
    setup = pipeline(settings={"vector": True})
    setup.errors["lexical"] = error
    setup.store.vector_rows = tuple(setup.rows[:1])
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget())
    assert [item.id for item in page.items] == ["r00"]
    assert "lexical_candidate_timeout" in page.coverage.missing


async def test_optional_database_connection_failure_keeps_other_lanes():
    setup = pipeline(settings={"vector": True})
    setup.errors["lexical"] = psycopg.OperationalError("connection temporarily unavailable")
    setup.store.vector_rows = tuple(setup.rows[:1])
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget())
    assert [item.id for item in page.items] == ["r00"]
    assert "lexical_candidate_unavailable" in page.coverage.missing


@pytest.mark.parametrize("error", [psycopg.errors.InsufficientPrivilege("denied"),
    psycopg.errors.SyntaxError("invalid query"), PermissionError("denied")])
async def test_sql_privilege_and_invalid_query_errors_are_not_degraded(error):
    setup = pipeline()
    setup.errors["lexical"] = error
    with pytest.raises(type(error)) as raised:
        await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget())
    assert raised.value is error


async def test_rerank_timeout_preserves_the_base_order_and_complete_answer_text():
    setup = pipeline()
    expected = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    setup.retrieval.settings["rerank"] = True
    setup.errors["rerank"] = TimeoutError()
    actual = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert [item.id for item in actual.items] == [item.id for item in expected.items]
    assert "rerank_unavailable" in actual.coverage.missing and len(setup.calls.models) == 1
    assert json.loads(evidence_text(actual.items[0]))["source_refs"]
    assert "source_refs" not in setup.calls.models[0].native_payload["documents"][0]


@pytest.mark.parametrize("phase,code,status", [
    ("profile", "embedding_profile_not_configured", 503),
    ("embedding", "model_route_unavailable", 503),
    ("embedding", "model_capability_unverified", 422),
    ("rerank", "model_function_not_configured", 503),
    ("rerank", "model_capability_unverified", 422),
])
async def test_optional_model_configuration_keeps_lexical_fallback(phase, code, status):
    setup = pipeline(settings={"vector": True, "rerank": True})
    setup.errors[phase] = AppError(code, status=status)
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert len(page.items) == 2
    assert ("rerank_unavailable" if phase == "rerank" else code) in page.coverage.missing


@pytest.mark.parametrize("phase", ["coverage", "lexical", "profile", "vector", "rerank", "sources", "display"])
@pytest.mark.parametrize("code,status", [("permission_denied", 403), ("invalid_source_context", 400)])
async def test_authority_and_input_errors_are_never_optional_lane_failures(phase, code, status):
    setup = pipeline(settings={"vector": True, "rerank": True})
    error = AppError(code, status=status)
    setup.errors[phase] = error
    with pytest.raises(AppError) as raised:
        await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert raised.value is error


@pytest.mark.parametrize("phase", ["lexical", "embedding", "rerank", "sources"])
async def test_external_cancellation_propagates_out_of_optional_phases(phase):
    setup = pipeline(settings={"vector": True, "rerank": True})
    setup.errors[phase] = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)


async def test_final_recheck_only_hydrates_requested_rows_and_fills_a_withdrawn_whole_unit():
    setup = pipeline(30, settings={"rerank": True})
    setup.values["r00"] = replace(setup.values["r00"], source_refs=(SourceRef("source:r00", 1), SourceRef("opposite", 1)))
    async def withdraw(_request):
        setup.revoked.add("opposite")
    setup.models.after_rerank = withdraw
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert [item.id for item in page.items] == ["r01", "r02"]
    assert [len(rows) for rows in setup.calls.hydrated] == [24, 2, 1]
    assert "memory_evidence_unavailable" in page.coverage.missing and len(setup.calls.models) == 1
    assert all(ref.source_id != "opposite" for unit in page.items for ref in unit.source_refs)
    assert setup.calls.reread[1][1] is setup.calls.reread[2][1]
    assert len(setup.calls.local) == 3  # lexical, preparation, one final fill transaction
    assert all("set_config('jit','off',true)" in sql for sql in setup.calls.sql)


async def test_final_refill_timeout_keeps_only_previously_fully_checked_units():
    setup = pipeline()
    setup.revoked.add("source:r00")
    async def interrupt(_selected):
        if len(setup.calls.reread) > 1:
            raise psycopg.errors.QueryCanceled("statement timeout")
    setup.store.before_reread = interrupt
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=2)
    assert [item.id for item in page.items] == ["r01"]
    assert "memory_final_check_timeout" in page.coverage.missing
    assert len(setup.calls.local) == 2 and setup.calls.reread[0][1] is setup.calls.reread[1][1]


@pytest.mark.parametrize("rerank,withdrawn", [(False, 0), (False, 6), (True, 0), (True, 24)])
async def test_mmr_only_computes_the_needed_prefix_and_extends_in_the_same_order_on_refill(monkeypatch, rerank, withdrawn):
    setup = pipeline(128, settings={"rerank": rerank})
    for index, row in enumerate(setup.rows):
        row["vector"] = [1.0, 0.0] if index < 6 else [0.0, 1.0]
    # The first diverse pick is r06, followed by the remaining leading scores.
    # Both vector groups are then represented, so the rest follows base scores.
    order = ["r00", "r06", "r01", "r02", "r03", "r04", "r05"] + ["r%02d" % index for index in range(7, 128)]
    similarity, comparisons = retrieval_module.similarity, []
    def counted(left, right):
        comparisons.append((left["_id"], right["_id"]))
        return similarity(left, right)
    monkeypatch.setattr(retrieval_module, "similarity", counted)
    if rerank:
        async def withdraw(_request):
            setup.revoked.update("source:r%02d" % index for index in range(withdrawn))
        setup.models.after_rerank = withdraw
        expected = ["r%02d" % index for index in range(withdrawn, withdrawn + 6)]
        rounds = max(24, withdrawn + 6)
    else:
        setup.revoked.update("source:" + identifier for identifier in order[:withdrawn])
        expected = order[withdrawn:withdrawn + 6]
        rounds = withdrawn + 6
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=6)
    assert [item.id for item in page.items] == expected
    assert len(comparisons) == rounds * 128 - rounds * (rounds + 1) // 2
    assert len(comparisons) < 128 * (128 - 1) // 2
    assert len(setup.calls.models) == int(rerank)
    if rerank:
        assert len(setup.calls.models[0].native_payload["documents"]) == 24
    if withdrawn:
        final_reads = setup.calls.reread[1:] if rerank else setup.calls.reread
        assert all(conn is final_reads[0][1] for _, conn, _ in final_reads)


async def test_preparation_timeout_replays_the_most_relevant_unchecked_prefix_for_final_validation(monkeypatch):
    setup = pipeline(128, settings={"rerank": True})
    similarity, comparisons = retrieval_module.similarity, []
    def counted(left, right):
        comparisons.append((left["_id"], right["_id"]))
        return similarity(left, right)
    monkeypatch.setattr(retrieval_module, "similarity", counted)
    async def interrupt_preparation(_selected):
        if len(setup.calls.reread) == 1:
            raise TimeoutError()
    setup.store.before_reread = interrupt_preparation
    page = await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), budget(), limit=6)
    assert [item.id for item in page.items] == ["r%02d" % index for index in range(6)]
    assert [len(rows) for rows, _, _ in setup.calls.reread] == [24, 6]
    assert setup.calls.reread[1][0] == setup.calls.reread[0][0][:6]
    assert "memory_candidate_check_timeout" in page.coverage.missing and not setup.calls.models
    assert len(comparisons) == 24 * 128 - 24 * 25 // 2


async def test_parent_expiry_during_optional_work_stops_final_work_without_extending_the_deadline():
    setup = pipeline(settings={"rerank": True})
    parent = budget()
    deadline = parent.deadline_at
    async def expire(_request):
        object.__setattr__(parent, "_monotonic_deadline", 0)
        raise TimeoutError()
    setup.models.after_rerank = expire
    with pytest.raises(TimeoutError, match="request_deadline_exceeded"):
        await setup.retrieval.query(context(), MemoryQueryPlan(query="咖啡"), parent, limit=2)
    assert parent.deadline_at == deadline and len(setup.calls.reread) == 1


async def test_list_paging_keeps_snapshot_and_never_calls_optional_ranked_lanes():
    setup = pipeline(5, settings={"vector": True, "rerank": True})
    plan = MemoryQueryPlan(coverage_mode="list")
    collected, cursor, snapshots = [], None, []
    while True:
        page = await setup.retrieval.query(context(), plan, budget(), cursor=cursor, limit=2)
        collected.extend(unit.id for unit in page.items)
        snapshots.append(page.coverage.ranges[0]["as_of"])
        cursor = page.next_cursor
        if cursor is None:
            break
        assert not page.coverage.complete
    assert collected == ["r00", "r01", "r02", "r03", "r04"] and page.coverage.complete
    assert len(set(snapshots)) == 1 and not setup.calls.models and not setup.calls.embeddings
    setup.errors["page"] = TimeoutError()
    with pytest.raises(TimeoutError):
        await setup.retrieval.query(context(), plan, budget(), limit=2)


async def test_background_candidates_keep_separate_model_execution_windows_and_no_business_expiry():
    setup = pipeline(settings={"vector": True, "rerank": True})
    expired = budget(-1)
    page = await setup.retrieval.candidates(context(), "咖啡", expired, limit=2)
    assert len(page.items) == 2 and len(setup.calls.models) == len(setup.calls.embeddings) == 1
    embedding_budget, options = setup.calls.embeddings[0]
    assert options["execution_seconds"] == 30.0 and options["max_queue_wait"] is None
    assert options["business_deadline_at"] is None and options["service_class"] == ServiceClass.ELASTIC
    request = setup.calls.models[0]
    assert request.execution_timeout_seconds == 30.0 and request.max_queue_wait is None
    assert request.business_deadline_at is None and embedding_budget.remaining() <= 30.0


def test_rerank_document_preserves_semantic_sides_and_omits_the_evidence_ledger():
    now = datetime.now(UTC)
    own = replace(evidence("first"), content="周末不喝咖啡", valid_from=now, valid_until=now + timedelta(days=1),
        completion_basis={"kind": "human_report", "source_refs": [{"source_id": "completion-ledger", "revision": 1}]},
        actual_result={"execution": "completed", "effect": "confirmed", "delivery": "not_requested", "receipt_id": "receipt-ledger"},
        counterexamples=("只有工作日喝咖啡",), display_subjects=({"subject_id": "person", "display_name": "小明", "aliases": ("明",), "kind": "person"},))
    contrary = replace(evidence("other"), content="另一位说周末喝咖啡", subject_id="other-person", speaker_id="reporter", polarity=True)
    relation = SemanticLink("link-ledger", "actor", RecordRef("claim", "first", 1), RecordRef("claim", "other", 1),
        "contradicts", now, None, now, (SourceRef("link-source-ledger", 1),), ("scope",),
        from_evidence=wire(own), to_evidence=wire(contrary))
    own = replace(own, relations=(relation,), source_refs=(SourceRef("source-ledger", 1),) * 100)
    index_before = index_text(own)
    compact = json.loads(rerank_text(own))
    assert all(compact[key] == value for key, value in json.loads(index_before).items())
    assert compact["people"][0]["display_name"] == "小明" and compact["speaker_id"] == "person"
    assert compact["polarity"] is False and compact["conditions"] == {"day": "weekend"}
    assert compact["completion_basis"] == "human_report" and compact["counterexamples"] == ["只有工作日喝咖啡"]
    opposite = compact["relations"][0]["to_evidence"]
    assert opposite["content"] == "另一位说周末喝咖啡" and opposite["polarity"] is True
    assert opposite["speaker_id"] == "reporter" and compact["valid_from"] and compact["valid_until"]
    assert compact["actual_result"] == {"execution": "completed", "effect": "confirmed", "delivery": "not_requested"}
    assert all(marker not in rerank_text(own) for marker in ("source-ledger", "link-ledger", "completion-ledger", "receipt-ledger"))
    assert "source-ledger" in evidence_text(own) and index_text(own) == index_before
    assert len(rerank_text(own)) < len(evidence_text(own)) / 2
