"""One conversation-domain writer: freeze once, observe once, apply all targets atomically."""

from copy import deepcopy
import asyncio
from datetime import UTC, datetime, timedelta
import json
from uuid import NAMESPACE_URL, uuid4, uuid5

from psycopg.types.json import Jsonb

from tianshu.models.types import ModelMessage, ModelPart, ModelRequest
from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import OriginKind, RequestBudget, ServiceClass, SourceRef, TrustedContext
from . import codec
from .authority import context_record
from .extraction_contract import SCHEMA_VERSION, consolidation_example, consolidation_instruction, validate_proposal
from .generation import MODEL_REQUEST_SECONDS, REPAIR_LIMIT, native_request, repairable, repair_request, repair_input_estimate
from .materials import ConsolidationPolicy, MaterialBuilder, PreparationReads, append_context_material, batch_frozen, expand_record_endpoints, frozen_mapping, model_material, partition_identity, reference_key, smaller_material_prefix

_ACTIVE = {"prepared", "waiting_model", "ready"}
_OBSERVABLE = _ACTIVE | {"quarantined"}
LEASE_SECONDS = MODEL_REQUEST_SECONDS + 60
LEASE_RENEW_SECONDS = 60
COMMIT_RESERVE_SECONDS = 5


def context_from_bound(value):
    return TrustedContext(value["principal_id"], value.get("actor_id"), value.get("person_id"),
        value.get("conversation_id"), tuple(value["scope_ids"]), tuple(value["audience"]),
        value["access_epoch"], OriginKind(value["origin_kind"]), frozenset(value.get("permissions", ())),
        value.get("conversation_kind"), bool(value.get("delivery_allowed", False)))


