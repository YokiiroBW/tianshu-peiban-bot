from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from tianshu.life import day_plan, service


class Clock:
    def __init__(self):
        self.value = 0.0

    def now(self):
        return self.value

    def advance(self, milliseconds):
        self.value += milliseconds / 1000


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    async def fetchone(self):
        return self.rows[0] if self.rows else None

    async def fetchall(self):
        return self.rows


def source(identifier):
    return {"source_id": identifier, "revision": 1, "locator": {}}


class Database:
    def __init__(self, clock, *, has_plan):
        self.clock, self.has_plan, self.queries = clock, has_plan, []

    @asynccontextmanager
    async def transaction(self, *, read_only=False):
        assert read_only
        self.clock.advance(1)
        try:
            yield self
        finally:
            self.clock.advance(1)

    async def execute(self, query, _parameters):
        self.clock.advance(2)
        self.queries.append(query)
        if "FROM life.day_plans" in query:
            rows = [{"id": "plan", "source_refs": []}] if self.has_plan else []
        elif "FROM life.day_slots s" in query:
            rows = [{"id": "slot", "source_refs": [source("visible")]},
                    {"id": "revoked", "source_refs": [source("revoked")]}]
        elif "checkpoint_data->>'last_execution'" in query:
            rows = [{"id": "recent", "source_refs": []}]
        elif "FROM life.activities" in query:
            rows = [{"id": "unfinished", "source_refs": []}]
        elif "FROM life.plan_generations" in query:
            rows = [{"generation_key": "generation", "application_results": {"items": []}}]
        elif "FROM life.plan_watermarks" in query:
            rows = [{"revision": 7}]
        else:
            raise AssertionError("unexpected query")
        return Cursor(rows)


class Identity:
    def __init__(self, clock):
        self.clock = clock

    async def check(self, _context):
        self.clock.advance(5)

    async def check_many(self, _context, groups):
        self.clock.advance(7)
        return tuple(all(ref.source_id != "revoked" for ref in group) for group in groups)


@pytest.mark.asyncio
@pytest.mark.parametrize("has_plan", [True, False])
async def test_day_plan_reports_measured_stages_without_claiming_http_or_json_time(monkeypatch, has_plan):
    clock = Clock()
    database, identity = Database(clock, has_plan=has_plan), Identity(clock)

    async def state(_context):
        clock.advance(11)
        return {"timezone": "UTC"}

    def project(row):
        clock.advance(3)
        return {"id": row["id"]}

    monkeypatch.setattr(day_plan, "perf_counter", clock.now)
    monkeypatch.setattr(service, "public_record", project)
    life = SimpleNamespace(database=database, identity=identity, state=state)
    context = SimpleNamespace(actor_id="actor", scope_ids=("scope",), audience=("scope",))
    result = await day_plan.DayPlans(life).view(context)
    timings = result["timings_ms"]

    assert result["slots"] == [{"id": "slot"}]  # Revoked sources remain filtered.
    assert timings["identity_check"] == 5 and timings["state_lookup"] == 11
    assert timings["snapshot_database"] == 10
    assert timings["snapshot_source_check"] == (28 if has_plan else 21)
    assert timings["application_database"] == (4 if has_plan else None)
    assert timings["planning_database"] == 4 and timings["projection"] == (12 if has_plan else 9)
    assert timings["view_total"] == (74 if has_plan else 60)
    assert timings["json_serialization"] is None and timings["http_total"] is None
    assert any("FROM life.plan_generations" in query for query in database.queries) is has_plan
