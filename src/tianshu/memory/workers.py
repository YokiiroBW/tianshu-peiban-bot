"""Thin queue entry points for the unique consolidation writer and reusable indexes."""

from datetime import UTC, datetime

from tianshu.runtime.jobs import JobSpec
from tianshu.types import ServiceClass


class MemoryWorkers:
    def __init__(self, memory, assembler, embeddings, steps):
        self.memory, self.assembler, self.embeddings, self.steps = memory, assembler, embeddings, steps
        self.database, self.identity, self.sources = memory.database, memory.identity, memory.sources
        self.consolidation = memory.consolidation
        self.consolidation.bind_execution(steps, assembler.counter)
        from .recovery import ConsolidationRecovery
        self.recovery = ConsolidationRecovery(memory, steps)
        memory.pipeline_recovery=self.recovery
        from .index_jobs import IndexJobs
        self.index_jobs = IndexJobs(self)

    async def consolidate(self, partition_id):
        return await self.consolidation.run(partition_id)

    async def extract(self, episode_id, sequence):
        # Only a compatibility queue handler for already-enqueued keys. It never generates
        # another single-Episode request or replays an original input.
        return await self.recovery.convert_episode(episode_id, sequence)

    async def observe_legacy(self, batch_id):
        async with self.database.transaction(read_only=True) as conn:
            batch = await (await conn.execute(
                "SELECT * FROM memory.consolidation_batches WHERE batch_id=%s AND schema_version=%s AND status='waiting_model'",
                (batch_id,"memory.legacy-extraction.v0"))).fetchone()
        if batch:
            return await self.recovery.observe_legacy(batch)

    async def index(self, claim_id, revision, generation=None):
        """Finite adapter for already-queued pre-cutover Claim jobs."""
        return await self.index_object("claim", claim_id, revision, generation=generation)

    async def index_object(self, kind, id, revision, generation=None):
        return await self.index_jobs.index_object(kind, id, revision, generation=generation)

    async def index_retry(self, index_id, completed_attempt_id=None, generation=None):
        return await self.index_jobs.run(index_id, completed_attempt_id=completed_attempt_id, generation=generation)

    async def recover(self):
        await self.recovery.recover()
        await self.index_jobs.recover()
        await self.consolidation.claim_due_recoveries()
        if self.memory.statistics is not None:
            await self.memory.backfill_statistics()
        async with self.database.transaction() as conn:
            rows = await (await conn.execute(
                "SELECT partition_id FROM memory.consolidation_state WHERE "
                "(active_batch_id IS NOT NULL OR wake_due_at<=now() OR EXISTS(SELECT 1 "
                "FROM memory.consolidation_batches b WHERE b.partition_id=memory.consolidation_state.partition_id "
                "AND b.status='quarantined')) AND "
                "(lease_until IS NULL OR lease_until<now()) ORDER BY updated_at LIMIT 64"
            )).fetchall()
        for row in rows:
            # Recovery and new input share the partition row lock, without one
            # recovery transaction retaining locks on every scanned partition.
            async with self.database.transaction() as conn:
                current = await (await conn.execute(
                    "SELECT partition_id FROM memory.consolidation_state WHERE partition_id=%s "
                    "AND (active_batch_id IS NOT NULL OR wake_due_at<=now() OR EXISTS(SELECT 1 "
                    "FROM memory.consolidation_batches b WHERE b.partition_id=memory.consolidation_state.partition_id "
                    "AND b.status='quarantined')) AND (lease_until IS NULL OR lease_until<now()) "
                    "FOR UPDATE SKIP LOCKED", (row["partition_id"],))).fetchone()
                if current:
                    state=await (await conn.execute("SELECT active_batch_id FROM memory.consolidation_state WHERE partition_id=%s",
                        (row["partition_id"],))).fetchone()
                    if state["active_batch_id"] is not None:
                        await self.consolidation._schedule_on(conn,row["partition_id"],datetime.now(UTC),suffix="recover")
                    else:
                        await self.consolidation.retries.arm_on(conn,row["partition_id"])
        async with self.database.transaction() as conn:
            now = datetime.now(UTC)
            bucket = int(now.timestamp())//30+1
            await self.memory.jobs.enqueue(conn, JobSpec("memory.recover", "memory-recovery:"+str(bucket),
                {}, ServiceClass.DUE, scheduled_at=datetime.fromtimestamp(bucket*30,UTC)))
