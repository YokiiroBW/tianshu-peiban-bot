"""Translate only protocol options with an explicit, lossless equivalent."""

from dataclasses import replace

from tianshu.runtime.errors import AppError
from .budgets import with_output_tokens


_GENERATION = {"openai.chat", "openai.responses", "gemini.generate", "anthropic.messages"}


def adapt_request(request, source, target):
    if source == target:
        return request
    if (source not in _GENERATION or target not in _GENERATION or request.native_payload is not None
            or any(message.native or any(part.native for part in message.parts) for message in request.messages)):
        raise AppError("model_fallback_protocol_incompatible", status=422)
    parameters = dict(request.parameters)
    generation = parameters.pop("generationConfig", {})
    if not isinstance(generation, dict):
        raise AppError("model_fallback_protocol_incompatible", status=422)
    generation = dict(generation)
    for key in ("max_tokens", "max_completion_tokens", "max_output_tokens"):
        parameters.pop(key, None)
    generation.pop("maxOutputTokens", None)
    temperature = parameters.pop("temperature", generation.pop("temperature", None))
    top_p = parameters.pop("top_p", generation.pop("topP", None))
    format_value = parameters.pop("response_format", None)
    mime = generation.pop("responseMimeType", None)
    if format_value is not None and format_value != {"type": "json_object"}:
        raise AppError("model_fallback_protocol_incompatible", status=422)
    if mime not in (None, "application/json") or generation or parameters:
        raise AppError("model_fallback_protocol_incompatible", status=422)
    json_mode = format_value is not None or mime is not None
    if json_mode and target not in {"openai.chat", "gemini.generate"}:
        raise AppError("model_fallback_protocol_incompatible", status=422)
    converted = {}
    if target == "gemini.generate":
        config = {}
        if temperature is not None:
            config["temperature"] = temperature
        if top_p is not None:
            config["topP"] = top_p
        if json_mode:
            config["responseMimeType"] = "application/json"
        converted["generationConfig"] = config
    else:
        if temperature is not None:
            converted["temperature"] = temperature
        if top_p is not None:
            converted["top_p"] = top_p
        if json_mode:
            converted["response_format"] = {"type": "json_object"}
    return replace(request, protocol=target,
                   parameters=with_output_tokens(target, converted, request.max_output_tokens))
