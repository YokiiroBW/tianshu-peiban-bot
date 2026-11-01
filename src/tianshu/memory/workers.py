"""Thin queue entry points for the unique consolidation writer and reusable indexes."""

from datetime import UTC, datetime, timedelta

from pgvector import Vector
from pgvector.psycopg import register_vector_async

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import RequestBudget, ServiceClass


class MemoryWorkers:
    def __init__(self, memory, assembler, embeddings, steps):
        self.memory, self.assembler, self.embeddings, self.steps = memory, assembler, embeddings, steps
        self.database, self.identity, self.sources = memory.database, memory.identity, memory.sources
        self.consolidation = memory.consolidation
        self.consolidation.bind_execution(steps, assembler.counter)
        from .recovery import ConsolidationRecovery
        self.recovery = ConsolidationRecovery(memory, steps)
        memory.pipeline_recovery=self.recovery

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

    async def index(self, claim_id, revision):
        """Finite adapter for already-queued pre-cutover Claim jobs."""
        return await self.index_object("claim", claim_id, revision)

    async def index_object(self, kind, id, revision):
        from . import codec
        from .domain_utils import lexical
        from .evidence import build_evidence_unit
        from .indexing import index_text
        if kind in {"claim", "hypothesis"}:
            query = ("SELECT r.*,c.actor_id,c.principal_id FROM memory.claims c JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.claim_id=%s AND r.revision=%s "
                "AND r.state='active' AND r.current_status='current'")
            table, key = "claim_revisions", "claim_id"
        else:
            definitions = {"episode":("episodes","episode_id"),"matter":("matters","matter_id"),
                           "reflection":("reflections","proposal_id")}
            if kind not in definitions:
                raise AppError("memory_index_kind_invalid")
            table,key = definitions[kind]
            query = f"SELECT * FROM memory.{table} WHERE {key}=%s AND revision=%s AND active"
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute(query,(id,revision))).fetchone()
            if not row:
                return
            context = await self.identity.context_on(conn,row["principal_id"],actor_id=row["actor_id"])
            selected = ((await self.memory.claims.selected_groups(conn,context,(row,)))[0]
                if kind in {"claim","hypothesis","matter"} else codec.refs(row["source_refs"]))
            if not selected or not all(await self.sources.check_many_on(conn,context,(selected,))):
                return
            value = (codec.claim(row,selected) if kind in {"claim","hypothesis"} else
                codec.matter(row,selected_refs=selected) if kind == "matter" else getattr(codec,kind)(row))
            unit = build_evidence_unit(value)
            selected = unit.source_refs
            if not all(await self.sources.check_many_on(conn,context,(selected,))):
                return
            content = index_text(unit)
        state_clause = "state='active' AND current_status='current'" if kind in {"claim","hypothesis"} else "active"
        if kind in {"claim","hypothesis"}:
            state_clause += (" AND EXISTS(SELECT 1 FROM memory.claims head WHERE head.claim_id=memory.claim_revisions.claim_id "
                             "AND head.current_revision=memory.claim_revisions.revision)")
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn,context,selected):
                return
            written = await (await conn.execute(f"UPDATE memory.{table} SET lexical=%s WHERE {key}=%s AND revision=%s AND {state_clause} RETURNING revision",
                (lexical(content),id,revision))).fetchone()
            if not written:
                return
        try:
            vector,fingerprint = await self.embeddings.vector(context,content,
                RequestBudget(datetime.now(UTC)+timedelta(seconds=120)),
                intent=f"memory-index:{kind}:{id}:{revision}",source_refs=selected)
        except AppError:
            return
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn,context,selected):
                return
            await register_vector_async(conn)
            await conn.execute(f"UPDATE memory.{table} SET embedding=%s,embedding_profile=%s WHERE {key}=%s AND revision=%s AND {state_clause}",
                (Vector(vector),fingerprint,id,revision))

    async def recover(self):
        await self.recovery.recover()
        async with self.database.transaction() as conn:
            # Finite old rows use the same canonical index writer. Lexical text is
            # committed before an optional embedding, so provider failure cannot
            # make an old authorized object permanently invisible.
            for kind,table,key in (("episode","episodes","episode_id"),("matter","matters","matter_id"),
                    ("reflection","reflections","proposal_id")):
                old=await (await conn.execute(f"SELECT item.{key} AS id,item.revision FROM memory.{table} item "
                    f"WHERE item.active AND item.lexical='' AND NOT EXISTS(SELECT 1 FROM runtime.job_keys jobs "
                    f"WHERE jobs.key='memory-legacy-index:'||%s||':'||item.{key}||':'||item.revision::text) "
                    f"ORDER BY item.{key} LIMIT 24",(kind,))).fetchall()
                for row in old:
                    await self.memory.jobs.enqueue(conn,JobSpec("memory.index_object",
                        f"memory-legacy-index:{kind}:{row['id']}:{row['revision']}",
                        {"kind":kind,"id":row["id"],"revision":row["revision"]},ServiceClass.ELASTIC))
            rows = await (await conn.execute(
                "SELECT partition_id FROM memory.consolidation_state WHERE "
                "(active_batch_id IS NOT NULL OR wake_due_at<=now()) AND "
                "(lease_until IS NULL OR lease_until<now()) ORDER BY updated_at LIMIT 64"
            )).fetchall()
            now = datetime.now(UTC)
            for row in rows:
                await self.consolidation._schedule_on(conn,row["partition_id"],now,suffix="recover")
            bucket = int(now.timestamp())//30+1
            await self.memory.jobs.enqueue(conn, JobSpec("memory.recover", "memory-recovery:"+str(bucket),
                {}, ServiceClass.DUE, scheduled_at=datetime.fromtimestamp(bucket*30,UTC)))
