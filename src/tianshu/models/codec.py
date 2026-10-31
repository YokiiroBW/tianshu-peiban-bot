"""Strict reconstruction at the broker process boundary; no domain rules live here."""

from datetime import datetime

from tianshu.types import MediaRef, OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext
from tianshu.runtime.errors import AppError
from .types import (ExternalRequest, ModelEvent, ModelMessage, ModelPart, ModelReceipt,
                    ModelRequest, ModelResult, ModelUsage, ToolSpec)


def context_from_wire(value):
    return TrustedContext(value["principal_id"], value.get("actor_id"), value.get("person_id"),
        value.get("conversation_id"), tuple(value["scope_ids"]), tuple(value["audience"]),
        value["access_epoch"], OriginKind(value["origin_kind"]), frozenset(value.get("permissions", [])))


def budget_from_wire(value):
    return RequestBudget(datetime.fromisoformat(value["deadline_at"]), value["cancellation_id"],
                         value["trace_id"])


def part_from_wire(value):
    value = dict(value)
    if value.get("media"):
        value["media"] = MediaRef(**value["media"])
    return ModelPart(**value)


def request_from_wire(value):
    try:
        value = dict(value)
        value["context"] = context_from_wire(value["context"])
        value["budget"] = budget_from_wire(value["budget"])
        value["service_class"] = ServiceClass(value["service_class"])
        value["messages"] = tuple(ModelMessage(item["role"],
            tuple(part_from_wire(part) for part in item["parts"]), item.get("native", {}))
            for item in value["messages"])
        value["tools"] = tuple(ToolSpec(**item) for item in value.get("tools", []))
        value["required_capabilities"] = frozenset(value.get("required_capabilities", ["text"]))
        value["source_refs"] = tuple(SourceRef(**item) for item in value.get("source_refs", []))
        return ModelRequest(**value)
    except (KeyError, TypeError, ValueError):
        raise AppError("invalid_model_request") from None


def external_from_wire(value):
    try:
        value = dict(value)
        value["context"] = context_from_wire(value["context"])
        value["budget"] = budget_from_wire(value["budget"])
        value["service_class"] = ServiceClass(value["service_class"])
        value["source_refs"] = tuple(SourceRef(**item) for item in value.get("source_refs", []))
        return ExternalRequest(**value)
    except (KeyError, TypeError, ValueError):
        raise AppError("invalid_external_request") from None


def receipt_from_wire(value):
    if value is None:
        return None
    value = dict(value)
    value["usage"] = ModelUsage(**value.get("usage", {}))
    for name in ("queued_at", "started_at", "finished_at"):
        if value.get(name):
            value[name] = datetime.fromisoformat(value[name])
    return ModelReceipt(**value)


def result_from_wire(value):
    return ModelResult(tuple(part_from_wire(part) for part in value["parts"]),
                       receipt_from_wire(value["receipt"]), value.get("native_response"))


def event_from_wire(value):
    return ModelEvent(value["kind"], value.get("text"),
        part_from_wire(value["part"]) if value.get("part") else None,
        receipt_from_wire(value.get("receipt")), value.get("data"))
