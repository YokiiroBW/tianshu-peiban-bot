"""Evaluation-tool invariants only. Written for the final combined run; not run in development."""

from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace
import json

import pytest

from scripts.evaluation.memory_closure.broker import EvaluationModels
from scripts.evaluation.memory_closure.dataset import (COST_STORY_IDS, OLD_PRODUCTION_SHA,
    Story, comparison_key, stream_plan, write_json)
from scripts.evaluation.memory_closure.metering import aggregate_calls
from scripts.evaluation.memory_closure.recovery import plan as recovery_plan, validate_manifest
from scripts.evaluation.memory_closure.run import compare, observe, read_results
from scripts.evaluation.memory_closure.runtime import content_incomplete, progress_key
from tianshu.models.types import ModelMessage, ModelPart, ModelReceipt, ModelRequest
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext


def story():
    return Story("synthetic", "development", "evaluation_contract",
        (("A", "第一条"), ("B", "第二条"), ("A", "第三条"), ("C", "未来才说的内容")),
        "最后才提出的问题", ("人工判断项",), ("禁止结论",))


def test_stream_hides_future_question_and_rubric_and_pins_arrival_order():
    events = stream_plan(story(), {})
    assert events[0]["text"] == "第一条" and events[0]["flush"]
    assert "未来才说的内容" not in json.dumps(events[0], ensure_ascii=False, default=str)
    assert set(events[0]) == {"index", "speaker", "text", "occurred_at", "flush"}
    assert [event["index"] for event in events if event["flush"]] == [1, 4]
    assert all(a["occurred_at"] < b["occurred_at"] for a, b in zip(events, events[1:]))
    assert "required" not in story().execution_input()
    assert "forbidden" not in story().execution_input()
    assert comparison_key(story(), "same-corpus", {}) != comparison_key(story(), "same-corpus", {
        "stories": {"synthetic": {"flush_after": [1, 2, 4]}}})


def test_usage_recovery_counts_one_attempt_and_never_adds_cache_or_reasoning_to_total():
    receipt = {"status": "succeeded", "usage": {"input_tokens": 100, "output_tokens": 40,
        "cached_input_tokens": 60, "cache_write_tokens": 10, "metering": "reported",
        "raw": {"completion_tokens_details": {"reasoning_tokens": 30}}}}
    result = aggregate_calls([
        {"broker_attempt_id": "original", "receipt": None},
        {"broker_attempt_id": "original", "receipt": receipt},
        {"broker_attempt_id": "original", "receipt": receipt},
        {"broker_attempt_id": "unknown-original", "receipt": None}])
    assert result["attempt_identities"] == 2
    assert result["confirmed_broker_attempts"] == 1
    assert result["dispatch_unknown"] == 1
    assert result["input_tokens"]["known_sum"] == 100
    assert result["output_tokens"]["known_sum"] == 40
    assert result["reasoning_tokens"]["known_sum"] == 30
    assert result["cached_input_tokens"]["known_sum"] == 60
    assert not result["input_tokens"]["complete"]
    assert result["reported_cost_usd"]["known_sum"] is None
    assert result["total_tokens_or_fee"] is None


def test_old_result_truncation_is_reported_without_replacing_old_product_semantics():
    assert content_incomplete(SimpleNamespace(native_response={"choices": [{"finish_reason": "length"}]}))
    assert not content_incomplete(SimpleNamespace(native_response={"choices": [{"finish_reason": "stop"}]}))


def test_native_shrink_progress_is_not_lost_when_episode_cursor_is_unchanged():
    first = {"episode_id": "first", "revision": 1, "extraction_state": "pending", "extraction_cursor": {}}
    second = {**first, "episode_id": "second"}
    batch = {"batch_id": "original", "status": "waiting_model", "manifest": {"source_budget": 4096}}
    shrunk = {**batch, "status": "failed", "error_code": "memory_consolidation_output_truncated"}
    assert progress_key([first, second], [batch]) == progress_key([second, first], [batch])
    assert progress_key([first, second], [batch]) != progress_key([first, second], [shrunk])


def synthetic_context(principal="synthetic-local"):
    return TrustedContext(principal, "synthetic-actor", None, "synthetic-room",
        ("synthetic-scope",), ("synthetic-scope",), 1, OriginKind.HUMAN, frozenset({"*"}), "group")


