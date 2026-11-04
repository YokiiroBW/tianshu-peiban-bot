"""Build statistics from the original Models attempt, never from a wake."""


def _delta(points, start, end):
    left, right = points.get(start), points.get(end)
    return right - left if left is not None and right is not None and right >= left else None


def _milliseconds(start, end):
    return (end - start).total_seconds() * 1000 if start is not None and end is not None and end >= start else None


def attempt_event(row):
    usage = row.get("usage") or {}
    reported = usage.get("metering") == "reported"
    raw = usage.get("raw") if reported and isinstance(usage.get("raw"), dict) else {}
    token_details = raw.get("output_tokens_details") or raw.get("completion_tokens_details") or {}
    reasoning = token_details.get("reasoning_tokens") if isinstance(token_details, dict) else None
    if reasoning is None:
        reasoning = raw.get("thoughtsTokenCount")
    if type(reasoning) is not int or reasoning < 0:
        reasoning = None
    timing = row.get("http_timing") or {}
    points = timing.get("points_ms") or {}
    code = row.get("error_code") or ""
    if row["dispatch"] == "not_started":
        physical = 0
    elif (row.get("provider_handle") or points.get("headers_sent") is not None
          or code.startswith("upstream_http_") or row["status"] == "succeeded"
          or row.get("protocol") == "external" and row.get("started_at") is not None):
        physical = 1
    else:
        physical = None
    metrics = {
        "physical_attempts": physical,
        # A completed model response is not proof that the domain applied it.
        "business_success": None,
        "input_tokens": usage.get("input_tokens") if reported else None,
        "output_tokens": usage.get("output_tokens") if reported else None,
        "estimated_input_tokens": row.get("estimated_input_tokens"),
        "estimated_output_tokens": None,
        "cached_input_tokens": usage.get("cached_input_tokens") if reported else None,
        "cache_write_tokens": usage.get("cache_write_tokens") if reported else None,
        "reasoning_tokens": reasoning,
        "actual_cost": None,
        "estimated_cost": None,
        "queue_ms": _milliseconds(row.get("queued_at"), row.get("execution_started_at")),
        "execution_ms": _milliseconds(row.get("execution_started_at"), row.get("finished_at")),
        "ttft_ms": row.get("first_output_ms"),
        "pool_wait_ms": _delta(points, "pool_wait_start", "pool_wait_end"),
        "dns_ms": _delta(points, "dns_start", "dns_end"),
        "connect_ms": _delta(points, "connect_start", "connect_end"),
        "send_ms": _delta(points, "headers_sent", "body_sent"),
        "response_wait_ms": _delta(points, "body_sent", "response_headers"),
        "read_ms": _delta(points, "first_body_byte", "read_complete"),
        "effective_output_ms": _delta(points, "execution_started", "first_valid_output"),
        "http_total_ms": _delta(points, "request_start", "read_complete"),
    }
    return {"family": "models", "event_id": row["attempt_id"], "revision": row["revision"],
            "occurred_at": row["queued_at"], "module": "models",
            "actor_id": row.get("actor_id"), "route_id": row.get("route_id"),
            "dimensions": {"state": row["status"]}, "metrics": metrics}
