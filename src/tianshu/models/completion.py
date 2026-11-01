"""Lossless stop metadata for current responses and previously saved attempts."""

from .types import ModelCompletion


COMPLETE_REASONS = frozenset({"stop", "tool_calls", "function_call", "end_turn", "tool_use", "stop_sequence", "STOP"})


def completion_from_native(protocol, native):
    native = native or {}
    reasons = []
    status = native.get("status") if protocol == "openai.responses" else None
    if protocol == "openai.chat":
        reasons = [item.get("finish_reason") for item in native.get("choices", ())]
    elif protocol == "gemini.generate":
        reasons = [item.get("finishReason") for item in native.get("candidates", ())]
        if native.get("promptFeedback", {}).get("blockReason"):
            reasons.append(native["promptFeedback"]["blockReason"])
    elif protocol == "anthropic.messages":
        reasons = [native.get("stop_reason") or (native.get("delta") or {}).get("stop_reason")]
    elif protocol == "openai.responses":
        reason = (native.get("incomplete_details") or {}).get("reason")
        if reason:
            reasons.append(reason)
        if status == "failed":
            reasons.append((native.get("error") or {}).get("code") or "provider_failed")
    reasons = tuple(dict.fromkeys(reason for reason in reasons if isinstance(reason, str) and reason))
    state = (
        "incomplete" if status in {"incomplete", "failed"} or any(reason not in COMPLETE_REASONS for reason in reasons)
        else "complete" if reasons or status == "completed"
        else "unknown"
    )
    return ModelCompletion(state, reasons, status)


def merge_completion(previous, current):
    reasons = tuple(dict.fromkeys((*previous.stop_reasons, *current.stop_reasons)))
    state = (
        "incomplete" if "incomplete" in {previous.state, current.state}
        else "complete" if "complete" in {previous.state, current.state}
        else "unknown"
    )
    return ModelCompletion(state, reasons, current.provider_status or previous.provider_status)


def content_completion(result):
    """Typed summaries are authoritative; old results retain native fallback."""
    summary = getattr(result, "completion", None)
    if summary is not None and summary.state != "unknown":
        return summary
    return completion_from_native(getattr(getattr(result, "receipt", None), "protocol", ""), getattr(result, "native_response", None))
