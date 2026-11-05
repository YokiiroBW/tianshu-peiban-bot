"""Recovery uses the original evidence and a single action-wide correction."""

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tianshu.capabilities import action_checkpoint as checkpoint
from tianshu.capabilities.contracts import Fault, canonical, digest
from tianshu.capabilities.execution_context import CURRENT_EXECUTION
from tianshu.capabilities.external import ProviderError
from tianshu.capabilities.perception import correction
from tianshu.capabilities.perception.service import Perception
from tianshu.models.types import ModelCompletion, ModelPart, ModelReceipt, ModelResult
from tianshu.runtime.serialization import wire
from tianshu.types import Action, Attempt, MediaRef, OriginKind, Receipt, SourceRef, TrustedContext


GENERATION = "00000000-0000-0000-0000-000000000001"


def fixture():
    context = TrustedContext("owner", "actor", None, None, ("scope",), ("scope",), 1, OriginKind.ACTOR)
    source = SourceRef("source", 1)
    media = MediaRef("source:source:1", "controlled", "audio/wav")
    clip = MediaRef("artifact:clip", "controlled", "audio/wav", sha256="clip")
    action = Action("action", "intent", "video.observe", "actor", context,
                    {"source": wire(source), "media": wire(media), "start": 0, "end": 2}, OriginKind.ACTOR)
    video = Perception(SimpleNamespace(), SimpleNamespace(sources=SimpleNamespace()),
                       SimpleNamespace(check=AsyncMock()), None)
    original = replace(video._video_request(action, Attempt("original", action.action_id),
        [ModelPart("audio", media=clip)], (source,)), data_generation=GENERATION)
    segment = {"source": wire(source), "media": wire(media), "requested": {"start": 0, "end": 2},
               "model_range": {"start": 0, "end": 2}, "request": wire(original),
               "decoded": {"subtitle_ranges": []},
               "input": {"modalities": ["audio"], "model_input_modalities": ["audio"], "frame_times": [],
                         "audio_ranges": [{"start": 0, "end": 2}]},
               "dispatch_started": True, "registration_confirmed": True}
    receipt = ModelReceipt("original", "action", "succeeded", "submitted", "route", 1, "provider",
                           "model", "gemini.generate", "quota", data_generation=GENERATION,
                           request_digest="original-request")
    model = ModelResult((ModelPart("text", text="bad"),), receipt, completion=ModelCompletion("complete"))
    return video, action, source, segment, model


def test_correction_keeps_original_input_source_deadline_and_action_wide_identity():
    video, action, source, segment, model = fixture()
    items = video._catalog_for_segment(source, MediaRef(**segment["media"]), segment)
    request = correction._request(action, segment, model, "bad", items, GENERATION)
    original = segment["request"]
    assert request.messages[0].parts[0].media.media_id == "artifact:clip"
    assert request.source_refs == (source,) and request.context == action.context
    assert wire(request.budget) == original["budget"]
    assert request.business_deadline_at is None and request.execution_timeout_seconds == original["execution_timeout_seconds"]
    assert request.attempt_id != model.receipt.attempt_id
    assert correction._request(action, segment, model, "different", items, GENERATION).attempt_id == request.attempt_id
    assert correction.MAX_ATTEMPTS == 2
    with pytest.raises(Fault):
        correction._request(action, segment, replace(model, receipt=replace(model.receipt, data_generation="other")),
                            "bad", items, GENERATION)


