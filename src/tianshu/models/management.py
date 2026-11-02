"""Model configuration APIs backed by the same catalogue used for actual execution."""

from psycopg.types.json import Jsonb
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib

from tianshu.runtime.api import CommandResult, Page, command_body, json_response, require_context
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import Coverage
from .catalog import FUNCTIONS, NATIVE_PROTOCOLS, QuotaPolicy, embedding_fingerprint
from .ledger import digest, receipt
from .types import ModelMessage, ModelPart, ModelRequest
from tianshu.types import RequestBudget, ServiceClass


def text(body, name):
    value = body.get(name)
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        raise AppError("invalid_" + name)
    return value


def positive(body, name, *, optional=False, allow_zero=False):
    value = body.get(name)
    if optional and value is None:
        return None
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise AppError("invalid_" + name)
    return value


async def configuration(conn, settings):
    result = {"functions": list(FUNCTIONS), "protocols": sorted(NATIVE_PROTOCOLS)}
    for key, statement in (
        ("providers", "SELECT * FROM models.providers ORDER BY name,provider_id"),
        ("routes", "SELECT * FROM models.routes ORDER BY route_id"),
        ("quota_groups", "SELECT * FROM models.quota_groups ORDER BY quota_group"),
        ("bindings", "SELECT * FROM models.bindings ORDER BY function,service_class,actor_id"),
    ):
        result[key] = await (await conn.execute(statement)).fetchall()
    result["embedding_profile"] = await (
        await conn.execute("SELECT * FROM models.embedding_profile WHERE singleton")
    ).fetchone()
    sealed = {
        row["reference"]
        for row in await (
            await conn.execute(
                "SELECT reference FROM runtime.credentials WHERE reference=ANY(%s)",
                ([row["credential_ref"] for row in result["providers"]],),
            )
        ).fetchall()
    }
    from .credentials import provider_reference

    for provider in result["providers"]:
        reference = provider["credential_ref"]
        try:
            provider_reference(
                provider["provider_id"],
                reference,
                settings.get("broker", {}).get("provider_credential_refs", {}),
            )
            configured = (
                reference in sealed
                if reference.startswith("sealed:")
                else bool(settings.secrets.environ.get(reference.removeprefix("env:")))
            )
        except AppError:
            configured = False
        provider["credential_configured"] = configured
    return result


async def mutate(services, context, operation, body):
    if operation == "bindings" and (body.get("actor_id") or None) != context.actor_id:
        raise AppError("binding_actor_context_required", status=403)
    expected = body.get("expected_revision")
    if type(expected) is not int or expected < 0:
        raise AppError("expected_revision_required")

    async def apply(conn):
        return await _apply_configuration(conn, operation, body, expected, services)

    async def replay(conn, original):
        return replace(original, data=await configuration(conn, services.settings))

    return wire(
        await services.commands.execute(
            context,
            "models." + operation,
            body["client_request_id"],
            digest(body),
            apply,
            on_replay=replay,
        )
    )


