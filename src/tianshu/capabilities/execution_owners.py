"""Process leases fence orphaned attempts without replaying their business operation."""

import asyncio
import time
from uuid import uuid4
from .contracts import Fault


class ExecutionOwners:
    LEASE_SECONDS = 45
    HEARTBEAT_SECONDS = 10

    def __init__(self, actions):
        self.actions = actions
        self.id = str(uuid4())
        self.task = None
        self.closing = False
        self.lease_until = 0.0
        self.start_lock = asyncio.Lock()

    async def start(self):
        async with self.start_lock:
            if self.task and not self.task.done():
                return
            if self.closing:
                raise Fault("action_executor_stopping", 503)
            await self.renew()
            self.task = asyncio.create_task(self.loop(), name="capability-execution-owner")

    async def renew(self):
        async with self.actions.database.transaction(control=True) as conn:
            await conn.execute(
                "INSERT INTO capabilities.execution_owners(id,role,expires_at) "
                "VALUES(%s,%s,now()+%s*interval '1 second') ON CONFLICT(id) DO UPDATE "
                "SET expires_at=excluded.expires_at,stopped_at=NULL",
                (self.id, "worker" if self.actions.worker else "interactive", self.LEASE_SECONDS),
            )
        self.lease_until = time.monotonic() + self.LEASE_SECONDS

    async def check(self):
        if self.closing or self.lease_until <= time.monotonic():
            raise Fault("action_executor_lease_lost", 503)

    def status(self):
        return {
            "owner_id": self.id,
            "ready": bool(
                self.task
                and not self.task.done()
                and not self.closing
                and self.lease_until > time.monotonic()
            ),
        }

    async def loop(self):
        while not self.closing:
            try:
                await self.renew()
                await self.recover()
            except asyncio.CancelledError:
                raise
            except Exception:
                if self.lease_until <= time.monotonic() + self.HEARTBEAT_SECONDS:
                    self.closing = True
                    await self.interrupt()
                    return
            await asyncio.sleep(self.HEARTBEAT_SECONDS)

    async def recover(self):
        # This is executor recovery/control, never a second business work queue.
        async with self.actions.database.transaction(control=True, read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT a.id,a.execution_owner,a.attempt->>'attempt_id' AS attempt_id "
                    "FROM capabilities.actions a LEFT JOIN capabilities.execution_owners o "
                    "ON o.id=a.execution_owner WHERE a.receipt->>'execution'='running' AND a.execution_owner IS NOT NULL "
                    "AND (o.id IS NULL OR o.expires_at<=now()) ORDER BY a.created_at,a.id LIMIT 64"
                )
            ).fetchall()
        for row in rows:
            try:
                await self.actions.recover_orphan(
                    row["id"], row["attempt_id"], row["execution_owner"]
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                # Leave the original row eligible; a temporary authority/DB outage
                # cannot turn it into a new attempt or a completed business result.
                continue

    async def interrupt(self):
        current = asyncio.current_task()
        tasks = [task for task in tuple(self.actions.running.values()) if task is not current]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        self.closing = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        await self.interrupt()
        if self.lease_until:
            async with self.actions.database.transaction(control=True) as conn:
                await conn.execute(
                    "UPDATE capabilities.execution_owners SET expires_at=now(),stopped_at=now() WHERE id=%s",
                    (self.id,),
                )
