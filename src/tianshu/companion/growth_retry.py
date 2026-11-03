"""Durable yielding/retry of pure reflection computations on the Growth lane."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import aiohttp

from tianshu.conversation.codec import context_from_record, document
from tianshu.conversation.generation import attempt_for
from tianshu.models.retry import retry_delay_seconds, transient_model_failure
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.serialization import wire
from tianshu.types import RequestBudget
from .growth_recovery import authorization_error, sequence
from .growth_output_repair import (correction_available, corrected_request, output_diagnostic,
                                   review_available, upgrade_request)
from .reflection_output import (CORRECTABLE_ERRORS, IDENTITY_PROTOCOL, OUTPUT_PROTOCOL, REVIEW_ERRORS,
                                frozen_episodes, frozen_material, prepare_reflections, validate_batch_request)


def error_code(error):
    return error.code if isinstance(error, AppError) else type(error).__name__


def later_cursor(left, right):
    if left is None:
        return right
    if right is None:
        return left
    return left if sequence(left) >= sequence(right) else right


def material_time(episodes):
    return max(episode.occurred_at for episode in episodes.values())


def history_entry(batch, code, result=None):
    return {"material_key": batch["material_key"], "attempt_id": attempt_for(batch["material_key"]),
        "attempt_number": batch.get("attempt_number", 1),
        "status": result.receipt.status if result else "unobserved", "error_code": code,
        "observed_at": datetime.now(UTC).isoformat()}


def with_history(history, entry):
    if history and all(history[-1].get(key) == entry.get(key) for key in ("material_key", "status", "error_code")):
        return history
    return [*history, entry]


def manual_retry_allowed(code):
    return bool(code) and not any(marker in code for marker in (
        "source", "cancel", "withdraw",
        "retract", "business_deadline", "episode_revision", "episode_unavailable", "identity_conflict"))


class GrowthRetries:
    def __init__(self, growth):
        self.growth = growth
        self.database = growth.companion.database

    async def schedule_on(self, connection, row):
        await self.growth.jobs.enqueue(connection, JobSpec("companion.growth.retry",
            f"growth-retry:{row['actor_id']}:{row['scope_key']}:{row['retry_key']}:{row['revision']}:{datetime.now(UTC).isoformat()}",
            {"actor_id": row["actor_id"], "scope_key": row["scope_key"], "retry_key": row["retry_key"]},
            scheduled_at=row["due_at"] or datetime.now(UTC), subject_id=row["actor_id"]),
            queueing_lock=f"growth-retry:{row['actor_id']}:{row['scope_key']}:{row['retry_key']}")

    async def defer(self, state, batch, code, result=None, *, diagnostic=None):
        """One CAS moves this owner to the tail; later input never joins its batch."""
        key = batch.get("retry_key", batch["material_key"])
        number = batch.get("attempt_number", 1)
        explicit_cancel = bool(result and getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel")
        if explicit_cancel:
            code = "explicit_cancel"
        correcting = bool(result and result.receipt.status == "succeeded" and diagnostic is not None
                          and correction_available(batch, code))
        retryable = correcting or transient_model_failure(code, cancelled=explicit_cancel)
        due = datetime.now(UTC) + timedelta(seconds=0 if correcting else retry_delay_seconds(number)) if retryable else None
        async with self.database.transaction() as connection:
            current = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"]))).fetchone()
            if (not current or not current["active_batch"]
                    or current["active_batch"]["material_key"] != batch["material_key"]):
                return {"state": "changed_since_original_result_read"}
            await connection.execute(
                "INSERT INTO companion.growth_retry_batches(actor_id,scope_key,retry_key,batch,current_material_key,state) "
                "VALUES(%s,%s,%s,%s,%s,'active') ON CONFLICT DO NOTHING",
                (state["actor_id"], state["scope_key"], key, document(batch), batch["material_key"]))
            row = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
                (state["actor_id"], state["scope_key"], key))).fetchone()
            if row["state"] == "completed" or row["current_material_key"] != batch["material_key"]:
                return {"state": "changed_since_original_result_read"}
            reserved = {**batch, "retry_key": key, "attempt_number": number}
            if correcting:
                reserved.update(output_repair_count=batch.get("output_repair_count", 0)+1,
                    output_repair={"rejected_material_key": batch["material_key"],
                        "diagnostic": output_diagnostic(code, diagnostic), "attempt_id": result.receipt.attempt_id})
            row = await (await connection.execute(
                "UPDATE companion.growth_retry_batches SET batch=%s,state=%s,due_at=%s,last_error=%s,"
                "waiting_on_original=%s,explicit_retry=false,history=%s,revision=revision+1,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
                (document(reserved), "waiting" if retryable else "blocked", due, code, result is None,
                 document(with_history(row["history"], history_entry(reserved, code, result))),
                 state["actor_id"], state["scope_key"], key))).fetchone()
            for identifier, version in batch["inputs"]:
                await connection.execute(
                    "UPDATE companion.growth_inputs SET retry_key=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s AND revision=%s AND reviewed_revision<revision",
                    (key, state["actor_id"], state["scope_key"], identifier, version))
            if batch.get("legacy_material"):
                await connection.execute(
                    "UPDATE companion.growth_legacy_requests SET state='abandoned',error_code=%s,updated_at=now() "
                    "WHERE actor_id=%s AND scope_key=%s AND material_key=%s AND state='pending'",
                    (code, state["actor_id"], state["scope_key"], batch["material_key"]))
            revision = current["revision"] + 1
            await connection.execute(
                "UPDATE companion.organize_state SET active_batch=NULL,blocked_material=NULL,last_error=NULL,"
                "revision=%s,updated_at=now() WHERE actor_id=%s AND scope_key=%s",
                (revision, state["actor_id"], state["scope_key"]))
            if retryable:
                await self.schedule_on(connection, row)
            await self.growth._enqueue(connection, state["actor_id"], state["scope_key"], revision, "failed-batch-yield")
        return {"state": "retry_waiting" if retryable else "retry_blocked", "retry_key": key,
            "error_code": code, "attempt_id": attempt_for(batch["material_key"]), "due_at": due,
            "attempt_number": number}

    async def postpone(self, row, *, code=None, blocked=False, waiting_on_original=True):
        """An unreadable/live original is observed later, never double-submitted."""
        async with self.database.transaction() as connection:
            updated = await (await connection.execute(
                "UPDATE companion.growth_retry_batches SET state=%s,due_at=%s,last_error=COALESCE(%s,last_error),"
                "waiting_on_original=%s,revision=revision+1,updated_at=now() "
                "WHERE actor_id=%s AND scope_key=%s AND retry_key=%s AND revision=%s AND state='waiting' RETURNING *",
                ("blocked" if blocked else "waiting", None if blocked else datetime.now(UTC)+timedelta(seconds=30),
                 code, waiting_on_original and not blocked, row["actor_id"], row["scope_key"], row["retry_key"], row["revision"]))).fetchone()
            if updated and not blocked:
                await self.schedule_on(connection, updated)
        return {"state": "retry_blocked" if blocked else "waiting_original", "error_code": code}

    async def run(self, actor_id, scope_key, retry_key):
        async with self.database.transaction(read_only=True) as connection:
            row = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s",
                (actor_id, scope_key, retry_key))).fetchone()
            state = await (await connection.execute(
                "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s",
                (actor_id, scope_key))).fetchone()
        if not row or not state or row["state"] not in {"waiting", "active"}:
            return
        active = state["active_batch"]
        if active:
            if active["material_key"] == row["current_material_key"]:
                return await self.growth.organize(actor_id, scope_key, state["revision"])
            return await self.postpone(row, waiting_on_original=False)
        if row["state"] == "active":
            # The pointer is transactional with this row. An externally changed
            # owner is not permission to manufacture another execution.
            return {"state": "changed_since_retry_claim"}
        if row["due_at"] and row["due_at"] > datetime.now(UTC):
            async with self.database.transaction() as connection:
                current = await (await connection.execute(
                    "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
                    (actor_id, scope_key, retry_key))).fetchone()
                if current and current["state"] == "waiting":
                    await self.schedule_on(connection, current)
            return
        original = context_from_record(row["batch"]["context"])
        try:
            try:
                await self.growth.companion.identity.check(original)
                context = original
            except AppError as error:
                if not authorization_error(error):
                    raise
                context = await self.growth.recovery.reader(original)
            request = await self.growth.generations.request(attempt_for(row["current_material_key"]), original)
            if request is None:
                return await self.postpone(row, code="reflection_material_contract_invalid", blocked=True)
            result = await self.growth.generations.read(request.attempt_id, context)
            if result and getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel":
                return await self.postpone(row, code="explicit_cancel", blocked=True)
            if result and result.receipt.status in {"queued", "running"}:
                return await self.postpone(row)
            if request.business_deadline_at and request.business_deadline_at <= datetime.now(UTC):
                return await self.postpone(row, code="business_deadline_expired", blocked=True)
            code = (result.receipt.error_code or ("reflection_outcome_unknown" if result.receipt.status == "unknown"
                    else "reflection_generation_" + result.receipt.status)) if result else row["last_error"]
            if (result and result.receipt.status != "succeeded" and not row["explicit_retry"]
                    and not transient_model_failure(code)):
                return await self.postpone(row, code=code, blocked=True)
            episodes = frozen_episodes(request)
            row = {**row, "batch": validate_batch_request(row["batch"], request, episodes)}
            pending_repair = (row["batch"].get("output_repair") or {}).get("rejected_material_key") == row["current_material_key"]
            if pending_repair and (result is None or result.receipt.status != "succeeded"):
                return await self.postpone(row)
            manual_output = row["explicit_retry"] and row["last_error"] in REVIEW_ERRORS
            if manual_output and not pending_repair and not review_available(row["batch"], row["last_error"]):
                return await self.postpone(row, code=row["last_error"], blocked=True)
            output_error = None
            if manual_output and result and result.receipt.status == "succeeded":
                try:
                    prepare_reflections(result, context, episodes, request.source_refs, request=request)
                except AppError as error:
                    output_error = error
                else:
                    # Exact duplicates may now be harmless. Reuse the known
                    # original rather than paying for an unnecessary candidate.
                    manual_output = False
            repeat_rejected_output = pending_repair or manual_output
            successor = result is None or result.receipt.status != "succeeded" or repeat_rejected_output
            if successor:
                # Cancellation is a current Models projection, not an immutable
                # snapshot of the first failure. Re-read immediately before
                # choosing/freezing a replacement computation.
                result = await self.growth.generations.read(request.attempt_id, context)
                if result and getattr(result.receipt, "cancellation_reason", None) == "explicit_cancel":
                    return await self.postpone(row, code="explicit_cancel", blocked=True)
                if result and result.receipt.status in {"queued", "running"}:
                    return await self.postpone(row)
                if result and result.receipt.status == "succeeded" and not repeat_rejected_output:
                    successor = False
                elif result and result.receipt.status != "succeeded" and not row["explicit_retry"]:
                    latest_code = result.receipt.error_code or ("reflection_outcome_unknown"
                        if result.receipt.status == "unknown" else "reflection_generation_" + result.receipt.status)
                    if not transient_model_failure(latest_code):
                        return await self.postpone(row, code=latest_code, blocked=True)
            number = row["attempt_number"] + int(successor)
            key = f"{retry_key}:retry:{number}" if successor else row["current_material_key"]
            candidate = replace(request, intent_id=key, attempt_id=attempt_for(key), context=context,
                budget=RequestBudget(datetime.now(UTC)+timedelta(minutes=3)), execution_timeout_seconds=180.0,
                max_queue_wait=None, provider_session_scope="intent") if successor else request
            repair_update = {}
            if successor and (pending_repair or output_error and output_error.code in CORRECTABLE_ERRORS):
                if not pending_repair and not correction_available(row["batch"], output_error.code):
                    return await self.postpone(row, code=output_error.code, blocked=True)
                diagnostic = (row["batch"]["output_repair"]["diagnostic"] if pending_repair
                              else output_diagnostic(output_error.code, output_error.details))
                candidate = corrected_request(candidate, result, diagnostic, self.growth.assembler.counter)
                repair_update = {"output_repair_count": row["batch"].get("output_repair_count", 0) + int(not pending_repair),
                    "output_repair": {"rejected_material_key": row["current_material_key"],
                        "diagnostic": diagnostic, "attempt_id": request.attempt_id}}
            elif successor and manual_output:
                candidate = upgrade_request(candidate)
            activated = None

            async def claim(connection):
                nonlocal activated
                current = await (await connection.execute(
                    "SELECT * FROM companion.organize_state WHERE actor_id=%s AND scope_key=%s FOR UPDATE",
                    (actor_id, scope_key))).fetchone()
                saved = await (await connection.execute(
                    "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
                    (actor_id, scope_key, retry_key))).fetchone()
                if (not current or not saved or current["active_batch"] or saved["revision"] != row["revision"]
                        or saved["state"] != "waiting" or saved["current_material_key"] != row["current_material_key"]):
                    return False
                for identifier, version in row["batch"]["inputs"]:
                    reserved = await (await connection.execute(
                        "SELECT revision,reviewed_revision,retry_key FROM companion.growth_inputs "
                        "WHERE actor_id=%s AND scope_key=%s AND episode_id=%s FOR UPDATE",
                        (actor_id, scope_key, identifier))).fetchone()
                    if (not reserved or reserved["revision"] != version or reserved["reviewed_revision"] >= version
                            or reserved["retry_key"] != retry_key):
                        raise AppError("reflection_episode_revision_conflict", status=409)
                batch = {**row["batch"], "material_key": key, "context": wire(candidate.context),
                    "start_cursor": current["cursor"], "end_cursor": later_cursor(current["cursor"], row["batch"]["end_cursor"]),
                    "watermark": current["watermark"], "attempt_number": number,
                    "reflection_identity_protocol": IDENTITY_PROTOCOL, "material_at": material_time(episodes).isoformat(),
                    **repair_update}
                payload = frozen_material(candidate)
                if payload.get("model_protocol") == OUTPUT_PROTOCOL:
                    batch.update(reflection_output_protocol=OUTPUT_PROTOCOL, episode_refs=payload["episode_refs"])
                if successor:
                    batch.pop("legacy_material", None)
                    batch.pop("identity_conversion_key", None)
                elif batch.get("legacy_material"):
                    # Yielding excluded this original from the legacy scanner.
                    # The exact reserved original can re-enter identity conversion
                    # only after current source/version checks and this owner CAS.
                    await connection.execute(
                        "UPDATE companion.growth_legacy_requests SET state='pending',error_code=NULL,updated_at=now() "
                        "WHERE actor_id=%s AND scope_key=%s AND material_key=%s AND state='abandoned'",
                        (actor_id, scope_key, key))
                await connection.execute(
                    "UPDATE companion.growth_retry_batches SET state='active',batch=%s,current_material_key=%s,"
                    "previous_material_key=%s,attempt_number=%s,due_at=NULL,explicit_retry=false,waiting_on_original=false,"
                    "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s",
                    (document(batch), key, row["current_material_key"] if successor else row["previous_material_key"],
                     number, actor_id, scope_key, retry_key))
                activated = await (await connection.execute(
                    "UPDATE companion.organize_state SET active_batch=%s,blocked_material=%s,last_error=NULL,"
                    "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s RETURNING *",
                    (document(batch), None if successor else document({"recovery": "observe_original_attempt"}),
                     actor_id, scope_key))).fetchone()
                return True

            async with self.database.transaction() as connection:
                await self.growth._validate_material(connection, context, episodes, request.source_refs)
                if successor:
                    await self.growth.generations.prepare(key, candidate, connection=connection, guard=claim)
                else:
                    await claim(connection)
            if activated is None:
                return await self.postpone(row, waiting_on_original=False)
            return await self.growth.organize(actor_id, scope_key, activated["revision"])
        except (AppError, TimeoutError, aiohttp.ClientError) as error:
            if isinstance(error, AppError) and error.code == "generation_no_longer_current":
                return await self.postpone(row, waiting_on_original=False)
            code = error_code(error)
            return await self.postpone(row, code=code, blocked=not transient_model_failure(code))

    async def complete_on(self, connection, current, batch, *, history_only=False):
        if not batch.get("retry_key"):
            return
        entry = {**history_entry(batch, None), "status": "succeeded"}
        row = await (await connection.execute(
            "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
            (current["actor_id"], current["scope_key"], batch["retry_key"]))).fetchone()
        if not row or row["state"] != "active" or row["current_material_key"] != batch["material_key"]:
            raise AppError("generation_no_longer_current", status=409)
        await connection.execute(
            "UPDATE companion.growth_retry_batches SET state='completed',due_at=NULL,last_error=NULL,history=%s,"
            "outcome=%s,revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s",
            (document(with_history(row["history"], entry)), "historical_only" if history_only else "applied",
             row["actor_id"], row["scope_key"], row["retry_key"]))

    async def resume_on(self, connection, current, caller, retry_key):
        row = await (await connection.execute(
            "SELECT * FROM companion.growth_retry_batches WHERE actor_id=%s AND scope_key=%s AND retry_key=%s FOR UPDATE",
            (current["actor_id"], current["scope_key"], retry_key))).fetchone()
        if (not row or row["state"] != "blocked" or not manual_retry_allowed(row["last_error"])
                or not review_available(row["batch"], row["last_error"])):
            raise AppError("reflection_retry_not_available", status=409)
        if not self.growth.recovery.same_scene(caller, context_from_record(row["batch"]["context"])):
            raise AppError("reflection_recovery_context_mismatch", status=403)
        updated = await (await connection.execute(
            "UPDATE companion.growth_retry_batches SET state='waiting',due_at=now(),explicit_retry=true,"
            "revision=revision+1,updated_at=now() WHERE actor_id=%s AND scope_key=%s AND retry_key=%s RETURNING *",
            (row["actor_id"], row["scope_key"], retry_key))).fetchone()
        revision = current["revision"] + 1
        await connection.execute(
            "UPDATE companion.organize_state SET revision=%s,updated_at=now() WHERE actor_id=%s AND scope_key=%s",
            (revision, current["actor_id"], current["scope_key"]))
        await self.schedule_on(connection, updated)
        return revision

    async def blocked_for_scene(self, connection, state, caller):
        rows = await (await connection.execute(
            "SELECT retry_key,batch->'context' AS context,batch->'output_repair_count' AS output_repair_count,"
            "last_error FROM companion.growth_retry_batches "
            "WHERE actor_id=%s AND scope_key=%s AND state='blocked' ORDER BY created_at,retry_key",
            (state["actor_id"], state["scope_key"]))).fetchall()
        return next((row["retry_key"] for row in rows if manual_retry_allowed(row["last_error"])
            and review_available({"output_repair_count": row.get("output_repair_count") or 0}, row["last_error"])
            and self.growth.recovery.same_scene(caller, context_from_record(row["context"]))), None)

    async def recover(self):
        async with self.database.transaction() as connection:
            rows = await (await connection.execute(
                "SELECT * FROM companion.growth_retry_batches WHERE state IN ('waiting','active') "
                "ORDER BY actor_id,scope_key,retry_key FOR UPDATE")).fetchall()
            for row in rows:
                await self.schedule_on(connection, row)
