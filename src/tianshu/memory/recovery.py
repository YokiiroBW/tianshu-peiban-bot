"""Finite cutover receipts and observation of already-recorded legacy attempts."""

from datetime import UTC, datetime
import json

from jsonschema import Draft202012Validator
from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.api import CommandResult
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass
from . import codec
from .authority import context_record
from .consolidation import context_from_bound
from .extraction_contract import SCHEMA_VERSION, legacy_extraction_schema, validate_proposal

LEGACY_VERSION = "memory.legacy-extraction.v0"
LEGACY_TERMINAL = ("completed", "completed_with_missing", "legacy_waiting",
                   "consolidation_failed", "legacy_abandoned")


class ConsolidationRecovery:
    def __init__(self, memory, steps):
        self.memory, self.steps = memory, steps
        self.database, self.sources, self.identity = memory.database,memory.sources,memory.identity

    async def statuses_on(self,conn,context,episodes):
        if not episodes:
            return {}
        partitions=sorted({episode["consolidation_partition_id"] for episode in episodes if episode.get("consolidation_partition_id")})
        states=await (await conn.execute("SELECT partition_id,last_error,budget_failure FROM memory.consolidation_state WHERE partition_id=ANY(%s::text[])",(partitions,))).fetchall()
        errors={state["partition_id"]:state["last_error"] for state in states}
        budgets={state["partition_id"]:state["budget_failure"] for state in states}
        selectors=[Jsonb([{"episode_id":episode["episode_id"],"sequence":episode["sequence"]}]) for episode in episodes]
        batches=await (await conn.execute(
            "SELECT b.* FROM memory.consolidation_batches b JOIN memory.consolidation_state s USING(partition_id) "
            "WHERE s.actor_id=%s AND b.manifest->'episodes' @> ANY(%s::jsonb[]) ORDER BY b.created_at DESC",
            (context.actor_id,selectors))).fetchall()
        by_episode={}
        visible={}
        # Source checks are chunked by unique versions, preserving the existing authority limits.
        chunk=[]
        keys=set()
        async def check_chunk():
            if chunk:
                checked=await self.sources.check_many_on(conn,context,tuple(codec.refs(batch["dependency_refs"]) for batch in chunk))
                visible.update((batch["batch_id"],allowed) for batch,allowed in zip(chunk,checked,strict=True))
        for batch in batches:
            batch_keys={(ref.source_id,ref.revision) for ref in codec.refs(batch["dependency_refs"])}
            if chunk and (len(keys|batch_keys)>1800 or len(chunk)>=128):
                await check_chunk()
                chunk=[]
                keys=set()
            chunk.append(batch)
            keys.update(batch_keys)
            for episode in batch["manifest"].get("episodes",()):
                by_episode.setdefault((episode["episode_id"],episode["sequence"]),batch)
        await check_chunk()
        return {episode["episode_id"]:self._status_value(episode,by_episode.get((episode["episode_id"],episode["sequence"])),visible,
            errors.get(episode.get("consolidation_partition_id")),budgets.get(episode.get("consolidation_partition_id")))
            for episode in episodes}

    async def status_on(self,conn,context,episode):
        return (await self.statuses_on(conn,context,(episode,)))[episode["episode_id"]]

    def _status_value(self,episode,batch,visible,pipeline_error=None,budget_failure=None):
        action="wait_for_new_evidence" if episode["extraction_state"] in {"completed","completed_with_missing"} else "refresh_material"
        if episode["extraction_state"] == "legacy_abandoned":
            action="legacy_application_abandoned"
        active = batch and batch["status"] in {"prepared","waiting_model","ready"}
        if active:
            action="observe_original_attempt"
        unfinished=episode["extraction_state"] not in {"completed","completed_with_missing","legacy_abandoned"}
        error=batch["error_code"] if batch else pipeline_error
        if not batch and not unfinished:
            error=None
        budget=None
        if not active and unfinished and pipeline_error == "memory_consolidation_input_budget":
            # This failure has no model attempt/batch. Its persisted observed
            # configuration, rather than an older batch's error, controls recovery.
            error=pipeline_error
            current=self.memory.consolidation._input_budget_record()
            changed=bool(budget_failure and budget_failure.get("configuration") is not None
                and budget_failure["configuration"] != current)
            budget={"configuration_changed":changed,"failed_configuration":
                budget_failure.get("configuration") if budget_failure else None,"current_configuration":current,
                "required_tokens":budget_failure.get("required_tokens") if budget_failure else None}
            action="refresh_material" if changed else "configure_budget"
        elif (not active and error == "memory_consolidation_output_truncated"
                and batch and batch["manifest"].get("source_budget",256)<=256
                and batch["manifest"].get("budget",self.memory.consolidation._budget_record()) == self.memory.consolidation._budget_record()):
            action="configure_budget"
        result={"batch_id":batch["batch_id"] if batch else None,
            "status":batch["status"] if batch else episode["extraction_state"],
            "error_code":error,"action":action,
            "fragment_cursor":episode["extraction_cursor"],"targets":[],"target_results":[],"unresolved":[],
            "coverage":{"state":episode["extraction_state"],"missing":episode["extraction_missing"]}}
        if budget is not None:
            result["budget_failure"]=budget
        if batch and visible.get(batch["batch_id"]):
            manifest=batch["manifest"]
            fixed=(manifest.get("steps",{}).get(batch["step_ids"][-1]) if batch["step_ids"] else None)
            fixed=fixed or manifest.get("frozen") or manifest
            targets=fixed.get("targets",{})
            result.update(target_results=batch["commit_refs"].get("target_results",[]),
                targets=[{"subject_id":subject} for subject in sorted(targets)] if isinstance(targets,dict) else
                    [{"subject_id":item["subject_id"]} for item in targets if isinstance(item,dict) and item.get("subject_id")],
                unresolved=batch["commit_refs"].get("unresolved",[]),
                context_unfulfilled=batch["commit_refs"].get("context_unfulfilled",[]),
                step_ids=batch["step_ids"],schema_version=batch["schema_version"])
            own=next((item for item in batch["commit_refs"].get("coverage",())
                if item["episode_id"] == episode["episode_id"] and item["sequence"] == episode["sequence"]),None)
            if own:
                result["coverage"]=own
        return result

    async def replay_resume_on(self,conn,context,body,original):
        await self.identity.check_context(context,connection=conn)
        episode=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s "
            "AND active AND scope_ids<@%s::text[]",(body["episode_id"],context.actor_id,list(context.scope_ids)))).fetchone()
        if not episode:
            raise AppError("episode_unavailable",status=404)
        if not all(await self.sources.check_many_on(conn,context,(codec.refs(episode["source_refs"]),))):
            raise AppError("source_unavailable",status=403)
        status=await self.status_on(conn,context,episode)
        return CommandResult(original.client_request_id,revision=episode["revision"],action_id=original.action_id,
            attempt_id=original.attempt_id,data={"episode_id":episode["episode_id"],"queued":False,"replayed":True,
                "action":status["action"],"batch_id":status["batch_id"],"consolidation":status})

    async def resume_on(self,conn,context,body):
        snapshot = await (await conn.execute(
            "SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s AND active",
            (body["episode_id"],context.actor_id))).fetchone()
        if not snapshot:
            raise AppError("episode_unavailable",status=404)
        batch = await (await conn.execute(
            "SELECT * FROM memory.consolidation_batches WHERE manifest->'episodes' @> %s "
            "ORDER BY created_at DESC LIMIT 1",(Jsonb([{"episode_id":snapshot["episode_id"],"sequence":snapshot["sequence"]}]),))).fetchone()
        dependencies = codec.refs(snapshot["source_refs"])
        if batch:
            from tianshu.sources.dependencies import unique_refs
            dependencies = unique_refs(dependencies,codec.refs(batch["dependency_refs"]))
        if not await self.sources.validate_persistence(conn,context,dependencies):
            raise AppError("source_unavailable",status=403)
        if batch:
            await conn.execute("SELECT partition_id FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",(batch["partition_id"],))
            batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
        current = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",(snapshot["episode_id"],))).fetchone()
        if not current or current["sequence"] != snapshot["sequence"] or current["revision"] != body.get("expected_revision"):
            raise AppError("revision_conflict",status=409)
        data = {"episode_id":current["episode_id"],"queued":False,"action":"wait_for_new_evidence",
                "batch_id":batch["batch_id"] if batch else None}
        status=await self.status_on(conn,context,current)
        if status["action"] == "configure_budget":
            data["action"]="configure_budget"
            return CommandResult(body["client_request_id"],revision=current["revision"],data=data)
        if batch and batch["status"] in {"prepared","waiting_model","ready"}:
            if batch["schema_version"] == LEGACY_VERSION:
                await self.memory.jobs.enqueue(conn,JobSpec("memory.observe_legacy",
                    "memory-legacy-explicit-observe:"+body["client_request_id"],{"batch_id":batch["batch_id"]},ServiceClass.ELASTIC))
            else:
                await self.memory.consolidation._schedule_on(conn,batch["partition_id"],datetime.now(UTC),
                    suffix="explicit:"+body["client_request_id"])
            data.update(queued=True,action="observe_original_attempt")
        elif current["extraction_state"] not in {"completed","completed_with_missing","legacy_abandoned"}:
            original_context=context
            if current.get("consolidation_partition_id"):
                state=await (await conn.execute("SELECT authority_bound FROM memory.consolidation_state WHERE partition_id=%s",
                    (current["consolidation_partition_id"],))).fetchone()
                if state:
                    original_context=context_from_bound(state["authority_bound"])
                    await self.identity.check_context(original_context,connection=conn)
            await conn.execute("UPDATE memory.episodes SET extraction_state='pending',extraction_started_at=NULL,"
                "extraction_cursor=extraction_cursor || %s WHERE episode_id=%s",(Jsonb({"protocol":SCHEMA_VERSION}),current["episode_id"]))
            partition_id = await self.memory.consolidation.dirty_on(conn,original_context,current)
            await self.memory.consolidation._schedule_on(conn,partition_id,datetime.now(UTC),suffix="explicit:"+body["client_request_id"])
            data.update(queued=True,action="refresh_material")
        elif current["extraction_state"] == "legacy_abandoned":
            data["action"] = "legacy_application_abandoned"
        return CommandResult(body["client_request_id"],revision=current["revision"],data=data)

    async def convert_episode(self, episode_id, sequence):
        authority_error=None
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute(
                "SELECT * FROM memory.episodes WHERE episode_id=%s AND sequence=%s AND active",
                (episode_id,sequence))).fetchone()
            if (not row or row["extraction_state"] in LEGACY_TERMINAL
                    or (row["extraction_cursor"] or {}).get("protocol") == SCHEMA_VERSION):
                return
            step = (row["extraction_cursor"] or {}).get("step",0)
            step_id = f"extract:{episode_id}:{sequence}:{step}"
            old = await (await conn.execute("SELECT * FROM memory.model_steps WHERE step_id=%s",(step_id,))).fetchone()
            try:
                context = await self.identity.context_on(conn,row["principal_id"],actor_id=row["actor_id"],
                    person_id=row["person_id"],conversation_id=row["conversation_id"],origin_kind=row["origin_kind"])
            except AppError as exc:
                if exc.status not in {401,403,404,409}:
                    raise
                authority_error=exc.code
        if authority_error:
            return await self._abandon_legacy(row,"legacy_authority_unavailable",step_id=step_id,old=old)
        if not row["source_refs"]:
            return await self._abandon_legacy(row,"legacy_original_evidence_missing",context=context,step_id=step_id,old=old)
        try:
            await self.sources.check(context,codec.refs(row["source_refs"]))
        except AppError as exc:
            if exc.status not in {401,403,409}:
                raise
            return await self._abandon_legacy(row,"legacy_source_unavailable",context=context,step_id=step_id,old=old)
        if old:
            # Even an old prepared row may have crossed dispatch before a crash. Ask Broker first.
            try:
                _, request = await self.steps.recorded(step_id)
            except (AppError,ValueError,TypeError,KeyError) as exc:
                if isinstance(exc,AppError) and exc.status not in {401,403,404,409}:
                    raise
                return await self._abandon_legacy(row,"legacy_request_unavailable",context=context,step_id=step_id,old=old)
            try:
                receipt = await self.memory.models.status(request.attempt_id)
            except (AppError,TimeoutError):
                # A status transport failure cannot establish that submission never happened.
                return await self._register_observer(row,request,step_id)
            if receipt is not None or old["state"] not in {"prepared","legacy_unsubmitted_converted"}:
                return await self._register_observer(row,request,step_id)
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn,context,codec.refs(row["source_refs"])):
                return await self._abandon_legacy(row,"legacy_source_unavailable",context=context,step_id=step_id,old=old)
            current = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",(episode_id,))).fetchone()
            if (not current or current["sequence"] != sequence or not current["active"]
                    or current["extraction_state"] in LEGACY_TERMINAL
                    or (current["extraction_cursor"] or {}).get("protocol") == SCHEMA_VERSION):
                return
            await conn.execute("UPDATE memory.episodes SET extraction_state='pending',extraction_started_at=NULL,extraction_cursor=extraction_cursor || %s WHERE episode_id=%s",(Jsonb({'protocol':SCHEMA_VERSION}),episode_id))
            partition_id = await self.memory.consolidation.dirty_on(conn,context,current)
            conversion_id = "memory_conversion_"+body_hash({"episode_id":episode_id,"sequence":sequence,"cursor":row["extraction_cursor"]})[:48]
            manifest = {"conversion":{"task":"memory.extract","key":f"memory-extract:{episode_id}:{sequence}",
                "episode_id":episode_id,"sequence":sequence,"cursor":row["extraction_cursor"],"step_id":step_id if old else None,
                "attempt_id":request.attempt_id if old else None,"previous_state":row["extraction_state"],
                "action":"unsubmitted_to_partition"},"episodes":[]}
            await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,context_fingerprint,commit_refs,committed_at) "
                "VALUES(%s,%s,%s,'applied',%s,%s,%s,now()) ON CONFLICT DO NOTHING",
                (conversion_id,partition_id,LEGACY_VERSION,Jsonb(wire(manifest)),body_hash(context_record(context)),Jsonb({"action":"unsubmitted_to_partition"})))
            if old and old["state"] == "prepared":
                await conn.execute("UPDATE memory.model_steps SET state='legacy_unsubmitted_converted',error_code='legacy_writer_retired' WHERE step_id=%s AND state='prepared'",(step_id,))

    async def _abandon_legacy(self,row,reason,*,context=None,step_id=None,old=None):
        """A fixed, source-free retirement receipt; no model execution is inferred."""
        identity={"episode_id":row["episode_id"],"sequence":row["sequence"],"cursor":row["extraction_cursor"]}
        suffix=body_hash(identity)[:48]
        partition_id="memory_legacy_retired_"+suffix
        batch_id="memory_conversion_"+suffix
        recorded_request=old.get("request") if old else None
        attempt_id=recorded_request.get("attempt_id") if isinstance(recorded_request,dict) else None
        authority=context_record(context) if context else {"principal_id":row["principal_id"],
            "actor_id":row["actor_id"],"conversation_id":row["conversation_id"],
            "scope_ids":row["scope_ids"],"legacy_authority_unknown":True}
        conversion={"task":"memory.extract","key":f"memory-extract:{row['episode_id']}:{row['sequence']}",
            **identity,"step_id":step_id if old else None,"attempt_id":attempt_id,
            "previous_state":row["extraction_state"],"action":"application_abandoned","reason":reason}
        manifest={"conversion":conversion,"episodes":[{"episode_id":row["episode_id"],
            "revision":row["revision"],"sequence":row["sequence"]}]}
        async with self.database.transaction() as conn:
            await conn.execute("INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,conversation_id,scope_key,authority_bound) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (partition_id,row["actor_id"],row["principal_id"],row["conversation_id"],body_hash(row["scope_ids"]),Jsonb(wire(authority))))
            await conn.execute("SELECT partition_id FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",(partition_id,))
            current=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",(row["episode_id"],))).fetchone()
            if (not current or current["sequence"]!=row["sequence"] or current["extraction_state"] in LEGACY_TERMINAL
                    or (current["extraction_cursor"] or {}).get("protocol")==SCHEMA_VERSION):
                return
            await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,dependency_refs,"
                "context_fingerprint,step_ids,error_code,commit_refs,committed_at) VALUES(%s,%s,%s,'revoked',%s,%s,%s,%s,%s,%s,now()) ON CONFLICT DO NOTHING",
                (batch_id,partition_id,LEGACY_VERSION,Jsonb(wire(manifest)),Jsonb(row["source_refs"]),body_hash(authority),
                 [step_id] if old else [],reason,Jsonb({"action":"application_abandoned","original_attempt_retained":bool(old),"reason":reason})))
            await conn.execute("UPDATE memory.episodes SET extraction_state='legacy_abandoned',extraction_started_at=NULL,"
                "extraction_missing=CASE WHEN %s=ANY(extraction_missing) THEN extraction_missing ELSE array_append(extraction_missing,%s) END "
                "WHERE episode_id=%s AND sequence=%s",(reason,reason,row["episode_id"],row["sequence"]))
        return batch_id

    async def _register_observer(self,row,request,step_id):
        context = request.context
        # Legacy requests get separate finite observation partitions, never another default writer.
        partition_id = "memory_legacy_"+body_hash(step_id)[:48]
        batch_id = "memory_legacy_batch_"+body_hash(step_id)[:48]
        manifest = {"authority_bound":context_record(context),"episodes":[{
            "episode_id":row["episode_id"],"revision":row["revision"],"sequence":row["sequence"],
            "before":row["extraction_cursor"],"after":None,"source_refs":row["source_refs"],
            "scope_ids":row["scope_ids"],"missing":row["extraction_missing"],"complete":False,"material_ids":[]}],
            "conversion":{"task":"memory.extract","key":f"memory-extract:{row['episode_id']}:{row['sequence']}",
                "episode_id":row["episode_id"],"sequence":row["sequence"],"cursor":row["extraction_cursor"],
                "step_id":step_id,"attempt_id":request.attempt_id,"previous_state":row["extraction_state"],
                "action":"observe_original_attempt"}}
        async with self.database.transaction() as conn:
            if not await self.sources.validate_persistence(conn,context,request.source_refs):
                old={"request":wire(request)}
                return await self._abandon_legacy(row,"legacy_source_unavailable",context=context,step_id=step_id,old=old)
            await conn.execute("INSERT INTO memory.consolidation_state(partition_id,actor_id,principal_id,conversation_id,scope_key,authority_bound) "
                "VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (partition_id,context.actor_id,context.principal_id,context.conversation_id,body_hash(sorted(row["scope_ids"])),Jsonb(context_record(context))))
            current = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",(row["episode_id"],))).fetchone()
            if (not current or current["sequence"] != row["sequence"] or current["extraction_state"] in LEGACY_TERMINAL
                    or (current["extraction_cursor"] or {}).get("protocol")==SCHEMA_VERSION):
                return
            await conn.execute("INSERT INTO memory.consolidation_batches(batch_id,partition_id,schema_version,status,manifest,dependency_refs,context_fingerprint,step_ids) "
                "VALUES(%s,%s,%s,'waiting_model',%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (batch_id,partition_id,LEGACY_VERSION,Jsonb(wire(manifest)),Jsonb(wire(request.source_refs)),body_hash(context_record(context)),[step_id]))
            await conn.execute("UPDATE memory.episodes SET extraction_state='legacy_waiting' WHERE episode_id=%s AND sequence=%s",
                (row["episode_id"],row["sequence"]))
        return batch_id

    @staticmethod
    def _legacy_material(request):
        for message in request.messages:
            for part in message.parts:
                if not part.text:
                    continue
                text=part.text
                if text.startswith("材料 original_evidence"):
                    text=text.split("\n",1)[1]
                try:
                    material=json.loads(text)
                except (ValueError,TypeError):
                    continue
                if isinstance(material,dict) and "sources" in material and "allowed_subject_ids" in material:
                    return material
        raise AppError("legacy_fixed_mapping_unavailable",status=409)

    async def _translate(self,batch,result,request):
        completion=getattr(result,"completion",None)
        native=result.native_response or {}
        reasons={value.casefold() for value in (completion.stop_reasons if completion else ())}
        reasons.update(choice.get("finish_reason","").casefold() for choice in native.get("choices",()) if isinstance(choice,dict))
        reasons.add(str(native.get("finish_reason","")).casefold())
        if reasons & {"length","max_tokens","max_output_tokens"}:
            raise AppError("legacy_output_truncated",status=502)
        if completion and completion.state == "incomplete":
            raise AppError("legacy_output_incomplete",status=502)
        original=self._legacy_material(request)
        allowed=original["allowed_subject_ids"]
        parsed=json.loads("".join(part.text or "" for part in result.parts if part.kind == "text"))
        if not Draft202012Validator(legacy_extraction_schema(allowed)).is_valid(parsed):
            raise AppError("legacy_output_not_applicable",status=409)
        sources=original["sources"]
        if len(request.source_refs)<len(sources):
            raise AppError("legacy_fixed_mapping_unavailable",status=409)
        episode=batch["manifest"]["episodes"][0]
        frozen={"batch_id":batch["batch_id"],"schema_version":SCHEMA_VERSION,
            "context":{"actor_id":request.context.actor_id,"conversation_id":request.context.conversation_id},
            "materials":{},"records":{},"subjects":{},"targets":{},"read_set":[],"episodes":[episode]}
        for index,source in enumerate(sources):
            ref=request.source_refs[index]
            span=(source.get("coverage") or {}).get("ranges",[{}])[0]
            if any((ref.locator or {}).get(key)!=span.get(key) for key in ("start","end")):
                raise AppError("legacy_fixed_mapping_unavailable",status=409)
            speaker=source.get("speaker_id")
            frozen["materials"]["m"+str(index+1)]={"source_refs":wire((ref,)),"speaker_id":speaker,
                "origin_kind":source["origin_kind"],"occurred_at":original["occurred_at"],"role":"new",
                "episode_id":episode["episode_id"],"episode_revision":episode["revision"],"actual_result":None}
        async with self.database.transaction(read_only=True) as conn:
            subjects=await self.identity.resolve_memory_subjects_on(conn,request.context,allowed)
            row=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s",(episode["episode_id"],))).fetchone()
        frozen["subjects"]={item["person_id"]:{"subject_id":item["person_id"],"kind":item["kind"]} for item in subjects}
        frozen["targets"]={subject:{"new_material_ids":[key for key,item in frozen["materials"].items() if item["speaker_id"]==subject]} for subject in frozen["subjects"]}
        for index,matter in enumerate(original.get("current_matters",())):
            frozen["records"]["r"+str(index)]={**matter,"kind":"matter","id":matter["matter_id"]}
        frozen["read_set"]=[{"kind":"matter","id":item["id"],"revision":item["revision"]} for item in frozen["records"].values()]
        operations=[]
        for group in ("claims","matters"):
            for item in parsed[group]:
                evidence=["m"+str(index+1) for index in item["source_indexes"]]
                if any(key not in frozen["materials"] for key in evidence):
                    raise AppError("legacy_fixed_mapping_unavailable",status=409)
                if item["subject_id"] not in frozen["subjects"]:
                    raise AppError("legacy_subject_unavailable",status=409)
                op_id="op"+str(len(operations)+1)
                target={"create":"n"+str(len(operations)+1)}
                reads=[]
                if group=="claims":
                    speakers={frozen["materials"][ref]["speaker_id"] for ref in evidence}
                    speaker=next(iter(speakers)) if len(speakers)==1 else None
                    payload={key:item.get(key,default) for key,default in (
                        ("predicate",None),("value",None),("assertion","reported"),("polarity",True),
                        ("conditions",{}),("valid_from",None),("valid_until",None))}
                    payload["speaker_id"]=speaker
                    if payload["assertion"]=="self_confirmed" and any(
                            frozen["materials"][ref]["origin_kind"]!="human" or frozen["materials"][ref]["speaker_id"]!=item["subject_id"] for ref in evidence):
                        raise AppError("legacy_self_confirmation_unproven",status=409)
                    kind="add_claim"
                else:
                    old_ref=next((key for key,value in frozen["records"].items() if value["id"]==item["matter_id"]),None)
                    if old_ref:
                        target={"ref":old_ref,"expected_revision":frozen["records"][old_ref]["revision"]}
                        reads=[old_ref]
                    payload={key:item.get(key,default) for key,default in (
                        ("title",None),("state",None),("due_at",None),("summary",""),("conditions",{}))}
                    payload["completion_basis"]=None
                    kind="upsert_matter"
                operations.append({"op_id":op_id,"kind":kind,"subject_id":item["subject_id"],"target":target,"payload":payload,
                    "support_groups":[evidence],"context_refs":[],"reads":reads,"reason":"Apply original recorded pre-cutover result"})
        proposal={"schema_version":SCHEMA_VERSION,"batch_id":batch["batch_id"],"operations":operations,"unresolved":[],"needs_context":[],
            "target_results":[{"subject_id":subject,"status":"changed" if any(op["subject_id"]==subject for op in operations) else "no_change",
                "processed_material_ids":target["new_material_ids"],"operation_ids":[op["op_id"] for op in operations if op["subject_id"]==subject]}
                for subject,target in frozen["targets"].items()]}
        validate_proposal(frozen,proposal)
        # Source total and cursor come from the exact old request; no live source order is used.
        refs=codec.refs(row["extraction_sources"] or row["source_refs"])
        before=episode["before"] or {"source_index":0,"position":None,"step":0}
        selected=request.source_refs[:len(sources)]
        after={"protocol":SCHEMA_VERSION,"source_index":before.get("source_index",0),"position":before.get("position"),"step":before.get("step",0)+1}
        for index,ref in enumerate(selected):
            source_index=next((position for position in range(after["source_index"],len(refs))
                if (refs[position].source_id,refs[position].revision)==(ref.source_id,ref.revision)),None)
            if source_index is None:
                raise AppError("legacy_fixed_mapping_unavailable",status=409)
            end=(ref.locator or {}).get("end")
            total=(sources[index].get("coverage") or {}).get("total")
            target_end=min(total,(refs[source_index].locator or {}).get("end",total)) if total is not None else None
            if target_end is None or end is None:
                raise AppError("legacy_fixed_mapping_unavailable",status=409)
            after.update(source_index=source_index+1 if end>=target_end else source_index,position=None if end>=target_end else end)
        episode.update(after=after,complete=after["source_index"]>=len(refs),material_ids=list(frozen["materials"]))
        frozen["episodes"]=[episode]
        return frozen,proposal

    async def observe_legacy(self,batch):
        step_id=batch["step_ids"][0]
        observed_request=None
        try:
            _,observed_request=await self.steps.recorded(step_id)
            result,request=await self.steps.execute_recorded(step_id,observe_only=True)
            observed_request=request
            frozen,proposal=await self._translate(batch,result,request)
            async with self.database.transaction() as conn:
                if not await self.sources.validate_persistence(conn,request.context,request.source_refs):
                    raise AppError("source_unavailable",status=403)
                manifest={**batch["manifest"],"frozen":frozen,"episodes":frozen["episodes"]}
                await conn.execute("UPDATE memory.consolidation_batches SET manifest=%s,read_set=%s WHERE batch_id=%s AND status='waiting_model'",
                    (Jsonb(wire(manifest)),Jsonb(wire(frozen["read_set"])),batch["batch_id"]))
            batch={**batch,"manifest":manifest}
            return await self.memory.consolidation._apply(batch,request.context,frozen,proposal)
        except (AppError,ValueError,TypeError) as exc:
            if isinstance(exc,AppError) and exc.status==503 and (exc.details or {}).get("status") not in {"failed","cancelled"}:
                return {"action":"observe_original_attempt","error_code":exc.code}
            # The legacy receipt is left intact. Only application is abandoned.
            async with self.database.transaction() as conn:
                refresh=observed_request is not None and getattr(exc,"code","") not in {"source_unavailable","access_changed"}
                if refresh:
                    refresh=await self.sources.validate_persistence(conn,observed_request.context,observed_request.source_refs)
                await conn.execute("SELECT partition_id FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",(batch["partition_id"],))
                fresh=await (await conn.execute("SELECT status,commit_refs FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",(batch["batch_id"],))).fetchone()
                if fresh and fresh["status"]=="applied":
                    return fresh["commit_refs"]
                await conn.execute("UPDATE memory.consolidation_batches SET status='failed',error_code=%s,commit_refs=%s,updated_at=now() WHERE batch_id=%s AND status<>'applied'",
                    (getattr(exc,"code","legacy_output_not_applicable"),Jsonb({"action":"application_abandoned","original_attempt_retained":True}),batch["batch_id"]))
                for episode in batch["manifest"]["episodes"]:
                    if refresh:
                        current=await (await conn.execute("UPDATE memory.episodes SET extraction_state='pending',extraction_started_at=NULL,"
                            "extraction_cursor=extraction_cursor || %s WHERE episode_id=%s AND sequence=%s AND active RETURNING *",
                            (Jsonb({"protocol":SCHEMA_VERSION}),episode["episode_id"],episode["sequence"]))).fetchone()
                        if current:
                            await self.memory.consolidation.dirty_on(conn,observed_request.context,current)
                    else:
                        await conn.execute("UPDATE memory.episodes SET extraction_state='legacy_abandoned' WHERE episode_id=%s AND sequence=%s AND active",
                            (episode["episode_id"],episode["sequence"]))

    async def recover(self):
        async with self.database.transaction(read_only=True) as conn:
            # Completed history never enters this fixed conversion list.
            rows=await (await conn.execute("SELECT episode_id,sequence FROM memory.episodes WHERE active AND NOT extraction_state=ANY(%s::text[]) "
                "AND extraction_cursor->>'protocol' IS DISTINCT FROM %s "
                "AND (extraction_state=ANY(%s::text[]) OR extraction_cursor ?| ARRAY['source_index','position','step'] "
                "OR EXISTS(SELECT 1 FROM memory.model_steps old WHERE old.step_id='extract:'||memory.episodes.episode_id||':'||memory.episodes.sequence||':'||COALESCE(memory.episodes.extraction_cursor->>'step','0')) "
                "OR EXISTS(SELECT 1 FROM runtime.job_keys old WHERE old.task='memory.extract' AND old.key='memory-extract:'||memory.episodes.episode_id||':'||memory.episodes.sequence)) "
                "AND NOT EXISTS(SELECT 1 FROM memory.consolidation_batches b WHERE b.manifest->'episodes' @> "
                "jsonb_build_array(jsonb_build_object('episode_id',memory.episodes.episode_id,'sequence',memory.episodes.sequence))) "
                "ORDER BY sequence LIMIT 64",(list(LEGACY_TERMINAL),SCHEMA_VERSION,
                    ['pending','running','extraction_unavailable','model_outcome_unknown','extraction_failed']))).fetchall()
            legacy=await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE schema_version=%s AND status='waiting_model' ORDER BY created_at LIMIT 32",(LEGACY_VERSION,))).fetchall()
        for row in rows:
            await self.convert_episode(row["episode_id"],row["sequence"])
        for batch in legacy:
            await self.observe_legacy(batch)