class SyntheticDatabase:
    @asynccontextmanager
    async def transaction(self):
        yield object()


class SyntheticSources:
    def __init__(self, allowed):
        self.allowed = allowed

    async def validate_persistence(self, connection, context, refs):
        return self.allowed


class LostResponseClient:
    def __init__(self):
        self.requests = []
        self.receipts = {}

    async def generate(self, request):
        self.requests.append(request)
        raise TimeoutError("synthetic loss after possible dispatch")

    async def result(self, attempt_id, *, context):
        return None


@pytest.mark.parametrize("function,capabilities", [("chat", frozenset({"text"})),
                                                 ("rerank", frozenset({"rerank"}))])
async def test_adapter_validates_local_sources_maps_authority_and_never_resubmits_unknown(tmp_path, function, capabilities):
    config = {"broker_url": "http://127.0.0.1:9", "broker_token": "synthetic-not-a-secret",
              "model_context": wire(synthetic_context("synthetic-authorized-model-owner"))}
    sources = SyntheticSources(False)
    models = EvaluationModels(SyntheticDatabase(), sources, config, tmp_path, "synthetic-run")
    client = LostResponseClient()
    models.client = client
    models.allowed_actors.add("synthetic-actor")
    request = ModelRequest("synthetic-intent", "synthetic-attempt", function, ServiceClass.INTERACTIVE,
        synthetic_context(), RequestBudget(datetime.now(UTC) + timedelta(minutes=1)),
        (ModelMessage("user", (ModelPart("text", text="合成原件"),)),) if function == "chat" else (),
        required_capabilities=capabilities,
        native_payload={"query": "合成查询", "documents": ["合成原件"], "top_n": 1} if function == "rerank" else None,
        source_refs=(SourceRef("synthetic-source", 1, {}),))
    with pytest.raises(ValueError, match="evaluation_synthetic_sources_unavailable"):
        await models.generate(request)
    assert not client.requests and not models.mapping
    sources.allowed = True
    with pytest.raises(TimeoutError):
        await models.generate(request)
    assert len(client.requests) == 1
    remote = client.requests[0]
    assert remote.function == function and remote.required_capabilities == capabilities
    assert remote.context.principal_id == "synthetic-authorized-model-owner"
    assert remote.source_refs == () and remote.attempt_id != request.attempt_id
    assert models.mapping[request.attempt_id]["state"] == "unknown"
    with pytest.raises(AppError, match="evaluation_observe_original_attempt"):
        await models.generate(request)
    assert len(client.requests) == 1
    restarted = EvaluationModels(SyntheticDatabase(), sources, config, tmp_path, "synthetic-run")
    restarted.client = client
    restarted.allowed_actors.add("synthetic-actor")
    with pytest.raises(AppError, match="evaluation_observe_original_attempt"):
        await restarted.generate(request)
    assert len(client.requests) == 1
    saved = (tmp_path / "attempt-map.json").read_text(encoding="utf-8")
    assert "synthetic-not-a-secret" not in saved
    assert "synthetic-authorized-model-owner" not in saved


@pytest.mark.parametrize("status,dispatch,started", [("cancelled", "not_started", None),
    ("unknown", "unknown", "2026-11-01T10:00:01+00:00")])