async def _apply_configuration(conn, operation, body, expected, services):
    # Catalogue edits and explicit cascades share one short transaction lock.
    await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('models.configuration',0))")
    if operation == "providers":
        protocol = text(body, "protocol")
        if protocol not in NATIVE_PROTOCOLS:
            raise AppError("native_protocol_unsupported", status=422)
        provider_id = text(body, "provider_id")
        from .credentials import provider_reference

        own = "sealed:provider:" + provider_id
        clear = body.get("clear_api_key", False)
        if type(clear) is not bool or clear and body.get("api_key") is not None:
            raise AppError("invalid_provider_credential_change")
        current = await (
            await conn.execute(
                "SELECT * FROM models.providers WHERE provider_id=%s", (provider_id,)
            )
        ).fetchone()
        configured = services.settings.get("broker", {}).get("provider_credential_refs", {})
        if body.get("credential_ref") is not None:
            provider_reference(provider_id, body["credential_ref"], configured)
        reference = (
            own
            if clear or body.get("api_key") is not None
            else body.get("credential_ref") or (current["credential_ref"] if current else own)
        )
        provider_reference(provider_id, reference, configured)
        if clear:
            await conn.execute("DELETE FROM runtime.credentials WHERE reference=%s", (own,))
        if body.get("api_key") is not None:
            await services.credentials.put(conn, reference, body["api_key"])
        values = (
            provider_id,
            text(body, "name"),
            text(body, "base_url"),
            reference,
            protocol,
            bool(body.get("enabled", True)),
        )
        if expected == 0:
            cursor = await conn.execute(
                "INSERT INTO models.providers(provider_id,name,base_url,"
                "credential_ref,protocol,enabled) VALUES(%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING revision",
                values,
            )
        else:
            cursor = await conn.execute(
                "UPDATE models.providers SET name=%s,base_url=%s,"
                "credential_ref=%s,protocol=%s,enabled=%s,revision=revision+1 "
                "WHERE provider_id=%s AND revision=%s RETURNING revision",
                (*values[1:], values[0], expected),
            )
        if current and (current["base_url"] != values[2] or current["protocol"] != protocol):
            await conn.execute(
                "UPDATE models.routes SET verified_capabilities='{}',verification=NULL,revision=revision+1 WHERE provider_id=%s",
                (provider_id,),
            )
    elif operation == "routes":
        caps = body.get("verified_capabilities", [])
        if not isinstance(caps, list) or any(not isinstance(item, str) for item in caps):
            raise AppError("invalid_capabilities")
        # Being configured does not automatically establish capabilities: the UI must
        # submit actual probe/operator evidence, retained next to the route parameters.
        evidence = body.get("verification")
        if caps and not isinstance(evidence, dict):
            raise AppError("capability_verification_required")
        parameters = body.get("parameters", {})
        if not isinstance(parameters, dict):
            raise AppError("invalid_parameters")
        old = await (
            await conn.execute(
                "SELECT provider_id,model,parameters,verification,verified_capabilities FROM models.routes WHERE route_id=%s",
                (text(body, "route_id"),),
            )
        ).fetchone()
        changed_identity = old and (
            old["provider_id"] != body.get("provider_id")
            or old["model"] != body.get("model")
            or old["parameters"] != parameters
        )
        if caps:
            preserved = (
                old
                and not changed_identity
                and evidence == old["verification"]
                and set(caps) <= set(old["verified_capabilities"])
            )
            operator = (
                isinstance(evidence, dict)
                and evidence.get("source") == "operator"
                and bool(evidence.get("observed_at"))
            )
            if operator:
                try:
                    observed_at = datetime.fromisoformat(
                        evidence["observed_at"].replace("Z", "+00:00")
                    )
                    operator = observed_at.tzinfo is not None and not (
                        changed_identity
                        and old["verification"]
                        and evidence["observed_at"] == old["verification"].get("observed_at")
                    )
                except (ValueError, TypeError, AttributeError):
                    operator = False
            if not preserved and not operator:
                caps, evidence = [], None
        values = (
            text(body, "route_id"),
            text(body, "provider_id"),
            text(body, "model"),
            text(body, "quota_group"),
            caps,
            Jsonb(evidence),
            Jsonb(parameters),
            bool(body.get("enabled", True)),
        )
        if expected == 0:
            cursor = await conn.execute(
                "INSERT INTO models.routes(route_id,provider_id,model,"
                "quota_group,verified_capabilities,verification,parameters,enabled) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING revision",
                values,
            )
        else:
            cursor = await conn.execute(
                "UPDATE models.routes SET provider_id=%s,model=%s,"
                "quota_group=%s,verified_capabilities=%s,verification=%s,parameters=%s,enabled=%s,revision=revision+1 "
                "WHERE route_id=%s AND revision=%s RETURNING revision",
                (*values[1:], values[0], expected),
            )
    elif operation == "quota-groups":
        concurrency = positive(body, "concurrency")
        reserve = positive(body, "interactive_reserve", allow_zero=True)
        background = positive(body, "background_max", allow_zero=True)
        if reserve > concurrency or background > concurrency:
            raise AppError("invalid_quota_reservation")
        unit_limits = body.get("unit_limits", {})
        if not isinstance(unit_limits, dict) or any(
            type(value) not in (int, float) or value <= 0 for value in unit_limits.values()
        ):
            raise AppError("invalid_unit_limits")
        values = (
            text(body, "quota_group"),
            concurrency,
            reserve,
            background,
            positive(body, "rpm", optional=True),
            positive(body, "tpm", optional=True),
            positive(body, "max_queue"),
            positive(body, "max_wait_seconds", optional=True),
            positive(body, "unknown_hold_seconds"),
            Jsonb(unit_limits),
            body.get("interactive_rpm_reserve", 0),
            body.get("background_rpm_max"),
            Jsonb(body.get("interactive_unit_reserve", {})),
            Jsonb(body.get("background_unit_max", {})),
        )
        try:
            QuotaPolicy(
                values[0],
                concurrency,
                reserve,
                background,
                rpm=values[4],
                tpm=values[5],
                max_wait_seconds=values[7],
                unit_limits=unit_limits,
                interactive_rpm_reserve=values[10],
                background_rpm_max=values[11],
                interactive_unit_reserve=body.get("interactive_unit_reserve", {}),
                background_unit_max=body.get("background_unit_max", {}),
            )
        except (ValueError, TypeError):
            raise AppError("invalid_quota_reservation") from None
        if expected == 0:
            cursor = await conn.execute(
                "INSERT INTO models.quota_groups(quota_group,concurrency,"
                "interactive_reserve,background_max,rpm,tpm,max_queue,max_wait_seconds,"
                "unknown_hold_seconds,unit_limits,interactive_rpm_reserve,background_rpm_max,"
                "interactive_unit_reserve,background_unit_max) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT DO NOTHING RETURNING revision",
                values,
            )
        else:
            cursor = await conn.execute(
                "UPDATE models.quota_groups SET concurrency=%s,"
                "interactive_reserve=%s,background_max=%s,rpm=%s,tpm=%s,max_queue=%s,"
                "max_wait_seconds=%s,unknown_hold_seconds=%s,unit_limits=%s,interactive_rpm_reserve=%s,"
                "background_rpm_max=%s,interactive_unit_reserve=%s,background_unit_max=%s,revision=revision+1 "
                "WHERE quota_group=%s AND revision=%s RETURNING revision",
                (*values[1:], values[0], expected),
            )
    elif operation == "bindings":
        function = text(body, "function")
        if function not in (*FUNCTIONS, "*"):
            raise AppError("unknown_model_function")
        if function == "*" and body.get("actor_id"):
            raise AppError("default_binding_must_be_system_owned")
        service_class = text(body, "service_class")
        if service_class not in {"interactive", "due", "elastic"}:
            raise AppError("invalid_service_class")
        values = (function, service_class, body.get("actor_id") or "", text(body, "route_id"))
        if expected == 0:
            cursor = await conn.execute(
                "INSERT INTO models.bindings(function,service_class,actor_id,"
                "route_id) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING revision",
                values,
            )
        else:
            cursor = await conn.execute(
                "UPDATE models.bindings SET route_id=%s,revision=revision+1 "
                "WHERE function=%s AND service_class=%s AND actor_id=%s AND revision=%s RETURNING revision",
                (values[3], *values[:3], expected),
            )
    elif operation == "embedding-profile":
        values = (
            text(body, "route_id"),
            text(body, "model_version"),
            text(body, "normalization"),
            body.get("query_instruction", ""),
        )
        if body.get("dimensions", 1024) != 1024:
            raise AppError("embedding_dimension_must_be_1024")
        route = await (
            await conn.execute(
                "SELECT r.*,p.protocol,p.base_url FROM models.routes r "
                "JOIN models.providers p USING(provider_id) WHERE route_id=%s",
                (values[0],),
            )
        ).fetchone()
        if not route or route["protocol"] != "openai.embedding":
            raise AppError("embedding_route_required")
        current = await (
            await conn.execute("SELECT * FROM models.embedding_profile WHERE singleton FOR UPDATE")
        ).fetchone()
        if expected != (current["revision"] if current else 0):
            raise AppError("revision_conflict", status=409)
        if current and any(
            current[key] != value
            for key, value in zip(
                ("route_id", "model_version", "normalization", "query_instruction"), values
            )
        ):
            raise AppError("embedding_rebuild_required", status=409)
        cursor = await conn.execute(
            "INSERT INTO models.embedding_profile(route_id,model_version,"
            "normalization,query_instruction,route_fingerprint) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(singleton) "
            "DO UPDATE SET revision=models.embedding_profile.revision WHERE "
            "models.embedding_profile.revision=%s RETURNING revision",
            (*values, embedding_fingerprint(route), expected),
        )
    else:
        raise AppError("unknown_model_operation", status=404)
    changed = await cursor.fetchone()
    if not changed:
        raise AppError("revision_conflict", status=409)
    return CommandResult(
        body["client_request_id"],
        revision=changed["revision"],
        data=await configuration(conn, services.settings),
    )


