"""One active model-capacity owner, actual network cancellation and durable attempts."""

from __future__ import annotations

import asyncio
import base64
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime
import json
import math
import time
from uuid import uuid4

import aiohttp
import psycopg

from tianshu.runtime.errors import AppError
from tianshu.runtime.cancellation import finish_cleanup
from tianshu.runtime.serialization import wire
from tianshu.types import ServiceClass, SourceRef
from .catalog import ModelCatalog, RoutePlan
from .codec import result_from_wire
from .ledger import AttemptLedger, context_fingerprint, digest, receipt
from .protocols import NativeCompiler, ResponseCollector, normalize_usage, requested_capabilities
from .scheduler import QuotaScheduler
from .types import ModelEvent, ModelResult, ModelUsage
from .budgets import effective_output_tokens, with_output_tokens, merged_parameters
from .estimation import input_tokens
from .timing import execution_budget, waiting_budget, waiting_timeout, waiting, waiting_expired

MAX_RESPONSE_BYTES = 32 * 1024 * 1024
MAX_SSE_EVENT_BYTES = 2 * 1024 * 1024


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
    ):
        self.database = database
        self.catalog = ModelCatalog(database)
        self.compiler = NativeCompiler(files, secrets)
        self.instance_id = str(uuid4())
        self.ledger = AttemptLedger(database, self.instance_id)
        self.session = None
        self.scheduler = None
        self.owner = None
        self.owner_watch = None
        self.active = {}
        self.external = {}
        self.ready = False
        self.events = events
        self.validate_submission = validate_submission
        self.validate_persistence = validate_persistence
        self.prepare_slots = asyncio.Semaphore(8)
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
                    if inline_bytes > 32 * 1024 * 1024 or count > 128:
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
                    "SELECT e.*,r.provider_id,r.model,r.revision AS route_revision "
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
            await self.scheduler.start()
            self.session = aiohttp.ClientSession(
                # The bounded quota scheduler already owns supplier capacity.
                # A connector cap would add an invisible queue after admission.
                connector=aiohttp.TCPConnector(limit=0, limit_per_host=0),
                auto_decompress=False,
                timeout=aiohttp.ClientTimeout(total=None, sock_connect=30),
            )
            self.ready = True
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
            for task in list(self.active.values()):
                task.cancel()

    async def _watch_external(self):
        while self.ready:
            await asyncio.sleep(0.5)
            now = datetime.now(UTC)
            for attempt_id, permit in list(self.external.items()):
                if permit.request.budget.deadline_at <= now:
                    await permit.unknown("external_deadline_exceeded")

    async def close(self):
        self.ready = False
        for task in (self.owner_watch, getattr(self, "external_watch", None)):
            if task:
                task.cancel()
        for task in list(self.active.values()):
            task.cancel()
        await asyncio.gather(*self.active.values(), return_exceptions=True)
        for permit in list(self.external.values()):
            await permit.unknown("broker_stopping")
        if self.scheduler:
            await self.scheduler.close()
        if self.session:
            await self.session.close()
        if self.owner:
            await self.owner.close()
        await asyncio.gather(
            *(task for task in (self.owner_watch, getattr(self, "external_watch", None)) if task),
            return_exceptions=True,
        )

    async def status(self, attempt_id):
        row = await self.ledger.get(attempt_id)
        return receipt(row) if row else None

    async def result(self, attempt_id, *, context):
        from .results import read_result

        return await read_result(self, attempt_id, context=context)

    async def reload(self):
        from .scheduler import GroupState

        policies = await self.catalog.policies()
        async with self.scheduler.lock:
            for name in policies:
                self.scheduler.groups.setdefault(name, GroupState())
            self.scheduler.policies = policies
            self.scheduler.changed.set()
        return {"quota_groups": list(policies)}

    async def cancel(self, attempt_id):
        row = await self.ledger.request_cancel(attempt_id)
        await self.scheduler.cancel(attempt_id)
        if not row:
            return None
        if row["status"] == "queued" and row.get("execution_timeout_seconds") is not None:
            # request_cancel locked this row before submission. Its CAS now
            # forbids a future start, so a caller timeout has a known receipt.
            code = ("business_deadline_expired" if row.get("business_deadline_at")
                    and row["business_deadline_at"] <= datetime.now(UTC) else "cancelled_before_start")
            row = await self.ledger.finish(attempt_id, "cancelled", error_code=code)
        task = self.active.get(attempt_id)
        if task:
            task.cancel()
        elif attempt_id in self.external and self.external[attempt_id].submitted_at is None:
            return await self.external[attempt_id].unknown("cancelled_before_external_submit")
        elif row["status"] == "queued":
            row = await self.ledger.finish(
                attempt_id, "cancelled", error_code=("business_deadline_expired"
                    if row.get("execution_timeout_seconds") is not None and row.get("business_deadline_at")
                    and row["business_deadline_at"] <= datetime.now(UTC) else "cancelled_before_start")
            )
        # External domain I/O sees cancel_requested through its separate control watcher.
        return receipt(row)

    async def generate(self, request):
        result = None
        async for event in self._events(request, stream=False):
            if event.kind == "receipt":
                result = event.data
        return result

    def stream(self, request):
        return self._events(request, stream=True)

    async def generation_profile(self, request):
        """Read the selected native route without creating an attempt or permit."""
        request.budget.check()
        async with request.budget.timeout():
            await self.recheck(request)
            plan = await self.catalog.resolve(request)
        output = effective_output_tokens(plan, request)
        parameters = merged_parameters(plan, request)
        return {"route_id": plan.route_id, "route_revision": plan.revision,
                "protocol": plan.protocol, "model": plan.model,
                "verified_capabilities": sorted(plan.verified_capabilities),
                "max_output_tokens": output,
                "output_parameters": with_output_tokens(plan.protocol,
                    {"max_completion_tokens": output} if plan.protocol == "openai.chat"
                    and "max_completion_tokens" in parameters else {}, output)}

    async def _events(self, request, *, stream):
        if not self.ready:
            raise AppError("broker_unavailable", status=503)
        waiting_timeout(request)
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
        plan = await self.catalog.resolve(request)
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
        row, created = await self.ledger.create(request, plan, units)
        if not created or row["status"] == "cancelled":
            await self.recheck(request)
            if row["result"]:
                result = result_from_wire({**row["result"], "receipt": wire(receipt(row))})
            else:
                result = ModelResult((), receipt(row))
            yield ModelEvent("receipt", receipt=result.receipt, data=result)
            return
        if request.attempt_id in self.active:
            raise AppError("attempt_already_running", status=409)
        self.active[request.attempt_id] = asyncio.current_task()
        acquired, submitted, terminal = False, False, False
        collector = ResponseCollector(plan.protocol)
        started = None
        try:
            await self.scheduler.acquire(request, plan.quota_group, units)
            acquired = True
            # The frozen duration becomes an absolute execution deadline only
            # after the shared supplier permit has actually been granted.
            request = replace(request, budget=execution_budget(request))
            policy = self.scheduler.policies[plan.quota_group]
            async with request.budget.timeout():
                await self.recheck(request)
                if request.execution_timeout_seconds is None:
                    await self.ledger.started(request.attempt_id, policy.unknown_hold_seconds)
                else:
                    await self.ledger.started(request.attempt_id, policy.unknown_hold_seconds,
                                              deadline_at=request.budget.deadline_at)
                submitted, started = True, time.monotonic()
                await self.scheduler.submitted(plan.quota_group, request.attempt_id)
                await self.event("model.submitted", "started", request.attempt_id)
            async with request.budget.timeout():
                async with self.session.post(
                    call.url,
                    json=call.body,
                    headers=call.headers,
                    allow_redirects=False,
                    timeout=aiohttp.ClientTimeout(total=request.budget.remaining(), sock_connect=30),
                ) as response:
                    if response.status >= 300:
                        # Provider error bodies may contain secrets or prompts; retain status only.
                        raise AppError("upstream_http_" + str(response.status), status=502)
                    if stream and plan.protocol not in {"openai.embedding", "cohere.rerank"}:
                        buffer = b""
                        first = True
                        async for chunk in response.content.iter_chunked(32768):
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
                                for part in collector.feed(value):
                                    if first:
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
                        if buffer.strip():
                            raise AppError("incomplete_native_stream", status=502)
                        if not collector.completed:
                            raise AppError("incomplete_native_stream", status=502)
                    else:
                        chunks, total_bytes = [], 0
                        async for chunk in response.content.iter_chunked(65536):
                            total_bytes += len(chunk)
                            if total_bytes > MAX_RESPONSE_BYTES:
                                raise AppError("upstream_response_too_large", status=502)
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        if len(raw) > MAX_RESPONSE_BYTES:
                            raise AppError("upstream_response_too_large", status=502)
                        value = json.loads(raw)
                        collector.feed(value)
                        await self.ledger.checkpoint(
                            request.attempt_id,
                            provider_handle=value.get("id"),
                            first_output_ms=(time.monotonic() - started) * 1000,
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
            if request.execution_timeout_seconds is not None and not submitted:
                if isinstance(exc, AppError) and exc.code in {"queue_wait_expired", "business_deadline_expired"}:
                    status = "cancelled"
                elif isinstance(exc, (TimeoutError, asyncio.CancelledError)):
                    expired = waiting_expired(request)
                    code = (expired.code if expired.code == "business_deadline_expired" else
                        "execution_deadline_expired" if acquired and isinstance(exc, TimeoutError) else "cancelled_before_start")
            if not terminal:
                row = await finish_cleanup(
                    self.ledger.finish(request.attempt_id, status, error_code=code)
                )
                if acquired:
                    hold = row["hold_until"] if status == "unknown" else None
                    await finish_cleanup(
                        self.scheduler.release(
                            plan.quota_group,
                            request.attempt_id,
                            hold_until=hold,
                            not_started=not submitted,
                        )
                    )
                    acquired = False
                try:
                    await finish_cleanup(self.event("model.finished", status, request.attempt_id))
                except AppError:
                    pass
            raise
        finally:
            self.active.pop(request.attempt_id, None)

    @asynccontextmanager
    async def resource(self, request):
        permit = await self.begin_external(request)
        try:
            async with permit.request.budget.timeout():
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
        row, created = await self.ledger.create(request, plan, request.estimated_units)
        if not created:
            raise AppError("attempt_already_exists", status=409, details=wire(receipt(row)))
        if row["status"] == "cancelled":
            raise AppError("attempt_cancelled_before_start", status=409)
        acquired = False
        try:
            await self.scheduler.acquire(request, request.quota_group, request.estimated_units)
            acquired = True
            request = replace(request, budget=execution_budget(request))
            if request.execution_timeout_seconds is not None:
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
        self.broker, self.request = broker, request
        self.submitted_at = None
        self.done = False
        self.finish_lock = asyncio.Lock()

    async def submitted(self, provider_handle=None):
        async with self.finish_lock:
            if self.done:
                raise AppError("attempt_already_finished", status=409)
            self.request.budget.check()
            await finish_cleanup(self._mark_submitted(provider_handle))

    async def _mark_submitted(self, provider_handle):
        if self.submitted_at is None:
            await self.broker.recheck(self.request)
            self.request.budget.check()
            policy = self.broker.scheduler.policies[self.request.quota_group]
            await self.broker.ledger.started(self.request.attempt_id, policy.unknown_hold_seconds)
            self.submitted_at = datetime.now(UTC)
            await self.broker.scheduler.submitted(self.request.quota_group, self.request.attempt_id)
            self.request.budget.check()
            await self.broker.event("resource.submitted", "started", self.request.attempt_id)
        if provider_handle:
            await self.broker.ledger.checkpoint(
                self.request.attempt_id, provider_handle=provider_handle
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