async def test_original_receipt_observation_settles_local_dispatch_without_new_generation(tmp_path, status, dispatch, started):
    config = {"broker_url": "http://127.0.0.1:9", "broker_token": "synthetic-not-a-secret",
              "model_context": wire(synthetic_context())}
    models = EvaluationModels(SyntheticDatabase(), SyntheticSources(True), config, tmp_path, "original-run")
    client = LostResponseClient()
    models.client = client
    models.allowed_actors.add("synthetic-actor")
    request = ModelRequest("original-intent", "original-attempt", "embed", ServiceClass.ELASTIC,
        synthetic_context(), RequestBudget(datetime.now(UTC) + timedelta(seconds=120)), (),
        required_capabilities=frozenset({"embed"}), max_output_tokens=0)
    with pytest.raises(TimeoutError):
        await models.generate(request)
    remote = client.requests[0].attempt_id
    receipt = ModelReceipt(remote, "remote-intent", status, dispatch, "route", 1, "provider", "model",
        "openai.embedding", "original-quota", queued_at=datetime.fromisoformat("2026-11-01T10:00:00+00:00"),
        started_at=datetime.fromisoformat(started) if started else None,
        finished_at=datetime.fromisoformat("2026-11-01T10:00:30+00:00"))
    observed = []
    async def read_status(attempt_id):
        observed.append(attempt_id)
        return receipt
    client.status = read_status
    actual = await models.status(request.attempt_id)
    assert observed == [remote] and actual.attempt_id == request.attempt_id and len(client.requests) == 1
    assert models.mapping[request.attempt_id]["state"] == status
    assert models.mapping[request.attempt_id]["dispatch"] == dispatch
    metrics = aggregate_calls(models.calls)
    assert metrics["attempt_identities"] == 1 and metrics["not_started"] == (status == "cancelled")
    assert metrics["submitted_unknown"] == (status == "unknown")
    assert metrics["physical_dispatch_confirmed"] == bool(started) and metrics["dispatch_unknown"] == 0
    assert metrics["input_tokens"]["known_sum"] is None
    if not started:
        assert metrics["queue_ms"]["known_sum"] == 30000
        assert metrics["provider_ms"]["known_sum"] is None
    write_json(tmp_path / "run.json", {"variant": "optimized", "reports": [{"story_id": "S01", "report": "S01/report.json"}]})
    write_json(tmp_path / "S01/report.json", {"run_id": "same-run", "model_usage": {"stale": True}})
    log = tmp_path / "S01/model-calls.jsonl"
    log.write_text("\n".join(json.dumps(call) for call in models.calls) + "\n", encoding="utf-8")
    write_json(tmp_path / "S01/receipt-reconciliation.json", {"run_id": "same-run", "model_usage": metrics,
        "model_calls_sha256": sha256(log.read_bytes()).hexdigest()})
    assert read_results(tmp_path, "optimized")["S01"]["model_usage"] == metrics
    with log.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"broker_attempt_id": "later-original", "receipt": None}) + "\n")
    with pytest.raises(ValueError, match="receipt_reconciliation_stale"):
        read_results(tmp_path, "optimized")


async def test_observe_cli_reads_only_saved_unresolved_ids_and_keeps_original_report(tmp_path, monkeypatch):
    directory = tmp_path / "original-run" / "S01"
    config = tmp_path / "config.json"
    write_json(config, {"broker_url": "http://127.0.0.1:9", "broker_token": "synthetic-not-a-secret",
                        "model_context": wire(synthetic_context())})
    write_json(directory / "report.json", {"run_id": "original-run:S01", "answer": "saved answer"})
    write_json(directory / "attempt-map.json", {"local-original": {"state": "unknown",
        "broker_attempt_id": "broker-original", "local_attempt_id": "local-original", "local_intent_id": "intent"}})
    (directory / "model-calls.jsonl").write_text(json.dumps({"broker_attempt_id": "broker-original",
                                                          "receipt": None}) + "\n", encoding="utf-8")
    reads = []
    async def status(attempt_id):
        reads.append(attempt_id)
        return ModelReceipt(attempt_id, "intent", "cancelled", "not_started", "route", 1,
                            "provider", "model", "openai.embedding", "quota")
    async def close():
        pass
    async def open_status_client(models):
        models.client = SimpleNamespace(status=status, receipts={}, close=close)
    monkeypatch.setattr(EvaluationModels, "open", open_status_client)
    result = await observe(SimpleNamespace(code_root=tmp_path, private_root=tmp_path,
        run_root=directory.parent, config=config, stories="S01"))
    assert result == 0 and reads == ["broker-original"]
    assert json.loads((directory / "report.json").read_text())["answer"] == "saved answer"
    reconciled = json.loads((directory / "receipt-reconciliation.json").read_text())
    assert reconciled["generate_calls"] == 0 and reconciled["model_usage"]["not_started"] == 1


