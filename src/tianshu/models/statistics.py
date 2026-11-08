"""Build statistics from the original Models attempt, never from a wake."""

from datetime import timedelta


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


async def usage_breakdown_on(conn, *, start, end, timezone, module="", actor="", route=""):
    """Enrich the existing projection with frozen attempt identities.

    Usage and state come from the same revisable projection as the overview.
    Only the immutable function/provider/model fields come from the attempt;
    editing a route must not relabel its historical consumption. Unmatched
    projected attempts stay in an explicit unassigned group.
    """
    tables = await (await conn.execute(
        "SELECT to_regclass('models.attempts') AS attempts, "
        "to_regclass('models.providers') AS providers")).fetchone()
    if not tables["attempts"] or not tables["providers"]:
        return {"status": "unavailable", "reason": "model_ledger_unavailable"}
    rows = await (await conn.execute(
        "WITH facts AS ("
        " SELECT (e.occurred_at AT TIME ZONE %s)::date AS day,"
        " a.function,a.provider_id,a.model,p.name AS provider_name,"
        " e.metrics || jsonb_build_object("
        " 'total_tokens',CASE WHEN jsonb_typeof(e.metrics->'input_tokens')='number'"
        " AND jsonb_typeof(e.metrics->'output_tokens')='number'"
        " THEN (e.metrics->>'input_tokens')::numeric+(e.metrics->>'output_tokens')::numeric END,"
        " 'attempt_records',1,'model_failed',(e.state='failed')::int,"
        " 'model_succeeded',(e.state='succeeded')::int,"
        " 'model_unknown',(e.state='unknown')::int,"
        " 'model_cancelled',(e.state='cancelled')::int,"
        " 'model_inflight',(e.state IN ('queued','running'))::int) AS metrics"
        " FROM operations.statistic_events e"
        " LEFT JOIN models.attempts a ON a.attempt_id=e.event_id"
        " LEFT JOIN models.providers p ON p.provider_id=a.provider_id"
        " WHERE e.family='models' AND e.occurred_at >= %s AND e.occurred_at < %s"
        " AND (%s='' OR e.module=%s) AND (%s='' OR e.actor_id=%s)"
        " AND (%s='' OR e.route_id=%s))"
        " SELECT day,function,provider_id,model,provider_name,m.key AS metric,"
        " sum((m.value #>> '{}')::double precision) AS total,"
        " count(*) FILTER (WHERE m.value <> 'null'::jsonb) AS samples,"
        " count(*) FILTER (WHERE m.value = 'null'::jsonb) AS unknown"
        " FROM facts CROSS JOIN LATERAL jsonb_each(metrics) m"
        " WHERE m.key=ANY(%s)"
        " GROUP BY day,function,provider_id,model,provider_name,m.key ORDER BY day",
        (str(timezone), start, end, module, module, actor, actor, route, route,
         ["total_tokens", "input_tokens", "output_tokens", "cached_input_tokens",
          "cache_write_tokens", "reasoning_tokens", "physical_attempts", "attempt_records",
          "model_failed", "model_succeeded", "model_unknown", "model_cancelled",
          "model_inflight", "execution_ms", "ttft_ms", "actual_cost", "estimated_cost"]))).fetchall()
    summary, functions, models, providers, days = {}, {}, {}, {}, {}
    for row in rows:
        function = row["function"] or "unassigned"
        provider = row["provider_id"] or "unassigned"
        model = row["model"] or "unassigned"
        model_key = (provider, model)
        day = days.setdefault(str(row["day"]), {"metrics": {}, "functions": {}, "models": {}})
        function_info = {"function": function}
        model_info = {"provider_id": provider, "provider_name": row["provider_name"], "model": model}
        targets = [summary, day["metrics"]]
        for groups, key, info in ((functions, function, function_info),
                                  (models, model_key, model_info),
                                  (providers, provider, {"provider_id": provider, "provider_name": row["provider_name"]}),
                                  (day["functions"], function, function_info),
                                  (day["models"], model_key, model_info)):
            targets.append(groups.setdefault(key, {**info, "metrics": {}})["metrics"])
        for target in targets:
            metric = target.setdefault(row["metric"], {"total": None, "samples": 0, "unknown": 0})
            metric["samples"] += row["samples"]
            metric["unknown"] += row["unknown"]
            if row["samples"]:
                metric["total"] = (metric["total"] or 0) + row["total"]
    daily = []
    date = start.astimezone(timezone).date()
    last = (end - timedelta(microseconds=1)).astimezone(timezone).date()
    while date <= last:
        day = days.get(str(date), {"metrics": {}, "functions": {}, "models": {}})
        daily.append({"date": str(date), "metrics": day["metrics"],
                      "by_function": list(day["functions"].values()),
                      "by_model": list(day["models"].values())})
        date += timedelta(days=1)
    return {"status": "available", "summary": summary,
            "by_function": list(functions.values()), "by_model": list(models.values()),
            "by_provider": list(providers.values()), "daily": daily}
