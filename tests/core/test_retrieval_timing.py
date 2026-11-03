"""Default foreground entrypoints admit real remote phases without inflating SQL."""

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

from aiohttp import web
import pytest

from tianshu.capabilities.catalog import Catalog
from tianshu.conversation.context.assembly import ContextAssembler
from tianshu.memory.embedding import Embeddings
from tianshu.memory.module import create_module
from tianshu.memory.retrieval import MemoryRetrieval
from tianshu.memory.retrieval_budget import foreground_seconds
from tianshu.memory.tools import register_tools
from tianshu.memory.types import MemoryEvidenceUnit
from tianshu.models.types import ModelReceipt, ModelRequest, ModelResult
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.types import Action, Attempt, Coverage, OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def context():
    return TrustedContext("owner", "actor", "person", "chat", ("scope",), ("person",), 1,
        OriginKind.HUMAN, conversation_kind="private", delivery_allowed=True)


def unit(identifier):
    return MemoryEvidenceUnit("claim", identifier, 1, "actor", "person", "person", "self_confirmed",
        "synthetic " + identifier, {}, None, None, None, None, None, "current", "active",
        (SourceRef("source:" + identifier, 1),), ("scope",), Coverage(True))


class Authority:
    async def check(self, *_args):
        pass

    async def check_many(self, _context, groups):
        return tuple(True for _ in groups)


def pipeline(*, settings=None):
    units = {value.id: value for value in (unit("first"), unit("second"))}
    rows = [{"_kind": "claim", "_id": value.id, "revision": 1, "_content": value.content}
            for value in units.values()]
    local = []

    class Models:
        def __init__(self):
            self.requests = []
            self.after_queue_allowances = []

        async def embedding_profile(self):
            return {"dimensions": 1024, "normalization": "none"}

        async def generate(self, request):
            self.requests.append(request)
            if request.max_queue_wait is not None:
                self.after_queue_allowances.append(request.budget.remaining()-request.max_queue_wait)
            payload = ({"data": [{"embedding": [1.0] + [0.0] * 1023}]} if request.function == "embed"
                       else {"results": [{"index": 1}, {"index": 0}]})
            return SimpleNamespace(receipt=SimpleNamespace(status="succeeded"), native_response=payload)

    class Store:
        def note(self, name, budget):
            local.append((name, budget.remaining()))

        async def coverage_gaps(self, _context, _plan, budget):
            self.note("coverage", budget)
            return ()

        async def lexical(self, _context, _plan, budget):
            self.note("lexical", budget)
            return rows

        async def has_vectors(self, _context, _plan, _profile, budget):
            self.note("vector_exists", budget)
            return True

        async def vectors(self, _context, _plan, _vector, _profile, budget):
            self.note("vector_sql", budget)
            return [], False

    class Memory:
        database = None
        identity = Authority()
        settings = {}

        async def projection(self, *_args, **_kwargs):
            return {"evidence_units": (), "coverage": Coverage(True)}

        async def relationship(self, *_args):
            return {"source_refs": ()}

    models, memory, store = Models(), Memory(), Store()
    memory.models = models
    retrieval = MemoryRetrieval(memory, Embeddings(None, models, None),
        {"vector": True, "rerank": True, **(settings or {})})
    retrieval.store = store

    async def checked(_context, _plan, candidates, budget, **_kwargs):
        store.note("source_check", budget)
        return tuple((row, units[row["_id"]]) for row in candidates), ()

    async def revalidate(_context, records, _budget):
        return tuple(units[record.id] for record in records), ()

    retrieval._checked, retrieval.revalidate = checked, revalidate
    return memory, models, retrieval, local