def test_cost_comparison_reuses_same_inputs_and_refuses_a_fictional_old_baseline(tmp_path):
    paths = {}
    for variant in ("legacy", "conservative", "optimized"):
        directory = tmp_path / variant
        paths[variant] = directory
        manifest = {"variant": variant, "reports": []}
        for story_id in COST_STORY_IDS:
            path = story_id + "/report.json"
            manifest["reports"].append({"story_id": story_id, "report": path})
            write_json(directory / path, {"story_id": story_id, "comparison_key": "same-" + story_id,
                "execution_config_fingerprint": "same-common-inputs",
                "code": {"source_sha": OLD_PRODUCTION_SHA if variant == "legacy" else "candidate"},
                "state": "completed_for_human_review", "run_id": variant + ":" + story_id})
        write_json(directory / "run.json", manifest)
    args = SimpleNamespace(**paths, output=tmp_path / "comparison.json", private_root=tmp_path)
    compare(args)
    result = json.loads(args.output.read_text(encoding="utf-8"))
    assert result["semantic_verdict"] == "pending_human_review" and result["actual_fee"] is None
    assert len(result["pairs"]) == 8
    first = paths["legacy"] / COST_STORY_IDS[0] / "report.json"
    value = json.loads(first.read_text(encoding="utf-8"))
    value["code"]["source_sha"] = "invented_per_person_loop"
    write_json(first, value)
    with pytest.raises(ValueError, match="cost_legacy_not_actual_production"):
        compare(args)


def test_fixed_recovery_keeps_original_inbox_identity_and_rejects_retired_kinds():
    item = {"kind": "qq_inbox", "event_key": "synthetic-original-event",
            "connection_id": "synthetic-connection", "producer": "synthetic-qq",
            "event_id": "synthetic-event", "event_revision": 2, "expected_state": "accepted"}
    value = {"schema_version": "memory.closure.recovery.v1", "manifest_id": "synthetic-fixed-list",
             "items": [item]}
    assert validate_manifest(value) is value
    assert recovery_plan(value)["items"][0]["event_key"] == item["event_key"]
    with pytest.raises(ValueError, match="fixed_original_accepted_inbox_identity_required"):
        validate_manifest({**value, "items": [{key: data for key, data in item.items()
                                             if key != "event_revision"}]})
    with pytest.raises(ValueError, match="unsupported_recovery_kind"):
        validate_manifest({**value, "items": [{"kind": "imagined_episode"}]})


async def test_continuous_intake_cannot_push_wake_past_first_pending_maximum(core, ordinary_users):
    """The existing pipeline stories did not cover longest-wait wake scheduling."""
    context, _ = ordinary_users
    policy = replace(core.memory.consolidation.policy, quiet_seconds=120, max_wait_seconds=600)
    core.memory.consolidation.policy = policy
    core.memory.consolidation.materials.policy = policy

    class NoIntakeModel:
        def __init__(self):
            self.requests = []

        async def generate(self, request):
            self.requests.append(request)
            raise AssertionError("intake_or_early_wake_must_not_call_a_model")

    models = NoIntakeModel()
    core.steps.models = models

    async def observe(event_id):
        now = datetime.now(UTC)
        refs = await core.sources.record_event(context, producer="web", event_id=event_id,
            revision=1, occurred_at=now, parts=({"kind": "text", "text": "持续到来的合成消息"},),
            origin_kind=OriginKind.HUMAN)
        return await core.memory.observe_episode(context, {"event_id": event_id,
            "occurred_at": now, "summary": "持续到来的合成消息", "source_refs": refs, "origin_kind": "human"})

    first = await observe("synthetic-max-wait-1")
    anchor = datetime.now(UTC) - timedelta(seconds=590)
    async with core.database.transaction() as connection:
        row = await (await connection.execute(
            "SELECT consolidation_partition_id FROM memory.episodes WHERE episode_id=%s",
            (first.episode_id,))).fetchone()
        partition = row["consolidation_partition_id"]
        await connection.execute("UPDATE memory.consolidation_state SET first_pending_at=%s WHERE partition_id=%s",
                                 (anchor, partition))
    await observe("synthetic-max-wait-2")
    async with core.database.transaction(read_only=True) as connection:
        state = await (await connection.execute(
            "SELECT first_pending_at,wake_due_at FROM memory.consolidation_state WHERE partition_id=%s",
            (partition,))).fetchone()
    assert state["first_pending_at"] == anchor
    assert state["wake_due_at"] == anchor + timedelta(seconds=600)
    await core.workers.consolidate(partition)
    assert not models.requests
