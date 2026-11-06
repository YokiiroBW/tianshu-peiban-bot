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
        if batch_id is not None:
            async with self.database.transaction(read_only=True) as conn:
                batch = await (await conn.execute("SELECT status,error_code FROM memory.consolidation_batches "
                    "WHERE batch_id=%s AND partition_id=%s", (batch_id, partition_id))).fetchone()
            if batch and batch["status"] == "failed" and batch["error_code"] == "recovery_original_not_failed":
                result = await self.recovery.resume_local_failure(batch_id, repair_ref="rbi-local-recovery:"+batch_id)
                if result["reason"] not in {"reopened", "already_reopened"}:
                    return result
        return await self.consolidation.run(partition_id,target_batch_id=batch_id)

    async def index_object(self, kind, id, revision, generation=None):
        return await self.index_jobs.index_object(kind, id, revision, generation=generation)

    async def index_retry(self, index_id, completed_attempt_id=None, generation=None):
        return await self.index_jobs.run(index_id, completed_attempt_id=completed_attempt_id, generation=generation)

    async def reconcile_due(self, *, limit=32):
        """Scan one bounded partition page; complete means this traversal reached its end."""
        generation = current_execution_generation()
        if generation is None:
            async with self.database.transaction(read_only=True) as conn:
                generation = await self.memory.jobs.current_generation_on(conn)
        return await self._reconcile_due(generation=generation, limit=limit)

    async def _reconcile_due(self, *, generation, limit):
        from .rbi_maintenance import reconcile_due
        return await reconcile_due(self, generation=generation, limit=limit)

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
        await self._reconcile_due(generation=generation, limit=64)
        async with self.database.transaction() as conn:
            await self.memory.jobs.assert_generation_on(conn,generation)
            now = datetime.now(UTC)
            bucket = int(now.timestamp())//30+1
            await self.memory.jobs.enqueue(conn, JobSpec("memory.recover", f"memory-recovery:{generation}:{bucket}",
                {"generation":generation}, ServiceClass.DUE, scheduled_at=datetime.fromtimestamp(bucket*30,UTC),
                data_generation=generation))
