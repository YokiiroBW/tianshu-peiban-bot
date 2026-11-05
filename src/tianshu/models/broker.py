"""One active model-capacity owner, actual network cancellation and durable attempts."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
import math
import time
from uuid import uuid4
from types import SimpleNamespace

import aiohttp
import psycopg

from tianshu.runtime.errors import AppError
from tianshu.runtime.recovery_policy import RecoveryWait
from tianshu.runtime.cancellation import finish_cleanup
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass, SourceRef
from .catalog import ModelCatalog, RoutePlan
from .codec import event_from_wire, request_from_wire, result_from_wire
from .ledger import AttemptLedger, context_fingerprint, digest, receipt, submission_digest
from .protocols import NativeCompiler, ResponseCollector, normalize_usage, requested_capabilities
from .scheduler import QuotaScheduler
from .types import ModelEvent, ModelResult, ModelUsage
from .budgets import (
    MAX_INLINE_MEDIA_BYTES, MAX_INLINE_MEDIA_PARTS,
    effective_output_tokens, with_output_tokens, merged_parameters,
)
from .estimation import input_tokens
from .timing import execution_budget, waiting_budget, waiting_timeout, waiting, waiting_expired, ExternalExecutionClock
from .transport import validate_provider_session
from .http_timing import HttpTiming, retry_after, trace_config

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_SSE_EVENT_BYTES = 2 * 1024 * 1024


def _cancelled_before_start(row):
    if (row.get("execution_timeout_seconds") is not None and row.get("business_deadline_at")
            and row["business_deadline_at"] <= datetime.now(UTC)):
        return "business_deadline_expired"
    return "consumer_interrupted" if row.get("cancellation_reason") == "consumer_interrupted" else "cancelled_before_start"


def _stream_handle(protocol, value):
    if protocol == "openai.chat":
        return value.get("id")
    if protocol == "gemini.generate":
        return value.get("responseId")
    if protocol in {"openai.responses", "anthropic.messages"}:
        nested = value.get("response" if protocol == "openai.responses" else "message")
        return nested.get("id") if isinstance(nested, dict) else value.get("response_id")
    return None


class ModelBroker:
    def __init__(
        self,
        database,
        files,
        secrets,
        *,
        events=None,
        validate_submission=None,
        validate_persistence=None,
        credential_store=None,
        native_principals=frozenset(),
        media_resolver=None,
        provider_credential_refs=None,
        jobs=None,
        upstream_connections=64,
        diagnostic_connections=4,
        statistics=None,
        validate_behaviors=None,
    ):
        if any(type(value) is not int or value < 1 for value in (upstream_connections, diagnostic_connections)):
            raise ValueError("positive_upstream_connection_capacity_required")
        self.database = database
        self.catalog = ModelCatalog(database)
        self.compiler = NativeCompiler(files, secrets)
        self.instance_id = str(uuid4())
        self.ledger = AttemptLedger(database, self.instance_id, statistics=statistics)
        self.session = None
        self.diagnostic_session = None
        self.data_generation = None
        self.scheduler = None
        self.owner = None
        self.owner_watch = None
        self.active = {}
        self.drivers = {}
        self.driver_lock = asyncio.Lock()
        self.jobs = jobs
        self.completion_failures = set()
        self.external = {}
        self.ready = False
        self.events = events
        self.validate_submission = validate_submission
        self.validate_persistence = validate_persistence
        self.validate_behaviors = validate_behaviors
        self.prepare_slots = asyncio.Semaphore(8)
        # A real local connection/memory limit, independent of supplier quota.
        self.upstream_connections = upstream_connections
        self.diagnostic_connections = diagnostic_connections
        self.cloud_slots = asyncio.Semaphore(upstream_connections)
        self.diagnostic_slots = asyncio.Semaphore(diagnostic_connections)
        self.prepare_pending = 0
        self.credential_store = credential_store
        self.native_principals = frozenset(native_principals)
        self.media_resolver = media_resolver
        self.provider_credential_refs = dict(provider_credential_refs or {})

    async def prepare_media(self, request):
        messages, dependencies, inline_bytes, count = [], list(request.source_refs), 0, 0
        for message in request.messages:
            parts = []
            for part in message.parts:
                if part.media:
                    if self.media_resolver is None:
                        raise AppError("media_authority_not_bound", status=503)
                    actual, refs = await self.media_resolver(request.context, part.media)
                    if actual.size_bytes is None:
                        raise AppError("media_size_unknown", status=422)
                    inline_bytes += actual.size_bytes
                    count += 1
                    if inline_bytes > MAX_INLINE_MEDIA_BYTES or count > MAX_INLINE_MEDIA_PARTS:
                        raise AppError("model_inline_media_budget_exceeded", status=413)
                    dependencies.extend(refs)
                    part = replace(part, media=actual)
                parts.append(part)
            messages.append(replace(message, parts=tuple(parts)))
        return replace(
            request,
            messages=tuple(messages),
            source_refs=tuple({digest(ref): ref for ref in dependencies}.values()),
        )

    def native_grant(self, request):
        context = request.context
        return (
            context.principal_id in self.native_principals
            and not request.source_refs
            and context.actor_id is None
            and context.person_id is None
            and context.conversation_id is None
            and not context.scope_ids
            and not context.audience
        )

    async def event(self, name, outcome, attempt_id):
        if self.events:
            await self.events.emit(name, outcome, attempt_id=attempt_id)

    async def recheck(self, request):
        if self.native_grant(request):
            return
        if self.validate_submission:
            await self.validate_submission(request.context, request.source_refs)
        elif request.source_refs:
            raise AppError("source_validation_not_bound", status=503)

    async def recheck_behaviors(self, request):
        from collections.abc import Mapping

        refs = request.behavior_refs
        if not isinstance(refs, (tuple, list)) or any(not isinstance(ref, Mapping) for ref in refs):
            raise AppError("invalid_behavior_refs")
        if request.behavior_purpose is not None and (
                not isinstance(request.behavior_purpose, str) or not request.behavior_purpose):
            raise AppError("invalid_behavior_purpose")
        if not refs:
            return
        if not request.behavior_purpose:
            raise AppError("behavior_purpose_required")
        if self.validate_behaviors is None:
            raise AppError("behavior_validation_not_bound", status=503)
        accepted = await self.validate_behaviors(request.context, tuple(refs), purpose=request.behavior_purpose)
        if accepted is not True:
            raise AppError("behavior_dependency_invalid", status=409)

    async def may_persist(self, connection, request):
        if self.native_grant(request):
            return True
        if self.validate_persistence:
            return await self.validate_persistence(connection, request.context, request.source_refs)
        return not request.source_refs

    async def embedding_profile(self):
        async with self.database.transaction(control=True, read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT e.*,r.provider_id,r.model,r.revision AS route_revision,r.quota_group "
                    "FROM models.embedding_profile e JOIN models.routes r USING(route_id) WHERE singleton"
                )
            ).fetchone()
        if not row:
            raise AppError("embedding_profile_not_configured", status=503)
        return row

    async def open(self):
        try:
            self.owner = await psycopg.AsyncConnection.connect(
                self.database.config.dsn, autocommit=True
            )
            cursor = await self.owner.execute("SELECT pg_try_advisory_lock(733104303)")
            if not (await cursor.fetchone())[0]:
                raise AppError("broker_owner_already_active", status=503)
            policies = await self.catalog.policies()
            self.scheduler = QuotaScheduler(policies)
            self.scheduler.restore(await self.ledger.recover())
            self.scheduler.restore_cooldowns(await self.ledger.cooldowns())
            self.scheduler.restore_route_cooldowns(await self.ledger.route_cooldowns())
            await self.scheduler.start()
            async with self.database.transaction(control=True, read_only=True) as conn:
                generation = await (await conn.execute(
                    "SELECT id FROM runtime.installation_generation WHERE singleton")).fetchone()
                if generation is None:
                    raise AppError("installation_generation_missing", status=503)
                self.data_generation = str(generation["id"])
                self.compiler.session_generation = self.data_generation
            self.session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=self.upstream_connections, limit_per_host=0),
                cookie_jar=aiohttp.DummyCookieJar(),
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30),
                trace_configs=[trace_config()],
            )
            self.diagnostic_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(limit=self.diagnostic_connections, limit_per_host=0),
                cookie_jar=aiohttp.DummyCookieJar(), auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30),
                trace_configs=[trace_config()],
            )
            self.ready = True
            for saved in await self.ledger.pending_submissions():
                request = request_from_wire(saved["request"])
                accepted = await self._start_driver(request, streaming=saved["streaming"], resume=True)
                accepted.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            self.completion_watch = asyncio.create_task(self._watch_completions(), name="model-completions")
            self.statistics_watch = asyncio.create_task(self._watch_statistics(), name="model-statistics")
            self.owner_watch = asyncio.create_task(self._watch_owner(), name="broker-owner")
            self.external_watch = asyncio.create_task(
                self._watch_external(), name="external-attempts"
            )
        except BaseException:
            await finish_cleanup(self.close())
            raise

    async def _watch_owner(self):
        try:
            while self.ready:
                await asyncio.sleep(2)
                async with asyncio.timeout(3):
                    await self.owner.execute("SELECT 1")
        except (Exception, asyncio.CancelledError):
            self.ready = False
            for task in set(self.active.values()) | {entry[0] for entry in self.drivers.values()}:
                task.cancel()

    async def _watch_completions(self):
        from .async_jobs import enqueue_completion, pending_completions

        while self.ready:
            try:
                await self._wake_due_submissions()
            except Exception:
                # The original wait remains durable and will be read again.
                pass
            if self.jobs is not None:
                try:
                    after = ""
                    while True:
                        pending = await pending_completions(self.database, after=after)
                        for attempt_id in pending:
                            try:
                                await enqueue_completion(self.database, self.jobs, attempt_id)
                                self.completion_failures.discard(attempt_id)
                            except Exception:
                                # Keep this wake in the outbox; another unavailable task
                                # must not prevent independent completions from progressing.
                                if attempt_id not in self.completion_failures:
                                    self.completion_failures.add(attempt_id)
                                    try:
                                        await self.event("model.completion_enqueue", "failed", attempt_id)
                                    except Exception:
                                        pass
                        if len(pending) < 100:
                            break
                        after = pending[-1]
                except Exception:
                    pass
            await asyncio.sleep(1)

    async def _wake_due_submissions(self):
        await self.ledger.expire_stale_recovery_submissions()
        after = None
        while self.ready:
            pending = await self.ledger.wakeable_submissions(after=after)
            for saved in pending:
                try:
                    request = request_from_wire(saved["request"])
                    await self._start_driver(request, streaming=saved["streaming"], resume=True)
                except AppError:
                    await self.ledger.block_invalid_recovery(saved["attempt_id"])
            if len(pending) < 100:
                return
            last = pending[-1]
            after = (last["wake_at"], last["attempt_id"])

    async def _watch_external(self):
        while self.ready:
            await asyncio.sleep(0.5)
            for attempt_id, permit in list(self.external.items()):
                try:
                    permit.clock.check()
                except (TimeoutError, AppError):
                    await permit.unknown("business_deadline_expired" if permit.clock.deadline_at is None
                        else "external_deadline_exceeded")

    async def _watch_statistics(self):
        while self.ready:
            if self.ledger.statistics is not None:
                try:
                    await self.ledger.backfill_statistics()
                except Exception:
                    # The attempt ledger remains authoritative; retry the scan
                    # and do not declare producer coverage complete on failure.
                    pass
            await asyncio.sleep(30)

    async def close(self):
        self.ready = False
        for task in (self.owner_watch, getattr(self, "external_watch", None), getattr(self, "completion_watch", None), getattr(self, "statistics_watch", None)):
            if task:
                task.cancel()
        for task in set(self.active.values()) | {entry[0] for entry in self.drivers.values()}:
            task.cancel()
        await asyncio.gather(*(set(self.active.values()) | {entry[0] for entry in self.drivers.values()}), return_exceptions=True)
        for permit in list(self.external.values()):
            await permit.unknown("broker_stopping")
        if self.scheduler:
            await self.scheduler.close()
        if self.session:
            await self.session.close()
        if self.diagnostic_session:
            await self.diagnostic_session.close()
        if self.owner:
            await self.owner.close()
        await asyncio.gather(
            *(task for task in (self.owner_watch, getattr(self, "external_watch", None), getattr(self, "completion_watch", None), getattr(self, "statistics_watch", None)) if task),
            return_exceptions=True,
        )

    async def status(self, attempt_id, *, phase="observe"):
        from .lookup import lookup_phase

        lookup_phase(phase)
        row = await self.ledger.get(attempt_id)
        return receipt(row) if row else None

    async def require_receipt(self, attempt_id):
        from .lookup import missing_receipt

        original = await self.status(attempt_id, phase="observe")
        if original is None:
            raise missing_receipt(attempt_id)
        return original

    async def result(self, attempt_id, *, context):
        from .results import read_result

        return await read_result(self, attempt_id, context=context)

    async def reusable_result(self, attempt_id, *, context, expected_request_digest=None,
                              expected_data_generation=None):
        from .results import read_reusable_result

        return await read_reusable_result(self, attempt_id, context=context,
            expected_request_digest=expected_request_digest, expected_data_generation=expected_data_generation)

    async def observe_external(self, attempt_id, *, context, provider_handle, state):
        from .results import check_attempt_context

        if (not isinstance(state, str) or state not in {"queued", "running", "completed"}
                or not isinstance(provider_handle, str) or not provider_handle):
            raise AppError("invalid_external_provider_observation")
        row = await self.ledger.get(attempt_id)
        if row is None or row["protocol"] != "external":
            raise AppError("external_attempt_not_found", status=404)
        selected = await check_attempt_context(self, row, context)
        observation = SimpleNamespace(context=context, source_refs=selected)
        await self.recheck(observation)
        permit = self.external.get(attempt_id)
        live = (permit is not None and not permit.done and row["status"] == "running"
            and row["dispatch"] == "submitted")
        policy = self.scheduler.policies.get(row["quota_group"])
        updated = await self.ledger.observe_external(attempt_id, provider_handle, state,
            hold_seconds=policy.unknown_hold_seconds if live and policy else None,
            validate_observation=lambda connection: self.may_persist(connection, observation))
        if live:
            permit.clock.observe(updated["deadline_at"])
        return receipt(updated)

    async def observe_usage(self, attempt_id, *, context, provider_handle, usage):
        from .results import check_attempt_context

        if not isinstance(usage, ModelUsage) or usage.metering != "reported" or usage.raw is not None:
            raise AppError("invalid_model_usage_observation")
        for value in (usage.input_tokens, usage.output_tokens, usage.cached_input_tokens, usage.cache_write_tokens):
            if value is not None and (type(value) is not int or value < 0):
                raise AppError("invalid_model_usage_observation")
        if usage.input_tokens is None and usage.output_tokens is None:
            raise AppError("invalid_model_usage_observation")
        row = await self.ledger.get(attempt_id)
        if row is None:
            raise AppError("attempt_not_found", status=404)
        selected = await check_attempt_context(self, row, context)
        observation = SimpleNamespace(context=context, source_refs=selected)
        await self.recheck(observation)
        updated = await self.ledger.observe_usage(attempt_id, provider_handle, usage,
            validate_observation=lambda connection: self.may_persist(connection, observation))
        return receipt(updated)

    async def reload(self, *, configuration_event_id=None, expected_revision=None):
        from .scheduler import GroupState
        from .configuration_reload import mark_applied, status

        if configuration_event_id is not None:
            if not isinstance(configuration_event_id, str) or not configuration_event_id or type(expected_revision) is not int:
                raise AppError("invalid_model_configuration_reload")
            original = await status(self.database, configuration_event_id)
            if original is None or original["revision"] != expected_revision:
                raise AppError("model_configuration_reload_revision_conflict", status=409)

        policies = await self.catalog.policies()
        cooldowns = await self.ledger.cooldowns()
        route_cooldowns = await self.ledger.route_cooldowns()
        async with self.scheduler.lock:
            for name in policies:
                self.scheduler.groups.setdefault(name, GroupState())
            self.scheduler.policies = policies
            self.scheduler.restore_cooldowns(cooldowns)
            self.scheduler.restore_route_cooldowns(route_cooldowns)
            self.scheduler.changed.set()
        result = {"quota_groups": list(policies)}
        if configuration_event_id is not None:
            result["configuration_reload"] = await mark_applied(self.database, configuration_event_id, expected_revision)
        return result

    async def configuration_reload_status(self, identity):
        from .configuration_reload import status
        return await status(self.database, identity)

    async def pending_configuration_reloads(self, *, limit=100, cursor=None):
        from .configuration_reload import pending
        return await pending(self.database, limit=limit, cursor=cursor)

    async def cancel(self, attempt_id, *, reason="explicit_cancel"):
        if reason == "consumer_interrupted":
            current = await self.ledger.get(attempt_id)
            # An observer can disconnect while the Models-owned cloud request
            # continues. External resource owners retain their permit semantics.
            if current is None or current["protocol"] != "external":
                return receipt(current) if current else None
        return await self._cancel_attempt(attempt_id, reason=reason)

    async def cancel_external_owner(self, attempt_id):
        """An external permit owner may disappear before its enqueue ACK."""
        return await self._cancel_attempt(attempt_id, reason="consumer_interrupted")

    async def _cancel_attempt(self, attempt_id, *, reason):
        row = await self.ledger.request_cancel(attempt_id, reason=reason)
        await self.scheduler.cancel(attempt_id)
        if not row:
            return None
        if row["status"] == "queued" and row.get("execution_timeout_seconds") is not None:
            # request_cancel locked this row before submission. Its CAS now
            # forbids a future start, so a caller timeout has a known receipt.
            row = await self.ledger.finish(attempt_id, "cancelled", error_code=_cancelled_before_start(row))
        task = self.active.get(attempt_id)
        if task:
            task.cancel()
        elif attempt_id in self.external and self.external[attempt_id].submitted_at is None:
            return await self.external[attempt_id].unknown("cancelled_before_external_submit")
        elif row["status"] == "queued":
            row = await self.ledger.finish(
                attempt_id, "cancelled", error_code=_cancelled_before_start(row)
            )
        # External domain I/O sees cancel_requested through its separate control watcher.
        return receipt(row)

    async def generate(self, request):
        await self.submit(request)
        async for event in self._observe(request, streaming=False):
            if event.kind == "receipt":
                result = event.data
                if result.receipt.status != "succeeded" and result.native_response is None:
                    raise AppError(result.receipt.error_code or "model_attempt_" + result.receipt.status,
                        status=502, details={"attempt_id": request.attempt_id,
                            "status": result.receipt.status, "dispatch": result.receipt.dispatch})
                return result

    def stream(self, request):
        return self._stream(request)

    async def submit(self, request, *, completion=None):
        return await self._submit(request, completion=completion, streaming=False)

    async def _submit(self, request, *, completion=None, streaming=False):
        from .async_jobs import completion_record

        if not self.ready:
            raise AppError("broker_unavailable", status=503)
        record = completion_record(completion)
        try:
            await self.recheck(request)
        except RecoveryWait as exc:
            from .recovery_wait import preserve_wait_safety

            try:
                original = await self.ledger.get(request.attempt_id)
            except Exception:
                raise AppError("model_prestart_observation_required", status=503,
                    details={"attempt_id": request.attempt_id, "action": "observe_original_attempt"}) from None
            if original and original.get("submission_digest") == submission_digest(request):
                preserve_wait_safety(original, exc)
            raise
        existing = await self.ledger.existing_submission(request, record)
        if existing:
            row, recorded_streaming = existing
            try:
                await self.recheck(request)
            except RecoveryWait as exc:
                from .recovery_wait import preserve_wait_safety
                preserve_wait_safety(row, exc)
                raise
            if streaming and not recorded_streaming:
                raise AppError("model_stream_not_recorded", status=409)
            if row["status"] == "queued":
                self._raise_prestart_observation(row, allow_due=True)
                await self._start_driver(request, streaming=recorded_streaming, resume=True)
            return receipt(row)
        future = await self._start_driver(request, streaming=streaming, completion=record)
        # RPC disconnection is an observer interruption, not a cancellation of
        # the durable attempt. A caller can query the same ID after a lost ACK.
        await asyncio.shield(future)
        existing = await self.ledger.existing_submission(request, record)
        if existing is None:
            raise AppError("model_submission_not_recorded", status=503)
        row, recorded_streaming = existing
        if streaming and not recorded_streaming:
            raise AppError("model_stream_not_recorded", status=409)
        return receipt(row)

    async def _start_driver(self, request, *, streaming, completion=None, resume=False):
        async with self.driver_lock:
            existing = self.drivers.get(request.attempt_id)
            identity = submission_digest(request)
            if existing:
                if existing[2] != identity:
                    raise AppError("attempt_identity_conflict", status=409)
                return existing[1]
            accepted = asyncio.get_running_loop().create_future()
            accepted.add_done_callback(lambda done: done.exception() if not done.cancelled() else None)
            task = asyncio.create_task(self._drive(request, streaming=streaming, completion=completion,
                                                   resume=resume, accepted=accepted),
                                       name="model-attempt:" + request.attempt_id)
            self.drivers[request.attempt_id] = (task, accepted, identity)
            return accepted

    async def _drive(self, request, *, streaming, completion, resume, accepted):
        events = self._events(request, stream=streaming, completion=completion, resume=resume)
        size = 0
        try:
            async for event in events:
                if event.kind in {"accepted", "receipt"}:
                    if not accepted.done():
                        accepted.set_result(None)
                elif streaming:
                    size += len(json.dumps(wire(event), ensure_ascii=False).encode())
                    if size > 96 * 1024 * 1024:
                        await self.ledger.truncate_stream(request.attempt_id)
                        raise AppError("model_stream_record_limit_exceeded", status=502)
                    current = await self.ledger.get(request.attempt_id)
                    dependencies = tuple(SourceRef(**ref) for ref in current["source_refs"] or ())
                    observation = SimpleNamespace(context=request.context, source_refs=dependencies)
                    await self.ledger.append_event(request.attempt_id, event,
                        validate=lambda connection: self.may_persist(connection, observation))
        except BaseException as exc:
            # A persistence failure occurs while _events is suspended at a
            # yielded frame. Inject the real cause so it can settle the same
            # attempt without mislabelling it as an observer disconnect.
            try:
                await finish_cleanup(events.athrow(exc))
            except BaseException:
                pass
            if isinstance(exc, RecoveryWait):
                try:
                    await finish_cleanup(self.ledger.save_prestart_wait(request.attempt_id, exc))
                except Exception:
                    # A previously written checking marker remains observe-only
                    # if the evidence itself cannot be committed.
                    pass
            if not accepted.done():
                accepted.set_exception(exc)
            if not isinstance(exc, RecoveryWait) and accepted.done() and (resume or not accepted.exception()) and not (
                    isinstance(exc, asyncio.CancelledError) and not self.ready):
                # Preparation can fail before _events enters its cleanup. The
                # owner and unstarted state must still match in the same update.
                await finish_cleanup(self.ledger.finish_before_start(request.attempt_id, "failed",
                    error_code=exc.code if isinstance(exc, AppError) else "model_preparation_failed"))
        finally:
            await finish_cleanup(events.aclose())
            async with self.driver_lock:
                if self.drivers.get(request.attempt_id, (None,))[0] is asyncio.current_task():
                    self.drivers.pop(request.attempt_id, None)

    async def _stream(self, request):
        await self._submit(request, streaming=True)
        async for event in self._observe(request, streaming=True):
            yield event

    async def _observe(self, request, *, streaming):
        from .results import check_attempt_context

        cursor = 0
        while True:
            row = await self.ledger.get(request.attempt_id)
            if row is None:
                raise AppError("attempt_not_found", status=404)
            try:
                selected = await check_attempt_context(self, row, request.context)
                await self.recheck(SimpleNamespace(context=request.context, source_refs=selected))
            except RecoveryWait as exc:
                from .recovery_wait import preserve_wait_safety

                if (row["principal_id"] == request.context.principal_id
                        and row["context_fingerprint"] == context_fingerprint(request.context)):
                    preserve_wait_safety(row, exc)
                raise
            self._raise_prestart_observation(row)
            if streaming:
                events = await self.ledger.stream_events(request.attempt_id, cursor)
                for saved in events:
                    cursor = saved["sequence"]
                    yield replace(event_from_wire(saved["event"]), sequence=cursor)
                if len(events) == 128:
                    continue
            if row["status"] in {"succeeded", "failed", "cancelled", "unknown"}:
                # Read the ledger after the persisted event prefix. Notification
                # delivery and consumption are deliberately not result facts.
                result = await self.result(request.attempt_id, context=request.context)
                yield ModelEvent("receipt", receipt=result.receipt, data=result, sequence=cursor + 1 if streaming else None)
                return
            await asyncio.sleep(0.1)

    def _raise_prestart_observation(self, row, *, allow_due=False):
        from .recovery_wait import wait_error

        if row.get("status") != "queued":
            return
        state = row.get("recovery_state")
        if state == "checking":
            driver = self.drivers.get(row["attempt_id"])
            if row.get("broker_instance") != self.instance_id or not driver or driver[0].done():
                raise AppError("model_prestart_observation_required", status=503,
                    details={"attempt_id": row["attempt_id"], "action": "observe_original_attempt"})
        elif not (allow_due and state == "scheduled" and row.get("recovery_due")):
            error = wait_error(row)
            if error:
                raise error

    async def generation_profile(self, request):
        """Read the selected native route without creating an attempt or permit."""
        request.budget.check()
        async with request.budget.timeout():
            await self.recheck(request)
            plan = await self.catalog.resolve(request)
        output = effective_output_tokens(plan, request)
        parameters = merged_parameters(plan, request)
        return {"route_id": plan.route_id, "route_revision": plan.revision,
                "protocol": plan.protocol, "model": plan.model, "quota_group": plan.quota_group,
                "verified_capabilities": sorted(plan.verified_capabilities),
                "max_output_tokens": output,
                "output_parameters": with_output_tokens(plan.protocol,
                    {"max_completion_tokens": output} if plan.protocol == "openai.chat"
                    and "max_completion_tokens" in parameters else {}, output)}

    async def _events(self, request, *, stream, completion=None, resume=False):
        frozen = request
        if not self.ready:
            raise AppError("broker_unavailable", status=503)
        if resume:
            row, claimed, saved_request = await self.ledger.claim_queued_submission(frozen)
            if not claimed:
                result = ModelResult((), receipt(row))
                yield ModelEvent("receipt", receipt=result.receipt, data=result)
                return
            # The retry digest intentionally excludes the duration contract's
            # caller budget, so execute the saved original rather than the retry body.
            frozen = request_from_wire(saved_request)
            request = frozen
        waiting_timeout(request)
        validate_provider_session(request.provider_session_scope,
            parameters=request.parameters, native_payload=request.native_payload)
        if request.service_class == ServiceClass.CONTROL:
            raise AppError("control_does_not_generate")
        if type(request.max_output_tokens) is not int or request.max_output_tokens < 0:
            raise AppError("invalid_output_token_budget")
        if request.estimated_input_tokens is not None and (
            type(request.estimated_input_tokens) is not int or request.estimated_input_tokens < 0
        ):
            raise AppError("invalid_input_token_estimate")
        async with waiting(request):
            await self.recheck(request)
            request = await self.prepare_media(request)
        request = replace(request, required_capabilities=requested_capabilities(request, stream))
        saved_attempt = await self.ledger.get(request.attempt_id) if resume else None
        plan = (await self.catalog.resolve(request, frozen_attempt=saved_attempt)
                if saved_attempt is not None else await self.catalog.resolve(request))
        if plan.primary_protocol and plan.primary_protocol != plan.protocol:
            from .route_adaptation import adapt_request
            request = adapt_request(request, plan.primary_protocol, plan.protocol)
        request = replace(request, max_output_tokens=effective_output_tokens(plan, request))
        if self.credential_store:
            from .credentials import provider_reference

            provider_reference(plan.provider_id, plan.credential_ref, self.provider_credential_refs)
        api_key = (
            await self.credential_store.resolve(plan.credential_ref)
            if self.credential_store
            else None
        )
        state_reference = (request.native_payload or {}).get(
            "previous_response_id", request.parameters.get("previous_response_id")
        )
        if state_reference:
            async with self.database.transaction(waiting_budget(request), read_only=True) as conn:
                previous = await (
                    await conn.execute(
                        "SELECT provider_id,model,protocol,source_refs,context_fingerprint,persona_revision "
                        "FROM models.attempts "
                        "WHERE principal_id=%s AND provider_handle=%s AND status='succeeded' LIMIT 1",
                        (request.context.principal_id, state_reference),
                    )
                ).fetchone()
            if (
                not previous
                or any(
                    previous[key] != getattr(plan, key)
                    for key in ("provider_id", "model", "protocol")
                )
                or (
                    previous["context_fingerprint"] != context_fingerprint(request.context)
                    or previous["persona_revision"] != request.persona_revision
                )
            ):
                raise AppError("state_reference_route_incompatible", status=409)
            previous_refs = tuple(SourceRef(**ref) for ref in previous["source_refs"] or ())
            if previous_refs:
                await self.recheck(replace(request, source_refs=previous_refs))
                request = replace(
                    request,
                    source_refs=tuple(
                        {
                            digest(ref): ref for ref in (*request.source_refs, *previous_refs)
                        }.values()
                    ),
                )
        # Compilation/file reading is preparation, before a scarce model permit is held.
        if self.prepare_pending >= 64:
            raise AppError("model_preparation_queue_full", status=429)
        self.prepare_pending += 1
        try:
            async with waiting(request):
                async with self.prepare_slots:
                    prepare = asyncio.create_task(
                        asyncio.to_thread(
                            self.compiler.compile, plan, request, stream=stream, api_key=api_key
                        )
                    )
                    try:
                        call = await asyncio.shield(prepare)
                    except asyncio.CancelledError:
                        # The bounded preparation slot remains held until the underlying
                        # thread has actually finished; cancellation is not thread removal.
                        await finish_cleanup(prepare)
                        raise
        finally:
            self.prepare_pending -= 1
        estimate = request.estimated_input_tokens
        if estimate is None:
            estimate = input_tokens(plan.protocol, call.body)
        units = {"tokens": estimate + request.max_output_tokens}
        await self.event("model.queued", "started", request.attempt_id)
        attempt_generation = (saved_attempt.get("data_generation") if saved_attempt is not None
                              else self.data_generation)
        row, created = await self.ledger.create(request, plan, units, frozen=frozen,
                                                 streaming=stream, completion=completion,
                                                 input_estimate=estimate, data_generation=attempt_generation)
        if not created and resume and row["status"] == "queued" and any(
                row[key] != getattr(plan, attribute) for key, attribute in (
                    ("route_id", "route_id"), ("route_revision", "revision"),
                    ("provider_id", "provider_id"), ("model", "model"),
                    ("protocol", "protocol"), ("quota_group", "quota_group"))):
            raise AppError("model_route_changed_before_execution", status=409)
        if (not created and not (resume and row["status"] == "queued")) or row["status"] == "cancelled":
            await self.recheck(request)
            if row["result"]:
                result = result_from_wire({**row["result"], "receipt": wire(receipt(row))})
            else:
                result = ModelResult((), receipt(row))
            yield ModelEvent("receipt", receipt=result.receipt, data=result)
            return
        if resume:
            await self.ledger.prestart_check(request.attempt_id, active=False)
        yield ModelEvent("accepted", receipt=receipt(row))
        if request.attempt_id in self.active:
            raise AppError("attempt_already_running", status=409)
        self.active[request.attempt_id] = asyncio.current_task()
        acquired, submitted, terminal, local_acquired, network_started = False, False, False, False, False
        collector = ResponseCollector(plan.protocol)
        started = None
        timing = HttpTiming(row.get("queued_at"))
        read_complete = False
        local_slots = self.diagnostic_slots if request.function == "ops.diagnose" else self.cloud_slots
        http_session = self.diagnostic_session if request.function == "ops.diagnose" else self.session
        try:
            await self.ledger.record_wire(request.attempt_id, digest(call.body),
                {"route_id": plan.route_id, "quota_domain": plan.quota_group,
                 "protocol": plan.protocol, "primary_route_id": plan.primary_route_id,
                 "reason": plan.selection_reason})
            policy = self.scheduler.policies[plan.quota_group]
            age = ((datetime.now(UTC) - row["queued_at"]).total_seconds()
                   if row.get("queued_at") is not None else 0)
            candidates = [(waiting_timeout(request), "business_deadline_expired"),
                          (max(0, request.max_queue_wait - age) if request.max_queue_wait is not None else None,
                           "queue_wait_expired"),
                          (max(0, policy.max_wait_seconds - age) if policy.max_wait_seconds is not None else None,
                           "queue_wait_expired")]
            limits = [(seconds, why) for seconds, why in candidates if seconds is not None]
            limit, reason = min(limits, key=lambda item: item[0]) if limits else (None, None)
            try:
                async with asyncio.timeout(limit):
                    while True:
                        await self.scheduler.acquire(request, plan.quota_group, units, route_id=plan.route_id)
                        acquired = True
                        timing.mark("scheduler_admitted")
                        timing.mark("local_slot_wait_start")
                        await local_slots.acquire()
                        local_acquired = True
                        timing.mark("local_slot_wait_end")
                        if not await self.scheduler.group_deferred(plan.quota_group, plan.route_id):
                            break
                        # A sibling can receive 429 while this request is waiting
                        # for a socket. Return both reservations and wait in the
                        # scheduler rather than sending into a known cooldown.
                        local_slots.release()
                        local_acquired = False
                        await self.scheduler.release(plan.quota_group, request.attempt_id, not_started=True)
                        acquired = False
            except TimeoutError:
                raise waiting_expired(request, reason=reason) from None
            # The frozen duration becomes an absolute execution deadline only
            # after the shared supplier permit has actually been granted.
            request = replace(request, budget=execution_budget(request))
            async with request.budget.timeout():
                await self.ledger.prestart_check(request.attempt_id, active=True)
                await self.recheck(request)
                if not self.ready:
                    raise AppError("broker_stopping", status=503)
                saved_attempt = await self.ledger.get(request.attempt_id)
                await self.catalog.resolve(request, frozen_attempt=saved_attempt)
                await self.recheck_behaviors(request)
                if request.execution_timeout_seconds is None:
                    await self.ledger.started(request.attempt_id, None)
                else:
                    await self.ledger.started(request.attempt_id, None,
                                              deadline_at=request.budget.deadline_at)
                submitted, started = True, time.monotonic()
                timing.mark("execution_started")
                await self.scheduler.submitted(plan.quota_group, request.attempt_id)
                await self.event("model.submitted", "started", request.attempt_id)
            async with request.budget.timeout():
                # Policy or installation generation can change while writing
                # the started receipt. Recheck before the provider call.
                await self.recheck_behaviors(request)
                await self.ledger.assert_generation(request.attempt_id)
                network_started = True
                async with http_session.post(
                    call.url,
                    json=call.body,
                    headers=call.headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=request.budget.remaining(), sock_connect=30),
                    trace_request_ctx=timing,
                ) as response:
                    timing.mark("response_headers")
                    if response.status >= 300:
                        if response.status in {429, 503}:
                            until = retry_after(response.headers.get("Retry-After"))
                            if until is None:
                                from .retry import retry_delay_seconds
                                until = datetime.now(UTC) + timedelta(seconds=retry_delay_seconds(1))
                            if response.status == 429:
                                await self.ledger.defer_quota(plan.quota_group, until)
                                await self.scheduler.defer_group(plan.quota_group, until)
                            else:
                                await self.ledger.defer_route(plan.route_id, until)
                                await self.scheduler.defer_route(plan.quota_group, plan.route_id, until)
                        # Provider error bodies may contain secrets or prompts; retain status only.
                        raise AppError("upstream_http_" + str(response.status), status=502)
                    if stream and plan.protocol not in {"openai.embedding", "cohere.rerank"}:
                        buffer = b""
                        first = True
                        observed_handle = None
                        async for chunk in response.content.iter_chunked(32768):
                            if chunk:
                                timing.mark("first_body_byte")
                            yield ModelEvent(
                                "native", data={"chunk_b64": base64.b64encode(chunk).decode()}
                            )
                            buffer = (buffer + chunk).replace(b"\r\n", b"\n")
                            if len(buffer) > MAX_SSE_EVENT_BYTES:
                                raise AppError("upstream_event_too_large", status=502)
                            while b"\n\n" in buffer:
                                block, buffer = buffer.split(b"\n\n", 1)
                                data = b"\n".join(
                                    line[5:].lstrip()
                                    for line in block.split(b"\n")
                                    if line.startswith(b"data:")
                                )
                                if not data or data == b"[DONE]":
                                    continue
                                value = json.loads(data)
                                handle = _stream_handle(plan.protocol, value)
                                if isinstance(handle, str) and handle and observed_handle is None:
                                    await self.ledger.checkpoint(request.attempt_id, provider_handle=handle,
                                                                 output_observed=False)
                                    observed_handle = handle
                                for part in collector.feed(value):
                                    if first and (part.kind != "text" or part.text):
                                        timing.mark("first_valid_output")
                                        await self.ledger.checkpoint(
                                            request.attempt_id,
                                            first_output_ms=(time.monotonic() - started) * 1000,
                                        )
                                        first = False
                                    yield ModelEvent(
                                        "text" if part.kind == "text" else "part",
                                        text=part.text,
                                        part=part,
                                    )
                        read_complete = True
                        timing.mark("read_complete")
                        if buffer.strip():
                            raise AppError("incomplete_native_stream", status=502)
                        if not collector.completed:
                            raise AppError("incomplete_native_stream", status=502)
                    else:
                        chunks, total_bytes = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            if chunk:
                                timing.mark("first_body_byte")
                            total_bytes += len(chunk)
                            if total_bytes > MAX_RESPONSE_BYTES:
                                raise AppError("upstream_response_too_large", status=502)
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        read_complete = True
                        timing.mark("read_complete")
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise AppError("upstream_response_too_large", status=502)
                        value = json.loads(raw)
                        parts = collector.feed(value)
                        valid_output = any(part.kind != "text" or part.text for part in parts) or (
                                plan.protocol == "openai.embedding" and value.get("data")) or (
                                plan.protocol == "cohere.rerank" and value.get("results"))
                        if valid_output:
                            timing.mark("first_valid_output")
                        await self.ledger.checkpoint(
                            request.attempt_id,
                            provider_handle=value.get("id"),
                            first_output_ms=(time.monotonic() - started) * 1000 if valid_output else None,
                            output_observed=bool(valid_output),
                        )
                        if not collector.completed:
                            raise AppError("upstream_response_incomplete", status=502)
                        if plan.protocol == "openai.embedding":
                            vectors = value.get("data", [])
                            if not vectors or any(
                                len(item.get("embedding", [])) != 1024 for item in vectors
                            ):
                                raise AppError("embedding_dimension_mismatch", status=502)
            extra = collector.tool_parts()
            for part in extra:
                yield ModelEvent("part", part=part)
            usage = normalize_usage(plan.protocol, collector.raw_usage, estimate)
            charged = (
                usage.input_tokens + usage.output_tokens
                if usage.input_tokens is not None and usage.output_tokens is not None
                else units["tokens"]
            )
            result_data = {
                "parts": wire(tuple(collector.parts) + extra),
                "native_response": collector.native_response,
                "completion": wire(collector.completion),
            }
            await self.ledger.save_observation(request.attempt_id, timing=timing.record(),
                diagnostic={"category": "complete" if collector.completion.state == "complete" else "known_incomplete",
                            "part_kinds": sorted({part.kind for part in collector.parts}),
                            "part_count": len(collector.parts), "native_completed": collector.completed})
            settled_status = "failed" if collector.completion.provider_status == "failed" else "succeeded"
            provider_error = ((collector.native_response or {}).get("error") or {}).get("code")
            row = await self.ledger.finish(
                request.attempt_id,
                settled_status,
                usage=usage,
                result=result_data,
                charged_units={"tokens": charged},
                error_code=(provider_error or "upstream_generation_failed") if settled_status == "failed" else None,
                provider_handle=(collector.native_response or {}).get(
                    "id", (collector.native_response or {}).get("responseId")
                ),
                validate_result=lambda connection: self.may_persist(connection, request),
            )
            terminal = True
            await self.scheduler.release(
                plan.quota_group, request.attempt_id, units={"tokens": charged}
            )
            acquired = False
            saved = row["result"]
            result = ModelResult(
                tuple(collector.parts) + extra if saved is not None else (),
                receipt(row),
                collector.native_response if saved is not None else None,
                collector.completion,
            )
            try:
                await self.event("model.finished", settled_status, request.attempt_id)
            except AppError:
                pass
            yield ModelEvent("receipt", receipt=result.receipt, data=result)
        except BaseException as exc:
            if isinstance(exc, RecoveryWait) and not submitted:
                if acquired:
                    await finish_cleanup(self.scheduler.release(plan.quota_group, request.attempt_id, not_started=True))
                    acquired = False
                raise
            # A submitted request may have run even if the client saw no output.
            status = (
                "unknown"
                if submitted
                else "cancelled"
                if isinstance(exc, (asyncio.CancelledError, TimeoutError))
                else "failed"
            )
            if (
                submitted
                and isinstance(exc, AppError)
                and exc.code
                in {
                    "upstream_http_400",
                    "upstream_http_401",
                    "upstream_http_402",
                    "upstream_http_403",
                    "upstream_http_404",
                    "upstream_http_405",
                    "upstream_http_413",
                    "upstream_http_415",
                    "upstream_http_422",
                    "upstream_http_429",
                }
            ):
                status = "failed"
            not_sent = submitted and (not network_started or isinstance(exc, aiohttp.ClientConnectorError))
            incomplete = isinstance(exc, AppError) and exc.code in {"incomplete_native_stream", "upstream_response_incomplete"}
            if not_sent or (submitted and read_complete and not incomplete):
                status = "failed"
            code = (
                exc.code
                if isinstance(exc, AppError)
                else (
                    "cancelled_unknown"
                    if isinstance(exc, asyncio.CancelledError)
                    else "timeout_unknown"
                    if isinstance(exc, TimeoutError)
                    else "transport_unknown"
                )
            )
            if not_sent and not isinstance(exc, AppError):
                code = "transport_not_sent"
            elif read_complete and not isinstance(exc, AppError) and not isinstance(exc, (asyncio.CancelledError, TimeoutError)):
                code = "invalid_upstream_response"
            if request.execution_timeout_seconds is not None and not submitted:
                if isinstance(exc, AppError) and exc.code in {"queue_wait_expired", "business_deadline_expired"}:
                    status = "cancelled"
                elif isinstance(exc, (TimeoutError, asyncio.CancelledError)):
                    expired = waiting_expired(request)
                    code = (expired.code if expired.code == "business_deadline_expired" else
                        "execution_deadline_expired" if acquired and isinstance(exc, TimeoutError) else "cancelled_before_start")
            if not terminal and isinstance(exc, (asyncio.CancelledError, GeneratorExit)):
                # Closing a stream/consumer is not a human cancellation. Persist
                # the distinction before classifying the original unknown result;
                # a concurrent explicit cancel always wins the ledger UPSERT.
                if not self.ready:
                    code = "broker_stopping"
                else:
                    cancelled = await finish_cleanup(self.ledger.request_cancel(
                        request.attempt_id, reason="consumer_interrupted", expected_owner=self.ledger.instance_id))
                    code = ("cancelled_unknown" if submitted else "cancelled_before_start") if (
                        cancelled and cancelled.get("cancellation_reason") == "explicit_cancel") else "consumer_interrupted"
                    if not submitted and request.business_deadline_at is not None and request.business_deadline_at <= datetime.now(UTC):
                        code = "business_deadline_expired"
                status = "unknown" if submitted else "cancelled"
            elif not terminal and isinstance(exc, AppError) and exc.code == "attempt_cancelled_before_start":
                cancelled = await self.ledger.get(request.attempt_id)
                if cancelled and cancelled.get("cancellation_reason"):
                    status, code = "cancelled", _cancelled_before_start(cancelled)
            if not terminal and not (not self.ready and not submitted):
                category = ("not_sent" if not_sent else "incomplete_output" if incomplete else "known_invalid" if read_complete else
                    "incomplete_output" if collector.parts else "upstream_unknown" if submitted else "not_started")
                diagnostic = {"category": category, "part_count": len(collector.parts),
                    "part_kinds": sorted({part.kind for part in collector.parts}),
                    "text_bytes": sum(len((part.text or "").encode()) for part in collector.parts if part.kind == "text"),
                    "native_completed": collector.completed, "completion": wire(collector.completion)}
                if submitted:
                    partial_usage = normalize_usage(plan.protocol, collector.raw_usage, estimate) if collector.raw_usage else None
                    await finish_cleanup(self.ledger.save_observation(request.attempt_id,
                        timing=timing.record(), diagnostic=diagnostic))
                    row = await finish_cleanup(
                        self.ledger.finish(request.attempt_id, status, error_code=code, usage=partial_usage,
                            dispatch="not_started" if not_sent else None,
                            charged_units={"tokens": 0} if not_sent else None)
                    )
                else:
                    row = await finish_cleanup(self.ledger.finish_before_start(request.attempt_id, status,
                        error_code=code, timing=timing.record(), diagnostic=diagnostic))
                if acquired:
                    await finish_cleanup(
                        self.scheduler.release(
                            plan.quota_group,
                            request.attempt_id,
                            not_started=not submitted or not_sent,
                        )
                    )
                    acquired = False
                if row is not None:
                    try:
                        await finish_cleanup(self.event("model.finished", status, request.attempt_id))
                    except AppError:
                        pass
            elif acquired:
                await finish_cleanup(self.scheduler.release(plan.quota_group, request.attempt_id, not_started=True))
            raise
        finally:
            self.active.pop(request.attempt_id, None)
            if local_acquired:
                local_slots.release()

    @asynccontextmanager
    async def resource(self, request):
        permit = await self.begin_external(request)
        try:
            async with permit.clock.running():
                yield permit
        except BaseException:
            await finish_cleanup(permit.unknown("external_interrupted"))
            raise
        finally:
            if not permit.done:
                await finish_cleanup(permit.unknown("external_missing_receipt"))

    async def begin_external(self, request):
        if not self.ready:
            raise AppError("broker_unavailable", status=503)
        if request.service_class == ServiceClass.CONTROL:
            raise AppError("control_does_not_generate")
        waiting_timeout(request)
        if any(
            type(value) not in (int, float) or not math.isfinite(value) or value < 0
            for value in request.estimated_units.values()
        ):
            raise AppError("invalid_resource_units")
        plan = RoutePlan(
            "external:" + request.provider_id,
            1,
            request.provider_id,
            request.function,
            "external",
            "",
            "",
            request.quota_group,
            frozenset(),
            {},
        )
        await self.event("resource.queued", "started", request.attempt_id)
        row, created = await self.ledger.create(request, plan, request.estimated_units,
                                                 data_generation=self.data_generation)
        if not created:
            raise AppError("attempt_already_exists", status=409, details=wire(receipt(row)))
        if row["status"] == "cancelled":
            raise AppError("attempt_cancelled_before_start", status=409)
        acquired = False
        try:
            await self.scheduler.acquire(request, request.quota_group, request.estimated_units)
            acquired = True
            if request.execution_start != "provider_running":
                request = replace(request, budget=execution_budget(request))
            if request.execution_timeout_seconds is not None and request.execution_start != "provider_running":
                async with request.budget.timeout():
                    await self.ledger.admitted(request.attempt_id, request.budget.deadline_at)
        except BaseException as exc:
            await finish_cleanup(
                self.ledger.finish(
                    request.attempt_id, "cancelled", error_code=(
                        exc.code if isinstance(exc, AppError) and exc.code in {"queue_wait_expired", "business_deadline_expired"}
                        else waiting_expired(request).code if request.execution_timeout_seconds is not None
                        and request.business_deadline_at is not None and request.business_deadline_at <= datetime.now(UTC)
                        else "external_not_started")
                )
            )
            if acquired:
                await finish_cleanup(self.scheduler.release(request.quota_group,
                    request.attempt_id, not_started=True))
            raise
        permit = ExternalPermit(self, request)
        self.external[request.attempt_id] = permit
        return permit


class ExternalPermit:
    def __init__(self, broker, request):
        self.broker, self.clock = broker, ExternalExecutionClock(request)
        self.submitted_at = None
        self.done = False
        self.finish_lock = asyncio.Lock()

    @property
    def request(self):
        return self.clock.request

    async def observe_provider(self, state, *, provider_handle):
        async with self.finish_lock:
            return await self.broker.observe_external(self.request.attempt_id, context=self.request.context,
                provider_handle=provider_handle, state=state)

    async def submitted(self, provider_handle=None):
        async with self.finish_lock:
            if self.done:
                raise AppError("attempt_already_finished", status=409)
            self.clock.check()
            await finish_cleanup(self._mark_submitted(provider_handle))

    async def _mark_submitted(self, provider_handle):
        if self.submitted_at is None:
            await self.broker.recheck(self.request)
            self.clock.check()
            policy = self.broker.scheduler.policies[self.request.quota_group]
            await self.broker.ledger.started(self.request.attempt_id, policy.unknown_hold_seconds)
            self.submitted_at = datetime.now(UTC)
            await self.broker.scheduler.submitted(self.request.quota_group, self.request.attempt_id)
            self.clock.check()
            await self.broker.event("resource.submitted", "started", self.request.attempt_id)
        if provider_handle:
            await self.broker.ledger.checkpoint(
                self.request.attempt_id, provider_handle=provider_handle, output_observed=False
            )

    async def finish(self, usage=None, result=None):
        return await self._finish("succeeded", usage=usage, result=result)

    async def unknown(self, error_code="external_effect_unknown"):
        return await self._finish("unknown", error_code=error_code)

    async def rejected(self, error_code, usage=None):
        """Only a provider's confirmed no-effect rejection can release unknown occupancy."""
        return await self._finish("failed", usage=usage, error_code=error_code)

    async def _finish(self, status, *, usage=None, result=None, error_code=None):
        async with self.finish_lock:
            if self.done:
                return await self.broker.status(self.request.attempt_id)
            # submitted() may have been persisting its start while this caller waited.
            # Choose the effect/dispatch state only after that transition has completed.
            if self.submitted_at is None:
                if status == "succeeded":
                    raise AppError("external_attempt_not_submitted", status=409)
                status = "cancelled"
            if usage is not None and not isinstance(usage, dict):
                raise AppError("invalid_resource_usage")
            units = usage.get("units") if usage is not None else None
            if units is not None and (
                not isinstance(units, dict)
                or any(
                    type(value) not in (int, float) or not math.isfinite(value) or value < 0
                    for value in units.values()
                )
            ):
                raise AppError("invalid_resource_usage")
            if units is not None:
                from .scheduler import request_cost

                units = {**self.request.estimated_units, **units}
                request_cost(units)
            row = await self.broker.ledger.finish(
                self.request.attempt_id,
                status,
                usage=ModelUsage(
                    raw=usage, metering="reported" if usage is not None else "unknown"
                ),
                result=result,
                error_code=error_code,
                charged_units=units,
                validate_result=lambda connection: self.broker.may_persist(
                    connection, self.request
                ),
            )
            await self.broker.scheduler.release(
                self.request.quota_group,
                self.request.attempt_id,
                units=units,
                hold_until=row["hold_until"] if row["status"] == "unknown" else None,
                not_started=self.submitted_at is None,
            )
            self.done = True
            self.broker.external.pop(self.request.attempt_id, None)
            try:
                await self.broker.event("resource.finished", status, self.request.attempt_id)
            except AppError:
                pass
            return receipt(row)
