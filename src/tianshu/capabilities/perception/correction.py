"""One source-fenced, action-wide correction of a successful video model result."""

import json
from dataclasses import replace

from tianshu.models.codec import request_from_wire
from tianshu.models.types import ModelMessage, ModelPart
from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import FailureCategory, lineage_id
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs

from .. import action_checkpoint as checkpoint
from ..contracts import Fault, canonical, digest
from ..execution_context import CURRENT_EXECUTION, current_generation
from ..external import ProviderError
from ..withdrawal import depend, validate_sources
from .understanding import OBSERVATION_INSTRUCTION


OWNER = "capabilities.video.parse"
DOMAIN = "video_parse"
MAX_ATTEMPTS = 2  # the original success and at most one correction, across all segments
_OLD_CORRECTION_KEYS = frozenset({"correction", "corrections", "correction_history", "correction_attempts",
                                  "correction_attempt_id", "correction_model_attempt_id",
                                  "correction_model_receipt"})


def identity(action_id, generation):
    material = "video-parse:" + action_id
    return material, digest([action_id, generation]), lineage_id(DOMAIN, material, generation=generation)


def _old_correction(value):
    if isinstance(value, dict):
        return any((key in _OLD_CORRECTION_KEYS and bool(item)) or _old_correction(item)
                   for key, item in value.items())
    if isinstance(value, list):
        return any(_old_correction(item) for item in value)
    return False


async def inspect_budget(video, action, saved, generation, segment):
    """Read-only classification; reserve and ownership are checked again at dispatch."""
    if getattr(video, "recovery_policy", None) is None:
        raise Fault("video_correction_policy_unavailable", 503)
    if not generation or _old_correction({key: value for key, value in saved.items() if key != "correction"}):
        raise Fault("video_correction_budget_unconfirmed", 409)
    _, _, lineage = identity(action.action_id, generation)
    correction = saved.get("correction")
    if correction and (correction.get("segment") != segment or correction.get("lineage_id") != lineage):
        raise Fault("video_correction_budget_exhausted", 409)
    async with video.database.transaction(read_only=True) as conn:
        rows = await (await conn.execute(
            "SELECT receipt FROM capabilities.attempts WHERE action_id=%s", (action.action_id,))).fetchall()
        if any(_old_correction(row["receipt"]) for row in rows):
            raise Fault("video_correction_budget_unconfirmed", 409)
        row = await (await conn.execute(
            "SELECT owner,attempts,max_attempts FROM runtime.recovery_lineages WHERE lineage_id=%s",
            (lineage,))).fetchone()
        attempt = None
        if correction:
            attempt = await (await conn.execute(
                "SELECT lineage_id,request_digest,route,version FROM runtime.recovery_attempts WHERE attempt_id=%s",
                (correction.get("attempt_id"),))).fetchone()
    if row and (row["owner"] != OWNER or row["max_attempts"] != MAX_ATTEMPTS
                or not correction or row["attempts"] != MAX_ATTEMPTS):
        raise Fault("video_correction_budget_unconfirmed", 409)
    if correction and not row:
        raise Fault("video_correction_budget_unconfirmed", 409)
    if correction and (not attempt or attempt["lineage_id"] != lineage
            or attempt["version"] != correction.get("version")
            or attempt["route"] != correction.get("route")
            or attempt["request_digest"] != correction.get("request_digest")
            or digest(correction.get("request")) != correction.get("request_digest")):
        raise Fault("video_correction_budget_unconfirmed", 409)


def _request(action, segment, model, text, items, generation):
    original = request_from_wire(segment["request"])
    receipt = model.receipt
    execution = CURRENT_EXECUTION.get()
    if (receipt.attempt_id != original.attempt_id or receipt.status != "succeeded"
            or receipt.data_generation != generation or original.data_generation not in {None, generation}
            or original.context != action.context or not receipt.route_id or not receipt.quota_group
            or segment["source"] not in [wire(ref) for ref in original.source_refs]
            or original.native_payload is not None or original.tools
            or execution and (wire(original.behavior_refs) != wire(execution.get("behavior_refs", ()))
                or original.behavior_refs and original.behavior_purpose != execution.get("behavior_purpose", "conversation"))):
        raise Fault("video_correction_original_unconfirmed", 409)
    identifier = "video-correction:" + digest([action.action_id, generation])
    prompt = ("The previous response failed structural or evidence validation. Correct only this segment, "
              "using the original supplied inputs and this authoritative catalog. Return the requested JSON; "
              "do not invent evidence. Catalog: " + canonical(items))
    return replace(original, intent_id=action.action_id + ":parse-correction", attempt_id=identifier,
                   route_id=receipt.route_id, route_revision=receipt.route_revision, protocol=receipt.protocol,
                   data_generation=generation, system=OBSERVATION_INSTRUCTION,
                   messages=(*original.messages,
                             ModelMessage("assistant", (ModelPart("text", text=text),)),
                             ModelMessage("user", (ModelPart("text", text=prompt),))))