@pytest.mark.parametrize("entry", ["chat", "tool", "query"])
async def test_default_foreground_entrypoints_execute_both_thirty_second_phases(entry):
    memory, models, retrieval, local = pipeline()
    current = context()
    if entry == "chat":
        parent = RequestBudget(datetime.now(UTC)+timedelta(minutes=15))
        pack = await ContextAssembler(memory.identity, memory, retrieval).assemble(current, "chat", "synthetic", parent)
        assert any(item.kind == "memory_evidence" for item in pack.materials)
        assert all(request.business_deadline_at < parent.deadline_at for request in models.requests)
    elif entry == "tool":
        catalog = Catalog()
        register_tools(catalog, memory, retrieval)
        action = Action("query", "query", "memory.search", "actor", current, {"query": "synthetic"}, OriginKind.HUMAN)
        result = await catalog.handler("memory.search").execute(action, Attempt("attempt", action.action_id))
        assert len(result.result["items"]) == 2
    else:
        app = web.Application()

        async def authorized(*_args, **_kwargs):
            return current

        async def unused(*_args, **_kwargs):
            pytest.fail("Only the Memory query route may be called")

        memory.identity.for_request = authorized
        memory.claims = SimpleNamespace(correct=unused, share_ordinary=unused, save_matter=unused)
        memory.save_relationship = unused
        create_module(memory, retrieval, None, memory.identity).register_routes(app, None)
        handler = next(route.handler for route in app.router.routes() if route.resource.canonical == "/api/v1/memory/query")

        async def body():
            return {"actor_id": "actor", "query": "synthetic"}

        response = await handler(SimpleNamespace(json=body, **{"principal_id": "owner"}))
        assert len(json.loads(response.text)["items"]) == 2
    assert [request.function for request in models.requests] == ["embed", "rerank"]
    for request in models.requests:
        assert request.execution_timeout_seconds == 30.0
        assert request.business_deadline_at == request.budget.deadline_at
    assert all(seconds >= 30.0 for seconds in models.after_queue_allowances)
    assert local and all(seconds <= (0.8 if name.startswith("vector_") else 2.0) for name, seconds in local)


@pytest.mark.parametrize("entry", ["chat", "tool"])
async def test_short_upstream_deadline_keeps_lexical_evidence_without_starting_remote_work(entry):
    memory, models, retrieval, _local = pipeline()
    current = context()
    parent = RequestBudget(datetime.now(UTC)+timedelta(seconds=2))
    if entry == "chat":
        pack = await ContextAssembler(memory.identity, memory, retrieval).assemble(current, "chat", "synthetic", parent)
        missing = pack.missing
        assert len([item for item in pack.materials if item.kind == "memory_evidence"]) == 2
    else:
        catalog = Catalog()
        register_tools(catalog, memory, retrieval)
        action = Action("query", "query", "memory.search", "actor", current, {"query": "synthetic"},
            OriginKind.HUMAN, deadline_at=parent.deadline_at)
        result = await catalog.handler("memory.search").execute(action, Attempt("attempt", action.action_id))
        missing = result.coverage.missing
        assert len(result.result["items"]) == 2
    assert {"embedding_budget_insufficient", "rerank_budget_insufficient"} <= set(missing)
    assert models.requests == []


def test_foreground_budget_uses_enabled_phases_and_honors_explicit_author_response_limits():
    memory, _models, retrieval, _local = pipeline()
    assert foreground_seconds(retrieval.settings) == pytest.approx(70.6)
    assert foreground_seconds({"vector": True, "rerank": False}) == pytest.approx(40.6)
    assert foreground_seconds({"vector": False, "rerank": False}) == 8.0
    retrieval.settings["request_seconds"] = 2.0
    parent = RequestBudget(datetime.now(UTC)+timedelta(minutes=2))
    inherited = retrieval.foreground_budget(parent)
    assert 0 < inherited.remaining() <= 2.0
    assert inherited.trace_id == parent.trace_id and inherited.cancellation_id == parent.cancellation_id
    overridden = retrieval.foreground_budget(parent, seconds=1.0)
    assert 0 < overridden.remaining() <= 1.0


