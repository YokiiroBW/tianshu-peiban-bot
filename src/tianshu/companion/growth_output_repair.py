"""Bounded correction of known reflection output within the Growth retry owner."""

from dataclasses import replace
from collections.abc import Mapping
import json

from tianshu.capabilities.contracts import model_json
from tianshu.life.writing import generated_text
from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS
from tianshu.models.retry import transient_model_failure
from tianshu.models.types import ModelMessage, ModelPart
from tianshu.runtime.errors import AppError
from .reflection_output import (
    CORRECTABLE_ERRORS, OUTPUT_ERRORS, OUTPUT_REPAIR_LIMIT, OUTPUT_REPAIR_RESERVE,
    episode_references, frozen_episodes,
)

REPAIR_INSTRUCTION = ("The rejected candidate is not evidence. Return the complete corrected notes JSON; "
    "use only the frozen short references and one note per kind/evidence set. Do not guess or repair UUIDs.")


def _message(correction):
    return ModelMessage("user", (ModelPart("text", text=json.dumps(
        {"output_repair": correction}, ensure_ascii=False, separators=(",", ":"))),))


def repair_reserve_tokens(counter):
    """Use this lane's production tokenizer for the bounded correction envelope."""
    diagnostic = output_diagnostic("reflection_duplicate_proposal", {
        "path": ["notes", 2, "episode_ids", 7], "allowed_refs": ["e"+str(index) for index in range(1, 33)],
        "conflict_with": ["notes", 0], "conflict_fields": ["content", "conditions", "affect"],
        "actual": "诊"*80, "expected": "据"*80, "rule": "one note per kind and episode_ids set"})
    message = _message({"original_attempt_id": "0"*36, "diagnostic": diagnostic,
        "previous_output_fragment": "文"*170, "instruction": REPAIR_INSTRUCTION})
    return max(OUTPUT_REPAIR_RESERVE, counter.measure("", (message,), ())[0]+256)


def correction_available(batch, code):
    return code in CORRECTABLE_ERRORS and batch.get("output_repair_count", 0) < OUTPUT_REPAIR_LIMIT


def review_available(batch, code):
    if code in OUTPUT_ERRORS and batch.get("output_repair_count", 0) >= OUTPUT_REPAIR_LIMIT:
        return False
    return code not in CORRECTABLE_ERRORS or correction_available(batch, code)


def review_original(receipt, repair_ref=None):
    """Choose a Growth path from the original Models evidence, without guessing."""
    if receipt is None or receipt.status in {'queued', 'running', 'unknown'}:
        return 'stop', 'original_attempt_unresolved'
    if receipt.cancellation_reason == 'explicit_cancel':
        return 'stop', 'explicit_cancel'
    if receipt.status == 'cancelled':
        return (('transient', None) if transient_model_failure(receipt.error_code)
                else ('stop', 'original_cancelled'))
    if receipt.status == 'succeeded':
        # The existing retry owner reparses the saved success and may make one
        # bounded correction; no failed Models output is invented here.
        return 'reuse', None
    if receipt.status != 'failed':
        return 'stop', 'original_attempt_unresolved'
    diagnostic = receipt.diagnostic if isinstance(receipt.diagnostic, Mapping) else {}
    stage = diagnostic.get('failure_stage')
    if stage in {'local_program', 'protocol_decode'}:
        # Existing reusable_result only accepts successes. A failed response
        # marked reusable needs a Models reparse port, not a new request.
        reusable = diagnostic.get('original_output_reusable')
        if reusable is True:
            return 'stop', 'original_reparse_unavailable'
        if reusable is not None and reusable is not False:
            return 'stop', 'growth_failure_diagnostic_incomplete'
        # None does not assert that output was absent or free; qualification
        # relies on the known failure, exact repair and original Runtime budget.
        if not isinstance(diagnostic.get('error_type'), str) or not diagnostic['error_type']:
            return 'stop', 'growth_failure_diagnostic_incomplete'
        if not repair_ref:
            return 'stop', 'growth_repair_ref_required'
        return 'repaired', None
    if stage in {None, 'unknown', 'transport'} and transient_model_failure(receipt.error_code):
        return 'transient', None
    if stage not in {'transport', 'domain_validation'}:
        return 'stop', 'growth_failure_stage_unknown'
    return 'stop', 'original_failure_not_retryable'


def admitted_review_matches(review, receipt):
    if review.get('state') != 'admitted':
        return True
    action, _reason = review_original(receipt, review.get('repair_ref'))
    if action not in {'reuse', 'transient', 'repaired'} or receipt is None:
        return False
    diagnostic = receipt.diagnostic if isinstance(receipt.diagnostic, Mapping) else {}
    return (review.get('original_attempt_id') == receipt.attempt_id
            and review.get('error_code') == receipt.error_code
            and review.get('failure_stage') == diagnostic.get('failure_stage')
            and review.get('error_type') == diagnostic.get('error_type'))


def _clip(value, maximum):
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text.encode()) <= maximum:
        return value
    text = text.encode()[:max(0, maximum-16)].decode("utf-8", errors="ignore")
    return text + "…"


def output_diagnostic(code, details=None):
    details = details or {}
    diagnostic = {"error_code": code, **{key: _clip(details[key], 240)
        for key in ("path", "validator", "actual", "expected", "allowed_refs", "conflict_with", "conflict_fields", "rule")
        if key in details}}
    if len(json.dumps(diagnostic, ensure_ascii=False).encode()) > 1000:
        for key in ("actual", "expected", "rule"):
            if key in diagnostic:
                diagnostic[key] = _clip(diagnostic[key], 100)
    return diagnostic


def corrected_request(request, result, diagnostic, counter):
    candidate = request
    references = episode_references(candidate, frozen_episodes(candidate))
    diagnostic = {**diagnostic, "allowed_refs": list(references)}
    raw = generated_text(result)
    try:
        value = model_json(raw)
        path = diagnostic.get("path", [])
        for key in path[:-1] if path and isinstance(path[-1], str) else path:
            value = value[key]
    except (AppError, ValueError, TypeError, KeyError, IndexError):
        value = raw
    correction = {"original_attempt_id": result.receipt.attempt_id,
        "diagnostic": diagnostic, "previous_output_fragment": _clip(value, 512),
        "instruction": REPAIR_INSTRUCTION}
    message = _message(correction)
    candidate = replace(candidate, messages=(*candidate.messages, message))
    estimate = counter.measure(candidate.system, candidate.messages, candidate.tools)[0]
    if estimate > BACKGROUND_INPUT_TOKENS:
        raise AppError("reflection_material_budget_exceeded", status=413,
            details={"estimated": estimate, "input_budget": BACKGROUND_INPUT_TOKENS,
                     "repair": True, "original_attempt_id": result.receipt.attempt_id})
    return replace(candidate, estimated_input_tokens=estimate)