@pytest.mark.asyncio
async def test_legacy_success_is_reparsed_without_new_model_submission(monkeypatch):
    video, action, source, segment, model = fixture()
    text = canonical({"summary": "sound", "observations": [
        {"start": 0, "end": 2, "modality": "sound", "detail": "heard sound"}]})
    model = replace(model, parts=(ModelPart("text", text=text),))
    saved = {"segments": {"main": segment}, "original_attempt_id": "original"}
    receipt = Receipt("failed-receipt", action.action_id, "original", "failed", "none", "not_requested",
                      result={"phase": "parse", "recovery": "invalid_model_output", "model_attempt_id": "original"})
    video.broker = SimpleNamespace(status=AsyncMock(return_value=model.receipt),
                                   reusable_result=AsyncMock(return_value=model), generate=AsyncMock())
    video._saved_generation = AsyncMock(return_value=GENERATION)
    monkeypatch.setattr(checkpoint, "load", AsyncMock(return_value=saved))
    proof = await video.resume_preflight(action, Attempt("original", action.action_id), receipt)
    assert proof["parse_mode"] == "local_parse" and proof["model_attempt_id"] == "original"
    assert proof["checkpoint_digest"] == digest(saved)
    video.broker.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_changed_checkpoint_or_withdrawn_source_blocks_resume(monkeypatch):
    video, action, source, segment, _ = fixture()
    state = {"segments": {"main": segment}}
    row = {"attempt": {"attempt_id": "original"}, "receipt": {"receipt_id": "receipt"},
           "document": canonical({"video_observation": state})}
    conn = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value=row))))
    import tianshu.capabilities.withdrawal as withdrawal
    validate = AsyncMock(return_value=True)
    monkeypatch.setattr(withdrawal, "validate_sources", validate)
    receipt = Receipt("receipt", action.action_id, "original", "failed", "none", "not_requested")
    proof = {"source_refs": (source,), "checkpoint_digest": digest(state)}
    await video.validate_resume_on(conn, action, Attempt("original", action.action_id), receipt, proof)
    row["document"] = canonical({"video_observation": {"changed": True}})
    with pytest.raises(Fault, match="video_checkpoint_changed"):
        await video.validate_resume_on(conn, action, Attempt("original", action.action_id), receipt, proof)
    validate.return_value = False
    with pytest.raises(Fault, match="source_revoked"):
        await video.validate_resume_on(conn, action, Attempt("original", action.action_id), receipt, proof)


@pytest.mark.asyncio
async def test_policy_reserves_one_correction_after_recording_original_success(monkeypatch):
    video, action, source, segment, model = fixture()
    attempt = Attempt("resumed", action.action_id)
    state = {"segments": {"main": segment}, "resume_intent": {
        "attempt_id": attempt.attempt_id, "mode": "bounded_correction", "segment": "main",
        "model_attempt_id": "original"}}
    row = {"document": canonical({"video_observation": state, "data_generation": GENERATION}),
           "attempt": {"attempt_id": attempt.attempt_id}, "receipt": {"execution": "running"},
           "execution_owner": "owner", "owner_live": True}
    async def execute(query, params):
        if "SELECT document,attempt,receipt" in query:
            return SimpleNamespace(fetchone=AsyncMock(return_value=row))
        if "SELECT receipt FROM capabilities.attempts" in query:
            return SimpleNamespace(fetchall=AsyncMock(return_value=[]))
        return SimpleNamespace()
    @asynccontextmanager
    async def transaction(**kwargs):
        yield SimpleNamespace(execute=execute)
    video.database = SimpleNamespace(transaction=transaction)
    lineage = correction.identity(action.action_id, GENERATION)[2]
    video.recovery_policy = SimpleNamespace(
        register_on=AsyncMock(return_value={"lineage_id": lineage, "attempts": 0}),
        reserve_attempt_on=AsyncMock(side_effect=[
            SimpleNamespace(attempt_id="original", status="reserved", probe_token=None),
            SimpleNamespace(attempt_id="video-correction:" + digest([action.action_id, GENERATION]),
                            status="reserved", probe_token=None)]),
        succeed_attempt_on=AsyncMock(),
        claim_continuation_on=AsyncMock(return_value={"claim_token": "claim"}))
    monkeypatch.setattr(correction, "inspect_budget", AsyncMock())
    monkeypatch.setattr(correction, "validate_sources", AsyncMock(return_value=True))
    monkeypatch.setattr(correction, "depend", AsyncMock())
    execution = {"data_generation": GENERATION, "owner": SimpleNamespace(owners=SimpleNamespace(id="owner"),
        jobs=SimpleNamespace(assert_generation_on=AsyncMock()))}
    items = video._catalog_for_segment(source, MediaRef(**segment["media"]), segment)
    token = CURRENT_EXECUTION.set(execution)
    try:
        stored = await correction._reserve(video, action, attempt, source, state, "main", model, "bad", items)
    finally:
        CURRENT_EXECUTION.reset(token)
    policy = video.recovery_policy
    assert policy.register_on.await_args.kwargs["max_attempts"] == 2
    assert [call.kwargs["original_state"] for call in policy.reserve_attempt_on.await_args_list] == ["none", "succeeded"]
    assert policy.reserve_attempt_on.await_args_list[1].kwargs["claim_token"] == "claim"
    policy.succeed_attempt_on.assert_awaited_once()
    assert policy.succeed_attempt_on.await_args.args[1] == "original"
    assert model.receipt.status == "succeeded" and stored["original_model_attempt_id"] == "original"
    assert stored["attempt_id"] != "original" and stored["request_digest"] == digest(stored["request"])


