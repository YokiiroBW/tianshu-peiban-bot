"""Provider-owned assistant metadata required by a tool continuation."""

from .types import ModelMessage


def assistant_native(result):
    if result.receipt.protocol != "openai.chat":
        return {}
    fragments = [
        part.native["reasoning_content"]
        for part in result.parts
        if part.native.get("thought") and isinstance(part.native.get("reasoning_content"), str)
    ]
    if fragments:
        return {"reasoning_content": "".join(fragments)}
    # Previously persisted non-streaming results already contain this field.
    choices = (result.native_response or {}).get("choices") or []
    message = (choices[0].get("message") or {}) if choices else {}
    reasoning = message.get("reasoning_content")
    return {"reasoning_content": reasoning} if isinstance(reasoning, str) else {}


def assistant_message(result, *, parts=None):
    parts = result.parts if parts is None else parts
    # Chat carries its private reasoning once, in the native assistant field.
    # Other protocols keep their original signed content blocks unchanged.
    if result.receipt.protocol == "openai.chat":
        parts = tuple(part for part in parts if not part.native.get("thought"))
    return ModelMessage("assistant", tuple(parts), assistant_native(result))
