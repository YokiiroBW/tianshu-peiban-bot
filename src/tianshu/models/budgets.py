"""Shared generation input policy and native output limits."""

from tianshu.runtime.errors import AppError

BACKGROUND_INPUT_TOKENS = 200000
MAX_INLINE_MEDIA_BYTES = 32 * 1024 * 1024
MAX_INLINE_MEDIA_PARTS = 128


def merged_parameters(plan, request):
    value = dict(plan.parameters)
    if plan.protocol == "openai.chat" and {"max_tokens", "max_completion_tokens"} & request.parameters.keys():
        value.pop("max_tokens", None)
        value.pop("max_completion_tokens", None)
    value.update(request.parameters)
    if plan.protocol == "gemini.generate" and "generationConfig" in request.parameters:
        value["generationConfig"] = {**plan.parameters.get("generationConfig", {}),
                                     **request.parameters["generationConfig"]}
    return value


def output_parameters(protocol, parameters):
    if protocol == "gemini.generate":
        return (parameters.get("generationConfig") or {}).get("maxOutputTokens")
    keys = ("max_completion_tokens", "max_tokens") if protocol == "openai.chat" else (
        ("max_output_tokens",) if protocol == "openai.responses" else ("max_tokens",))
    values = [parameters[key] for key in keys if key in parameters]
    if len(values) > 1 and any(value != values[0] for value in values[1:]):
        raise AppError("conflicting_output_token_budget", status=422)
    return values[0] if values else None


def effective_output_tokens(plan, request):
    if request.function in {"embed", "rerank"}:
        return request.max_output_tokens
    parameters = (request.native_payload if request.native_payload is not None
                  else merged_parameters(plan, request))
    value = output_parameters(plan.protocol, parameters)
    value = request.max_output_tokens if value is None else value
    if type(value) is not int or not 1 <= value <= 2**31 - 1:
        raise AppError("invalid_output_token_budget", status=422)
    return value


def with_output_tokens(protocol, parameters, tokens):
    """Keep unrelated native options and one authoritative output limit."""
    value = dict(parameters)
    if protocol == "gemini.generate":
        value["generationConfig"] = {**value.get("generationConfig", {}), "maxOutputTokens": tokens}
    elif protocol == "openai.responses":
        value["max_output_tokens"] = tokens
    elif protocol == "openai.chat" and "max_completion_tokens" in value:
        value.pop("max_tokens", None)
        value["max_completion_tokens"] = tokens
    else:
        value["max_tokens"] = tokens
    return value