@pytest.mark.asyncio
async def test_recovery_observes_same_correction_and_never_mints_another_for_a_segment(monkeypatch):
    video, action, source, segment, model = fixture()
    items = video._catalog_for_segment(source, MediaRef(**segment["media"]), segment)
    request = correction._request(action, segment, model, "bad", items, GENERATION)
    lineage = correction.identity(action.action_id, GENERATION)[2]
    stored = {"segment": "main", "original_model_attempt_id": "original", "request": wire(request),
              "attempt_id": request.attempt_id, "evidence_digest": digest(items), "lineage_id": lineage,
              "version": correction.identity(action.action_id, GENERATION)[1], "registration_confirmed": True,
              "request_digest": digest(wire(request)), "route": "route"}
    saved = {"segments": {"main": segment}, "correction": stored}
    corrected_receipt = replace(model.receipt, attempt_id=request.attempt_id, intent_id=request.intent_id)
    corrected = replace(model, receipt=corrected_receipt)
    video.broker = SimpleNamespace(status=AsyncMock(return_value=corrected_receipt),
                                   reusable_result=AsyncMock(return_value=corrected), generate=AsyncMock())
    monkeypatch.setattr(checkpoint, "load", AsyncMock(return_value=saved))
    monkeypatch.setattr(correction, "inspect_budget", AsyncMock())
    settle = AsyncMock()
    monkeypatch.setattr(correction, "_settle", settle)
    token = CURRENT_EXECUTION.set({"data_generation": GENERATION})
    try:
        returned, _ = await correction.correct(video, action, Attempt("resumed", action.action_id),
                                              source, "main", model, "bad", items)
        assert returned.receipt.attempt_id == request.attempt_id
        video.broker.generate.assert_not_awaited()
        settle.assert_awaited_once()
        with pytest.raises(Fault, match="video_correction_budget_exhausted"):
            await correction.correct(video, action, Attempt("another", action.action_id),
                                     source, ":revisit", model, "bad", items)
        video.broker.status.return_value = None
        with pytest.raises(ProviderError) as unresolved:
            await correction.correct(video, action, Attempt("another", action.action_id),
                                     source, "main", model, "bad", items)
        assert unresolved.value.effect == "unknown"
        video.broker.generate.assert_not_awaited()
    finally:
        CURRENT_EXECUTION.reset(token)


