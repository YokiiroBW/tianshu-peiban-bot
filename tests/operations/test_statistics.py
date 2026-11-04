from datetime import UTC, datetime, timedelta

from tianshu.operations.statistics import StatisticsProjection


async def test_attempts_late_usage_and_replayed_revision(operations_db):
    projection = StatisticsProjection(operations_db)
    when = datetime.now(UTC).replace(second=0, microsecond=0)
    def event(attempt, revision, usage):
        return {"family": "models", "event_id": attempt, "revision": revision,
                "occurred_at": when, "module": "models", "actor_id": "actor-1", "route_id": "route-1",
                "dimensions": {"state": "succeeded"},
                "metrics": {"physical_attempts": 1, "input_tokens": usage, "actual_cost": None}}
    async with operations_db.transaction() as conn:
        for attempt, usage in (("a1", 100), ("a2", None), ("a3", 200)):
            assert await projection.record_on(conn, event(attempt, 1, usage))
        assert await projection.record_on(conn, event("a2", 2, 150))
        assert await projection.record_on(conn, event("a2", 2, 150))
        assert await projection.record_on(conn, event("a2", 1, None))
    response = await projection.query({"start": (when-timedelta(minutes=1)).isoformat(),
                                       "end": (when+timedelta(minutes=1)).isoformat(),
                                       "timezone": "Asia/Shanghai", "actor_id": "actor-1"})
    assert response["overview"]["physical_attempts"] == {"total": 3, "samples": 3, "unknown": 0}
    assert response["overview"]["input_tokens"] == {"total": 450, "samples": 3, "unknown": 0}
    assert response["overview"]["actual_cost"] == {"total": None, "samples": 0, "unknown": 3}
    assert "models" in response["coverage"]["missing"]


async def test_projection_failure_rolls_back_only_savepoint(operations_db):
    projection = StatisticsProjection(operations_db)
    event = {"family": "messages", "event_id": "message-1", "revision": 1,
             "occurred_at": datetime.now(UTC), "module": "channels", "metrics": {"received": 1}}
    async with operations_db.transaction() as conn:
        await conn.execute("ALTER TABLE operations.statistic_buckets RENAME TO unavailable_buckets")
        assert not await projection.record_safely_on(conn, event)
        await conn.execute("INSERT INTO runtime.job_keys(key,task) VALUES('business-key','fixture')")
        await conn.execute("ALTER TABLE operations.unavailable_buckets RENAME TO statistic_buckets")
    async with operations_db.transaction(read_only=True) as conn:
        assert await (await conn.execute("SELECT key FROM runtime.job_keys WHERE key='business-key'")).fetchone()


async def test_life_outcomes_and_independent_media_remain_distinct(operations_db):
    projection = StatisticsProjection(operations_db)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    async with operations_db.transaction() as conn:
        for identity, state, metric in (("c1", "deferred_no_slot", "life_deferred_no_slot"),
                                        ("c2", "stale_state", "life_stale_state"),
                                        ("c3", "scheduled", "life_scheduled")):
            await projection.record_on(conn, {"family": "life", "event_id": identity, "revision": 1,
                "occurred_at": now, "module": "life", "dimensions": {"state": state}, "metrics": {metric: 1}})
        await projection.record_on(conn, {"family": "media", "event_id": "download", "revision": 1,
            "occurred_at": now, "module": "media", "metrics": {"media_completed": 1}})
    result = await projection.query({"start": (now-timedelta(minutes=1)).isoformat(),
                                     "end": (now+timedelta(minutes=1)).isoformat()})
    assert result["overview"]["life_deferred_no_slot"]["total"] == 1
    assert "life_intentional_idle" not in result["overview"]
    assert result["breakdowns"]["family"]["media"]["media_completed"]["total"] == 1
    assert "physical_attempts" not in result["overview"]
