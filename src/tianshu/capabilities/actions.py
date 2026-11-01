"""The sole durable Action, Attempt and Receipt owner for every capability."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from jsonschema import Draft202012Validator, ValidationError
from tianshu.types import Action, Attempt, Receipt, ServiceClass
from tianshu.sources.dependencies import unique_refs
from tianshu.runtime.jobs import JobSpec
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.serialization import wire
from .contracts import Fault, digest, canonical
from .serialization import action_from, attempt_from, receipt_from
from .execution_context import CURRENT_ACTION, CURRENT_EXECUTION
from .external import execution_service_class
from .routing import execution_lane


class Actions:
    def __init__(self, database, jobs, catalog, authority, events=None):
        self.database, self.jobs, self.catalog, self.authority, self.events = (
            database,
            jobs,
            catalog,
            authority,
            events,
        )
        self.running = {}
        self.prepare_handler = None
        self.argument_resolver = None
        self.worker = False
        self.sources = None
        from .execution_owners import ExecutionOwners

        self.owners = ExecutionOwners(self)

    async def start(self):
        await self.owners.start()

    async def close(self):
        await self.owners.close()

    def status(self):
        return self.owners.status()

    async def handler(self, action):
        if action.capability_id not in self.catalog.handlers and self.prepare_handler:
            await self.prepare_handler(action)
        return self.catalog.handler(action.capability_id)

    async def invoke(self, action: Action, *, background=False, service_class=None):
        handler = await self.handler(action)
        service_class = service_class or execution_service_class(action)
        lane = execution_lane(action.capability_id)
        if getattr(handler, "requires_worker", False) and not self.worker:
            background = True
        if action.actor_id != action.context.actor_id:
            raise Fault("actor_mismatch", 403)
        if (
            "*" not in action.context.permissions
            and not handler.definition.permissions <= action.context.permissions
        ):
            raise Fault("forbidden", 403)
        authorized_refs = {canonical(wire(ref)) for ref in action.source_refs}
        if any(canonical(wire(ref)) not in authorized_refs for ref in action.current_input_refs):
            raise Fault("current_input_source_not_authorized", 403)
        await self.authority.check(action.context, action.source_refs)
        availability = await self.catalog.availability(action.context, handler)
        if not availability.can_execute:
            raise Fault(availability.reason or "not_configured", 503)
        arguments = (
            await self.argument_resolver(action.context, action.arguments)
            if "command_ref" in action.arguments and self.argument_resolver
            else action.arguments
        )
        try:
            Draft202012Validator(handler.definition.input_schema).validate(arguments)
        except ValidationError:
            raise Fault("invalid_input", 400) from None
        key = digest([action.context.principal_id, action.capability_id, action.intent_key])
        semantics = digest(wire(replace(action, action_id="", revision=1)))
        attempt = Attempt(str(uuid4()), action.action_id)
        receipt = Receipt(
            str(uuid4()), action.action_id, attempt.attempt_id, "pending", "none", "not_requested"
        )
        async with self.database.transaction() as conn:
            from .withdrawal import validate_sources, depend

            if not await validate_sources(conn, action.context, action.source_refs, self.sources):
                raise Fault("source_revoked", 410)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            old = await (
                await conn.execute(
                    "SELECT action,receipt,semantics FROM capabilities.actions WHERE intent=%s FOR UPDATE",
                    (key,),
                )
            ).fetchone()
            if old:
                if old["semantics"] != semantics:
                    raise Fault("idempotency_conflict", 409)
                receipt = receipt_from(old["receipt"])
            else:
                await depend(conn, "action", action.action_id, action.source_refs)
                await conn.execute(
                    "INSERT INTO capabilities.actions(id,intent,semantics,principal_id,kind,created_at,action,attempt,receipt,document) VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s)",
                    (
                        action.action_id,
                        key,
                        semantics,
                        action.context.principal_id,
                        action.capability_id,
                        datetime.now(UTC).timestamp(),
                        encoded(action),
                        encoded(attempt),
                        encoded(receipt),
                        canonical({"service_class": service_class.value, "execution_lane": lane.value,
                                   "job_key": action.action_id if background else None}),
                    ),
                )
                if background:
                    await self.jobs.enqueue(
                        conn,
                        JobSpec(
                            "capabilities.execute",
                            action.action_id,
                            {"action_id": action.action_id},
                            service_class=service_class,
                            subject_id=action.actor_id,
                            matter_id=action.matter_id,
                            execution_lane=lane,
                        ),
                    )
        if old:
            await self.authority.check(action.context, result_sources(receipt))
            return receipt
        return receipt if background else await self.run(action.action_id)

    async def find(self, capability_id, client_request_id, context):
        if (
            not isinstance(capability_id, str)
            or not 1 <= len(capability_id) <= 512
            or not isinstance(client_request_id, str)
            or not 1 <= len(client_request_id) <= 128
        ):
            raise Fault("invalid_input", 400)
        key = digest([context.principal_id, capability_id, client_request_id])
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT id FROM capabilities.actions WHERE intent=%s AND principal_id=%s",
                    (key, context.principal_id),
                )
            ).fetchone()
        return await self.get(row["id"], context) if row else None

    async def get(self, action_id, context):
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT * FROM capabilities.actions WHERE id=%s AND principal_id=%s",
                    (action_id, context.principal_id),
                )
            ).fetchone()
        if row is None:
            raise Fault("not_found", 404)
        action = action_from(row["action"])
        await self.authority.check(context, action.source_refs)
        if action.context.actor_id != context.actor_id or not set(action.context.scope_ids) <= set(
            context.scope_ids
        ):
            raise Fault("forbidden", 403)
        await self.authority.check(context, result_sources(receipt_from(row["receipt"])))
        async with self.database.transaction(read_only=True) as conn:
            execution = await self._execution_on(conn, row)
        result = {
            k: row[k]
            for k in ("action", "attempt", "receipt", "result_revision", "result_observed_at")
        }
        return {**result, "execution": execution}

    async def _execution_on(self, connection, row):
        document = json.loads(row["document"])
        key = document.get("job_key")
        observation = None
        keys = (key,) if key else (
            row["id"] + ":resume:" + row["attempt"]["attempt_id"], row["id"],
        )
        for candidate in keys:
            observation = await self.jobs.observe_on(connection, key=candidate)
            if observation is not None:
                break
        return {
            "execution_lane": document.get("execution_lane") or execution_lane(row["kind"]).value,
            "service_class": document.get("service_class", ServiceClass.ELASTIC.value),
            "wait_reason": None,
            **(observation or {}),
        }

    async def execution_lane_on(self, connection, payload):
        row = await (await connection.execute(
            "SELECT kind FROM capabilities.actions WHERE id=%s", (payload["action_id"],)
        )).fetchone()
        # An orphan job still reaches the existing not_found outcome in its own lane.
        return execution_lane(row["kind"]) if row else ExecutionLane.MAINTENANCE

    async def _load(self, action_id):
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT action,attempt,receipt FROM capabilities.actions WHERE id=%s",
                    (action_id,),
                )
            ).fetchone()
        if row is None:
            raise Fault("not_found", 404)
        return (
            action_from(row["action"]),
            attempt_from(row["attempt"]),
            receipt_from(row["receipt"]),
        )

    async def load_for_execution(self, action_id):
        """Trusted registered executors rehydrate the one original ledger identity."""
        return await self._load(action_id)

    async def submitted(self, attempt, handle=None, dispatch="submitted"):
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE capabilities.actions SET attempt=attempt||%s::jsonb WHERE id=%s AND attempt->>'attempt_id'=%s",
                (
                    canonical({"dispatch": dispatch, "provider_handle": handle}),
                    attempt.action_id,
                    attempt.attempt_id,
                ),
            )

    async def run(self, action_id, mode="execute"):
        action, attempt, current = await self._load(action_id)
        if current.execution != "pending":
            return current
        await self.owners.start()
        await self.owners.check()
        attempt = replace(attempt, started_at=datetime.now(UTC))
        running = replace(current, receipt_id=str(uuid4()), execution="running")
        async with self.database.transaction() as conn:
            claim = await (
                await conn.execute(
                    "UPDATE capabilities.actions SET receipt=%s::jsonb,attempt=%s::jsonb,execution_owner=%s,"
                    "result_revision=result_revision+1,result_observed_at=now() WHERE id=%s "
                    "AND receipt->>'execution'='pending' AND EXISTS(SELECT 1 FROM capabilities.execution_owners "
                    "WHERE id=%s AND expires_at>now()) RETURNING document",
                    (encoded(running), encoded(attempt), self.owners.id, action_id, self.owners.id),
                )
            ).fetchone()
        if claim is None:
            return (await self._load(action_id))[2]
        state = {
            "owner": self,
            "attempt": attempt,
            "submitted": False,
            "service_class": ServiceClass(
                json.loads(claim["document"]).get("service_class", "elastic")
            ),
        }
        action_token = CURRENT_ACTION.set(action)
        execution_token = CURRENT_EXECUTION.set(state)
        self.running[action_id] = asyncio.current_task()
        watcher = asyncio.create_task(self.watch_cancel(action_id, self.running[action_id]))
        handler = None
        try:
            timeout = None
            if action.deadline_at:
                timeout = max(0, (action.deadline_at - datetime.now(UTC)).total_seconds())
            async with asyncio.timeout(timeout) as deadline:
                handler = await self.handler(action)
                await self.authority.check(action.context, action.source_refs)
                availability = await self.catalog.availability(action.context, handler)
                if not availability.can_execute:
                    raise Fault(availability.reason or "capability_unavailable", 503)
                await self.owners.check()
                method = getattr(handler, mode, None)
                if mode not in {"execute", "resume"} or method is None:
                    raise Fault("operation_unavailable", 409)
                receipt = await method(action, attempt)
                if not isinstance(receipt, Receipt):
                    raise TypeError("capability_receipt_required")
        except TimeoutError:
            receipt = await self.interrupted(
                action,
                attempt,
                handler,
                "deadline_expired" if deadline.expired() else "capability_timeout",
                explicit=deadline.expired(),
            )
        except asyncio.CancelledError:
            async with self.database.transaction(control=True, read_only=True) as conn:
                row = await (
                    await conn.execute(
                        "SELECT document FROM capabilities.actions WHERE id=%s", (action_id,)
                    )
                ).fetchone()
            explicit = bool(row and json.loads(row["document"]).get("cancel_requested"))
            receipt = await asyncio.shield(
                self.interrupted(action, attempt, handler, "interrupted", explicit=explicit)
            )
            await asyncio.shield(self.save(receipt, attempt))
            raise
        except Exception as error:
            persisted = (await self._load(action_id))[1]
            submitted = state["submitted"] or persisted.dispatch in {"submitted", "unknown"}
            unknown = getattr(error, "effect", None) == "unknown" or (
                submitted and not hasattr(error, "effect")
            )
            receipt = Receipt(
                str(uuid4()),
                action_id,
                attempt.attempt_id,
                "unknown" if unknown else "failed",
                "unknown" if unknown else "none",
                "not_requested",
                error_code=getattr(error, "code", "capability_failed"),
            )
        finally:
            watcher.cancel()
            await asyncio.gather(watcher, return_exceptions=True)
            self.running.pop(action_id, None)
            CURRENT_ACTION.reset(action_token)
            CURRENT_EXECUTION.reset(execution_token)
        return await self.save(receipt, attempt)

    async def interrupted(self, action, attempt, handler, code, *, explicit=False):
        _, persisted, _ = await self._load(action.action_id)
        submitted = persisted.dispatch in {"submitted", "unknown"}
        receipt = Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown" if submitted else "cancelled",
            "unknown" if submitted else "none",
            "not_requested",
            error_code=code,
        )
        if explicit and submitted and handler is not None:
            try:
                async with asyncio.timeout(15):
                    receipt = await handler.cancel(action, persisted)
            except Exception:
                pass
            receipt = replace(
                receipt,
                result={**receipt.result, "provider_cancel_attempted": True},
                error_code=receipt.error_code or code,
            )
        return receipt

    async def recover_orphan(self, action_id, attempt_id, previous_owner):
        action, attempt, current = await self._load(action_id)
        if attempt.attempt_id != attempt_id or current.execution != "running":
            return current
        async with self.database.transaction() as conn:
            from .withdrawal import validate_sources

            # Keep the same source→domain lock order as all terminal writes.
            valid = await validate_sources(conn, action.context, action.source_refs, self.sources)
            claimed = await (
                await conn.execute(
                    "UPDATE capabilities.actions SET execution_owner=%s WHERE id=%s AND execution_owner "
                    "IS NOT DISTINCT FROM %s AND attempt->>'attempt_id'=%s AND receipt->>'execution'='running' "
                    "AND NOT EXISTS(SELECT 1 FROM capabilities.execution_owners WHERE id=%s AND expires_at>now()) RETURNING id",
                    (self.owners.id, action_id, previous_owner, attempt_id, previous_owner),
                )
            ).fetchone()
        if not claimed:
            return (await self._load(action_id))[2]
        orphan = Receipt(
            str(uuid4()),
            action_id,
            attempt_id,
            "unknown",
            "unknown",
            "not_requested",
            error_code="execution_owner_lost",
        )
        if not valid:
            from .withdrawal import redacted

            orphan = redacted(orphan)
        receipt = await self.save(orphan, attempt)
        if attempt.provider_handle is not None:
            async with self.database.transaction() as conn:
                await self.jobs.enqueue(
                    conn,
                    JobSpec(
                        "capabilities.control",
                        action_id + ":recover:" + attempt_id,
                        {"action_id": action_id, "mode": "reconcile"},
                        service_class=ServiceClass.INTERACTIVE,
                        subject_id=action.actor_id,
                    ),
                )
        return receipt

    async def execution_class(self, action_id):
        async with self.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT document FROM capabilities.actions WHERE id=%s", (action_id,)
                )
            ).fetchone()
        if not row:
            raise Fault("not_found", 404)
        return ServiceClass(json.loads(row["document"]).get("service_class", "elastic"))

    async def watch_cancel(self, action_id, owner):
        """Transient control observation for the actual task, including nested foreground work."""
        while True:
            await asyncio.sleep(1)
            async with self.database.transaction(control=True, read_only=True) as conn:
                row = await (
                    await conn.execute(
                        "SELECT document FROM capabilities.actions WHERE id=%s", (action_id,)
                    )
                ).fetchone()
            if row and json.loads(row["document"]).get("cancel_requested"):
                owner.cancel()
                return

    async def save(self, receipt, attempt):
        receipt = replace(
            receipt, result={**receipt.result, "source_refs": wire(result_sources(receipt))}
        )
        async with self.database.transaction() as conn:
            from .withdrawal import validate_sources, depend

            snapshot = await (
                await conn.execute(
                    "SELECT action FROM capabilities.actions WHERE id=%s", (receipt.action_id,)
                )
            ).fetchone()
            if snapshot is None:
                raise Fault("not_found", 404)
            original = action_from(snapshot["action"])
            refs = unique_refs(original.source_refs, result_sources(receipt))
            valid = await validate_sources(conn, original.context, refs, self.sources)
            old = await (
                await conn.execute(
                    "SELECT attempt,document,receipt,action,result_revision,result_observed_at FROM capabilities.actions WHERE id=%s FOR UPDATE",
                    (receipt.action_id,),
                )
            ).fetchone()
            if not old or old["attempt"]["attempt_id"] != attempt.attempt_id:
                raise Fault("attempt_changed", 409)
            previous_receipt = receipt_from(old["receipt"])
            source_invalid = not valid
            if not valid or json.loads(old["document"]).get("source_revoked"):
                from .withdrawal import redacted

                receipt = redacted(receipt)
                source_invalid = True
            cancel_requested = json.loads(old["document"]).get("cancel_requested")
            if (
                previous_receipt.execution in {"succeeded", "failed", "cancelled"}
                and previous_receipt.effect != "unknown"
                and receipt.execution != "succeeded"
                and not source_invalid
            ):
                if not cancel_requested:
                    return previous_receipt
                receipt = replace(previous_receipt, receipt_id=str(uuid4()))
            persisted = attempt_from(old["attempt"])
            attempt = replace(
                persisted,
                finished_at=datetime.now(UTC)
                if receipt.execution not in {"pending", "running"}
                else None,
            )
            if json.loads(old["document"]).get("cancel_requested"):
                receipt = replace(
                    receipt,
                    result={
                        **receipt.result,
                        "cancel_requested": True,
                        "sharing": {"decision": "archive", "reason": "cancel_requested"},
                    },
                )
            previous = dict(old["receipt"])
            current = wire(receipt)
            if {k: v for k, v in previous.items() if k != "receipt_id"} == {
                k: v for k, v in current.items() if k != "receipt_id"
            }:
                receipt = replace(receipt, receipt_id=previous["receipt_id"])
            changed = old["receipt"] != wire(receipt)
            revision = old["result_revision"] + int(changed)
            observed_at = datetime.now(UTC) if changed else old["result_observed_at"]
            await depend(conn, "action", receipt.action_id, refs)
            await conn.execute(
                "UPDATE capabilities.actions SET receipt=%s::jsonb,attempt=%s::jsonb,result_revision=%s,result_observed_at=%s,execution_owner=NULL WHERE id=%s",
                (encoded(receipt), encoded(attempt), revision, observed_at, receipt.action_id),
            )
            await conn.execute(
                "INSERT INTO capabilities.attempts(id,action_id,attempt,receipt) VALUES(%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT(id) DO UPDATE SET attempt=excluded.attempt,receipt=excluded.receipt",
                (attempt.attempt_id, receipt.action_id, encoded(attempt), encoded(receipt)),
            )
            if (
                self.events
                and receipt.execution in {"succeeded", "failed", "cancelled", "unknown"}
                and old["receipt"] != wire(receipt)
            ):
                await self.events.publish(
                    conn,
                    {
                        "type": "capability.result",
                        "version": 1,
                        "event_id": receipt.receipt_id,
                        "action_id": receipt.action_id,
                        "result_revision": revision,
                        "result_observed_at": observed_at.isoformat() if observed_at else None,
                        "context": old["action"]["context"],
                        "source_refs": old["action"].get("source_refs", []),
                        "receipt": wire(receipt),
                    },
                )
        return receipt

    async def reconcile(self, action_id, context):
        await self.get(action_id, context)
        action, attempt, current = await self._load(action_id)
        if (
            current.execution in {"succeeded", "failed", "cancelled"}
            and current.effect != "unknown"
        ):
            return current
        handler = await self.handler(action)
        if getattr(handler, "requires_worker", False) and not self.worker:
            return await self.enqueue_control(action, attempt, current, "reconcile")
        return await self.save(await handler.reconcile(action, attempt), attempt)

    async def cancel(self, action_id, context):
        await self.get(action_id, context)
        action, attempt, current = await self._load(action_id)
        if (
            current.execution in {"succeeded", "failed", "cancelled"}
            and current.effect != "unknown"
        ):
            return current
        async with self.database.transaction() as conn:
            await conn.execute(
                "UPDATE capabilities.actions SET document=(document::jsonb||%s::jsonb)::text WHERE id=%s",
                (canonical({"cancel_requested": True}), action_id),
            )
            await self.jobs.cancel(conn, action_id)
        task = self.running.get(action_id)
        if task and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            action, attempt, current = await self._load(action_id)
            # An explicit cancel must reach the provider's original handle. An
            # interrupted transport alone does not prove that the effect stopped.
            handler = await self.handler(action)
            if attempt.dispatch != "not_started" and not current.result.get(
                "provider_cancel_attempted"
            ):
                return await self.save(await handler.cancel(action, attempt), attempt)
            return current
        if current.execution == "pending":
            receipt = Receipt(
                str(uuid4()), action_id, attempt.attempt_id, "cancelled", "none", "not_requested"
            )
        elif current.execution == "running":
            receipt = replace(current, result={**current.result, "cancel_requested": True})
        else:
            handler = await self.handler(action)
            if getattr(handler, "requires_worker", False) and not self.worker:
                return await self.enqueue_control(action, attempt, current, "cancel")
            else:
                receipt = await handler.cancel(action, attempt)
        return await self.save(receipt, attempt)

    async def reroute_pending(self, action_id, context, *, client_request_id,
                              expected_attempt_id, expected_job_id):
        """Move an accepted, never-started job without issuing another attempt."""
        if not client_request_id:
            raise Fault("client_request_id_required", 400)
        await self.get(action_id, context)
        action, _, _ = await self._load(action_id)
        await self.authority.check(action.context, action.source_refs)
        service_class = execution_service_class(context)
        lane = execution_lane(action.capability_id)
        key = digest([context.principal_id, action_id, "resume", client_request_id])
        async with self.database.transaction() as conn:
            from .withdrawal import validate_sources

            if not await validate_sources(conn, action.context, action.source_refs, self.sources):
                raise Fault("source_revoked", 410)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            row = await (await conn.execute(
                "SELECT * FROM capabilities.actions WHERE id=%s AND principal_id=%s FOR UPDATE",
                (action_id, context.principal_id),
            )).fetchone()
            if row is None:
                raise Fault("not_found", 404)
            prior = await (await conn.execute(
                "SELECT attempt_id FROM capabilities.action_controls WHERE id=%s", (key,)
            )).fetchone()
            if prior:
                if expected_attempt_id is not None and prior["attempt_id"] != expected_attempt_id:
                    raise Fault("idempotency_conflict", 409)
                saved = await (await conn.execute(
                    "SELECT receipt FROM capabilities.attempts WHERE id=%s", (prior["attempt_id"],)
                )).fetchone()
                return receipt_from(saved["receipt"] if saved else row["receipt"])
            if not expected_attempt_id or type(expected_job_id) is not int:
                raise Fault("pending_reroute_identity_required", 400)
            if row["attempt"]["attempt_id"] != expected_attempt_id:
                raise Fault("attempt_changed", 409)
            attempt = attempt_from(row["attempt"])
            receipt = receipt_from(row["receipt"])
            document = json.loads(row["document"])
            if (receipt.execution != "pending" or attempt.dispatch != "not_started"
                    or attempt.provider_handle is not None or attempt.started_at is not None
                    or attempt.finished_at is not None or row.get("execution_owner") is not None):
                raise Fault("action_already_started", 409)
            if document.get("source_revoked") or document.get("cancel_requested"):
                raise Fault("pending_reroute_unavailable", 409)
            observation = await self._execution_on(conn, row)
            if observation.get("job_id") != expected_job_id:
                raise Fault("job_changed", 409)
            moved = await self.jobs.reroute_pending(
                conn, expected_job_id, execution_lane=lane,
                task="capabilities.execute", service_class=service_class,
            )
            if not moved:
                raise Fault("action_already_started", 409)
            document.update(service_class=service_class.value, execution_lane=lane.value)
            await conn.execute(
                "UPDATE capabilities.actions SET document=%s WHERE id=%s",
                (canonical(document), action_id),
            )
            await conn.execute(
                "INSERT INTO capabilities.action_controls(id,action_id,attempt_id) VALUES(%s,%s,%s)",
                (key, action_id, attempt.attempt_id),
            )
        return receipt

    async def resume(self, action_id, context, *, client_request_id,
                     expected_attempt_id=None, expected_job_id=None):
        if not client_request_id:
            raise Fault("client_request_id_required", 400)
        await self.get(action_id, context)
        action, old, current = await self._load(action_id)
        if current.execution == "pending" or expected_job_id is not None or expected_attempt_id is not None:
            return await self.reroute_pending(
                action_id, context, client_request_id=client_request_id,
                expected_attempt_id=expected_attempt_id, expected_job_id=expected_job_id,
            )
        handler = await self.handler(action)
        if not hasattr(handler, "resume"):
            raise Fault("resume_unavailable", 409)
        service_class = execution_service_class(context)
        lane = execution_lane(action.capability_id)
        key = digest([context.principal_id, action_id, "resume", client_request_id])
        async with self.database.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            prior = await (
                await conn.execute(
                    "SELECT attempt_id FROM capabilities.action_controls WHERE id=%s", (key,)
                )
            ).fetchone()
            if prior:
                row = await (
                    await conn.execute(
                        "SELECT receipt FROM capabilities.attempts WHERE id=%s",
                        (prior["attempt_id"],),
                    )
                ).fetchone()
                if row:
                    return receipt_from(row["receipt"])
                row = await (
                    await conn.execute(
                        "SELECT receipt FROM capabilities.actions WHERE id=%s", (action_id,)
                    )
                ).fetchone()
                return receipt_from(row["receipt"])
            if current.execution not in {"unknown", "failed", "cancelled"}:
                raise Fault("resume_state_invalid", 409)
            attempt = Attempt(str(uuid4()), action_id)
            receipt = Receipt(
                str(uuid4()), action_id, attempt.attempt_id, "pending", "none", "not_requested"
            )
            claimed = await (
                await conn.execute(
                    "UPDATE capabilities.actions SET attempt=%s::jsonb,receipt=%s::jsonb,"
                    "document=((document::jsonb-'cancel_requested') || %s::jsonb)::text,"
                    "result_revision=result_revision+1,result_observed_at=now() WHERE id=%s "
                    "AND attempt->>'attempt_id'=%s AND NOT coalesce((document::jsonb->>'source_revoked')::boolean,false) RETURNING id",
                    (encoded(attempt), encoded(receipt), canonical({"service_class": service_class.value,
                     "execution_lane": lane.value, "job_key": action_id + ":resume:" + attempt.attempt_id}),
                     action_id, old.attempt_id),
                )
            ).fetchone()
            if not claimed:
                raise Fault("attempt_changed", 409)
            await conn.execute(
                "INSERT INTO capabilities.action_controls(id,action_id,attempt_id) VALUES(%s,%s,%s)",
                (key, action_id, attempt.attempt_id),
            )
            await conn.execute(
                "INSERT INTO capabilities.attempts(id,action_id,attempt,receipt) VALUES(%s,%s,%s::jsonb,%s::jsonb)",
                (attempt.attempt_id, action_id, encoded(attempt), encoded(receipt)),
            )
            await self.jobs.enqueue(
                conn,
                JobSpec(
                    "capabilities.execute",
                    action_id + ":resume:" + attempt.attempt_id,
                    {"action_id": action_id, "mode": "resume"},
                    service_class=service_class,
                    subject_id=action.actor_id,
                    matter_id=action.matter_id,
                    execution_lane=lane,
                ),
            )
        return receipt

    async def enqueue_control(self, action, attempt, current, mode):
        async with self.database.transaction() as conn:
            await self.jobs.enqueue(
                conn,
                JobSpec(
                    "capabilities.control",
                    action.action_id + ":" + mode + ":" + str(uuid4()),
                    {"action_id": action.action_id, "mode": mode},
                    service_class=ServiceClass.INTERACTIVE,
                    subject_id=action.actor_id,
                ),
            )
        return replace(current, result={**current.result, mode + "_requested": True})

    async def control(self, action_id, mode):
        action, attempt, current = await self._load(action_id)
        await self.authority.check(action.context, action.source_refs)
        if mode == "reconcile":
            return await self.reconcile(action_id, action.context)
        if mode == "cancel":
            return await self.cancel(action_id, action.context)
        raise Fault("invalid_control", 400)

    def register_tasks(self):
        self.jobs.register("capabilities.execute", self.run,
                           lane_resolver=self.execution_lane_on)
        self.jobs.register("capabilities.control", self.control, execution_lane=ExecutionLane.CONTROL)


def encoded(value):
    return canonical(wire(value))


digest_json = encoded


def result_sources(receipt):
    from tianshu.types import SourceRef

    refs = [ref for artifact in receipt.artifacts for ref in artifact.source_refs]
    refs.extend(SourceRef(**value) for value in receipt.result.get("source_refs", []))
    for value in (
        receipt.result.get("source"),
        receipt.result.get("consumption", {}).get("source"),
    ):
        if isinstance(value, dict) and "source_id" in value:
            refs.append(SourceRef(**value))
    return unique_refs(refs)
