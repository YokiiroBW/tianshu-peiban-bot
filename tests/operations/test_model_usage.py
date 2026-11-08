from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from psycopg.types.json import Jsonb

from tianshu.models.statistics import attempt_event, usage_breakdown_on
from tianshu.operations.statistics import StatisticsProjection
from tianshu.runtime.errors import AppError


async def seed_usage(database):
    projection = StatisticsProjection(database)
    async with database.transaction() as conn:
        await conn.execute("INSERT INTO models.providers(provider_id,name,base_url,credential_ref,protocol) "
                           "VALUES('p1','供应商一','https://example.invalid','KEY','openai.chat'),"
                           "('p2','供应商二','https://example.invalid','KEY','openai.chat')")
        for identifier, function, provider, model, actor, route, stamp, state, tokens in (
            ("a1", "memory.extract", "p1", "original", "actor-1", "r1", "2026-11-07T15:59:00+00:00", "succeeded", (100, 10)),
            ("a2", "memory.extract", "p1", "original", "actor-1", "r1", "2026-11-07T16:01:00+00:00", "unknown", (200, None)),
            ("a3", "chat", "p1", "other", "actor-2", "r2", "2026-11-08T01:00:00+00:00", "failed", (50, 5)),
            ("a4", "chat", "p2", "original", "actor-1", "r3", "2026-11-08T02:00:00+00:00", "succeeded", (0, 0)),
        ):
            row = await (await conn.execute(
                "INSERT INTO models.attempts(attempt_id,intent_id,principal_id,actor_id,function,"
                "service_class,route_id,route_revision,provider_id,model,protocol,quota_group,"
                "request_digest,context_fingerprint,status,dispatch,queued_at,usage,provider_handle) "
                "VALUES(%s,'intent','test',%s,%s,'elastic',%s,1,%s,%s,'openai.chat','q',"
                "'digest','context',%s,'submitted',%s,%s,'handle') RETURNING *",
                (identifier, actor, function, route, provider, model, state, datetime.fromisoformat(stamp),
                 Jsonb({"metering": "reported", "input_tokens": tokens[0], "output_tokens": tokens[1],
                        "cached_input_tokens": 20, "raw": {"completion_tokens_details": {"reasoning_tokens": 3}}})))).fetchone()
            await projection.record_on(conn, attempt_event(row))
        await projection.record_on(conn, {"family": "models", "event_id": "unmatched", "revision": 1,
            "occurred_at": datetime(2026, 11, 8, 3, tzinfo=UTC), "module": "models",
            "dimensions": {"state": "unknown"}, "metrics": {
                "physical_attempts": None, "input_tokens": None, "output_tokens": None}})
    return projection


def query(**filters):
    return {"start": "2026-11-07T00:00:00+08:00", "end": "2026-11-09T00:00:00+08:00",
            "timezone": "Asia/Shanghai", **filters}


async def test_usage_groups_frozen_identities_and_keeps_unknown_and_zero(operations_db):
    projection = await seed_usage(operations_db)
    result = await projection.query(query())
    usage = result["model_usage"]
    assert usage["summary"]["total_tokens"] == {"total": 165, "samples": 3, "unknown": 2}
    assert usage["summary"]["physical_attempts"] == result["overview"]["physical_attempts"]
    assert usage["summary"]["input_tokens"] == result["overview"]["input_tokens"]
    assert usage["summary"]["model_failed"]["total"] == 1
    assert usage["summary"]["model_unknown"]["total"] == 2
    models = {(item["provider_id"], item["model"]): item for item in usage["by_model"]}
    assert len(models) == 4
    assert models["p2", "original"]["metrics"]["total_tokens"]["total"] == 0
    assert models["unassigned", "unassigned"]["metrics"]["total_tokens"]["total"] is None
    assert [day["date"] for day in usage["daily"]] == ["2026-11-07", "2026-11-08"]
    assert usage["daily"][0]["metrics"]["total_tokens"]["total"] == 110
    assert usage["daily"][1]["metrics"]["total_tokens"]["unknown"] == 2
    # Current route configuration is deliberately unrelated to the frozen attempts.
    async with operations_db.transaction() as conn:
        await conn.execute("INSERT INTO models.quota_groups(quota_group,concurrency,interactive_reserve,background_max) "
                           "VALUES('q',1,0,1)")
        await conn.execute("INSERT INTO models.routes(route_id,provider_id,model,quota_group) "
                           "VALUES('r1','p2','replacement','q')")
    revised = (await projection.query(query()))["model_usage"]
    assert revised == usage


async def test_late_usage_replaces_prior_values_and_all_filters_apply(operations_db):
    projection = await seed_usage(operations_db)
    async with operations_db.transaction() as conn:
        row = await (await conn.execute("UPDATE models.attempts SET revision=2,usage=usage || %s "
            "WHERE attempt_id='a2' RETURNING *", (Jsonb({"output_tokens": 20}),))).fetchone()
        await projection.record_on(conn, attempt_event(row))
        await projection.record_on(conn, attempt_event(row))
    result = await projection.query(query(actor_id="actor-1", route_id="r1", module="models"))
    usage = result["model_usage"]
    assert usage["summary"]["total_tokens"] == {"total": 330, "samples": 2, "unknown": 0}
    assert usage["summary"]["physical_attempts"]["total"] == 2
    assert len(usage["by_model"]) == len(usage["by_function"]) == 1
    assert usage["by_function"][0]["function"] == "memory.extract"
    excluded = await projection.query(query(module="life"))
    assert excluded["model_usage"]["summary"] == {}


async def test_month_range_daily_resolution_and_empty_days_remain_unmeasured(operations_db):
    projection = await seed_usage(operations_db)
    result = await projection.query(query(start="2026-10-10T00:00:00+08:00"))
    assert result["range"]["resolution"] == "day"
    assert len(result["model_usage"]["daily"]) == 30
    assert result["model_usage"]["daily"][0]["metrics"] == {}
    assert result["series"][0]["time"] == "2026-11-07T00:00:00+08:00"
    with pytest.raises(AppError, match="invalid_statistics_range"):
        await projection.query(query(start="2026-10-01T00:00:00+08:00"))


async def test_missing_model_ledger_reports_unavailable(operations_db):
    async with operations_db.transaction() as conn:
        await conn.execute("ALTER TABLE models.attempts RENAME TO unavailable_attempts")
        usage = await usage_breakdown_on(conn, start=datetime(2026, 11, 7, tzinfo=UTC),
            end=datetime(2026, 11, 9, tzinfo=UTC), timezone=ZoneInfo("Asia/Shanghai"))
    assert usage == {"status": "unavailable", "reason": "model_ledger_unavailable"}
