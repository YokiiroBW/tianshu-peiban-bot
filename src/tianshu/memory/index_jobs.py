"""Durable, source-checked vector computations on the existing Memory index lane."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid4, uuid5

from pgvector import Vector
from pgvector.psycopg import register_vector_async
from psycopg.types.json import Jsonb

from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.types import RequestBudget, ServiceClass
from . import codec
from .domain_utils import lexical
from .evidence import build_evidence_unit
from .indexing import index_text


TABLES = {"claim": ("claim_revisions", "claim_id"), "hypothesis": ("claim_revisions", "claim_id"),
          "episode": ("episodes", "episode_id"), "matter": ("matters", "matter_id"),
          "reflection": ("reflections", "proposal_id")}
ACTIVE = ("pending", "running", "observing", "retry_wait")
LEASE_SECONDS = 90
OBSERVE_SECONDS = 30
SCAN_LIMIT = 24


class IndexJobs:
    def __init__(self, workers):
        self.workers = workers
        self.memory, self.database = workers.memory, workers.database

    async def _load_on(self, conn, kind, object_id, revision, *, lock=False):
        if kind not in TABLES:
            raise AppError("memory_index_kind_invalid")
        table, key = TABLES[kind]
        if kind in {"claim", "hypothesis"}:
            query = ("SELECT r.*,c.actor_id,c.principal_id FROM memory.claims c JOIN memory.claim_revisions r "
                "ON r.claim_id=c.claim_id AND r.revision=c.current_revision WHERE c.claim_id=%s AND r.revision=%s "
                "AND r.state='active' AND r.current_status='current'")
            if lock:
                query += " FOR UPDATE OF c,r"
        else:
            query = f"SELECT * FROM memory.{table} WHERE {key}=%s AND revision=%s AND active"
            if lock:
                query += " FOR UPDATE"
        row = await (await conn.execute(query, (object_id, revision))).fetchone()
        if not row:
            return None
        context = await self.memory.identity.context_on(conn, row["principal_id"], actor_id=row["actor_id"])
        selected = ((await self.memory.claims.selected_groups(conn, context, (row,)))[0]
            if kind in {"claim", "hypothesis", "matter"} else codec.refs(row["source_refs"]))
        if not selected or not all(await self.memory.sources.check_many_on(conn, context, (selected,))):
            raise AppError("source_unavailable", status=403)
        value = (codec.claim(row, selected) if kind in {"claim", "hypothesis"} else
            codec.matter(row, selected_refs=selected) if kind == "matter" else getattr(codec, kind)(row))
        unit = build_evidence_unit(value)
        if not all(await self.memory.sources.check_many_on(conn, context, (unit.source_refs,))):
            raise AppError("source_unavailable", status=403)
        return row, context, unit.source_refs, index_text(unit)

    async def _ensure_on(self, conn, kind, object_id, revision, profile, fingerprint, *, error=None):
        index_id = "memory_index_" + body_hash((kind, object_id, revision, fingerprint))[:48]
        await conn.execute("INSERT INTO memory.index_jobs(index_id,kind,object_id,revision,profile_key,profile,state,next_due,last_error) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,CASE WHEN %s THEN NULL ELSE now() END,%s) ON CONFLICT DO NOTHING",
            (index_id, kind, object_id, revision, fingerprint, Jsonb(profile) if profile else None,
             "failed" if error else "pending", bool(error), error))
        return await (await conn.execute("SELECT *,lease_until>now() AS leased FROM memory.index_jobs "
            "WHERE index_id=%s FOR UPDATE", (index_id,))).fetchone()

    async def _schedule_on(self, conn, job):
        if job["state"] not in ACTIVE or job["next_due"] is None:
            return
        await self.memory.jobs.enqueue(conn, JobSpec("memory.index_retry", "memory-index-wake:" + str(uuid4()),
            {"index_id": job["index_id"]}, ServiceClass.ELASTIC, scheduled_at=job["next_due"]),
            queueing_lock="memory-index:" + job["index_id"])

    async def _material(self, kind, object_id, revision):
        # Keyword visibility never depends on a supplier succeeding. Both queue
        # deliveries and consistency recovery use this same canonical document.
        async with self.database.transaction() as conn:
            loaded = await self._load_on(conn, kind, object_id, revision)
            if loaded is None:
                return None
            _, context, refs, content = loaded
            if not await self.memory.sources.validate_persistence(conn, context, refs):
                raise AppError("source_unavailable", status=403)
            fresh = await self._load_on(conn, kind, object_id, revision, lock=True)
            if fresh is None or fresh[2:] != loaded[2:]:
                return None
            table, key = TABLES[kind]
            await conn.execute(f"UPDATE memory.{table} SET lexical=%s WHERE {key}=%s AND revision=%s",
                               (lexical(content), object_id, revision))
            return fresh

    async def index_object(self, kind, object_id, revision):
        try:
            loaded = await self._material(kind, object_id, revision)
            if loaded is None:
                return
        except AppError as error:
            if error.status in {403, 404}:
                return
            raise
        if kind in {"claim", "hypothesis"}:
            kind = "hypothesis" if loaded[0]["memory_kind"] == "hypothesis" else "claim"
        try:
            profile, fingerprint = await self.workers.embeddings.profile()
        except AppError as error:
            async with self.database.transaction() as conn:
                await self._ensure_on(conn, kind, object_id, revision, None, "unavailable", error=error.code)
            return
        async with self.database.transaction() as conn:
            job = await self._ensure_on(conn, kind, object_id, revision, profile, fingerprint)
        return await self.run(job["index_id"])

    async def _attempt_on(self, conn, job, number):
        intent = f"memory-index:{job['kind']}:{job['object_id']}:{job['revision']}"
        if number > 1:
            intent += f":retry:{number}"
        step_id = intent + ":" + job["profile_key"]
        attempt_id = str(uuid5(NAMESPACE_URL, step_id))
        if number == 1:
            # Claims and hypotheses share a revision ledger. Earlier automatic
            # jobs used the claim queue label for both; explicit rebuilds used
            # the semantic kind. Adopt the recorded identity, never replay it.
            kinds = ("claim", "hypothesis") if job["kind"] in {"claim", "hypothesis"} else (job["kind"],)
            old = await (await conn.execute("SELECT step_id,request->>'intent_id' AS intent_id,"
                "request->>'attempt_id' AS attempt_id FROM memory.model_steps WHERE step_id=ANY(%s) "
                "ORDER BY step_id LIMIT 1", ([f"memory-index:{kind}:{job['object_id']}:{job['revision']}:{job['profile_key']}"
                    for kind in kinds],))).fetchone()
            if old:
                step_id = old["step_id"]
                intent = old["intent_id"] or step_id.rsplit(":", 1)[0]
                attempt_id = old["attempt_id"] or str(uuid5(NAMESPACE_URL, step_id))
        await conn.execute("INSERT INTO memory.index_attempts(index_id,number,intent_id,step_id,model_attempt_id) "
            "VALUES(%s,%s,%s,%s,%s)", (job["index_id"], number, intent, step_id, attempt_id))
        await conn.execute("UPDATE memory.index_jobs SET attempt_count=%s WHERE index_id=%s", (number, job["index_id"]))
        return {"number": number, "intent_id": intent, "step_id": step_id, "model_attempt_id": attempt_id}

    async def run(self, index_id):
        owner = str(uuid4())
        async with self.database.transaction() as conn:
            job = await (await conn.execute("SELECT *,lease_until>now() AS leased,next_due>now() AS deferred "
                "FROM memory.index_jobs WHERE index_id=%s FOR UPDATE", (index_id,))).fetchone()
            if not job or job["state"] not in ACTIVE:
                return
            if job["leased"]:
                return
            if job["deferred"]:
                await self._schedule_on(conn, job)
                return
            await conn.execute("UPDATE memory.index_jobs SET state='running',lease_owner=%s,"
                "lease_until=now()+%s*interval '1 second',updated_at=now() WHERE index_id=%s", (owner, LEASE_SECONDS, index_id))
            attempt = await (await conn.execute("SELECT * FROM memory.index_attempts WHERE index_id=%s AND number=%s",
                (index_id, job["attempt_count"]))).fetchone()
        lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._keep_lease(index_id, owner, asyncio.current_task(), lost))
        try:
            await self._execute(job, attempt, owner, allow_retry=job["state"] == "retry_wait")
        except asyncio.CancelledError:
            if not lost.is_set():
                await self._finish(index_id, owner, "observing", "index_worker_interrupted", delay=OBSERVE_SECONDS)
                raise
            worker = asyncio.current_task()
            worker.uncancel()
            if worker.cancelling():
                raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            async with self.database.transaction() as conn:
                released = await (await conn.execute("UPDATE memory.index_jobs SET lease_owner=NULL,lease_until=NULL "
                    "WHERE index_id=%s AND lease_owner=%s RETURNING *", (index_id, owner))).fetchone()
                if released:
                    await self._schedule_on(conn, released)

    async def _keep_lease(self, index_id, owner, worker, lost):
        try:
            while True:
                await asyncio.sleep(LEASE_SECONDS / 3)
                async with self.database.transaction() as conn:
                    renewed = await (await conn.execute("UPDATE memory.index_jobs SET lease_until=now()+%s*interval '1 second' "
                        "WHERE index_id=%s AND lease_owner=%s AND lease_until>now() RETURNING index_id",
                        (LEASE_SECONDS, index_id, owner))).fetchone()
                if not renewed:
                    break
        except asyncio.CancelledError:
            return
        except Exception:
            pass
        lost.set()
        worker.cancel()

    async def _finish(self, index_id, owner, state, error=None, *, delay=None):
        async with self.database.transaction() as conn:
            await conn.execute("UPDATE memory.index_jobs SET state=%s,last_error=%s,"
                "next_due=now()+%s::double precision*interval '1 second',updated_at=now() "
                "WHERE index_id=%s AND lease_owner=%s AND lease_until>now()", (state, error, delay, index_id, owner))

    async def _execute(self, job, attempt, owner, *, allow_retry):
        try:
            loaded = await self._material(job["kind"], job["object_id"], job["revision"])
            if loaded is None:
                return await self._finish(job["index_id"], owner, "obsolete", "memory_index_revision_obsolete")
            row, context, refs, content = loaded
            current_profile = await self.workers.embeddings.profile()
            if current_profile[1] != job["profile_key"]:
                return await self._finish(job["index_id"], owner, "obsolete", "memory_index_profile_changed")
            if row["embedding"] is not None and row["embedding_profile"] == job["profile_key"]:
                return await self._finish(job["index_id"], owner, "succeeded")
            if attempt is None:
                async with self.database.transaction() as conn:
                    owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                        "AND lease_owner=%s AND lease_until>now() AND attempt_count=0 FOR UPDATE",
                        (job["index_id"], owner))).fetchone()
                    if not owned:
                        return
                    attempt = await self._attempt_on(conn, job, 1)
            values, fingerprint = await self.workers.embeddings.vector(context, content,
                RequestBudget(datetime.now(UTC) + timedelta(seconds=120)), intent=attempt["intent_id"],
                source_refs=refs, profile_snapshot=(job["profile"], job["profile_key"]))
        except Exception as error:
            return await self._failure(job, attempt, owner, error, allow_retry=allow_retry)
        # Control reads precede the write transaction. The fingerprint checked
        # here is the same configured profile used to select this logical job.
        try:
            if (await self.workers.embeddings.profile())[1] != fingerprint:
                return await self._finish(job["index_id"], owner, "obsolete", "memory_index_profile_changed")
            async with self.database.transaction() as conn:
                if not await self.memory.sources.validate_persistence(conn, context, refs):
                    return await self._finish_on(conn, job, owner, "revoked", "source_unavailable")
                fresh = await self._load_on(conn, job["kind"], job["object_id"], job["revision"], lock=True)
                if fresh is None or fresh[3] != content or fresh[2] != refs:
                    return await self._finish_on(conn, job, owner, "obsolete", "memory_index_revision_obsolete")
                owned = await (await conn.execute("SELECT index_id FROM memory.index_jobs WHERE index_id=%s "
                    "AND lease_owner=%s AND lease_until>now() AND state='running' AND attempt_count=%s FOR UPDATE",
                    (job["index_id"], owner, attempt["number"]))).fetchone()
                if not owned:
                    return
                table, key = TABLES[job["kind"]]
                await register_vector_async(conn)
                await conn.execute(f"UPDATE memory.{table} SET lexical=%s,embedding=%s,embedding_profile=%s "
                    f"WHERE {key}=%s AND revision=%s AND (embedding IS NULL OR embedding_profile IS DISTINCT FROM %s)",
                    (lexical(content), Vector(values), fingerprint, job["object_id"], job["revision"], fingerprint))
                await conn.execute("UPDATE memory.index_attempts SET status='succeeded',error_code=NULL,updated_at=now() "
                    "WHERE index_id=%s AND number=%s", (job["index_id"], attempt["number"]))
                await self._finish_on(conn, job, owner, "succeeded")
        except AppError as error:
            await self._failure(job, attempt, owner, error, allow_retry=False)

    @staticmethod
    async def _finish_on(conn, job, owner, state, error=None):
        await conn.execute("UPDATE memory.index_jobs SET state=%s,last_error=%s,next_due=NULL,updated_at=now() "
            "WHERE index_id=%s AND lease_owner=%s AND lease_until>now()", (state, error, job["index_id"], owner))

    async def _failure(self, job, attempt, owner, error, *, allow_retry):
        code = getattr(error, "code", type(error).__name__)
        if getattr(error, "status", None) in {403, 404} or code in {"context_changed", "access_changed"}:
            return await self._finish(job["index_id"], owner, "revoked", code)
        if attempt is None:
            retry = transient_model_failure(code)
            return await self._finish(job["index_id"], owner, "retry_wait" if retry else "failed", code,
                                      delay=retry_delay_seconds(1) if retry else None)
        details = getattr(error, "details", None) or {}
        status = details.get("status")
        explicit_cancel = False
        if transient_model_failure(code):
            try:
                receipt = await self.workers.embeddings.models.status(attempt["model_attempt_id"])
            except Exception:
                # A control read failure is not proof the original stopped.
                return await self._finish(job["index_id"], owner, "observing", code, delay=OBSERVE_SECONDS)
            status = receipt.status if receipt else status or "unknown"
            code = (receipt.error_code if receipt else None) or code
            explicit_cancel = bool(receipt and getattr(receipt, "cancellation_reason", None) == "explicit_cancel")
        status = status or "failed"
        async with self.database.transaction() as conn:
            owned = await (await conn.execute("SELECT * FROM memory.index_jobs WHERE index_id=%s AND lease_owner=%s "
                "AND lease_until>now() AND attempt_count=%s FOR UPDATE",
                (job["index_id"], owner, attempt["number"]))).fetchone()
            if not owned:
                return
            await conn.execute("UPDATE memory.index_attempts SET status=%s,error_code=%s,updated_at=now() "
                "WHERE index_id=%s AND number=%s", (status, code, job["index_id"], attempt["number"]))
            successor = None
            if explicit_cancel:
                state, delay = "cancelled", None
            elif status in {"queued", "running"}:
                state, delay = "observing", OBSERVE_SECONDS
            elif transient_model_failure(code, cancelled=status == "cancelled" and code in {None, "cancelled", "model_cancelled"}):
                if allow_retry:
                    successor = await self._attempt_on(conn, job, attempt["number"] + 1)
                else:
                    successor = None
                state, delay = "retry_wait", retry_delay_seconds(attempt["number"])
            else:
                state, delay = "cancelled" if status == "cancelled" else "failed", None
                successor = None
            if successor is None:
                await conn.execute("UPDATE memory.index_jobs SET state=%s,next_due=now()+%s::double precision*interval '1 second',"
                    "last_error=%s,updated_at=now() WHERE index_id=%s AND lease_owner=%s",
                    (state, delay, "explicit_cancel" if explicit_cancel else code, job["index_id"], owner))
                return
        # Selection is committed before dispatch; a late original cannot win a
        # write after this new attempt has been selected.
        await self._execute(job, successor, owner, allow_retry=False)

    async def recover(self):
        try:
            profile, fingerprint = await self.workers.embeddings.profile()
        except AppError:
            # Still repair keyword documents once. No embedding is dispatched
            # until a real configured profile exists; permanent errors settle
            # this placeholder, and a newly configured profile has a new key.
            profile, fingerprint = None, "unavailable"
        async with self.database.transaction() as conn:
            for kind in ("claim", "episode", "matter", "reflection"):
                cursor = await (await conn.execute("SELECT * FROM memory.index_scan WHERE kind=%s FOR UPDATE SKIP LOCKED", (kind,))).fetchone()
                if cursor is None:
                    continue
                table, key = TABLES[kind]
                if kind == "claim":
                    query = ("SELECT r.claim_id AS id,r.revision,CASE WHEN r.memory_kind='hypothesis' THEN 'hypothesis' ELSE 'claim' END AS kind "
                        ",c.current_revision=r.revision AS eligible "
                        "FROM memory.claim_revisions r JOIN memory.claims c ON c.claim_id=r.claim_id "
                        "WHERE r.state='active' AND r.current_status='current' AND (r.embedding IS NULL OR r.lexical='') "
                        "AND (r.claim_id,r.revision)>(%s,%s) ORDER BY r.claim_id,r.revision LIMIT %s")
                else:
                    query = (f"SELECT {key} AS id,revision FROM memory.{table} WHERE active AND (embedding IS NULL OR lexical='') "
                        f"AND ({key},revision)>(%s,%s) ORDER BY {key},revision LIMIT %s")
                rows = await (await conn.execute(query, (cursor["after_id"], cursor["after_revision"], SCAN_LIMIT))).fetchall()
                for row in rows:
                    if not row.get("eligible", True):
                        continue
                    job = await self._ensure_on(conn, row.get("kind", kind), row["id"], row["revision"], profile, fingerprint)
                    if not job["leased"]:
                        await self._schedule_on(conn, job)
                last = rows[-1] if len(rows) == SCAN_LIMIT else {"id": "", "revision": 0}
                await conn.execute("UPDATE memory.index_scan SET after_id=%s,after_revision=%s WHERE kind=%s",
                    (last["id"], last["revision"], kind))
            due = await (await conn.execute("SELECT * FROM memory.index_jobs "
                "WHERE state IN ('pending','running','observing','retry_wait') AND next_due<=now() "
                "AND (lease_until IS NULL OR lease_until<now()) ORDER BY next_due,index_id LIMIT 64 FOR UPDATE SKIP LOCKED")).fetchall()
            for job in due:
                await self._schedule_on(conn, job)
