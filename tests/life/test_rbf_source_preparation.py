"""Prepare all known planning inputs before acquiring Life rows."""
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest

from test_rbf_planning_recovery import planning_context
from tianshu.conversation.codec import wire
from tianshu.life import planning_sources, writing_progress
from tianshu.runtime.errors import AppError
from tianshu.types import SourceRef


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchall(self):
        return self.rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None


class SnapshotConnection:
    def __init__(self):
        self.goal = {"id":"goal","revision":4,"source_refs":wire((SourceRef("goal-source",1),)),
            "intention_source_refs":wire((SourceRef("intention",1),))}
        self.activity = {"id":"activity","source_refs":wire((SourceRef("activity-source",1),))}
        self.plan = {"actor_id":"actor","day":date(2026,11,5),"scope_ids":["scope"],
            "source_refs":wire((SourceRef("day-source",1),)),"revision":2}

    async def execute(self, query, args):
        if "life.goals" in query:
            return Cursor([self.goal])
        if "life.activities" in query:
            return Cursor([self.activity])
        if "life.day_plans" in query:
            return Cursor([self.plan])
        raise AssertionError(query)


@pytest.mark.asyncio
async def test_goal_event_and_day_sources_are_prepared_as_one_set_before_domain_cas():
    connection = SnapshotConnection()
    class Database:
        @asynccontextmanager
        async def transaction(self, **kwargs):
            yield connection
    calls = []
    class Sources:
        async def prepare_event_writes(self, conn, context, events, *, additional_refs):
            calls.append(("sources",events,additional_refs))
    async def validate(conn, context, refs):
        calls.append(("validate",refs))
    life = SimpleNamespace(database=Database(),sources=Sources(),validate_persistence=validate)
    context = planning_context()
    decision = {"activity":{"existing_activity_id":"activity","goal_id":"goal"},"arrangements":[],
        "cancel_activity_ids":[],"goal_changes":[{"operation":"update","id":"goal","key":"g",
            "basis_ids":["basis"],"reason":"continue the direction"}]}
    frozen = {"basis":[{"id":"basis","source_refs":wire((SourceRef("basis-source",1),))}]}
    daily = {"slots":[],"unfinished":[],"recent_results":[],"plan":None}
    prepared = await planning_sources.prepare(life,context,decision,frozen,"generation",daily,(),None,date(2026,11,5))
    await planning_sources.lock_on(life,connection,context,prepared,None)
    assert [call[0] for call in calls] == ["sources","validate"]
    assert len(prepared["events"]) == 1
    assert {ref.source_id for ref in prepared["events"][0]["support_refs"]} == {"intention","basis-source"}
    assert {ref.source_id for ref in prepared["refs"]} == {
        "intention","basis-source","goal-source","activity-source","day-source"}
    await planning_sources.check_rows_on(connection,context,prepared)
    connection.plan = {**connection.plan,"revision":3}
    with pytest.raises(AppError,match="planner_preparation_changed"):
        await planning_sources.check_rows_on(connection,context,prepared)


@pytest.mark.asyncio
async def test_chapter_preparation_collects_each_authorized_goal_and_milestone_source():
    refs = lambda name: wire((SourceRef(name,1),))
    chapter = {"id":"chapter","actor_id":"actor","work_id":"work","source_refs":refs("chapter"),
        "work_source_refs":refs("work"),"work_snapshot_revision":1}
    goals = [{"id":"one","intention_source_refs":refs("goal-one")},
        {"id":"two","intention_source_refs":refs("goal-two")}]
    milestones = [{"goal_id":"one","milestone_id":"chapter:old-one","source_refs":refs("milestone-one")},
        {"goal_id":"two","milestone_id":"chapter:old-two","source_refs":refs("milestone-two")}]
    class Connection:
        async def execute(self, query, args):
            if "FROM life.chapters c JOIN life.works" in query:
                return Cursor([chapter])
            if "FROM life.goals" in query:
                return Cursor(goals)
            if "FROM life.goal_milestones" in query:
                return Cursor(milestones)
            raise AssertionError(query)
    class Database:
        @asynccontextmanager
        async def transaction(self, **kwargs):
            yield Connection()
    class Identity:
        async def check_many(self, context, groups):
            return (True,True,True,False)
    prepared = await writing_progress.prepare_completed(SimpleNamespace(database=Database(),identity=Identity()),planning_context(),"chapter")
    assert {ref.source_id for ref in prepared["refs"]} == {"chapter","work","goal-one","goal-two","milestone-one"}
    assert prepared["valid_goals"] == {"one":True,"two":True}
    assert not prepared["valid_milestones"][("two","chapter:old-two")]
