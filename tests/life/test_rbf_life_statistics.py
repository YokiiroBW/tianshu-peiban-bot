"""Coverage proves a finite live boundary and honors outstanding replay."""
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from test_rbf_planning_recovery import make_life
from tianshu.conversation.codec import document
from tianshu.life.statistics import backfill, record_on


@pytest.mark.asyncio
async def test_live_backfill_reports_pending_until_original_replay_job_finishes(database):
    value = await make_life(database)
    life = value.worker.life
    stats = type("Statistics",(),{})()
    stats.record_safely_on = AsyncMock(side_effect=[False,True,True])
    stats.mark_producer_on = AsyncMock(return_value=True)
    life.bind_statistics(stats)
    value.jobs.register("life.statistics_replay",value.worker.statistics_replay)
    when = datetime.now(UTC)-timedelta(minutes=1)
    application = {"items":[{"candidate_key":"candidate","status":"scheduled","reason":"ok"}],"next_due_at":None}
    async with database.transaction() as connection:
        await connection.execute("CREATE SCHEMA IF NOT EXISTS operations")
        await connection.execute("CREATE TABLE operations.statistic_producers(producer text PRIMARY KEY,last_seen timestamptz NOT NULL DEFAULT now(),detail jsonb)")
        await connection.execute("INSERT INTO life.plan_generations(generation_key,actor_id,day,material_fingerprint,base_revision,context,state,reviewed_at,application_results) "
            "VALUES ('g','actor',%s,'m',1,%s,'applied',%s,%s)",(when.date(),document(value.context),when,document(application)))
        assert not await record_on(life,connection,"g","actor",when,application,enqueue_replay=True)
    pending = await backfill(life)
    detail = stats.mark_producer_on.await_args.kwargs["detail"]
    assert not pending["complete"] and pending["state"] == "pending"
    assert detail["mode"] == "live" and detail["backfill"]["state"] == "pending"
    async with database.transaction() as connection:
        await connection.execute("UPDATE procrastinate_jobs SET status='succeeded' WHERE task_name='life.statistics_replay'")
    complete = await backfill(life,cursor=pending["next_cursor"])
    detail = stats.mark_producer_on.await_args.kwargs["detail"]
    assert complete["complete"] and detail["backfill"]["state"] == "complete"
    assert detail["backfill"]["watermark"]["generation_key"] == "g"