@pytest.mark.parametrize("original_seconds", [None, 120.0])
async def test_existing_index_step_keeps_frozen_timing_and_rejects_changed_payload(core, ordinary_users, monkeypatch, original_seconds):
    current, _ = ordinary_users
    profile = {"dimensions": 1024, "normalization": "none"}
    fingerprint = body_hash(profile)
    intent, text = "timing-index", "synthetic index"
    attempt = str(uuid5(NAMESPACE_URL, intent+":"+fingerprint))
    original = ModelRequest(intent, attempt, "embed", ServiceClass.ELASTIC, current,
        RequestBudget(datetime.now(UTC)+timedelta(seconds=120)), (), required_capabilities=frozenset({"embed"}),
        max_output_tokens=0, native_payload={"input": [text]}, execution_timeout_seconds=original_seconds,
        max_queue_wait=30.0)
    calls, reads = [], []
    unknown = ModelResult((), ModelReceipt(attempt, intent, "unknown", "submitted", "route", 1,
        "provider", "model", "openai.embedding", "quota"))

    async def generate(request):
        calls.append(request)
        return unknown

    async def result(attempt_id, **_kwargs):
        reads.append(attempt_id)
        return unknown

    async def status(attempt_id):
        assert attempt_id == attempt
        return unknown.receipt

    monkeypatch.setattr(core.models, "generate", generate)
    monkeypatch.setattr(core.models, "result", result, raising=False)
    monkeypatch.setattr(core.models, "status", status, raising=False)
    with pytest.raises(AppError, match="model_outcome_unknown"):
        await core.steps.run(intent+":"+fingerprint, original)
    async with core.database.transaction(read_only=True) as connection:
        before = await (await connection.execute("SELECT request,request_timeout_seconds FROM memory.model_steps WHERE step_id=%s", (intent+":"+fingerprint,))).fetchone()
    embeddings = Embeddings(core.database, core.models, core.steps)
    with pytest.raises(AppError, match="model_outcome_unknown"):
        await embeddings.vector(current, text, RequestBudget(datetime.now(UTC)+timedelta(seconds=5)),
            intent=intent, profile_snapshot=(profile, fingerprint))
    with pytest.raises(AppError, match="memory_model_step_identity_conflict"):
        await embeddings.vector(current, text+" changed", RequestBudget(datetime.now(UTC)+timedelta(seconds=5)),
            intent=intent, profile_snapshot=(profile, fingerprint))
    async with core.database.transaction(read_only=True) as connection:
        after = await (await connection.execute("SELECT request,request_timeout_seconds FROM memory.model_steps WHERE step_id=%s", (intent+":"+fingerprint,))).fetchone()
    assert after == before and calls == [original] and reads == [attempt]


async def test_new_index_step_gets_thirty_seconds_and_unavailable_step_read_never_submits():
    memory, models, _retrieval, _local = pipeline()
    profile = {"dimensions": 1024, "normalization": "none"}

    class Steps:
        error = AppError("memory_model_step_unavailable", status=404)

        async def recorded(self, _step_id):
            raise self.error

        async def run(self, _step_id, request):
            return await models.generate(request)

    steps = Steps()
    embeddings = Embeddings(None, models, steps)
    await embeddings.vector(context(), "index", RequestBudget(datetime.now(UTC)+timedelta(seconds=120)),
        intent="new-index", profile_snapshot=(profile, body_hash(profile)))
    assert len(models.requests) == 1 and models.requests[0].execution_timeout_seconds == 30.0
    assert models.requests[0].business_deadline_at is None and models.requests[0].max_queue_wait is None
    steps.error = AppError("step_read_unavailable", status=503)
    with pytest.raises(AppError, match="step_read_unavailable"):
        await embeddings.vector(context(), "index", RequestBudget(datetime.now(UTC)+timedelta(seconds=120)),
            intent="unreadable-index", profile_snapshot=(profile, body_hash(profile)))
    assert len(models.requests) == 1
