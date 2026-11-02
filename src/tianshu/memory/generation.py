"""Memory extraction's native request policy; shared author routes stay unchanged."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid5

from tianshu.models.budgets import with_output_tokens
from tianshu.models.types import ModelMessage, ModelPart
from tianshu.runtime.errors import AppError
from tianshu.types import RequestBudget

MODEL_REQUEST_SECONDS = 300
REPAIR_LIMIT = 1
REPAIRABLE = frozenset({
    "memory_consolidation_output_invalid", "memory_consolidation_invalid",
    "memory_consolidation_target_coverage", "memory_consolidation_duplicate_operation",
    "memory_consolidation_target_unprocessed", "memory_consolidation_revision_invalid",
    "memory_consolidation_read_missing", "memory_consolidation_duplicate_create",
    "memory_consolidation_material_coverage", "memory_consolidation_result_mismatch",
    "memory_consolidation_operation_owner", "memory_consolidation_unresolved_missing",
    "memory_consolidation_operation_coverage", "memory_consolidation_unknown_endpoint",
    "memory_consolidation_create_target", "memory_consolidation_support_target",
    "memory_consolidation_revision_target", "memory_context_ref_invalid",
    "memory_context_range_required", "memory_context_query_required",
    "claim_self_confirmation_unproven", "claim_actor_author_unproven", "claim_speaker_mismatch",
})
REPAIR_INSTRUCTION = (
    "\n上次输出未提交。按诊断修正候选并返回完整JSON；仍以冻结来源为准，候选不是证据。"
    "不得省略目标或把未知冒充no_change。"
)


def _repair_message(value):
    return ModelMessage("user",(ModelPart("text",text=json.dumps(
        {"output_repair":value},ensure_ascii=False,separators=(",",":"))),))


def repair_input_estimate(counter,instruction,messages,attempt_id):
    """Reserve the bounded diagnostic before freezing any source prefix.

    Contract paths/validators come from the server schema, not candidate text.
    This conservative probe includes all diagnostic fields together plus a
    tokenizer boundary margin; the concrete repair is still measured again.
    """
    diagnostic={"error_code":max(REPAIRABLE,key=lambda code:(len(code),code)),
        "path":["operations",63,"payload","completion_basis","material_refs",15],
        "validator":"unevaluatedProperties","line":128000,"column":128000,"position":128000}
    message=_repair_message({"original_attempt_id":attempt_id,"diagnostic":diagnostic,
        "previous_output_omitted":"input_budget"})
    return counter.measure(instruction+REPAIR_INSTRUCTION,(*messages,message),())[0]+64


def deepseek_chat(profile):
    return profile["protocol"] == "openai.chat" and profile["model"].casefold().startswith("deepseek")


async def native_request(models, request):
    """Freeze the route and actual output key before the first durable step exists."""
    profile = await models.generation_profile(request)
    parameters = with_output_tokens(profile["protocol"], profile["output_parameters"], request.max_output_tokens)
    if deepseek_chat(profile):
        # DeepSeek documents low/high/max and JSON-object output. Its default
        # high effort can consume the whole combined reasoning/output allowance.
        # https://api-docs.deepseek.com/guides/thinking_mode/
        # https://api-docs.deepseek.com/guides/json_mode/
        parameters.update(reasoning_effort="low", response_format={"type": "json_object"})
    return replace(request, route_id=profile["route_id"], route_revision=profile["route_revision"],
        protocol=profile["protocol"], parameters=parameters,
        execution_timeout_seconds=MODEL_REQUEST_SECONDS,
        budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=MODEL_REQUEST_SECONDS),
            cancellation_id=request.budget.cancellation_id, trace_id=request.budget.trace_id))


def known_output(result):
    """A successful physical receipt alone does not prove a completed response."""
    if result.receipt.status != "succeeded":
        return False
    if result.completion.state in {"complete", "incomplete"}:
        return True
    native = result.native_response or {}
    return bool(native.get("finish_reason") or native.get("stop_reason")
        or native.get("status") in {"completed", "incomplete"}
        or any(isinstance(choice, dict) and choice.get("finish_reason") for choice in native.get("choices", ())))


def repairable(result, original, code):
    if not known_output(result):
        return False
    if code == "memory_consolidation_output_truncated":
        return (original.protocol == "openai.chat"
            and original.parameters.get("reasoning_effort") == "low"
            and original.parameters.get("response_format") == {"type": "json_object"}
            and original.parameters.get("thinking") != {"type": "disabled"})
    return code in REPAIRABLE and result.completion.state != "incomplete"


def repair_request(original, step_id, result, error, counter, input_tokens):
    """One new, recorded correction of known output; never mutates/replays its attempt."""
    details = error.details or {}
    diagnostic = {"error_code": error.code, **{key: details[key] for key in
        ("path", "validator", "line", "column", "position") if key in details}}
    candidate = "".join(part.text or "" for part in result.parts if part.kind == "text")
    correction = {"original_attempt_id": original.attempt_id,
        "diagnostic": diagnostic, "previous_output": candidate}
    # The original system already owns the full schema, target.create example
    # and human/actor evidence rules. Do not repeat it in the repair overhead.
    instruction = original.system + REPAIR_INSTRUCTION
    parameters = dict(original.parameters)
    if error.code == "memory_consolidation_output_truncated":
        # Keep the original combined allowance available for visible JSON;
        # disabling reasoning must not lower 12,288 tokens to 4,096.
        parameters.pop("reasoning_effort",None)
        parameters["thinking"] = {"type": "disabled"}
    def messages():
        return (*original.messages,_repair_message(correction))
    selected = messages()
    estimate = counter.measure(instruction, selected, ())[0]
    if estimate > input_tokens:
        # Failed output is optional diagnostic context. Original evidence is
        # always complete and unchanged; never trim it to fit a repair.
        correction.pop("previous_output")
        correction["previous_output_omitted"] = "input_budget"
        selected = messages()
        estimate = counter.measure(instruction, selected, ())[0]
    if estimate > input_tokens:
        raise AppError("memory_consolidation_repair_input_budget", status=502)
    return replace(original, intent_id=step_id, attempt_id=str(uuid5(NAMESPACE_URL, step_id)),
        budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=MODEL_REQUEST_SECONDS)),
        execution_timeout_seconds=MODEL_REQUEST_SECONDS,
        messages=selected, system=instruction, parameters=parameters, estimated_input_tokens=estimate)