async def _reserve(video, action, attempt, source, saved, segment_key, model, text, items):
    execution = CURRENT_EXECUTION.get()
    if not execution:
        raise Fault("video_correction_owner_unavailable", 409)
    generation = current_generation()
    await inspect_budget(video, action, saved, generation, segment_key)
    segment = saved["segments"][segment_key]
    request = _request(action, segment, model, text, items, generation)
    video._check_original_admission_time(request)
    material, version, expected_lineage = identity(action.action_id, generation)
    policy = video.recovery_policy
    refs = unique_refs(action.source_refs, (source,))
    async with video.database.transaction() as conn:
        if not await validate_sources(conn, action.context, refs, video.artifacts.sources):
            raise Fault("source_revoked", 410)
        row = await (await conn.execute(
            "SELECT document,attempt,receipt,execution_owner,EXISTS(SELECT 1 FROM capabilities.execution_owners o "
            "WHERE o.id=capabilities.actions.execution_owner AND o.expires_at>now() AND o.stopped_at IS NULL) AS owner_live "
            "FROM capabilities.actions WHERE id=%s FOR UPDATE", (action.action_id,))).fetchone()
        document = json.loads(row["document"]) if row else {}
        state = document.get("video_observation", {})
        intent = state.get("resume_intent", {})
        if (not row or row["attempt"]["attempt_id"] != attempt.attempt_id
                or row["receipt"]["execution"] != "running" or not row["owner_live"]
                or row["execution_owner"] != execution["owner"].owners.id
                or document.get("source_revoked") or document.get("cancel_requested")
                or digest(state) != digest(saved) or intent.get("attempt_id") != attempt.attempt_id
                or intent.get("mode") != "bounded_correction" or intent.get("segment") != segment_key
                or intent.get("model_attempt_id") != model.receipt.attempt_id
                or state.get("correction")):
            raise Fault("video_checkpoint_owner_changed", 409)
        await execution["owner"].jobs.assert_generation_on(conn, document.get("data_generation"))
        prior = await (await conn.execute(
            "SELECT receipt FROM capabilities.attempts WHERE action_id=%s", (action.action_id,))).fetchall()
        if _old_correction(state) or any(_old_correction(item["receipt"]) for item in prior):
            raise Fault("video_correction_budget_unconfirmed", 409)
        dependency = "models:quota:" + model.receipt.quota_group
        lineage = await policy.register_on(conn, domain=DOMAIN, subject_id=action.actor_id or action.context.principal_id,
            owner=OWNER, material_id=material, material_digest=version, dependency=dependency,
            current_version=version, max_attempts=MAX_ATTEMPTS, data_generation=generation,
            recovery_kind="pure_computation")
        if lineage["lineage_id"] != expected_lineage or lineage["attempts"]:
            raise Fault("video_correction_budget_unconfirmed", 409)
        first = await policy.reserve_attempt_on(conn, expected_lineage, owner=OWNER, expected_version=version,
            attempt_id=model.receipt.attempt_id, request_digest=model.receipt.request_digest or digest(segment["request"]),
            route=model.receipt.route_id, original_state="none")
        if first.status != "reserved":
            raise Fault("video_correction_budget_unconfirmed", 409)
        await policy.succeed_attempt_on(conn, first.attempt_id, probe_token=first.probe_token)
        claim = await policy.claim_continuation_on(conn, expected_lineage, owner=OWNER, expected_version=version,
            prior_attempt_id=first.attempt_id, reason="schema_correction")
        request_wire = wire(request)
        request_digest = digest(request_wire)
        reservation = await policy.reserve_attempt_on(conn, expected_lineage, owner=OWNER, expected_version=version,
            attempt_id=request.attempt_id, request_digest=request_digest, route=model.receipt.route_id,
            original_state="succeeded", claim_token=claim["claim_token"])
        correction = {"segment": segment_key, "original_model_attempt_id": first.attempt_id,
                      "original_model_receipt": wire(model.receipt), "request": request_wire,
                      "evidence_digest": digest(items), "lineage_id": expected_lineage, "version": version,
                      "attempt_id": reservation.attempt_id, "probe_token": reservation.probe_token,
                      "request_digest": request_digest, "route": model.receipt.route_id,
                      "dispatch_started": False, "registration_confirmed": False}
        state["correction"] = correction
        document["video_observation"] = state
        await depend(conn, "action", action.action_id, refs)
        await conn.execute("UPDATE capabilities.actions SET document=%s WHERE id=%s", (canonical(document), action.action_id))
    return correction


