"""Coalesced Procrastinate maintenance, persistent due times and worker observations."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass
from ..contracts import canonical


SCHEDULE = (
    "INSERT INTO capabilities.maintenance(name,owner,lease_until,next_due_at,wake_generation) "
    "VALUES(%s,'',to_timestamp(0),coalesce(%s::timestamptz,now()),1) ON CONFLICT(name) DO UPDATE SET "
    "next_due_at=LEAST(capabilities.maintenance.next_due_at,excluded.next_due_at),wake_generation=capabilities.maintenance.wake_generation+1 "
    "WHERE capabilities.maintenance.next_due_at IS NULL OR capabilities.maintenance.next_due_at>excluded.next_due_at "
    "RETURNING wake_generation,next_due_at"
)


ACTIVATE = (
    "INSERT INTO capabilities.maintenance(name,owner,lease_until,next_due_at,wake_generation) "
    "VALUES(%s,'',to_timestamp(0),now(),1) ON CONFLICT(name) DO UPDATE SET "
    "next_due_at=coalesce(capabilities.maintenance.next_due_at,excluded.next_due_at),"
    "wake_generation=capabilities.maintenance.wake_generation+1 "
    "RETURNING wake_generation,next_due_at,GREATEST(next_due_at,lease_until,now()) AS scheduled_at"
)


def maintenance_job(name, row, *, scheduled_at=None):
    generation = row["wake_generation"]
    return JobSpec("capabilities.media." + name, f"media-maintenance:{name}:{generation}",
        {"wake_generation": generation}, ServiceClass.ELASTIC,
        scheduled_at=scheduled_at if scheduled_at is not None else row["next_due_at"])


def wake_sync(runtime, connection, name, due=None):
    task = "capabilities.media." + name
    if task not in runtime.jobs.sync_tasks:
        return
    row = connection.execute(SCHEDULE, (name, due)).fetchone()
    if row:
        runtime.jobs.enqueue_sync(connection, maintenance_job(name, row))


class Background:
    def __init__(self, runtime, *, next_due=None, owner=None):
        self.runtime, self.next_due, self.owner = runtime, next_due, owner
        self.operations, self.states = {}, {}
        self.closed = False

    def start(self, name, operation, *args):
        self.operations[name] = (operation, args)
        self.states.setdefault(name, {"state": "scheduled", "restarts": 0, "code": None})
        task = "capabilities.media." + name
        if task not in self.runtime.jobs.tasks:
            async def dispatch(**payload):
                await self.dispatch(name, **payload)
            self.runtime.jobs.register(task, dispatch)
        work_task = "capabilities.media.work." + name
        if work_task not in self.runtime.jobs.tasks:
            async def work(**payload):
                await self.run(name, **payload)
            self.runtime.jobs.register(work_task, work)

    async def activate(self):
        for name in self.operations:
            if self.closed:
                return
            # Startup cannot assume that the saved generation still has a live
            # native job. Re-enqueue one generation without advancing future
            # work or taking an existing owner's lease.
            async with self.runtime.database.transaction() as connection:
                row = await (await connection.execute(ACTIVATE, (name,))).fetchone()
                await self.runtime.jobs.enqueue(connection,
                    maintenance_job(name, row, scheduled_at=row["scheduled_at"]))

    async def schedule(self, name, due=None):
        """Omitted due means immediately on the queue's database clock."""
        if self.closed:
            return
        async with self.runtime.database.transaction() as connection:
            row = await (await connection.execute(SCHEDULE, (name, due))).fetchone()
            if row:
                await self.runtime.jobs.enqueue(connection, maintenance_job(name, row))

    async def dispatch(self, name, wake_generation=None):
        """Only queue durable work; the resource-specific worker owns its lease."""
        if self.closed:
            return
        async with self.runtime.database.transaction() as connection:
            row = await (await connection.execute(
                "SELECT wake_generation,lease_until,GREATEST(lease_until,now()) AS scheduled_at "
                "FROM capabilities.maintenance WHERE name=%s AND next_due_at<=now() "
                "AND (%s::bigint IS NULL OR wake_generation=%s)",
                (name, wake_generation, wake_generation))).fetchone()
            if row:
                # A wake racing with a live owner waits in the native queue, not
                # in a maintenance slot. Its lease value distinguishes a later
                # busy-owner wake from a completed job of the same generation.
                key = f"media-work:{name}:{row['wake_generation']}:{row['lease_until'].isoformat()}"
                await self.runtime.jobs.enqueue(connection, JobSpec(
                    "capabilities.media.work." + name, key,
                    {"wake_generation": row["wake_generation"]},
                    ServiceClass.ELASTIC, scheduled_at=row["scheduled_at"]))

    async def run(self, name, wake_generation=None):
        if self.closed:
            return
        owner = str(uuid4())
        worker_owner = self.owner() if self.owner else None
        async with self.runtime.database.transaction() as connection:
            row = await (await connection.execute(
                "UPDATE capabilities.maintenance SET owner=%s,lease_until=now()+interval '60 seconds',"
                "next_due_at=NULL,body=body||%s::jsonb WHERE name=%s AND lease_until<=now() "
                "AND (next_due_at IS NULL OR next_due_at<=now()) "
                "AND (%s::bigint IS NULL OR wake_generation=%s) RETURNING body,wake_generation",
                (owner, canonical({"state": "running", "worker_owner": worker_owner,
                    "started_at": datetime.now(UTC).isoformat(), "code": None}), name,
                 wake_generation, wake_generation))).fetchone()
        if not row:
            # A change can arrive while the current owner is busy. Preserve one
            # due observation after its lease instead of dropping the wakeup.
            async with self.runtime.database.transaction() as connection:
                busy = await (await connection.execute(
                    "SELECT lease_until,wake_generation FROM capabilities.maintenance WHERE name=%s AND lease_until>now() "
                    "AND (%s::bigint IS NULL OR wake_generation=%s)", (name, wake_generation, wake_generation))).fetchone()
                if busy:
                    await self.runtime.jobs.enqueue(connection, JobSpec("capabilities.media." + name,
                        f"media-maintenance-busy:{name}:{busy['wake_generation']}:{busy['lease_until'].isoformat()}",
                        {"wake_generation": busy["wake_generation"]}, scheduled_at=busy["lease_until"] + timedelta(seconds=1)))
            return

        async def renew():
            while True:
                await asyncio.sleep(15)
                async with self.runtime.database.transaction() as connection:
                    await connection.execute("UPDATE capabilities.maintenance SET lease_until=now()+interval '60 seconds' "
                        "WHERE name=%s AND owner=%s", (name, owner))

        heartbeat = asyncio.create_task(renew())
        operation, args = self.operations[name]
        state = {**row["body"], "state": "running"}
        self.states[name] = state
        try:
            await operation(*args)
        except BaseException as error:
            state.update(state="failed", code=getattr(error, "code", "media_background_failed"))
            raise
        else:
            state.update(state="scheduled", code=None)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            now = datetime.now(UTC)
            idle_runs = min(int(state.get("idle_runs", 0)) + 1, 5)
            try:
                plan = await self.next_due(name) if self.next_due else {"known": False}
            except Exception:
                plan = {"known": False}
                state.update(state="failed", code="media_due_state_unavailable")
            if plan.get("known"):
                due = datetime.fromtimestamp(plan["due"], UTC) if plan.get("due") is not None else None
                delay = 10 if due is not None else 300
            else:
                due = None
                delay = 10 if self.next_due is None else min(300, 10 * 2**idle_runs)
            state.update(idle_runs=idle_runs, observed_at=now.isoformat(), worker_owner=worker_owner)
            # One decision governs both the generation and its native job even
            # if close arrives while the following transaction is awaiting IO.
            reschedule = not self.closed
            async with self.runtime.database.transaction() as connection:
                # Finish and the next wake commit together while we still own
                # this lease. A business change during the operation keeps its
                # earlier due, and an expired owner cannot replace a newer one.
                queued = await (await connection.execute(
                    "UPDATE capabilities.maintenance SET owner='',lease_until=now(),body=%s::jsonb,"
                    "next_due_at=LEAST(next_due_at,GREATEST(coalesce(%s::timestamptz,now()),"
                    "now()+%s*interval '1 second')),wake_generation=wake_generation+%s "
                    "WHERE name=%s AND owner=%s RETURNING wake_generation,next_due_at",
                    (canonical(state), due, delay, int(reschedule), name, owner))).fetchone()
                if queued and reschedule:
                    await self.runtime.jobs.enqueue(connection, maintenance_job(name, queued))

    async def snapshot(self):
        async with self.runtime.database.transaction(read_only=True) as connection:
            rows = await (await connection.execute(
                "SELECT m.name,m.body,m.next_due_at,m.wake_generation,"
                "coalesce(o.expires_at>now() AND o.stopped_at IS NULL,false) AS owner_live "
                "FROM capabilities.maintenance m LEFT JOIN capabilities.execution_owners o ON o.id=m.body->>'worker_owner' "
                "WHERE m.name=ANY(%s) ORDER BY m.name", (list(self.operations),))).fetchall()
        saved = {row["name"]: row for row in rows}
        return [{"name": name, **(saved[name]["body"] if name in saved else {}),
                 "state": saved[name]["body"].get("state", "unknown") if name in saved and saved[name]["owner_live"] else "unknown",
                 "code": saved[name]["body"].get("code") if name in saved and saved[name]["owner_live"] else "media_worker_observation_missing",
                 "next_due_at": saved[name]["next_due_at"] if name in saved else None,
                 "generation": saved[name]["wake_generation"] if name in saved else None,
                 "owner_live": bool(name in saved and saved[name]["owner_live"])} for name in sorted(self.operations)]

    def view(self):
        return [{"name": name, **state} for name, state in self.states.items()]

    def healthy(self):
        return not self.closed and all(state["state"] != "failed" for state in self.states.values())

    async def close(self):
        self.closed = True