@pytest.mark.asyncio
async def test_late_correction_cannot_settle_after_action_owner_changes(monkeypatch):
    video, action, source, _, model = fixture()
    row = {"document": canonical({"video_observation": {"correction": {"attempt_id": "correction"}}}),
           "attempt": {"attempt_id": "newer"}, "receipt": {"execution": "running"},
           "execution_owner": "owner", "owner_live": True}
    conn = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value=row))))
    @asynccontextmanager
    async def transaction(**kwargs):
        yield conn
    video.database = SimpleNamespace(transaction=transaction)
    video.recovery_policy = SimpleNamespace(succeed_attempt_on=AsyncMock())
    monkeypatch.setattr(correction, "validate_sources", AsyncMock(return_value=True))
    execution = {"data_generation": GENERATION, "owner": SimpleNamespace(owners=SimpleNamespace(id="owner"))}
    token = CURRENT_EXECUTION.set(execution)
    try:
        with pytest.raises(Fault, match="video_checkpoint_owner_changed"):
            await correction._settle(video, action, Attempt("old", action.action_id), source,
                                     {"attempt_id": "correction"}, model.receipt)
        video.recovery_policy.succeed_attempt_on.assert_not_awaited()
    finally:
        CURRENT_EXECUTION.reset(token)


@pytest.mark.asyncio
async def test_ambiguous_prior_correction_and_other_segments_cannot_reset_budget():
    video, action, _, segment, _ = fixture()
    video.recovery_policy = SimpleNamespace()
    old_receipts = [{"receipt": {"result": {"correction_model_attempt_id": "old-correction"}}}]
    lineage = {"owner": correction.OWNER, "attempts": 2, "max_attempts": 2}
    request_wire = {"attempt_id": "correction"}
    version = correction.identity(action.action_id, GENERATION)[1]
    attempt = {"lineage_id": correction.identity(action.action_id, GENERATION)[2], "version": version,
               "route": "route", "request_digest": digest(request_wire)}
    async def execute(query, params):
        if "capabilities.attempts" in query:
            return SimpleNamespace(fetchall=AsyncMock(return_value=old_receipts))
        if "runtime.recovery_attempts" in query:
            return SimpleNamespace(fetchone=AsyncMock(return_value=attempt))
        return SimpleNamespace(fetchone=AsyncMock(return_value=lineage))
    @asynccontextmanager
    async def transaction(**kwargs):
        yield SimpleNamespace(execute=execute)
    video.database = SimpleNamespace(transaction=transaction)
    state = {"segments": {"main": segment}}
    with pytest.raises(Fault, match="video_correction_budget_unconfirmed"):
        await correction.inspect_budget(video, action, state, GENERATION, "main")
    old_receipts.clear()
    state["correction"] = {"segment": "main", "lineage_id": correction.identity(action.action_id, GENERATION)[2],
                           "attempt_id": "correction", "version": version, "route": "route",
                           "request": request_wire, "request_digest": digest(request_wire)}
    await correction.inspect_budget(video, action, state, GENERATION, "main")
    with pytest.raises(Fault, match="video_correction_budget_exhausted"):
        await correction.inspect_budget(video, action, state, GENERATION, ":revisit")


@pytest.mark.asyncio
async def test_interrupted_commit_reuses_consumption_and_does_not_decode_or_submit(monkeypatch):
    video, action, source, _, _ = fixture()
    state = {"original_attempt_id": "original"}
    row = {"body": {"source": wire(source)}}
    @asynccontextmanager
    async def transaction(**kwargs):
        yield SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=AsyncMock(return_value=row))))
    video.database = SimpleNamespace(transaction=transaction)
    video._decode_video = AsyncMock()
    video.broker = SimpleNamespace(generate=AsyncMock())
    expected = Receipt("complete", action.action_id, "resumed", "succeeded", "none", "not_requested")
    video._receipt_from_body = AsyncMock(return_value=expected)
    monkeypatch.setattr(checkpoint, "load", AsyncMock(return_value=state))
    for attempt_id in ("resumed", "repeated"):
        assert await video.execute(action, Attempt(attempt_id, action.action_id)) == expected
    assert video._receipt_from_body.await_count == 2
    video._decode_video.assert_not_awaited()
    video.broker.generate.assert_not_awaited()