async def _settle(video, action, attempt, source, correction, receipt):
    execution = CURRENT_EXECUTION.get()
    async with video.database.transaction() as conn:
        if not await validate_sources(conn, action.context, unique_refs(action.source_refs, (source,)), video.artifacts.sources):
            raise Fault("source_revoked", 410)
        row = await (await conn.execute(
            "SELECT document,attempt,receipt,execution_owner,EXISTS(SELECT 1 FROM capabilities.execution_owners o "
            "WHERE o.id=capabilities.actions.execution_owner AND o.expires_at>now() AND o.stopped_at IS NULL) AS owner_live "
            "FROM capabilities.actions WHERE id=%s FOR UPDATE", (action.action_id,))).fetchone()
        document = json.loads(row["document"]) if row else {}
        state = document.get("video_observation", {})
        saved = state.get("correction", {})
        if (not execution or not row or row["attempt"]["attempt_id"] != attempt.attempt_id
                or row["receipt"]["execution"] != "running" or not row["owner_live"]
                or row["execution_owner"] != execution["owner"].owners.id
                or document.get("source_revoked") or document.get("cancel_requested")
                or saved.get("attempt_id") != correction["attempt_id"]
                or saved.get("request_digest") != correction.get("request_digest")
                or digest(saved.get("request")) != correction.get("request_digest")):
            raise Fault("video_checkpoint_owner_changed", 409)
        await execution["owner"].jobs.assert_generation_on(conn, document.get("data_generation"))
        policy_row = await (await conn.execute(
            "SELECT status FROM runtime.recovery_attempts WHERE attempt_id=%s", (correction["attempt_id"],))).fetchone()
        if not policy_row:
            raise Fault("video_correction_budget_unconfirmed", 409)
        state_value = policy_row["status"]
        if receipt.status == "succeeded":
            if state_value == "reserved":
                await video.recovery_policy.succeed_attempt_on(conn, correction["attempt_id"])
            elif state_value == "unknown":
                await video.recovery_policy.resolve_unknown_on(conn, correction["attempt_id"],
                    observation_ref="models:" + correction["attempt_id"] + ":succeeded", state="succeeded")
            elif state_value != "succeeded":
                raise Fault("video_correction_not_applicable", 409)
        elif receipt.status in {"failed", "cancelled"}:
            if state_value == "reserved":
                await video.recovery_policy.fail_attempt_on(conn, correction["attempt_id"], code=receipt.error_code,
                                                           category=FailureCategory.OTHER)
            elif state_value == "unknown":
                await video.recovery_policy.resolve_unknown_on(conn, correction["attempt_id"],
                    observation_ref="models:" + correction["attempt_id"] + ":" + receipt.status,
                    state="failed", code=receipt.error_code, category=FailureCategory.OTHER)
            elif state_value != "failed":
                raise Fault("video_correction_not_applicable", 409)
        saved.update(registration_confirmed=True, model_receipt=wire(receipt))
        state["correction"] = saved
        document["video_observation"] = state
        await conn.execute("UPDATE capabilities.actions SET document=%s WHERE id=%s", (canonical(document), action.action_id))


async def correct(video, action, attempt, source, segment_key, model, text, items):
    saved = await checkpoint.load(video.database, action)
    correction = saved.get("correction")
    if correction is None:
        correction = await _reserve(video, action, attempt, source, saved, segment_key, model, text, items)
    else:
        await inspect_budget(video, action, saved, current_generation(), segment_key)
    if (correction.get("segment") != segment_key or correction.get("original_model_attempt_id") != model.receipt.attempt_id
            or correction.get("evidence_digest") != digest(items)
            or digest(correction.get("request")) != correction.get("request_digest")):
        raise Fault("video_correction_budget_exhausted", 409)
    request = request_from_wire(correction["request"])
    if request.attempt_id != correction["attempt_id"]:
        raise Fault("video_checkpoint_changed", 409)
    confirmed = correction.get("registration_confirmed") is True
    try:
        receipt = await video.broker.status(request.attempt_id, phase="observe" if confirmed else "pre_submit")
    except AppError as error:
        from tianshu.runtime.recovery_policy import RecoveryWait
        if isinstance(error, RecoveryWait):
            raise
        raise ProviderError("video_correction_model_unresolved", 503, effect="unknown") from error
    if receipt is not None and (receipt.attempt_id != request.attempt_id
                                or receipt.data_generation != current_generation()):
        raise ProviderError("video_correction_model_unresolved", 503, effect="unknown")
    if receipt is None:
        if confirmed or "registration_confirmed" not in correction:
            raise ProviderError("video_correction_model_unresolved", 503, effect="unknown")
        video._check_original_admission_time(request)
        correction = {**correction, "dispatch_started": True}
        await checkpoint.save(video.database, video.artifacts.sources, action, attempt, {"correction": correction}, (source,))
        result, corrected_text = await video._video_result(request)
        receipt = result.receipt
    elif receipt.status == "succeeded":
        result = await video.broker.reusable_result(request.attempt_id, context=action.context,
                                                    expected_data_generation=current_generation())
        result, corrected_text = video._checked_video_result(result)
        receipt = result.receipt
    elif receipt.status in {"failed", "cancelled"}:
        await _settle(video, action, attempt, source, correction, receipt)
        raise ProviderError("video_correction_model_failed", 502, effect="none")
    else:
        raise ProviderError("video_correction_model_unresolved", 503, effect="unknown")
    if receipt.attempt_id != request.attempt_id or receipt.data_generation != current_generation():
        raise Fault("video_correction_original_unconfirmed", 409)
    await _settle(video, action, attempt, source, correction, receipt)
    return result, corrected_text