def register_routes(app, services):
    async def reload_observation(result):
        try:
            await services.models.reload()
            result["data"]["broker_reload"] = "applied"
        except Exception:
            result["data"]["broker_reload"] = "pending"
        return result

    async def view(request):
        require_context(request, "models.read")
        async with services.database.transaction(read_only=True) as conn:
            return json_response(await configuration(conn, services.settings))

    async def save(request):
        context = require_context(request, "models.manage")
        body = await command_body(request)
        result = await mutate(services, context, request.match_info["operation"], body)
        # Config is committed even if the broker is restarting. Report reload state rather
        # than failing the command and encouraging an unknown write to be repeated.
        return json_response(await reload_observation(result))

    async def delete(request):
        from .configuration_delete import delete_configuration

        context = require_context(request, "models.manage")
        body = dict(await command_body(request))
        operation = request.match_info["operation"]
        identifier = request.match_info.get("identifier")
        if identifier:
            body["provider_id" if operation == "providers" else "route_id"] = identifier
        result = wire(await delete_configuration(services, context, operation, body))
        return json_response(await reload_observation(result))

    async def enumerate_provider(request):
        from .discovery import enumerate_models

        context = require_context(request, "models.manage")
        body = await command_body(request)
        return json_response(
            await enumerate_models(services, context, request.match_info["provider_id"], body)
        )

    async def enumeration_result(request):
        from .discovery import observed

        context = require_context(request, "models.manage")
        body = await request.json()
        if not isinstance(body, dict):
            raise AppError("invalid_input")
        result = await observed(
            services, context, request.match_info["provider_id"], text(body, "client_request_id")
        )
        return json_response({"found": result is not None, "command": result})

    async def attempts(request):
        context = require_context(request)
        limit = min(max(int(request.query.get("limit", 50)), 1), 100)
        cursor_value = request.query.get("cursor")
        async with services.database.transaction(control=True, read_only=True) as conn:
            cursor = await conn.execute(
                "SELECT a.*,c.reason AS cancellation_reason FROM models.attempts a "
                "LEFT JOIN models.cancellations c USING(attempt_id) WHERE principal_id=%s "
                "AND (%s::text IS NULL OR a.attempt_id<%s) ORDER BY a.attempt_id DESC LIMIT %s",
                (context.principal_id, cursor_value, cursor_value, limit + 1),
            )
            rows = await cursor.fetchall()
        more = len(rows) > limit
        rows = rows[:limit]
        return json_response(
            Page(
                tuple(receipt(row) for row in rows),
                rows[-1]["attempt_id"] if more else None,
                Coverage(complete=not more),
            )
        )

    async def cancel(request):
        context = require_context(request)
        body = await command_body(request)
        attempt_id = request.match_info["attempt_id"]
        async with services.database.transaction(control=True, read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT principal_id FROM models.attempts WHERE attempt_id=%s", (attempt_id,)
                )
            ).fetchone()
        if not row:
            raise AppError("attempt_not_found", status=404)
        if (
            row["principal_id"] != context.principal_id
            and "models.manage" not in context.permissions
        ):
            raise AppError("forbidden", status=403)
        result = await services.models.cancel(attempt_id)
        return json_response(
            CommandResult(body["client_request_id"], data=result, attempt_id=attempt_id)
        )

    async def read_attempt(request):
        context = require_context(request)
        body = await request.json()
        if not isinstance(body, dict):
            raise AppError("invalid_input")
        for field in ("actor_id", "person_id", "conversation_id"):
            if body.get(field) is not None and body[field] != getattr(context, field):
                raise AppError("model_result_context_conflict", status=400)
        result = await services.models.result(text(body, "attempt_id"), context=context)
        return json_response(
            {
                "found": result is not None,
                "receipt": result.receipt if result else None,
                "output_available": bool(result and (result.parts or result.native_response)),
            }
        )

    async def probe(request):
        context = require_context(request, "models.manage")
        body = await command_body(request)
        route_id = request.match_info["route_id"]
        expected = body.get("expected_revision")
        if type(expected) is not int:
            raise AppError("expected_revision_required")

        async def replay(conn, original):
            return replace(original, data={"catalog": await configuration(conn, services.settings)})

        cached = await services.commands.replay(
            context, "models.probe", body["client_request_id"], digest(body), on_replay=replay
        )
        if cached:
            return json_response(cached)
        capability = body.get("capability", "text")
        if capability not in {"text", "stream"}:
            raise AppError("probe_requires_actual_modality_fixture", status=422)
        probe_id = (
            "probe-"
            + hashlib.sha256(
                (context.principal_id + route_id + body["client_request_id"]).encode()
            ).hexdigest()
        )
        model_request = ModelRequest(
            probe_id,
            probe_id,
            "chat",
            ServiceClass.INTERACTIVE,
            context,
            RequestBudget(datetime.now(UTC) + timedelta(seconds=180)),
            (ModelMessage("user", (ModelPart("text", text="Reply with OK."),)),),
            route_id=route_id,
            required_capabilities=frozenset(),
            max_output_tokens=8192,
            capability_probe=True,
            route_revision=expected,
        )
        from .budgets import with_output_tokens
        from .completion import content_completion
        output_tokens = body.get("max_output_tokens", 8192)
        timeout_seconds = body.get("timeout_seconds", 180)
        if type(output_tokens) is not int or not 256 <= output_tokens <= 16384:
            raise AppError("invalid_probe_output_budget", status=422)
        if type(timeout_seconds) is not int or not 30 <= timeout_seconds <= 600:
            raise AppError("invalid_probe_timeout", status=422)
        profile = await services.models.generation_profile(model_request)
        model_request = replace(model_request,
            budget=RequestBudget(datetime.now(UTC) + timedelta(seconds=timeout_seconds)),
            execution_timeout_seconds=timeout_seconds,
            protocol=profile["protocol"], max_output_tokens=output_tokens,
            parameters=with_output_tokens(profile["protocol"], profile["output_parameters"], output_tokens))
        result = None
        if capability == "stream":
            terminal = None
            saw_text = False
            async for event in services.models.stream(model_request):
                saw_text |= event.kind == "text" and bool(event.text)
                if event.receipt:
                    terminal = event.receipt
            result = await services.models.result(probe_id, context=context)
        else:
            result = await services.models.generate(model_request)
            terminal = result.receipt
            saw_text = any(part.kind == "text" and part.text and not part.native.get("thought") for part in result.parts)
        completion = content_completion(result) if result else None
        if terminal is None or terminal.status != "succeeded" or not saw_text or not completion or completion.state != "complete":
            return json_response(
                CommandResult(
                    body["client_request_id"],
                    attempt_id=probe_id,
                    data={"verification": "not_verified", "receipt": terminal, "completion": completion},
                )
            )

        async def save_probe(conn):
            await conn.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended('models.configuration',0))"
            )
            changed = await (
                await conn.execute(
                    "UPDATE models.routes SET verified_capabilities="
                    "ARRAY(SELECT DISTINCT unnest(verified_capabilities || %s::text[])),verification=%s,"
                    "revision=revision+1 WHERE route_id=%s AND revision=%s RETURNING revision",
                    (
                        ["text", "stream"] if capability == "stream" else ["text"],
                        Jsonb({"source": "provider_test", "attempt_id": probe_id}),
                        route_id,
                        expected,
                    ),
                )
            ).fetchone()
            if not changed:
                raise AppError("revision_conflict", status=409)
            return CommandResult(
                body["client_request_id"],
                revision=changed["revision"],
                attempt_id=probe_id,
                data={
                    "verification": "provider_test",
                    "receipt": terminal,
                    "catalog": await configuration(conn, services.settings),
                },
            )

        return json_response(
            await services.commands.execute(
                context,
                "models.probe",
                body["client_request_id"],
                digest(body),
                save_probe,
                on_replay=replay,
            )
        )

    app.router.add_get("/api/v1/models/catalog", view)
    app.router.add_post(
        "/api/v1/models/{operation:providers|routes|quota-groups|bindings|embedding-profile}", save
    )
    app.router.add_get("/api/v1/models/attempts", attempts)
    app.router.add_post("/api/v1/models/attempts/read", read_attempt)
    app.router.add_post("/api/v1/models/attempts/{attempt_id}/cancel", cancel)
    app.router.add_post("/api/v1/models/routes/{route_id}/probe", probe)
    app.router.add_post("/api/v1/models/providers/{provider_id}/models", enumerate_provider)
    app.router.add_post("/api/v1/models/providers/{provider_id}/models/result", enumeration_result)
    app.router.add_post("/api/v1/models/{operation:providers|routes}/{identifier}/delete", delete)
    app.router.add_post("/api/v1/models/{operation:bindings|embedding-profile}/delete", delete)
