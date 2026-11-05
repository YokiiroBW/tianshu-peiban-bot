"""Thin queue entry points for the unique consolidation writer and reusable indexes."""

from datetime import UTC, datetime

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from tianshu.types import ServiceClass


class MemoryWorkers:
    def __init__(self, memory, assembler, embeddings, steps):
        self.memory, self.assembler, self.embeddings, self.steps = memory, assembler, embeddings, steps
        self.database, self.identity, self.sources = memory.database, memory.identity, memory.sources
        self.consolidation = memory.consolidation
        self.consolidation.bind_execution(steps, assembler.counter)
        from .recovery import ConsolidationRecovery
        self.recovery = ConsolidationRecovery(memory)
        memory.pipeline_recovery=self.recovery
        from .index_jobs import IndexJobs
        self.index_jobs = IndexJobs(self)

    async def consolidate(self, partition_id, batch_id=None):
        return await self.consolidation.run(partition_id,target_batch_id=batch_id)

    async def index_object(self, kind, id, revision, generation=None):
        return await self.index_jobs.index_object(kind, id, revision, generation=generation)

    async def index_retry(self, index_id, completed_attempt_id=None, generation=None):
        return await self.index_jobs.run(index_id, completed_attempt_id=completed_attempt_id, generation=generation)

    async def recover(self, generation=None):
        executing = current_execution_generation()
        if executing and generation is not None and generation != executing:
            raise AppError("job_generation_expired",status=409)
        generation = executing or generation
        if generation is None:
            # A directly invoked fresh maintenance scan starts in the current
            # installation; a queued wake always carries its original one.
            async with self.database.transaction(read_only=True) as conn:
                generation = await self.memory.jobs.current_generation_on(conn)
        await self.index_jobs.recover(generation)
        await self.consolidation.claim_due_recoveries(generation=generation)
        if self.memory.statistics is not None:
            await self.memory.backfill_statistics(generation=generation)
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn,generation)
            rows = await (await conn.execute(
                "SELECT partition_id FROM memory.consolidation_state WHERE "
                "((active_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM memory.consolidation_batches a "
                "WHERE a.batch_id=memory.consolidation_state.active_batch_id AND a.manifest ? 'remote_wait_unresolved')) "
                "OR wake_due_at<=now() OR EXISTS(SELECT 1 FROM memory.consolidation_batches b "
                "WHERE b.partition_id=memory.consolidation_state.partition_id AND b.status='quarantined' "
                "AND NOT (b.manifest ? 'remote_wait_unresolved' OR b.manifest ? 'original_observation_only'))) AND "
                "(lease_until IS NULL OR lease_until<now()) ORDER BY updated_at LIMIT 64"
            )).fetchall()
        for row in rows:
            # Recovery and new input share the partition row lock, without one
            # recovery transaction retaining locks on every scanned partition.
            async with self.database.transaction() as conn:
                await self.memory.jobs.assert_generation_on(conn,generation)
                current = await (await conn.execute(
                    "SELECT partition_id FROM memory.consolidation_state WHERE partition_id=%s "
                    "AND ((active_batch_id IS NOT NULL AND NOT EXISTS (SELECT 1 FROM memory.consolidation_batches a "
                    "WHERE a.batch_id=memory.consolidation_state.active_batch_id AND a.manifest ? 'remote_wait_unresolved')) "
                    "OR wake_due_at<=now() OR EXISTS(SELECT 1 FROM memory.consolidation_batches b "
                    "WHERE b.partition_id=memory.consolidation_state.partition_id AND b.status='quarantined' "
                    "AND NOT (b.manifest ? 'remote_wait_unresolved' OR b.manifest ? 'original_observation_only'))) AND (lease_until IS NULL OR lease_until<now()) "
                    "FOR UPDATE SKIP LOCKED", (row["partition_id"],))).fetchone()
                if current:
                    state=await (await conn.execute("SELECT active_batch_id,EXISTS(SELECT 1 FROM memory.consolidation_batches b "
                        "WHERE b.batch_id=memory.consolidation_state.active_batch_id "
                        "AND b.manifest ? 'remote_wait_unresolved') AS active_unresolved "
                        "FROM memory.consolidation_state WHERE partition_id=%s",(row["partition_id"],))).fetchone()
                    if state["active_batch_id"] is not None and not state["active_unresolved"]:
                        await self.consolidation._schedule_on(conn,row["partition_id"],datetime.now(UTC),suffix="recover")
                    elif state["active_batch_id"] is None:
                        await self.consolidation.retries.arm_on(conn,row["partition_id"])
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn,generation)
            now = datetime.now(UTC)
            bucket = int(now.timestamp())//30+1
            await self.memory.jobs.enqueue(conn, JobSpec("memory.recover", f"memory-recovery:{generation}:{bucket}",
                {"generation":generation}, ServiceClass.DUE, scheduled_at=datetime.fromtimestamp(bucket*30,UTC),
                data_generation=generation))