class Consolidation:
    def __init__(self, memory, settings=None):
        self.memory = memory
        self.database, self.identity, self.sources, self.jobs = memory.database, memory.identity, memory.sources, memory.jobs
        self.policy = ConsolidationPolicy.from_settings(settings)
        self.materials = MaterialBuilder(memory, self.policy)
        self.steps = self.counter = None

    def bind_execution(self, steps, counter):
        self.steps, self.counter = steps, counter

    async def _schedule_on(self, conn, partition_id, due, *, suffix=""):
        await self.jobs.enqueue(conn, JobSpec("memory.consolidate",
            f"memory-consolidate:{partition_id}:{due.isoformat()}:{suffix}",
            {"partition_id": partition_id}, ServiceClass.ELASTIC, scheduled_at=due))

    async def lock_partition_on(self,conn,context,scope_ids):
        partition_id, scope_key, bound = partition_identity(context, scope_ids)
        await conn.execute(
            "INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,conversation_id,scope_key,authority_bound) "
            "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (partition_id, context.actor_id, context.principal_id, context.conversation_id, scope_key, Jsonb(bound)))
        state = await (await conn.execute(
            "SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE", (partition_id,))).fetchone()
        return state

    async def dirty_on(self, conn, context, row):
        state = await self.lock_partition_on(conn,context,row["scope_ids"])
        partition_id=state["partition_id"]
        await conn.execute("UPDATE memory.episodes SET consolidation_partition_id=%s WHERE episode_id=%s AND sequence=%s",
            (partition_id,row["episode_id"],row["sequence"]))
        now = datetime.now(UTC)
        first = state["first_pending_at"] or now
        due = min(now+timedelta(seconds=self.policy.quiet_seconds), first+timedelta(seconds=self.policy.max_wait_seconds))
        count = await (await conn.execute(
            "SELECT count(*) AS total,COALESCE(sum(length(summary)),0) AS characters FROM "
            "(SELECT summary FROM memory.episodes WHERE consolidation_partition_id=%s "
            "AND active AND extraction_state='pending' LIMIT %s) e",
            (partition_id,self.policy.max_episodes))).fetchone()
        if count["total"] >= self.policy.max_episodes or count["characters"] >= self.policy.max_source_characters:
            due = now
        await conn.execute(
            "UPDATE memory.consolidation_state SET dirty_generation=dirty_generation+1,first_pending_at=%s,"
            "last_activity_at=%s,wake_due_at=%s,updated_at=now() WHERE partition_id=%s",
            (first, now, due, partition_id))
        # An earlier wake checks the latest due time and coalesces subsequent input.
        if not state["first_pending_at"] or not state["wake_due_at"] or due < state["wake_due_at"]:
            await self._schedule_on(conn, partition_id, due)
        return partition_id

    async def run(self, partition_id):
        owner = str(uuid4())
        async with self.database.transaction() as conn:
            state = await (await conn.execute(
                "SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE", (partition_id,))).fetchone()
            if not state:
                return
            now = datetime.now(UTC)
            if state["lease_until"] and state["lease_until"] > now:
                return
            held = await (await conn.execute("SELECT batch_id FROM memory.consolidation_batches "
                "WHERE partition_id=%s AND status='quarantined' ORDER BY updated_at,batch_id LIMIT 1",
                (partition_id,))).fetchone()
            if not state["active_batch_id"] and state["wake_due_at"] is None and not held:
                return
            if not held and not state["active_batch_id"] and state["wake_due_at"] and state["wake_due_at"] > now:
                await self._schedule_on(conn, partition_id, state["wake_due_at"])
                return
            lease = await (await conn.execute(
                "UPDATE memory.consolidation_state SET lease_owner=%s,lease_until=now()+%s*interval '1 second',updated_at=now() "
                "WHERE partition_id=%s RETURNING lease_until", (owner, LEASE_SECONDS, partition_id))).fetchone()
        batch_id = state["active_batch_id"]
        lease_lost = asyncio.Event()
        heartbeat = asyncio.create_task(self._keep_lease(partition_id, owner,
            asyncio.current_task(), lease_lost), name="memory-consolidation-lease")
        try:
            context = context_from_bound(state["authority_bound"])
            await self.identity.check_context(context)
            # One bounded original observation per wake. An unavailable original
            # must not prevent this same wake from processing unrelated input.
            if held:
                try:
                    result = await self._execute(held["batch_id"], context, owner=owner)
                    if result is not None:
                        return result
                except (AppError, TimeoutError, ValueError, TypeError) as exc:
                    await self._failure(partition_id, held["batch_id"], exc)
            if not batch_id and state["wake_due_at"] is None:
                return
            if not batch_id and state["wake_due_at"] and state["wake_due_at"] > datetime.now(UTC):
                return
            if not batch_id:
                batch_id = await self._prepare(state, context, owner,
                    budget=self.preparation_budget(lease["lease_until"]))
            if batch_id:
                await self._execute(batch_id, context, owner=owner)
        except asyncio.CancelledError:
            if not lease_lost.is_set():
                raise
            worker = asyncio.current_task()
            worker.uncancel()  # Consume only the lease heartbeat's own cancellation.
            if worker.cancelling():
                raise
            # The next owner now owns all batch/episode mutations. Cancellation
            # still reaches the original ModelClient, preserving its receipt.
        except (AppError, TimeoutError, ValueError, TypeError) as exc:
            await self._failure(partition_id, batch_id, exc)
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            async with self.database.transaction() as conn:
                await conn.execute(
                    "UPDATE memory.consolidation_state SET lease_owner=NULL,lease_until=NULL WHERE partition_id=%s AND lease_owner=%s",
                    (partition_id, owner))

    async def _renew_lease(self, partition_id, owner):
        async with self.database.transaction() as conn:
            row = await (await conn.execute("UPDATE memory.consolidation_state "
                "SET lease_until=now()+%s*interval '1 second' "
                "WHERE partition_id=%s AND lease_owner=%s AND lease_until>now() RETURNING partition_id",
                (LEASE_SECONDS, partition_id, owner))).fetchone()
        return row is not None

    async def _keep_lease(self, partition_id, owner, worker, lost):
        try:
            while True:
                await asyncio.sleep(min(LEASE_RENEW_SECONDS, LEASE_SECONDS / 3))
                if not await self._renew_lease(partition_id, owner):
                    break
        except asyncio.CancelledError:
            return
        except Exception:
            # An unconfirmed lease cannot authorize a late apply.
            pass
        lost.set()
        worker.cancel()

    @staticmethod
    def preparation_budget(lease_until):
        # Retain the preparation allowance at the assembly boundary. Background
        # candidate phases do not treat it as a business/queue expiry, and the
        # owner heartbeat covers both preparation and admitted model execution.
        return RequestBudget(lease_until - timedelta(seconds=MODEL_REQUEST_SECONDS + COMMIT_RESERVE_SECONDS))

    def _request(self, context, frozen, step_id, *, context_round=0, requests=()):
        payload = model_material(frozen, self.policy, context_round=context_round, requests=requests)
        instruction = consolidation_instruction(frozen)
        messages = (ModelMessage("user", (ModelPart("text", json.dumps(wire(payload), ensure_ascii=False, separators=(",", ":"))),)),)
        attempt_id=str(uuid5(NAMESPACE_URL,step_id))
        estimate = self.counter.measure(instruction, messages, ())[0]
        repair_estimate=repair_input_estimate(self.counter,instruction,messages,attempt_id)
        if max(estimate,repair_estimate) > self.policy.max_input_tokens:
            # Measure the same schema, identity mappings and full metadata with
            # text removed. Character count alone cannot budget UTF-8 evidence.
            fixed=deepcopy(payload)
            for material in fixed["materials"]:
                material["text"]=""
            fixed_messages=(ModelMessage("user",(ModelPart("text",json.dumps(wire(fixed),ensure_ascii=False,separators=(",",":"))),)),)
            fixed_tokens=max(self.counter.measure(instruction,fixed_messages,())[0],
                repair_input_estimate(self.counter,instruction,fixed_messages,attempt_id))
            raise AppError("memory_consolidation_input_budget", details={
                "estimated":estimate if estimate>self.policy.max_input_tokens else repair_estimate,
                "request_estimated":estimate,"repair_required_tokens":repair_estimate,
                "input_budget": self.policy.max_input_tokens, "visible_output": self.policy.max_output_tokens,
                "reasoning_reserve":self.policy.reasoning_reserve_tokens,"fixed_tokens":fixed_tokens,
                "new_characters":sum(len(item["text"]) for item in payload["materials"] if item["role"]=="new")})
        return ModelRequest(step_id, attempt_id, "memory.extract", ServiceClass.ELASTIC,
            context, RequestBudget(datetime.now(UTC)+timedelta(seconds=MODEL_REQUEST_SECONDS)), messages,
            execution_timeout_seconds=MODEL_REQUEST_SECONDS,
            system=instruction, max_output_tokens=self.policy.max_output_tokens+self.policy.reasoning_reserve_tokens, estimated_input_tokens=estimate,
            source_refs=self.materials.dependencies(frozen))

    async def _prepare(self, state, context, owner, *, budget):
        # Source scopes, not the current person selection, identify eligible episodes.
        scopes = state["authority_bound"].get("write_scope_ids")
        if scopes is None:
            async with self.database.transaction(read_only=True) as conn:
                scopes_row = await (await conn.execute(
                    "SELECT scope_ids FROM memory.episodes WHERE actor_id=%s AND principal_id=%s "
                    "AND conversation_id IS NOT DISTINCT FROM %s AND active AND extraction_state='pending' "
                    "ORDER BY sequence LIMIT 64", (context.actor_id, context.principal_id, context.conversation_id))).fetchall()
            scopes = next((row["scope_ids"] for row in scopes_row if body_hash(sorted(row["scope_ids"])) == state["scope_key"]), None)
        if scopes is None:
            await self._idle(state["partition_id"], owner)
            return None
        rows = await self.materials.pending(context, state["partition_id"])
        if not rows:
            await self._idle(state["partition_id"], owner)
            return None
        batch_id = "memory_batch_"+uuid4().hex
        text_ceiling = min(self.policy.max_source_characters, self.policy.max_input_tokens//2)
        async with self.database.transaction(read_only=True) as conn:
            previous = await (await conn.execute(
                "SELECT manifest,status,error_code,step_ids FROM memory.consolidation_batches WHERE partition_id=%s "
                "ORDER BY created_at DESC LIMIT 1",(state["partition_id"],))).fetchone()
        reads=PreparationReads(self.sources)
        max_new_materials = None
        if await self._related_truncation(previous, rows, context, reads):
            smaller = smaller_material_prefix(batch_frozen(previous))
            if smaller is None:
                # A pre-upgrade pending wake or an unchanged explicit resume
                # must not repeat a known irreducible failed input.
                await self._stop_minimum_prefix(state, context, owner, previous)
                return None
            max_new_materials = smaller["max_new_materials"]
            text_ceiling = min(text_ceiling, smaller.get("text_ceiling", text_ceiling))
        while True:
            try:
                frozen=await self.materials.build(context,rows,batch_id,text_ceiling=text_ceiling,
                    max_new_materials=max_new_materials,reads=reads,budget=budget)
                if not frozen:
                    await self._idle(state["partition_id"],owner)
                    return None
                step_id=batch_id+":step:0"
                request=self._budgeted_request(context,frozen,step_id) if frozen["materials"] else None
                if request:
                    request=await native_request(self.steps.models,request)
                break
            except AppError as exc:
                if exc.code not in {"memory_consolidation_input_budget","memory_identity_candidates_too_large"}:
                    raise
                if len(rows) > 1:
                    rows = rows[:max(1,len(rows)//2)]
                elif text_ceiling<=1:
                    # A real minimum source prefix, its author and the protocol
                    # still do not fit. No cursor or model attempt has advanced.
                    raise
                else:
                    details=exc.details or {}
                    fixed=details.get("fixed_tokens",0)
                    estimate=details.get("estimated",0)
                    if fixed and fixed<self.policy.max_input_tokens and estimate>fixed:
                        available=self.policy.max_input_tokens-fixed
                        text_ceiling=min(text_ceiling-1,max(1,int(details["new_characters"]*available/(estimate-fixed))))
                    elif fixed>=self.policy.max_input_tokens and len(frozen["materials"])==1:
                        text_ceiling=1
                    else:
                        text_ceiling=max(1,text_ceiling//2)
        dependencies = self.materials.dependencies(frozen)
        manifest = {"frozen": frozen_mapping(frozen), "episodes": frozen["episodes"], "source_budget":text_ceiling,
            "budget":self._budget_record(),
            "authority_bound": context_record(context), "generation": state["dirty_generation"],
            "steps": {step_id: frozen_mapping(frozen)} if request else {}, "context_round": 0}
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn, context, dependencies):
                raise AppError("source_unavailable", status=403)
            current = await (await conn.execute(
                "SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE", (state["partition_id"],))).fetchone()
            if current["lease_owner"] != owner or current["active_batch_id"]:
                return current["active_batch_id"]
            await self.identity.check_context(context, connection=conn)
            for episode in frozen["episodes"]:
                original = await (await conn.execute(
                    "SELECT revision,sequence,extraction_cursor,active FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                    (episode["episode_id"],))).fetchone()
                if not original or not original["active"] or original["sequence"] != episode["sequence"] or original["extraction_cursor"] != episode["before"]:
                    raise AppError("memory_consolidation_episode_changed", status=409)
            if request:
                await self.steps.prepare_on(conn, step_id, request)
            await conn.execute(
                "INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,read_set,dependency_refs,context_fingerprint,step_ids) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (batch_id, state["partition_id"], SCHEMA_VERSION, "prepared" if request else "ready", Jsonb(wire(manifest)),
                 Jsonb(wire(frozen["read_set"])), Jsonb(wire(dependencies)), body_hash(context_record(context)), [step_id] if request else []))
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=%s,last_error=NULL,budget_failure=NULL "
                "WHERE partition_id=%s", (batch_id,state["partition_id"]))
            for episode in frozen["episodes"]:
                await conn.execute(
                    "UPDATE memory.episodes SET extraction_state='running',extraction_started_at=now(),extraction_sources=%s "
                    "WHERE episode_id=%s AND sequence=%s", (Jsonb(episode["source_refs"]), episode["episode_id"],episode["sequence"]))
        return batch_id

    async def _related_truncation(self, previous, rows, context, reads):
        if (not previous or previous["status"] != "failed"
                or previous["error_code"] != "memory_consolidation_output_truncated"
                or previous["manifest"].get("budget") != self._budget_record()):
            return False
        episodes = previous["manifest"]["episodes"]
        if not episodes or len(episodes) > len(rows):
            return False
        for old, current in zip(episodes, rows):
            if (any(old[key] != current[key] for key in ("episode_id", "revision", "sequence"))
                    or old["before"] != wire(current["extraction_cursor"])
                    or sorted(old["scope_ids"]) != sorted(current["scope_ids"])):
                return False
            sources, _ = await reads.source_set(self.materials, context, current)
            if tuple(map(reference_key, codec.refs(old["source_refs"]))) != tuple(map(reference_key, sources)):
                return False
        return True

    async def _stop_minimum_prefix(self, state, context, owner, previous):
        async with self.database.transaction() as conn:
            await self.identity.check_context(context, connection=conn)
            current = await (await conn.execute(
                "SELECT lease_owner,active_batch_id FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                (state["partition_id"],))).fetchone()
            if current["lease_owner"] != owner or current["active_batch_id"]:
                return
            for episode in previous["manifest"]["episodes"]:
                before = Jsonb(episode["before"]) if episode["before"] is not None else None
                await conn.execute(
                    "UPDATE memory.episodes SET extraction_state='consolidation_failed',extraction_started_at=NULL "
                    "WHERE episode_id=%s AND revision=%s AND sequence=%s AND active AND extraction_state='pending' "
                    "AND extraction_cursor IS NOT DISTINCT FROM %s",
                    (episode["episode_id"],episode["revision"],episode["sequence"],before))
            pending = await (await conn.execute(
                "SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s AND active "
                "AND extraction_state='pending' LIMIT 1", (state["partition_id"],))).fetchone()
            due = datetime.now(UTC) if pending else None
            await conn.execute(
                "UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=%s,last_error=%s,budget_failure=NULL "
                "WHERE partition_id=%s", (due,due,"memory_consolidation_output_truncated",state["partition_id"]))
            if pending:
                await self._schedule_on(conn, state["partition_id"], due)

    def _budget_record(self):
        return {"input_tokens":self.policy.max_input_tokens,"output_tokens":self.policy.max_output_tokens,
                "reasoning_tokens":self.policy.reasoning_reserve_tokens,
                "generation_policy":"memory-native-low-json-v1","request_seconds":MODEL_REQUEST_SECONDS,
                "timing_policy":"admitted-execution-v1",
                "repair_input_policy":"bounded-diagnostic-headroom-v1"}

    def _input_budget_record(self):
        # These settings determine the smallest complete input we can prepare.
        # Scheduling changes alone do not make a rejected input fit its budget.
        return {**self._budget_record(),"input_layout":"consolidation-matter-semantics-v2",
            **{name:getattr(self.policy,name) for name in
            ("max_episodes","max_subjects","max_materials","max_old_records",
             "context_messages","max_source_characters","time_zone")}}

    def _budgeted_request(self,context,frozen,step_id):
        """Keep whole old records when possible; explicit stubs can be expanded once."""
        candidates=list(reversed(frozen["records"]))
        while True:
            try:
                return self._request(context,frozen,step_id)
            except AppError as exc:
                if exc.code != "memory_consolidation_input_budget" or not candidates:
                    raise
                key=candidates.pop(0)
                record=frozen["records"][key]
                frozen["records"][key]={name:record[name] for name in
                    ("kind","id","revision","head_revision","current_status","subject_id","source_refs","scope_ids") if name in record}
                frozen["records"][key]["content_omitted"]=True
                frozen.setdefault("selection_coverage",{}).setdefault("record_content_omitted",[]).append(key)

    async def _idle(self, partition_id, owner):
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE memory.consolidation_state SET first_pending_at=NULL,wake_due_at=NULL,last_error=NULL,budget_failure=NULL "
                "WHERE partition_id=%s AND lease_owner=%s AND active_batch_id IS NULL", (partition_id,owner))

    async def _execute(self, batch_id, context, *, owner=None):
        # Both additional paths have durable, independent limits: one original
        # context supplement and one repair of known output, at most three steps.
        while True:
            async with self.database.transaction() as conn:
                batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s", (batch_id,))).fetchone()
                if not batch or batch["status"] not in _OBSERVABLE:
                    return batch["commit_refs"] if batch else None
                if owner:
                    renewed=await (await conn.execute("UPDATE memory.consolidation_state "
                        "SET lease_until=now()+%s*interval '1 second' WHERE partition_id=%s AND lease_owner=%s "
                        "RETURNING partition_id",(LEASE_SECONDS,batch["partition_id"],owner))).fetchone()
                    if not renewed:
                        raise AppError("memory_consolidation_lease_changed",status=409)
                await conn.execute("UPDATE memory.consolidation_batches SET status='waiting_model',updated_at=now() WHERE batch_id=%s AND status='prepared'", (batch_id,))
            manifest = batch["manifest"]
            if not batch["step_ids"]:
                proposal = consolidation_example(manifest["frozen"])
                return await self._apply(batch, context, manifest["frozen"], proposal,owner=owner)
            step_id = batch["step_ids"][-1]
            result, recorded = await self.steps.execute_recorded(step_id,observe_only=batch["status"] == "quarantined")
            frozen = manifest["steps"][step_id]
            try:
                proposal = self._parse(frozen, result)
            except AppError as exc:
                if await self._repair(batch,context,frozen,recorded,result,exc,owner=owner):
                    continue
                raise
            if (proposal["needs_context"] and not manifest["context_round"] and batch["status"] != "quarantined"
                    and await self._supplement(batch,context,frozen,recorded,proposal["needs_context"],owner=owner)):
                continue
            # A quarantined result remains outside the active unique predicate,
            # including while its unchanged frozen proposal is being checked.
            try:
                async with self.database.transaction() as conn:
                    await self._lock_owned_partition(conn,batch["partition_id"],owner)
                    await conn.execute("UPDATE memory.consolidation_batches SET status=CASE WHEN status='quarantined' "
                        "THEN status ELSE 'ready' END,proposal_digest=%s,updated_at=now() "
                        "WHERE batch_id=%s AND status=ANY(%s::text[]) AND (proposal_digest IS NULL OR proposal_digest=%s)",
                        (body_hash(proposal),batch_id,list(_OBSERVABLE),body_hash(proposal)))
                return await self._apply(batch, context, frozen, proposal,owner=owner)
            except AppError as exc:
                if await self._repair(batch,context,frozen,recorded,result,exc,owner=owner):
                    continue
                raise

    async def _repair(self,batch,context,frozen,recorded,result,error,*,owner=None):
        if (batch["status"] == "quarantined" or not repairable(result,recorded,error.code)
                or len(batch["manifest"].get("output_repairs",())) >= REPAIR_LIMIT):
            return False
        step_id=batch["batch_id"]+":repair:1"
        try:
            request=repair_request(recorded,step_id,result,error,self.counter,self.policy.max_input_tokens)
        except AppError as exc:
            if exc.code == "memory_consolidation_repair_input_budget":
                return False
            raise
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn,context,codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable",status=403)
            await self.identity.check_context(context,connection=conn)
            await self._lock_owned_partition(conn,batch["partition_id"],owner)
            current=await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
            if current["status"] not in _ACTIVE or len(current["manifest"].get("output_repairs",())) >= REPAIR_LIMIT:
                return False
            manifest=deepcopy(current["manifest"])
            manifest["steps"][step_id]=frozen_mapping(frozen)
            manifest["output_repairs"]=[{"step_id":step_id,"original_step_id":recorded.intent_id,
                "original_attempt_id":recorded.attempt_id,"error_code":error.code,
                "rejected_proposal_digest":current["proposal_digest"],
                "diagnostic":{key:value for key,value in (error.details or {}).items()
                    if key in {"path","validator","line","column","position"}}}]
            await self.steps.prepare_on(conn,step_id,request)
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,step_ids=array_append(step_ids,%s),"
                "status='prepared',proposal_digest=NULL,error_code=NULL,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest),step_id,batch["batch_id"]))
        return True

    @staticmethod
    def _parse(frozen, result):
        native = result.native_response or {}
        reasons = {native.get("finish_reason"),native.get("stop_reason"),native.get("status")}
        completion=getattr(result,"completion",None)
        if completion:
            reasons.update(completion.stop_reasons)
        if isinstance(native.get("incomplete_details"),dict):
            reasons.add(native["incomplete_details"].get("reason"))
        for choice in native.get("choices",()):
            if isinstance(choice,dict):
                reasons.add(choice.get("finish_reason"))
        normalized={value.casefold() for value in reasons if isinstance(value,str)}
        if normalized & {"length","max_tokens","max_output_tokens"}:
            raise AppError("memory_consolidation_output_truncated", status=502)
        if (completion and completion.state == "incomplete") or "incomplete" in normalized:
            raise AppError("memory_consolidation_output_incomplete",status=502,
                details={"stop_reasons":sorted(normalized)})
        try:
            proposal = json.loads("".join(part.text or "" for part in result.parts if part.kind == "text"))
        except (TypeError, ValueError) as exc:
            raise AppError("memory_consolidation_output_invalid", status=502,details={
                key:getattr(exc,field) for key,field in (("line","lineno"),("column","colno"),("position","pos"))
                if hasattr(exc,field)}) from exc
        return validate_proposal(frozen, proposal)

    async def _supplement(self, batch, context, frozen, recorded, requests, *, owner=None):
        # Reuse the original exact payload. New aliases append; no old alias is renumbered.
        payload = json.loads(recorded.messages[0].parts[0].text)
        expanded = deepcopy(frozen)
        for item in payload["materials"]:
            expanded["materials"][item["id"]]["text"] = item["text"]
        for item in payload["existing_records"]:
            expanded["records"][item["ref"]].update({key:value for key,value in item.items() if key != "ref"})
        added = False
        outcomes = []
        for request in requests:
            kind, ref = request["kind"], request["ref"]
            outcome = {**request, "outcome":"unavailable"}
            if kind == "material":
                material = frozen["materials"][ref]
                original_ref = codec.refs(material["source_refs"])[0]
                parent = SourceRef(**material.get("original_ref",wire(original_ref)))
                start = (original_ref.locator or {}).get("end", 0)
                read = await self.sources.read(context, parent, {"position": start, "max_characters": 2000})
                if read["content"]:
                    self._append_material(expanded, read, role="context")
                    added = True
                    outcome["outcome"] = "provided"
            elif kind == "record":
                record=expanded["records"][ref]
                if record.get("content_omitted"):
                    expanded["records"][ref]=await self.materials.expand_record(context,record)
                    omitted=expanded.get("selection_coverage",{}).get("record_content_omitted",[])
                    if ref in omitted:
                        omitted.remove(ref)
                    added=True
                    outcome["outcome"]="provided"
                else:
                    outcome["outcome"] = "already_provided"
            elif kind == "conversation_range" and self.materials.conversation:
                values = await self.materials.conversation(context, cutoff=frozen["context"]["message_cutoff"],
                    start=request["start"], end=request["end"], limit=self.policy.context_messages or 1)
                for message in values["items"]:
                    for source in codec.refs(message["source_refs"]):
                        read = await self.sources.read(context, source, {"max_characters":1200})
                        if read["content"]:
                            self._append_material(expanded, read, role="context")
                            added = True
                            outcome["outcome"] = "provided"
            elif kind == "identity":
                async with self.database.transaction(read_only=True) as conn:
                    people = await self.identity.resolve_memory_subjects_on(conn,context,(),aliases=(request["query"],),include_group=False)
                for person in people:
                    subject_id = person["subject_id"]
                    if subject_id not in expanded["subjects"]:
                        expanded["subjects"][subject_id] = {"subject_id":subject_id,"name":person["display_name"],
                            "kind":person["kind"],"aliases":list(person["aliases"]),"revision":person["revision"]}
                        expanded["targets"][subject_id] = {"new_material_ids":[]}
                        added = True
                        outcome["outcome"] = "provided"
            outcomes.append(outcome)
        if not added:
            return False
        expanded["records"]=expand_record_endpoints(expanded["records"])
        expanded["read_set"]=[{"kind":record["kind"],"id":record["id"],"revision":record["revision"],
                              "head_revision":record.get("head_revision") or record["revision"]}
            for record in expanded["records"].values()]
        authors={item["speaker_id"] for item in expanded["materials"].values() if item["speaker_id"]} - set(expanded["subjects"])
        if authors:
            async with self.database.transaction(read_only=True) as conn:
                subjects=await self.identity.resolve_memory_subjects_on(conn,context,authors)
            for subject in subjects:
                subject_id=subject["subject_id"]
                expanded["subjects"][subject_id]={"subject_id":subject_id,"name":subject["display_name"],
                    "kind":subject["kind"],"aliases":list(subject["aliases"]),"revision":subject["revision"]}
                expanded["targets"].setdefault(subject_id,{"new_material_ids":[]})
        step_id = batch["batch_id"]+":step:1"
        try:
            request = self._request(context,expanded,step_id,context_round=1,requests=outcomes)
            request = await native_request(self.steps.models,request)
        except AppError as exc:
            if exc.code == "memory_consolidation_input_budget":
                return False
            raise
        dependencies = self.materials.dependencies(expanded)
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn,context,dependencies):
                raise AppError("source_unavailable",status=403)
            await self._lock_owned_partition(conn,batch["partition_id"],owner)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
            if current["manifest"]["context_round"]:
                return True
            manifest = deepcopy(current["manifest"])
            manifest["steps"][step_id] = frozen_mapping(expanded)
            manifest["context_round"] = 1
            await self.steps.prepare_on(conn,step_id,request)
            await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,step_ids=array_append(step_ids,%s),dependency_refs=%s,read_set=%s,updated_at=now() WHERE batch_id=%s",
                (Jsonb(manifest),step_id,Jsonb(wire(dependencies)),Jsonb(wire(expanded["read_set"])),batch["batch_id"]))
        return True

    @staticmethod
    def _append_material(frozen, read, *, role):
        return append_context_material(frozen["materials"],read,extra={"speaker_id":read["speaker_id"] or
            ("actor:"+frozen["context"]["actor_id"] if read["origin_kind"]=="actor" else None)})

    async def _lock_owned_partition(self,conn,partition_id,owner):
        state=await (await conn.execute("SELECT lease_owner FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
            (partition_id,))).fetchone()
        if owner is not None and (not state or state["lease_owner"] != owner):
            raise AppError("memory_consolidation_lease_changed",status=409)
        return state

    async def _apply(self, batch, context, frozen, proposal, *, owner=None):
        digest = body_hash(proposal)
        async with self.database.transaction() as conn:
            # This order matches Sources withdrawal: sources -> partition -> batch -> stable domain objects.
            if not await self.sources.validate_persistence(conn,context,codec.refs(batch["dependency_refs"])):
                raise AppError("source_unavailable",status=403)
            await self._lock_owned_partition(conn,batch["partition_id"],owner)
            current = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
            if current["status"] == "applied":
                return current["commit_refs"]
            if current["status"] not in _OBSERVABLE:
                raise AppError("memory_consolidation_batch_terminal",status=409)
            if current["proposal_digest"] is not None and current["proposal_digest"] != digest:
                raise AppError("memory_consolidation_proposal_changed",status=409)
            if current["context_fingerprint"] != body_hash(context_record(context)):
                raise AppError("memory_consolidation_authority_changed",status=409)
            await self.identity.check_context(context,connection=conn)
            for episode in current["manifest"]["episodes"]:
                row = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",(episode["episode_id"],))).fetchone()
                if not row or not row["active"] or row["revision"] != episode["revision"] or row["sequence"] != episode["sequence"] or row["extraction_cursor"] != episode["before"]:
                    raise AppError("memory_consolidation_episode_changed",status=409)
            result = await self.memory.claims.revisions.apply_operations_on(conn,context,batch_id=batch["batch_id"],
                frozen=frozen,operations=proposal["operations"])
            if result.get("derived_invalidations"):
                from tianshu.sources.dependencies import unique_refs
                new_sources=unique_refs(*(codec.refs(item["source_refs"]) for item in frozen["materials"].values() if item["role"]=="new"))
                await self.memory.domain_changed_on(conn,context,result["derived_invalidations"],source_refs=new_sources,
                    affected_subject_ids=result.get("affected_subject_ids",()))
            unresolved = {item["subject_id"] for item in proposal["unresolved"]}
            coverage = []
            for episode in current["manifest"]["episodes"]:
                missing = set(episode["missing"])
                if any(item["status"] == "unresolved" and set(item["processed_material_ids"]) & set(episode["material_ids"])
                        for item in proposal["target_results"]) or None in unresolved or proposal["needs_context"]:
                    missing.add("semantic_unresolved")
                if frozen.get("selection_coverage",{}).get("record_content_omitted"):
                    missing.add("old_record_context_incomplete")
                state = "pending" if not episode["complete"] else "completed_with_missing" if missing else "completed"
                await conn.execute("UPDATE memory.episodes SET extraction_cursor=%s,extraction_state=%s,extraction_missing=%s,extraction_started_at=NULL WHERE episode_id=%s AND sequence=%s",
                    (Jsonb(episode["after"]),state,sorted(missing),episode["episode_id"],episode["sequence"]))
                coverage.append({"episode_id":episode["episode_id"],"revision":episode["revision"],"sequence":episode["sequence"],
                    "cursor":episode["after"],"state":state,"missing":sorted(missing)})
            result = {**result,"coverage":coverage,"target_results":proposal["target_results"],"unresolved":proposal["unresolved"],
                "context_unfulfilled":proposal["needs_context"],"batch_id":batch["batch_id"]}
            await conn.execute("UPDATE memory.consolidation_batches SET status='applied',proposal_digest=%s,commit_refs=%s,committed_at=now(),updated_at=now(),error_code=NULL WHERE batch_id=%s",
                (digest,Jsonb(wire(result)),batch["batch_id"]))
            await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL,last_error=NULL,budget_failure=NULL,updated_at=now() "
                "WHERE partition_id=%s AND active_batch_id=%s",(batch["partition_id"],batch["batch_id"]))
            pending = await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                "AND active AND extraction_state='pending' LIMIT 1",(batch["partition_id"],))).fetchone()
            if pending:
                due = datetime.now(UTC)
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=COALESCE(first_pending_at,now()) WHERE partition_id=%s",(due,batch["partition_id"]))
                await self._schedule_on(conn,batch["partition_id"],due)
            else:
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=NULL,first_pending_at=NULL WHERE partition_id=%s",(batch["partition_id"],))
        return result

    async def _failure(self, partition_id, batch_id, exc):
        code = getattr(exc,"code","memory_consolidation_contract_error" if isinstance(exc,(TypeError,ValueError)) else "memory_consolidation_unavailable")
        if code == "memory_consolidation_lease_changed":
            # A successor may already be observing/applying this same batch.
            # The expired writer has no authority to settle or reset its work.
            return
        revoked = code in {"source_unavailable","access_changed","delivery_access_changed","actor_forbidden","conversation_forbidden"}
        stale = code in {"revision_conflict","memory_consolidation_read_changed","memory_consolidation_episode_changed",
            "memory_read_set_changed","memory_read_revision_conflict","claim_revision_conflict","matter_revision_conflict"}
        details = getattr(exc,"details",None) or {}
        terminal = revoked or stale or isinstance(exc,(TypeError,ValueError)) or getattr(exc,"status",None) in {400,401,403,409,413,502} or details.get("status") in {"failed","cancelled"}
        quarantine=None
        if batch_id and not terminal:
            try:
                steps=await self._batch_steps(batch_id)
                receipt=None
                if steps:
                    _,original=await self.steps.recorded(steps[-1])
                    receipt=await self.steps.models.status(original.attempt_id)
                if receipt is not None and receipt.status == "unknown":
                    quarantine={"attempt_id":original.attempt_id,"status":receipt.status,
                        "dispatch":receipt.dispatch,"error_code":receipt.error_code,
                        "finished_at":wire(receipt.finished_at),"action":"observe_original_attempt"}
            except (AppError,TimeoutError):
                # Missing authority/receipt is not evidence that a known active
                # request has settled unknown. Keep observing its original ID.
                pass
        shrink = held = False
        async with self.database.transaction() as conn:
            await conn.execute("SELECT partition_id FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",(partition_id,))
            if batch_id:
                batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch_id,))).fetchone()
                if batch and batch["status"] in _OBSERVABLE:
                    held=batch["status"] == "quarantined"
                    status = "revoked" if revoked else "stale" if stale else "failed" if terminal else "quarantined" if held or quarantine else "waiting_model"
                    manifest=deepcopy(batch["manifest"])
                    if quarantine and "quarantine" not in manifest:
                        manifest["quarantine"]=quarantine
                    await conn.execute("UPDATE memory.consolidation_batches SET status=%s,error_code=%s,manifest=%s,updated_at=now() WHERE batch_id=%s",(status,code,Jsonb(manifest),batch_id))
                    if terminal or status == "quarantined":
                        shrink = (code == "memory_consolidation_output_truncated"
                            and not held and smaller_material_prefix(batch_frozen(batch)) is not None)
                        for episode in batch["manifest"]["episodes"]:
                            state = ("consolidation_unknown" if status == "quarantined" else
                                "consolidation_stale" if held and stale else
                                "pending" if stale or shrink else "consolidation_failed")
                            await conn.execute("UPDATE memory.episodes SET extraction_state=%s,extraction_started_at=NULL "
                                "WHERE episode_id=%s AND revision=%s AND sequence=%s AND active "
                                "AND extraction_cursor IS NOT DISTINCT FROM %s",
                                (state,episode["episode_id"],episode["revision"],episode["sequence"],Jsonb(episode["before"])))
                        await conn.execute("UPDATE memory.consolidation_state SET active_batch_id=NULL "
                            "WHERE partition_id=%s AND active_batch_id=%s",(partition_id,batch_id))
            budget_failure=None
            if not batch_id and code == "memory_consolidation_input_budget":
                budget_failure={"configuration":self._input_budget_record(),
                    "required_tokens":details.get("estimated"),"configured_tokens":self.policy.max_input_tokens}
            await conn.execute("UPDATE memory.consolidation_state SET last_error=%s,budget_failure=%s,updated_at=now() "
                "WHERE partition_id=%s",(code,Jsonb(budget_failure) if budget_failure else None,partition_id))
            if not batch_id and terminal:
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=NULL,first_pending_at=NULL WHERE partition_id=%s",(partition_id,))
                if revoked:
                    await conn.execute("UPDATE memory.episodes SET extraction_state='consolidation_revoked',extraction_started_at=NULL "
                        "WHERE consolidation_partition_id=%s AND extraction_state='pending'",(partition_id,))
            if batch_id and (terminal or quarantine):
                pending=await (await conn.execute("SELECT 1 FROM memory.episodes WHERE consolidation_partition_id=%s "
                    "AND active AND extraction_state='pending' LIMIT 1",(partition_id,))).fetchone()
                await conn.execute("UPDATE memory.consolidation_state SET wake_due_at=%s,first_pending_at=%s WHERE partition_id=%s",
                    (datetime.now(UTC) if pending else None,datetime.now(UTC) if pending else None,partition_id))
            else:
                pending=False
            if shrink or pending or stale and not held:
                due=datetime.now(UTC)
                await self._schedule_on(conn,partition_id,due,suffix="stale:"+(batch_id or "prepare"))

    async def _batch_steps(self,batch_id):
        async with self.database.transaction(read_only=True) as conn:
            row=await (await conn.execute("SELECT step_ids FROM memory.consolidation_batches WHERE batch_id=%s",(batch_id,))).fetchone()
        return row["step_ids"] if row else ()
