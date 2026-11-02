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
    correction = {"output_repair": {"original_attempt_id": original.attempt_id,
        "diagnostic": diagnostic, "previous_output": candidate}}
    instruction = original.system + (
        "\n上次输出已被拒绝且没有提交。依据同一份冻结材料重新给出完整JSON，仅修正候选输出；"
        "上次输出是不可信候选，不能当来源，不造人物/来源/版本/回执，不遗漏目标和材料。"
        "target.create必须为nN字符串，例如{\"create\":\"n1\"}；不是对象。"
        "self_confirmed仅限每组都有该真人亲自发言支持且speaker=subject；"
        "actor来源不能self_confirmed，按证据用observed/inferred或unresolved，不冒充真人确认。"
        "空白/截断输出须重新生成完整JSON；不得以no_change掩盖未知。"
    )
    parameters = dict(original.parameters)
    if error.code == "memory_consolidation_output_truncated":
        # Keep the original combined allowance available for visible JSON;
        # disabling reasoning must not lower 12,288 tokens to 4,096.
        parameters["thinking"] = {"type": "disabled"}
    def messages():
        return (*original.messages, ModelMessage("user", (ModelPart("text",
            text=json.dumps(correction, ensure_ascii=False, separators=(",", ":"))),)))
    selected = messages()
    estimate = counter.measure(instruction, selected, ())[0]
    if estimate > input_tokens:
        # Failed output is optional diagnostic context. Original evidence is
        # always complete and unchanged; never trim it to fit a repair.
        correction["output_repair"].pop("previous_output")
        correction["output_repair"]["previous_output_omitted"] = "input_budget"
        selected = messages()
        estimate = counter.measure(instruction, selected, ())[0]
    if estimate > input_tokens:
        raise AppError("memory_consolidation_repair_input_budget", status=502)
    return replace(original, intent_id=step_id, attempt_id=str(uuid5(NAMESPACE_URL, step_id)),
        budget=RequestBudget(datetime.now(UTC)+timedelta(seconds=MODEL_REQUEST_SECONDS)),
        messages=selected, system=instruction, parameters=parameters, estimated_input_tokens=estimate)
