"""The sole durable Action, Attempt and Receipt owner for every capability."""

import asyncio
import json
import logging
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from jsonschema import Draft202012Validator, ValidationError
from tianshu.types import Action, Attempt, Receipt, ServiceClass
from tianshu.sources.dependencies import unique_refs
from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import JobSpec, current_execution_generation
from tianshu.runtime.execution import ExecutionLane
from tianshu.runtime.serialization import wire
from .contracts import Fault, digest, canonical
from .serialization import action_from, attempt_from, receipt_from
from .execution_context import CURRENT_ACTION, CURRENT_EXECUTION
from .external import execution_service_class
from .routing import execution_lane

logger = logging.getLogger(__name__)
_RESULT_HEAD_READ_LIMIT = 3


def _result_event(action, receipt, revision, observed_at):
    """Build the existing result envelope from the persisted Action head."""
    actual = wire(receipt)
    return {"type": "capability.result", "version": 1, "event_id": actual["receipt_id"],
            "action_id": actual["action_id"], "result_revision": revision,
            "result_observed_at": observed_at.isoformat() if observed_at else None,
            "context": action["context"], "source_refs": action.get("source_refs", []),
            "receipt": actual}


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
        self.process_role = None
        self.sources = None
        self.behavior_validator = None
        self.statistics = None
        from .execution_owners import ExecutionOwners

        self.owners = ExecutionOwners(self)

    async def start(self):
        await self.owners.start()

    async def close(self):
        await self.owners.close()

    def status(self):
        return self.owners.status()

    def bind_behavior_validator(self, validator):
        """Bind Growth's trusted validation port, not a model-supplied argument."""
        self.behavior_validator = validator

    async def check_behaviors(self, context, refs, *, purpose):
        if not refs:
            return
        if self.behavior_validator is None:
            raise Fault("behavior_validation_unavailable", 503)
        outcome = await self.behavior_validator(context, tuple(refs), purpose=purpose)
        # The port either accepts with None/True or raises for an incompatible
        # rule; returning an unexamined resolution is not a successful check.
        if outcome is not None and outcome is not True:
            raise Fault("behavior_validation_invalid", 503)

    async def saved_behaviors(self, action, attempt):
        """Rehydrate trusted dependencies for domain-owned continuations."""
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute(
                "SELECT document,attempt FROM capabilities.actions WHERE id=%s", (action.action_id,)
            )).fetchone()
        if row is None or row["attempt"]["attempt_id"] != attempt.attempt_id:
            raise Fault("attempt_changed", 409)
        document = json.loads(row["document"])
        return tuple(document.get("behavior_refs", ())), document.get("behavior_purpose", "conversation")

    async def check_saved_behaviors(self, action, attempt):
        """Domain-owned continuation checks the same frozen dependencies."""
        refs, purpose = await self.saved_behaviors(action, attempt)
        await self.check_behaviors(action.context, refs, purpose=purpose)

    async def saved_generation(self, action, attempt=None):
        """Return the original installation generation for trusted continuations."""
        async with self.database.transaction(read_only=True) as conn:
            row = await (await conn.execute(
                "SELECT document,attempt FROM capabilities.actions WHERE id=%s", (action.action_id,)
            )).fetchone()
        if row is None or (attempt is not None and row["attempt"]["attempt_id"] != attempt.attempt_id):
            raise Fault("attempt_changed", 409)
        generation = json.loads(row["document"]).get("data_generation")
        if not isinstance(generation, str) or not generation:
            raise Fault("action_generation_unavailable", 503)
        return generation

    async def handler(self, action):
        if action.capability_id not in self.catalog.handlers and self.prepare_handler:
            await self.prepare_handler(action)
        return self.catalog.handler(action.capability_id)

    async def invoke(self, action: Action, *, background=False, service_class=None,
                     behavior_refs=(), behavior_purpose="conversation", generation=None):
        if not isinstance(behavior_refs, (tuple, list)) or any(not isinstance(ref, Mapping) for ref in behavior_refs):
            raise Fault("behavior_reference_invalid", 400)
        if not isinstance(behavior_purpose, str) or not behavior_purpose:
            raise Fault("behavior_purpose_invalid", 400)
        job_generation = current_execution_generation()
        if job_generation is not None:
            if generation is not None and generation != job_generation:
                raise Fault("job_generation_expired", 409)
            generation = job_generation
        parent = CURRENT_EXECUTION.get()
        if parent is not None:
            inherited = parent.get("data_generation")
            if not inherited:
                raise Fault("action_generation_unavailable", 503)
            if generation is not None and generation != inherited:
                raise Fault("job_generation_expired", 409)
            generation = inherited
            parent_refs = tuple(parent.get("behavior_refs", ()))
            parent_purpose = parent.get("behavior_purpose", "conversation")
            if parent_refs:
                if behavior_refs and behavior_purpose != parent_purpose:
                    raise Fault("behavior_purpose_mismatch", 409)
                behavior_refs = (*parent_refs, *behavior_refs)
                behavior_purpose = parent_purpose
        # Canonicalize equivalent dependency sets without changing Action or
        # exposing these trusted references to the capability's input schema.
        refs_by_digest = {canonical(wire(ref)): wire(ref) for ref in behavior_refs}
        behavior_refs = tuple(refs_by_digest[key] for key in sorted(refs_by_digest))
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
        await self.check_behaviors(action.context, behavior_refs, purpose=behavior_purpose)
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
        action_semantics = wire(replace(action, action_id="", revision=1))
        semantics = (digest([action_semantics, behavior_purpose, behavior_refs]) if behavior_refs
                     else digest(action_semantics))
        attempt = Attempt(str(uuid4()), action.action_id)
        receipt = Receipt(
            str(uuid4()), action.action_id, attempt.attempt_id, "pending", "none", "not_requested"
        )
        async with self.database.transaction() as conn:
            from .withdrawal import validate_sources, depend

            if generation is None:
                generation = await self.jobs.current_generation_on(conn)
            await self.jobs.assert_generation_on(conn, generation)
            if not await validate_sources(conn, action.context, action.source_refs, self.sources):
                raise Fault("source_revoked", 410)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            old = await (
                await conn.execute(
                    "SELECT action,receipt,semantics,document FROM capabilities.actions WHERE intent=%s FOR UPDATE",
                    (key,),
                )
            ).fetchone()
            if old:
                if json.loads(old["document"]).get("data_generation") != generation:
                    raise Fault("job_generation_expired", 409)
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
                                   "job_key": action.action_id if background else None,
                                   "data_generation": generation,
                                   "behavior_refs": behavior_refs, "behavior_purpose": behavior_purpose}),
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
                            data_generation=generation,
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
            if row is not None:
                generation = json.loads(row["document"]).get("data_generation")
                if generation != await self.jobs.current_generation_on(conn):
                    raise Fault("job_generation_expired", 409)
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

    async def assert_result_head_on(self, connection, context, *, action_id, expected_receipt,
                                    result_revision, data_generation, source_refs=()):
        """Fence a consumer's original receipt until its domain transaction ends.

        The caller must have already locked its complete domain-specific source
        set. This port locks the Action's own sources before the Action row.
        """
        if self.sources is None:
            raise Fault("source_authority_unavailable", 503)
        await self.jobs.assert_generation_on(connection, data_generation)
        if not await self.sources.validate_persistence(connection, context, tuple(source_refs)):
            raise Fault("source_unavailable", 403)
        row = await (await connection.execute(
            "SELECT principal_id,action,receipt,result_revision,document FROM capabilities.actions "
            "WHERE id=%s AND principal_id=%s FOR SHARE", (action_id, context.principal_id))).fetchone()
        if not row:
            raise Fault("result_head_unavailable", 404)
        action = action_from(row["action"])
        if (action.context.actor_id != context.actor_id or
                not set(action.context.scope_ids) <= set(context.scope_ids)):
            raise Fault("forbidden", 403)
        if json.loads(row["document"]).get("data_generation") != data_generation:
            raise Fault("job_generation_expired", 409)
        if row["result_revision"] > result_revision:
            error = Fault("result_head_advanced", 409)
            # Retain only an internal comparison token; callers see the same
            # error contract, while a bounded reread can detect a rollback or
            # a same-version receipt mutation after this locked observation.
            error._observed_result_head = (row["result_revision"], digest([row["action"], row["receipt"]]),
                                           row["receipt"].get("receipt_id"))
            raise error
        if row["result_revision"] < result_revision:
            raise Fault("result_head_regressed", 409)
        if canonical(row["receipt"]) != canonical(wire(expected_receipt)):
            raise Fault("result_receipt_revision_conflict", 409)
        required = unique_refs(action.source_refs, result_sources(receipt_from(row["receipt"])))
        locked = {canonical(wire(ref)) for ref in source_refs}
        if any(canonical(wire(ref)) not in locked for ref in required):
            raise Fault("result_source_set_changed", 409)
        return {"action_id": action_id, "receipt_id": row["receipt"]["receipt_id"],
                "result_revision": row["result_revision"]}

    async def ensure_result_consumption(self, context, action_id, *, data_generation,
                                        expected_result_revision=None, expected_receipt_id=None) -> dict:
        """Hand off only the current original terminal result to its consumer.

        A stale caller may refer to an older head. Each fresh head is source-
        checked and locked before the event port runs; this never executes or
        resumes the Action or infers an unknown effect's outcome.
        """
        if not isinstance(data_generation, str) or not data_generation:
            raise Fault("action_generation_unavailable", 400)
        if (expected_result_revision is None) != (expected_receipt_id is None):
            raise Fault("result_expectation_incomplete", 400)
        if expected_result_revision is not None and (
                type(expected_result_revision) is not int or expected_result_revision < 0
                or not isinstance(expected_receipt_id, str) or not expected_receipt_id):
            raise Fault("result_expectation_invalid", 400)

        previous = None
        observed_head = None
        for read_number in range(_RESULT_HEAD_READ_LIMIT):
            snapshot = await self.get(action_id, context)
            async with self.database.transaction(read_only=True) as connection:
                if await self.jobs.current_generation_on(connection) != data_generation:
                    raise Fault("job_generation_expired", 409)
            action = action_from(snapshot["action"])
            actual = snapshot["receipt"]
            revision = snapshot["result_revision"]
            if (action.action_id != action_id or type(revision) is not int or revision < 0
                    or not isinstance(actual, dict) or not isinstance(snapshot["attempt"], dict)
                    or actual.get("action_id") != action_id
                    or actual.get("attempt_id") != snapshot["attempt"].get("attempt_id")
                    or not isinstance(actual.get("attempt_id"), str) or not actual["attempt_id"]
                    or not isinstance(actual.get("receipt_id"), str) or not actual["receipt_id"]):
                raise Fault("result_receipt_revision_conflict", 409)
            if expected_result_revision is not None:
                if revision < expected_result_revision:
                    raise Fault("result_head_regressed", 409)
                if ((revision == expected_result_revision and actual["receipt_id"] != expected_receipt_id)
                        or (revision > expected_result_revision and actual["receipt_id"] == expected_receipt_id)):
                    raise Fault("result_receipt_revision_conflict", 409)
            if observed_head is not None:
                minimum_revision, receipt_digest, observed_receipt_id = observed_head
                if revision < minimum_revision:
                    raise Fault("result_head_regressed", 409)
                if (revision == minimum_revision and receipt_digest is not None
                        and digest([snapshot["action"], actual]) != receipt_digest):
                    raise Fault("result_receipt_revision_conflict", 409)
                if revision > minimum_revision and observed_receipt_id == actual["receipt_id"]:
                    raise Fault("result_receipt_revision_conflict", 409)
            if previous is not None:
                prior_revision, prior_receipt = previous
                if revision < prior_revision:
                    raise Fault("result_head_regressed", 409)
                if ((revision == prior_revision and canonical(actual) != canonical(prior_receipt))
                        or (revision > prior_revision and actual["receipt_id"] == prior_receipt["receipt_id"])):
                    raise Fault("result_receipt_revision_conflict", 409)
            previous = (revision, actual)
            refs = unique_refs(action.source_refs, result_sources(receipt_from(actual)))
            fence_error = None
            try:
                async with self.database.transaction() as connection:
                    try:
                        await self.assert_result_head_on(connection, context, action_id=action_id,
                            expected_receipt=actual, result_revision=revision,
                            data_generation=data_generation, source_refs=refs)
                    except AppError as error:
                        if error.code == "result_head_advanced":
                            fence_error = error
                        raise
                    head = await (await connection.execute(
                        "SELECT action,attempt,receipt,result_revision,result_observed_at FROM capabilities.actions "
                        "WHERE id=%s AND principal_id=%s", (action_id, context.principal_id))).fetchone()
                    if (not head or head["result_revision"] != revision
                            or not isinstance(head["attempt"], dict)
                            or head["attempt"].get("attempt_id") != actual["attempt_id"]
                            or canonical(head["receipt"]) != canonical(actual)
                            or canonical(head["action"]) != canonical(snapshot["action"])
                            or head["result_observed_at"] != snapshot["result_observed_at"]):
                        raise Fault("result_receipt_revision_conflict", 409)
                    execution = actual.get("execution")
                    if execution in {"pending", "running"}:
                        return {"state": "waiting", "reason": "action_" + execution,
                                "action_id": action_id, "receipt_id": None, "result_revision": None,
                                "consumer_progress": {}}
                    if execution not in {"succeeded", "failed", "cancelled", "unknown"}:
                        raise Fault("result_receipt_state_invalid", 409)
                    identity = {"action_id": action_id, "receipt_id": actual["receipt_id"],
                                "result_revision": revision}
                    ensure_on = getattr(self.events, "ensure_on", None)
                    if not callable(ensure_on):
                        return {"state": "blocked", "reason": "result_events_unavailable",
                                "consumer_progress": {}, **identity}
                    outcome = await ensure_on(connection, _result_event(
                        head["action"], head["receipt"], revision, head["result_observed_at"]))
                    if (not isinstance(outcome, dict)
                            or outcome.get("state") not in {"accepted", "waiting", "blocked", "verified"}
                            or not isinstance(outcome.get("reason"), str) or not outcome["reason"]
                            or not isinstance(outcome.get("consumer_progress"), dict)):
                        raise Fault("result_wake_contract_invalid", 503)
                    return {**outcome, **identity}
            except AppError as error:
                # Catch only the fence's own advancement; the transaction has
                # rolled back before any reread, and all other errors escape.
                if error is not fence_error or read_number == _RESULT_HEAD_READ_LIMIT - 1:
                    raise
                observed_head = getattr(error, "_observed_result_head", (revision + 1, None, None))

    async def inspect_recovery(self, action_id, context):
        """Inspect only the canonical Action, attempt, receipt and queue identity."""
        return await self.get(action_id, context)

    async def inspect_video_parse_recovery(self, action_id, context, *, expected_attempt_id=None):
        """Classify the original video parse failure without dispatching work."""
        snapshot = await self.get(action_id, context)
        action, attempt, current = (action_from(snapshot["action"]), attempt_from(snapshot["attempt"]),
                                    receipt_from(snapshot["receipt"]))
        if action.capability_id != "video.observe":
            raise Fault("video_recovery_not_eligible", 409)
        if expected_attempt_id is not None and attempt.attempt_id != expected_attempt_id:
            raise Fault("attempt_changed", 409)
        handler = await self.handler(action)
        details = await handler.inspect_parse_recovery(action, attempt, current)
        return {**details, "action_id": action_id, "attempt_id": attempt.attempt_id,
                "receipt_id": current.receipt_id, "result_revision": snapshot["result_revision"]}

    async def context_for_action(self, action_id):
        """Trusted recovery code can refresh only the Action's original grant.

        This is an internal port, not an authorization decision: the caller must
        refresh and check the returned context before using it.
        """
        action, _, _ = await self._load(action_id)
        return action.context

    async def observe_recovery(self, action_id, context, *, expected_attempt_id):
        """Read the original receipt, including unknown, without dispatching work."""
        value = await self.get(action_id, context)
        if value["attempt"]["attempt_id"] != expected_attempt_id:
            raise Fault("attempt_changed", 409)
        return value

    async def invoke_registered_recovery(self, action_id, context, *, operation,
                                         client_request_id, expected_revision,
                                         expected_attempt_id, expected_job_id,
                                         guard_on, guard_attempt_id, guard_owner_token):
        """Only reroute a proven never-started original; no generic effect replay."""
        if operation != "reroute_pending":
            raise Fault("recovery_operation_unavailable", 409)
        if type(expected_revision) is not int:
            raise Fault("recovery_revision_required", 400)
        if not callable(guard_on) or not guard_attempt_id or not guard_owner_token:
            raise Fault("recovery_guard_required", 400)
        return await self.reroute_pending(
            action_id, context, client_request_id=client_request_id,
            expected_attempt_id=expected_attempt_id, expected_job_id=expected_job_id,
            expected_revision=expected_revision,
            recovery_guard=(guard_on, guard_attempt_id, guard_owner_token),
        )

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
            row = await (await conn.execute(
                "SELECT document,attempt FROM capabilities.actions WHERE id=%s FOR UPDATE", (attempt.action_id,)
            )).fetchone()
            if row is None or row["attempt"]["attempt_id"] != attempt.attempt_id:
                raise Fault("attempt_changed", 409)
            await self.jobs.assert_generation_on(conn, json.loads(row["document"]).get("data_generation"))
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
            if claim is not None:
                document = json.loads(claim["document"])
                await self.jobs.assert_generation_on(conn, document.get("data_generation"))
        if claim is None:
            return (await self._load(action_id))[2]
        state = {
            "owner": self,
            "attempt": attempt,
            "submitted": False,
            "behavior_refs": document.get("behavior_refs", ()),
            "behavior_purpose": document.get("behavior_purpose", "conversation"),
            "data_generation": document["data_generation"],
            "service_class": ServiceClass(
                document.get("service_class", "elastic")
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
                await self.check_behaviors(action.context, document.get("behavior_refs", ()),
                                           purpose=document.get("behavior_purpose", "conversation"))
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
            from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable
            if isinstance(error, RecoveryWait) and not recovery_wait_retryable(error):
                receipt = Receipt(str(uuid4()), action_id, attempt.attempt_id, "unknown", "unknown", "not_requested",
                    error_code=error.code, result={"outcome": "wait", "recovery": "observe_original_attempt"})
                await self.save(receipt, attempt)
                raise
            if (isinstance(error, RecoveryWait) and action.capability_id == "video.observe"
                    and recovery_wait_retryable(error)):
                receipt = Receipt(str(uuid4()), action_id, attempt.attempt_id, "failed", "none", "not_requested",
                    error_code=error.code, result={"phase": "model", "outcome": "wait", "recovery": "resume_model",
                                                  "retry_at": error.retry_at.isoformat()})
            else:
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
        details = getattr(handler, "interruption_details", None)
        if callable(details):
            phase = details(explicit=explicit, unknown=receipt.effect == "unknown")
            if phase.get("recovery") == "observe_original_attempt":
                receipt = replace(receipt, execution="unknown", effect="unknown")
            receipt = replace(receipt, result={**receipt.result, **phase})
        return receipt

    async def recover_orphan(self, action_id, attempt_id, previous_owner):
        action, attempt, current = await self._load(action_id)
        if attempt.attempt_id != attempt_id or current.execution != "running":
            return current
        generation = await self.saved_generation(action, attempt)
        async with self.database.transaction() as conn:
            from .withdrawal import validate_sources

            await self.jobs.assert_generation_on(conn, generation)
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
                        data_generation=generation,
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
            document = json.loads(old["document"])
            await self.jobs.assert_generation_on(conn, document.get("data_generation"))
            previous_receipt = receipt_from(old["receipt"])
            source_invalid = not valid
            if not valid or document.get("source_revoked"):
                from .withdrawal import redacted

                receipt = redacted(receipt)
                source_invalid = True
            cancel_requested = document.get("cancel_requested")
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
            if cancel_requested:
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
            else:
                # The ID also keys result events and consumer deduplication. A
                # stale producer may submit either the current ID or an older
                # ID again; neither can identify different persisted content.
                history = document.get("receipt_lineage", {})
                used = {*history, previous["receipt_id"]}
                if receipt.receipt_id in used:
                    successor = str(uuid4())
                    while successor in used:
                        successor = str(uuid4())
                    receipt = replace(receipt, receipt_id=successor)
                observed = old["result_observed_at"]
                history[previous["receipt_id"]] = {
                    "receipt_id": previous["receipt_id"],
                    "attempt_id": previous.get("attempt_id"),
                    "result_revision": old["result_revision"],
                    "execution": previous.get("execution"),
                    "effect": previous.get("effect"),
                    "delivery": previous.get("delivery"),
                    "receipt_digest": digest(previous),
                    "result_observed_at": observed.isoformat() if observed else None,
                    "successor_receipt_id": receipt.receipt_id,
                }
                # History deliberately excludes receipt content and source data:
                # source withdrawal scrubs receipts but preserves this audit.
                document["receipt_lineage"] = history
            changed = old["receipt"] != wire(receipt)
            revision = old["result_revision"] + int(changed)
            observed_at = datetime.now(UTC) if changed else old["result_observed_at"]
            await depend(conn, "action", receipt.action_id, refs)
            if (original.capability_id in {"content.read", "video.observe"}
                    and previous_receipt.execution == "unknown" and receipt.execution != "unknown"
                    and not source_invalid):
                document.setdefault("prior_unknown_receipts", {}).setdefault(
                    attempt.attempt_id, wire(previous_receipt))
            if original.capability_id == "content.read" and (receipt.execution == "succeeded" or source_invalid):
                document.pop("content_read", None)
                document.pop("content_read_resume", None)
            if source_invalid:
                document.pop("prior_unknown_receipts", None)
            await conn.execute(
                "UPDATE capabilities.actions SET receipt=%s::jsonb,attempt=%s::jsonb,result_revision=%s,result_observed_at=%s,execution_owner=NULL,document=%s WHERE id=%s",
                (encoded(receipt), encoded(attempt), revision, observed_at, canonical(document), receipt.action_id),
            )
            await conn.execute(
                "INSERT INTO capabilities.attempts(id,action_id,attempt,receipt) VALUES(%s,%s,%s::jsonb,%s::jsonb) ON CONFLICT(id) DO UPDATE SET attempt=excluded.attempt,receipt=excluded.receipt",
                (attempt.attempt_id, receipt.action_id, encoded(attempt), encoded(receipt)),
            )
            if changed and original.capability_id == "media.acquire" and self.statistics is not None:
                from .media.statistics import record_action_job_on
                await record_action_job_on(self.statistics, conn, original)
            if (
                self.events
                and receipt.execution in {"succeeded", "failed", "cancelled", "unknown"}
                and old["receipt"] != wire(receipt)
            ):
                await self.events.publish(conn, _result_event(old["action"], receipt, revision, observed_at))
        if (changed and receipt.execution in {"failed", "unknown"}
                and receipt.result.get("outcome") != "wait"):
            log = getattr(self.jobs, "events", None)
            if log is not None:
                try:
                    # The original ledger remains authoritative. The safe event
                    # carries only stable lookup identities and a failure code.
                    await log.emit("capability.failed", receipt.execution,
                                   material_id=receipt.action_id, failure_id=receipt.attempt_id,
                                   attempt_id=receipt.attempt_id, error_code=receipt.error_code,
                                   dependency_ref=original.capability_id)
                except Exception as error:
                    logger.warning("capability failure event unavailable: %s", type(error).__name__)
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
        generation = await self.saved_generation(action, attempt)
        async with self.database.transaction() as conn:
            await self.jobs.assert_generation_on(conn, generation)
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
                              expected_attempt_id, expected_job_id, expected_revision=None,
                              recovery_guard=None):
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
            document = json.loads(row["document"])
            await self.jobs.assert_generation_on(conn, document.get("data_generation"))
            control_digest = digest([expected_attempt_id, expected_job_id, expected_revision])
            prior = await (await conn.execute(
                "SELECT attempt_id FROM capabilities.action_controls WHERE id=%s", (key,)
            )).fetchone()
            if prior:
                if expected_attempt_id is not None and prior["attempt_id"] != expected_attempt_id:
                    raise Fault("idempotency_conflict", 409)
                saved_digest = document.get("recovery_controls", {}).get(key)
                if saved_digest is not None and saved_digest != control_digest:
                    raise Fault("idempotency_conflict", 409)
                if saved_digest is None and expected_revision is not None:
                    raise Fault("idempotency_conflict", 409)
                saved = await (await conn.execute(
                    "SELECT receipt FROM capabilities.attempts WHERE id=%s", (prior["attempt_id"],)
                )).fetchone()
                return receipt_from(saved["receipt"] if saved else row["receipt"])
            if expected_revision is not None and row["result_revision"] != expected_revision:
                raise Fault("revision_conflict", 409)
            if not expected_attempt_id or type(expected_job_id) is not int:
                raise Fault("pending_reroute_identity_required", 400)
            if row["attempt"]["attempt_id"] != expected_attempt_id:
                raise Fault("attempt_changed", 409)
            attempt = attempt_from(row["attempt"])
            receipt = receipt_from(row["receipt"])
            if (receipt.execution != "pending" or attempt.dispatch != "not_started"
                    or attempt.provider_handle is not None or attempt.started_at is not None
                    or attempt.finished_at is not None or row.get("execution_owner") is not None):
                raise Fault("action_already_started", 409)
            if document.get("source_revoked") or document.get("cancel_requested"):
                raise Fault("pending_reroute_unavailable", 409)
            observation = await self._execution_on(conn, row)
            if observation.get("job_id") != expected_job_id:
                raise Fault("job_changed", 409)
            if recovery_guard is not None:
                guard_on, recovery_attempt_id, owner_token = recovery_guard
                if await guard_on(conn, recovery_attempt_id, owner_token) is False:
                    raise Fault("recovery_guard_rejected", 409)
            moved = await self.jobs.reroute_pending(
                conn, expected_job_id, execution_lane=lane,
                task="capabilities.execute", service_class=service_class,
            )
            if not moved:
                raise Fault("action_already_started", 409)
            document.update(service_class=service_class.value, execution_lane=lane.value)
            document.setdefault("recovery_controls", {})[key] = control_digest
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
                     expected_attempt_id=None, expected_job_id=None,
                     expected_receipt_id=None, expected_revision=None, recovery_guard=None):
        if not client_request_id:
            raise Fault("client_request_id_required", 400)
        await self.get(action_id, context)
        action, old, current = await self._load(action_id)
        generation = await self.saved_generation(action, old)
        if expected_revision is not None and type(expected_revision) is not int:
            raise Fault("recovery_revision_required", 400)
        key = digest([context.principal_id, action_id, "resume", client_request_id])
        control_digest = digest([expected_attempt_id, expected_receipt_id, expected_revision])
        async with self.database.transaction(read_only=True) as conn:
            replay = await (await conn.execute(
                "SELECT coalesce(t.receipt,a.receipt) AS receipt,a.document FROM capabilities.action_controls c "
                "JOIN capabilities.actions a ON a.id=c.action_id "
                "LEFT JOIN capabilities.attempts t ON t.id=c.attempt_id WHERE c.id=%s", (key,))).fetchone()
        if replay:
            recorded = json.loads(replay["document"]).get("resume_controls", {}).get(key)
            if recorded is not None:
                if recorded != control_digest:
                    raise Fault("idempotency_conflict", 409)
                receipt = receipt_from(replay["receipt"])
                await self.authority.check(context, result_sources(receipt))
                return receipt
        if current.execution == "pending":
            if expected_receipt_id is not None and current.receipt_id != expected_receipt_id:
                raise Fault("attempt_changed", 409)
            return await self.reroute_pending(
                action_id, context, client_request_id=client_request_id,
                expected_attempt_id=expected_attempt_id, expected_job_id=expected_job_id,
                expected_revision=expected_revision,
                recovery_guard=recovery_guard,
            )
        if expected_job_id is not None:
            raise Fault("pending_reroute_only", 409)
        if ((expected_attempt_id is not None and expected_attempt_id != old.attempt_id)
                or (expected_receipt_id is not None and expected_receipt_id != current.receipt_id)):
            raise Fault("attempt_changed", 409)
        handler = await self.handler(action)
        if not hasattr(handler, "resume"):
            raise Fault("resume_unavailable", 409)
        service_class = execution_service_class(context)
        lane = execution_lane(action.capability_id)
        preflight = getattr(handler, "resume_preflight", None)
        if preflight is not None:
            # A completed control remains an idempotent read, even when the new
            # head no longer qualifies for the original domain recovery proof.
            if replay:
                recorded = json.loads(replay["document"]).get("resume_controls", {}).get(key)
                if recorded != control_digest and (recorded is not None or
                        any(value is not None for value in (expected_attempt_id, expected_receipt_id, expected_revision))):
                    raise Fault("idempotency_conflict", 409)
                receipt = receipt_from(replay["receipt"])
                await self.authority.check(context, result_sources(receipt))
                return receipt
            # Models/provider observation must not hold a domain write lock.
            proof = await preflight(action, old, current)
        async with self.database.transaction() as conn:
            await self.jobs.assert_generation_on(conn, generation)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (key,))
            prior = await (
                await conn.execute(
                    "SELECT attempt_id FROM capabilities.action_controls WHERE id=%s", (key,)
                )
            ).fetchone()
            if prior:
                control = await (await conn.execute(
                    "SELECT document FROM capabilities.actions WHERE id=%s", (action_id,))).fetchone()
                recorded = json.loads(control["document"]).get("resume_controls", {}).get(key)
                if recorded != control_digest and (recorded is not None or
                        any(value is not None for value in (expected_attempt_id, expected_receipt_id, expected_revision))):
                    raise Fault("idempotency_conflict", 409)
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
            if preflight is not None:
                await handler.validate_resume_on(conn, action, old, current, proof)
            head = await (await conn.execute(
                "SELECT attempt,receipt,result_revision,document FROM capabilities.actions WHERE id=%s FOR UPDATE",
                (action_id,))).fetchone()
            if (not head or head["attempt"]["attempt_id"] != old.attempt_id
                    or head["receipt"]["receipt_id"] != current.receipt_id
                    or expected_attempt_id is not None and expected_attempt_id != old.attempt_id
                    or expected_receipt_id is not None and expected_receipt_id != current.receipt_id):
                raise Fault("attempt_changed", 409)
            if expected_revision is not None and head["result_revision"] != expected_revision:
                raise Fault("revision_conflict", 409)
            if recovery_guard is not None:
                guard_on, recovery_attempt_id, owner_token = recovery_guard
                if not callable(guard_on) or not recovery_attempt_id or not owner_token:
                    raise Fault("recovery_guard_required", 400)
                if await guard_on(conn, recovery_attempt_id, owner_token) is False:
                    raise Fault("recovery_guard_rejected", 409)
            prepare = getattr(handler, "prepare_resume_on", None)
            if prepare is not None:
                await prepare(conn, action, old, current, attempt, proof)
            controls = json.loads(head["document"]).get("resume_controls", {})
            controls[key] = control_digest
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
                     "execution_lane": lane.value, "job_key": action_id + ":resume:" + attempt.attempt_id,
                     "resume_controls": controls}),
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
                    data_generation=generation,
                ),
            )
        return receipt

    async def enqueue_control(self, action, attempt, current, mode):
        generation = await self.saved_generation(action, attempt)
        async with self.database.transaction() as conn:
            await self.jobs.enqueue(
                conn,
                JobSpec(
                    "capabilities.control",
                    action.action_id + ":" + mode + ":" + str(uuid4()),
                    {"action_id": action.action_id, "mode": mode},
                    service_class=ServiceClass.INTERACTIVE,
                    subject_id=action.actor_id,
                    data_generation=generation,
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
        self.jobs.register("capabilities.control", self.control)


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
