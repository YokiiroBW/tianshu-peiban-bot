"""Current consolidation status and scoped recovery of original attempts."""

from datetime import UTC, datetime
from copy import deepcopy
from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.api import CommandResult
from tianshu.runtime.serialization import wire
from . import codec
from .consolidation import context_from_bound, PREPARATION_DEPENDENCY_WAIT
from .consolidation_wait import batch_snapshot
from .recovery_wait import is_unresolved_wait
from .consolidation_retry import explicit_retry_available, semantic_output_failure, rpc_recovery_available
from .extraction_contract import SCHEMA_VERSION
from .materials import batch_frozen, closed_episode_split


class ConsolidationRecovery:
    def __init__(self, memory):
        self.memory = memory
        self.database, self.sources, self.identity = memory.database,memory.sources,memory.identity
        from .consolidation_attribution import ConsolidationAttribution
        self.attribution = ConsolidationAttribution(memory)

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
        active = batch and batch["status"] in {"prepared","waiting_model","ready","quarantined"}
        if active:
            action="observe_original_attempt"
        unfinished=episode["extraction_state"] not in {"completed","completed_with_missing"}
        error=batch["error_code"] if batch else pipeline_error
        if not batch and not unfinished:
            error=None
        if not batch and is_unresolved_wait(error):
            action, error = "wait_for_reconciliation", "memory_remote_wait_unresolved"
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
        elif (not active and error == "closed_component_exceeds_budget" and batch
                and batch["manifest"].get("closed_component_failure",{}).get("stage") == "preparation"):
            failure=batch["manifest"]["closed_component_failure"]
            current=self.memory.consolidation._input_budget_record()
            changed=failure.get("configuration") != current
            budget={"configuration_changed":changed,"failed_configuration":failure.get("configuration"),
                    "current_configuration":current,"required_tokens":failure.get("required_tokens")}
            action="refresh_material" if changed else "configure_budget"
        elif (not active and error in {"memory_consolidation_output_truncated","closed_component_exceeds_budget"}
                and batch and closed_episode_split(batch_frozen(batch)) is None
                and batch["manifest"].get("budget",self.memory.consolidation._budget_record()) == self.memory.consolidation._budget_record()):
            action="configure_budget"
        if batch and batch["status"] == "failed" and batch["manifest"].get("original_observation_only"):
            action="wait_for_new_evidence"
        result={"batch_id":batch["batch_id"] if batch else None,
            "status":batch["status"] if batch else episode["extraction_state"],
            "error_code":error,"action":action,
            "fragment_cursor":episode["extraction_cursor"],"targets":[],"target_results":[],"unresolved":[],
            "coverage":{"state":episode["extraction_state"],"missing":episode["extraction_missing"]},
            "commit_state":"not_committed", "required_context_missing":[],
            "indexing":{"state":"not_required","objects":[]}}
        if batch and batch["status"] == "superseded":
            result["action"] = "wait_for_reorganized_input"
            result["reorganization"] = batch["manifest"].get("superseded_by_reorganization")
        if budget is not None:
            result["budget_failure"]=budget
        if batch and batch["manifest"].get("closed_component_failure"):
            result["closed_component_failure"] = batch["manifest"]["closed_component_failure"]
        if batch and batch["status"] == "quarantined":
            result["isolated"]=True
            result["blocks_following"]=False
            result["retry_due_at"]=wire(batch.get("retry_due_at"))
            result["selected_step_id"]=batch.get("selected_step_id")
        if batch and batch["status"] == "failed" and batch.get("error_code") in PREPARATION_DEPENDENCY_WAIT:
            result["action"] = "resume_original_batch"
        if batch and batch["manifest"].get("local_wait"):
            result["action"] = "wait_for_dependency"
            result["retry_due_at"] = wire(batch.get("retry_due_at"))
        if batch and batch["manifest"].get("remote_wait_unresolved"):
            marker = batch["manifest"]["remote_wait_unresolved"]
            if marker.get("stage") == "route_profile" and batch["manifest"].get("profile_pending"):
                local_unknown = (marker.get("transaction_rollback_confirmed") is False
                    or marker.get("last_reobservation",{}).get("transaction_rollback_confirmed") is False)
                result["action"] = "wait_for_reconciliation" if local_unknown else "reobserve_preparation"
            elif marker.get("step_id"):
                result["action"] = "observe_original_attempt"
            else:
                result["action"] = "wait_for_reconciliation"
            result["recovery_observation"] = "rollback_unconfirmed"
            if marker.get("preparation_stage"):
                result["preparation_stage"] = marker["preparation_stage"]
        if explicit_retry_available(batch) and result["action"] != "configure_budget":
            result["action"]="retry_computation"
        elif rpc_recovery_available(batch):
            result["action"]="observe_original_attempt"
        if batch and batch["status"] == "stale" and batch["manifest"].get("quarantine"):
            result["held_for_refresh"]=True
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
            if batch["status"] == "applied":
                changed = batch["commit_refs"].get("changed", [])
                result["commit_state"] = "applied"
                # A durable queued object is not proof that the current index
                # profile has finished. The retrieval owner supplies readiness.
                result["indexing"] = {"state":"unknown" if changed else "not_required", "objects":changed}
            own=next((item for item in batch["commit_refs"].get("coverage",())
                if item["episode_id"] == episode["episode_id"] and item["sequence"] == episode["sequence"]),None)
            if own:
                result["coverage"]=own
            missing = result["coverage"].get("missing", [])
            result["required_context_missing"] = [value for value in missing if value in {
                "semantic_unresolved", "referenced_context_incomplete", "quoted_context_unavailable",
                "quoted_material_incomplete", "media_unavailable", "image_original_unavailable"}]
        return result

    async def replay_resume_on(self,conn,context,body,original):
        await self.identity.check_context(context,connection=conn)
        episode=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s AND actor_id=%s "
            "AND active AND scope_ids<@%s::text[]",(body["episode_id"],context.actor_id,list(context.scope_ids)))).fetchone()
        if not episode:
            raise AppError("episode_unavailable",status=404)
        if not all(await self.sources.check_many_on(conn,context,((*codec.refs(episode["source_refs"]),*codec.refs(episode.get("supplement_refs"))),))):
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
        from tianshu.sources.dependencies import unique_refs
        dependencies = (*codec.refs(snapshot["source_refs"]),*codec.refs(snapshot.get("supplement_refs")))
        original_context = context
        state_bound = None
        if batch:
            await self.memory.consolidation._assert_batch_generation_on(conn,batch)
            dependencies = unique_refs(dependencies,codec.refs(batch["dependency_refs"]))
            bound = batch["manifest"].get("authority_bound")
            if not bound:
                state_bound = await (await conn.execute("SELECT authority_bound FROM memory.consolidation_state WHERE partition_id=%s",
                    (batch["partition_id"],))).fetchone()
                if not state_bound:
                    raise AppError("memory_original_batch_unavailable",status=409)
                bound = state_bound["authority_bound"]
            original_context = context_from_bound(bound)
        elif snapshot.get("consolidation_partition_id"):
            state_bound = await (await conn.execute("SELECT authority_bound FROM memory.consolidation_state WHERE partition_id=%s",
                (snapshot["consolidation_partition_id"],))).fetchone()
            if state_bound:
                original_context = context_from_bound(state_bound["authority_bound"])
        await self.identity.check_context(context,connection=conn)
        if not set(snapshot["scope_ids"]) <= set(context.scope_ids):
            raise AppError("episode_unavailable",status=404)
        if not all(await self.sources.check_many_on(conn,context,(dependencies,))):
            raise AppError("source_unavailable",status=403)
        # Prove the whole batch under the frozen authorizing scene before any
        # domain row lock; the caller's own visibility was checked above.
        if not await self.sources.validate_persistence(conn,original_context,dependencies):
            raise AppError("source_unavailable",status=403)
        previous_batch = batch_snapshot(batch) if batch else None
        if batch:
            state = await (await conn.execute("SELECT * FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                (batch["partition_id"],))).fetchone()
            batch = await (await conn.execute("SELECT * FROM memory.consolidation_batches WHERE batch_id=%s FOR UPDATE",
                (batch["batch_id"],))).fetchone()
            if not state or not batch or batch_snapshot(batch) != previous_batch or (
                    state_bound is not None and state["authority_bound"] != state_bound["authority_bound"]):
                raise AppError("memory_consolidation_batch_changed",status=409)
        elif state_bound is not None:
            state = await (await conn.execute("SELECT authority_bound FROM memory.consolidation_state WHERE partition_id=%s FOR UPDATE",
                (snapshot["consolidation_partition_id"],))).fetchone()
            if not state or state["authority_bound"] != state_bound["authority_bound"]:
                raise AppError("memory_consolidation_batch_changed",status=409)
        current = await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",(snapshot["episode_id"],))).fetchone()
        if (not current or not current["active"] or current["sequence"] != snapshot["sequence"]
                or current["revision"] != body.get("expected_revision") or current["revision"] != snapshot["revision"]
                or current["source_refs"] != snapshot["source_refs"] or current.get("supplement_refs") != snapshot.get("supplement_refs")
                or current["scope_ids"] != snapshot["scope_ids"] or current["extraction_cursor"] != snapshot["extraction_cursor"]):
            raise AppError("revision_conflict",status=409)
        data = {"episode_id":current["episode_id"],"queued":False,"action":"wait_for_new_evidence",
                "batch_id":batch["batch_id"] if batch else None}
        status=await self.status_on(conn,context,current)
        if status["action"] == "configure_budget":
            data["action"]="configure_budget"
            return CommandResult(body["client_request_id"],revision=current["revision"],data=data)
        if batch and batch["status"] == "superseded":
            await self.memory.consolidation._schedule_on(conn,batch["partition_id"],datetime.now(UTC),
                suffix="reorganization:"+body["client_request_id"])
            data.update(queued=True,action="wait_for_reorganized_input")
        elif batch and batch["status"] in {"prepared","waiting_model","ready","quarantined"}:
            marker = batch["manifest"].get("remote_wait_unresolved")
            if batch["status"] == "quarantined":
                # Observation never advances the independent retry's due time.
                data["retry_due_at"]=wire(batch.get("retry_due_at"))
            local_unknown_profile = bool(marker and marker.get("stage") == "route_profile" and
                (marker.get("transaction_rollback_confirmed") is False
                 or marker.get("last_reobservation",{}).get("transaction_rollback_confirmed") is False))
            if marker and (not marker.get("step_id") or local_unknown_profile):
                # An unconfirmed preparation effect has no original model
                # attempt to observe or safely re-admit.
                data["action"] = "wait_for_reconciliation"
            else:
                await self.memory.consolidation._schedule_on(conn,batch["partition_id"],datetime.now(UTC),
                    suffix="explicit:"+body["client_request_id"],
                    target_batch_id=batch["batch_id"] if batch["status"] == "quarantined"
                        and (marker or batch["manifest"].get("original_observation_only")) else None)
                action = ("reobserve_preparation" if marker and marker.get("stage") == "route_profile"
                    and batch["manifest"].get("profile_pending") else "observe_original_attempt")
                data.update(queued=True,action=action)
        elif batch and batch["status"] == "failed" and batch.get("error_code") in PREPARATION_DEPENDENCY_WAIT:
            if await self.memory.consolidation.waits.reopen_failed_on(conn,batch,original_context,body["client_request_id"]):
                data.update(queued=True,action="resume_original_batch")
        elif rpc_recovery_available(batch):
            if await self.memory.consolidation.retries.resume_rpc_on(conn,batch,original_context,body["client_request_id"]):
                data.update(queued=True,action="observe_original_attempt")
        elif (batch and batch["status"] == "failed"
                and semantic_output_failure(batch)
                and batch["manifest"].get("budget",self.memory.consolidation._budget_record()) == self.memory.consolidation._budget_record()):
            if await self.memory.consolidation.retries.resume_on(conn,batch,original_context,body["client_request_id"]):
                data.update(queued=True,action="retry_computation")
            elif current["extraction_state"] not in {"completed","completed_with_missing"}:
                # Exhausted semantic output cannot escape its finite recovery
                # allowance by creating a fresh batch for unchanged input.
                data["action"]="wait_for_new_evidence"
        elif (batch and batch["status"] == "failed" and batch["error_code"] == "closed_component_exceeds_budget"):
            if (not batch["manifest"].get("original_observation_only")
                    and await self.resume_closed_component_on(conn,batch,body["client_request_id"])):
                data.update(queued=True,action="refresh_material")
            else:
                data["action"]="wait_for_new_evidence"
        elif current["extraction_state"] not in {"completed","completed_with_missing"}:
            await self.identity.check_context(original_context,connection=conn)
            await conn.execute("UPDATE memory.episodes SET extraction_state='pending',extraction_started_at=NULL,"
                "extraction_cursor=extraction_cursor || %s WHERE episode_id=%s",(Jsonb({"protocol":SCHEMA_VERSION}),current["episode_id"]))
            partition_id = await self.memory.consolidation.dirty_on(conn,original_context,current)
            await self.memory.consolidation._schedule_on(conn,partition_id,datetime.now(UTC),suffix="explicit:"+body["client_request_id"])
            data.update(queued=True,action="refresh_material")
        return CommandResult(body["client_request_id"],revision=current["revision"],data=data)

    async def resume_closed_component_on(self,conn,batch,request_id):
        """After a real budget change, restore the whole component under its original lineage."""
        failure = batch["manifest"].get("closed_component_failure") or {}
        original_context = context_from_bound(batch["manifest"]["authority_bound"])
        await self.memory.consolidation._assert_batch_generation_on(conn,batch)
        await self.identity.check_context(original_context,connection=conn)
        if not await self.sources.validate_persistence(conn,original_context,codec.refs(batch["dependency_refs"])):
            raise AppError("source_unavailable",status=403)
        episodes=[]
        for item in sorted(batch["manifest"]["episodes"],key=lambda value:value["episode_id"]):
            row=await (await conn.execute("SELECT * FROM memory.episodes WHERE episode_id=%s FOR UPDATE",
                (item["episode_id"],))).fetchone()
            if (not row or not row["active"] or row["revision"] != item["revision"]
                    or row["sequence"] != item["sequence"] or row["extraction_cursor"] != item["before"]
                    or row["extraction_state"] != "consolidation_failed"):
                raise AppError("memory_consolidation_episode_changed",status=409)
            episodes.append(row)
        if not episodes:
            return False
        await self.memory.claims.revisions.validate_read_set_on(conn,original_context,batch["manifest"]["frozen"])
        inherited = batch["manifest"].get("reorganization",{})
        needs_lineage = failure.get("stage") == "model_output" or bool(inherited)
        if needs_lineage:
            root = inherited.get("root_batch_id") or batch["batch_id"]
            root_row=await (await conn.execute("SELECT manifest FROM memory.consolidation_batches WHERE batch_id=%s",
                (root,))).fetchone()
            recovery=(root_row["manifest"].get("recovery") if root_row else None)
            if not recovery or self.memory.recovery_policy is None:
                return False
            lineage=await (await conn.execute("SELECT * FROM runtime.recovery_lineages WHERE lineage_id=%s FOR UPDATE",
                (recovery["lineage_id"],))).fetchone()
            last=await (await conn.execute("SELECT attempt_id,status FROM runtime.recovery_attempts WHERE lineage_id=%s "
                "ORDER BY attempt_number DESC LIMIT 1",(recovery["lineage_id"],))).fetchone()
            if (not lineage or lineage["owner"] != "memory" or lineage["domain"] != "memory"
                    or lineage["data_generation"] != batch["manifest"]["data_generation"]
                    or lineage["material_digest"] != body_hash(root_row["manifest"]["frozen"])
                    or lineage["current_version"] != recovery["version"] or lineage["paused"]
                    or lineage["cancelled"] or lineage.get("source_withdrawn")
                    or lineage["attempts"] >= lineage["max_attempts"] or not last or last["status"] != "succeeded"
                    or lineage["claim_token"] and lineage["claim_expires_at"]
                        and lineage["claim_expires_at"] > datetime.now(UTC)):
                return False
            if failure.get("stage") == "model_output":
                original=await (await conn.execute("SELECT model_attempt_id FROM memory.consolidation_attempts "
                    "WHERE batch_id=%s ORDER BY number DESC LIMIT 1",(batch["batch_id"],))).fetchone()
                if not original or original["model_attempt_id"] != last["attempt_id"]:
                    return False
            if not inherited:
                counts=await (await conn.execute("SELECT count(*) FILTER (WHERE kind='retry') AS retries "
                    "FROM memory.consolidation_attempts WHERE batch_id=%s",(batch["batch_id"],))).fetchone()
                await conn.execute("INSERT INTO memory.consolidation_reorganizations(root_batch_id,partition_id,"
                    "context_remaining,repair_remaining,retry_remaining) VALUES(%s,%s,%s,%s,%s)",
                    (root,batch["partition_id"],max(0,1-int(bool(batch["manifest"].get("context_round")))),
                     max(0,1-len(batch["manifest"].get("output_repairs",()))),max(0,1-counts["retries"])))
            else:
                changed=await (await conn.execute("UPDATE memory.consolidation_reorganization_parts SET state='superseded' "
                    "WHERE child_batch_id=%s AND state='failed' RETURNING child_batch_id",(batch["batch_id"],))).fetchone()
                if not changed:
                    raise AppError("memory_reorganization_part_changed",status=409)
            await conn.execute("INSERT INTO memory.consolidation_reorganization_parts(parent_batch_id,root_batch_id,ordinal,episodes) "
                "VALUES(%s,%s,1,%s)",(batch["batch_id"],root,Jsonb(batch["manifest"]["episodes"])))
            manifest=deepcopy(batch["manifest"])
            manifest["superseded_by_reorganization"]={"root_batch_id":root,"parts":1,
                "reason":"budget_configuration_changed","request_id":request_id}
            await conn.execute("UPDATE memory.consolidation_batches SET status='superseded',manifest=%s,updated_at=now() "
                "WHERE batch_id=%s",(Jsonb(manifest),batch["batch_id"]))
            await self.memory.record_batch_statistics_on(conn,batch["batch_id"],"superseded")
        for row in episodes:
            await conn.execute("UPDATE memory.episodes SET extraction_state='pending',extraction_started_at=NULL "
                "WHERE episode_id=%s",(row["episode_id"],))
        await self.memory.consolidation.dirty_on(conn,original_context,episodes[0])
        return True
