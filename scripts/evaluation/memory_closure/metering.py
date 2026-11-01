"""Provider receipt accounting without adding overlapping usage categories."""

from datetime import datetime
from hashlib import sha256
import json


def nested(value, *path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def number(*values):
    return next((value for value in values if type(value) in {int, float} and value >= 0), None)


def milliseconds(start, end):
    if start is None or end is None:
        return None
    start = datetime.fromisoformat(start) if isinstance(start, str) else start
    end = datetime.fromisoformat(end) if isinstance(end, str) else end
    return max(0, (end - start).total_seconds() * 1000)


def receipt_metrics(receipt):
    usage = receipt.get("usage") or {}
    raw = usage.get("raw") or {}
    # A provider may nest its original usage in usage.raw. Never infer missing
    # actual counts from estimated_input_tokens, bytes, or the context budget.
    provider = raw.get("usage", raw)
    provider = provider if isinstance(provider, dict) else {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "reasoning_tokens": number(nested(provider, "output_tokens_details", "reasoning_tokens"),
            nested(provider, "completion_tokens_details", "reasoning_tokens"),
            provider.get("reasoning_tokens"), provider.get("thoughtsTokenCount")),
        "cached_input_tokens": number(usage.get("cached_input_tokens"),
            nested(provider, "input_tokens_details", "cached_tokens"),
            nested(provider, "prompt_tokens_details", "cached_tokens")),
        "cache_write_tokens": usage.get("cache_write_tokens"),
        "estimated_input_tokens": usage.get("estimated_input_tokens"),
        "metering": usage.get("metering", "unknown"),
        "reported_cost_usd": number(provider.get("cost_usd")),
        "queue_ms": milliseconds(receipt.get("queued_at"), receipt.get("started_at")),
        "provider_ms": milliseconds(receipt.get("started_at"), receipt.get("finished_at")),
        "status": receipt.get("status"),
        "error_code": receipt.get("error_code"),
        "overlap": {"reasoning_tokens": "subset_or_provider_dependent_of_output; never add",
                    "cached_input_tokens": "subset_of_input; never add",
                    "cache_write_tokens": "provider_dependent; separate_column_only"},
    }


def aggregate_calls(calls):
    """One final observed receipt per physical attempt, including failed calls."""
    latest = {}
    for call in calls:
        if call.get("broker_attempt_id"):
            old = latest.get(call["broker_attempt_id"])
            if not old or call.get("receipt") is not None:
                latest[call["broker_attempt_id"]] = call
    metrics = [receipt_metrics(call["receipt"]) if call.get("receipt") else {}
               for call in latest.values()]
    result = {"attempt_identities": len(latest), "confirmed_broker_attempts": sum(bool(x) for x in metrics),
              "physical_dispatch_confirmed": sum((call.get("receipt") or {}).get("dispatch") == "submitted"
                                                  for call in latest.values()),
              "dispatch_unknown": sum(not call.get("receipt") or call["receipt"].get("dispatch") == "unknown"
                                       for call in latest.values()),
              "failed_or_unknown": sum(x.get("status") != "succeeded" for x in metrics)}
    for field in ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens",
                  "cache_write_tokens", "reported_cost_usd", "queue_ms", "provider_ms"):
        known = [item[field] for item in metrics if item.get(field) is not None]
        result[field] = {"known_sum": sum(known) if known else None,
                         "complete": len(known) == len(metrics) and bool(metrics),
                         "known_attempts": len(known), "unknown_attempts": len(metrics) - len(known)}
    result["total_tokens_or_fee"] = None
    routes = set()
    route_fields = ("route_id", "route_revision", "provider_id", "model", "protocol", "quota_group")
    for call in latest.values():
        receipt = call.get("receipt")
        if receipt:
            routes.add(tuple((key, receipt.get(key)) for key in route_fields))
    result["routes"] = [dict(items) for items in sorted(routes, key=repr)]
    result["reason"] = "Report separate provider columns; no cache/reasoning double count or token-priced fee."
    offered, repeated, seen = 0, 0, set()
    functions = {}
    for call in calls:
        if call.get("operation") != "prepared":
            continue
        request = call["request"]
        function = request["function"]
        functions[function] = functions.get(function, 0) + 1
        texts = [request.get("system", "")]
        texts.extend(part["text"] for message in request.get("messages", [])
                     for part in message.get("parts", []) if part.get("text"))
        if request.get("native_payload"):
            texts.append(json.dumps(request["native_payload"], ensure_ascii=False, sort_keys=True))
        for text in texts:
            if not text:
                continue
            encoded = text.encode("utf-8")
            key = sha256(encoded).hexdigest()
            offered += len(encoded)
            if key in seen:
                repeated += len(encoded)
            seen.add(key)
    result["request_text_utf8_bytes"] = offered
    result["repeated_exact_text_segment_utf8_bytes"] = repeated
    result["text_measurement"] = "actual prepared system/part/native text; not provider tokens, fees, or proof of duplicate authors"
    result["offered_attempts_by_function"] = functions
    return result
