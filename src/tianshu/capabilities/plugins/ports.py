"""Registered host operations; secrets are injected here and never serialized to workers."""

import base64
import json
import time
from urllib.parse import urlsplit

import aiohttp

from ..contracts import Fault, canonical, digest, require
from .port_schemas import OPERATIONS
from .schema import check


async def capability(executions, context, name, operation, value):
    schemas = OPERATIONS.get((name, operation))
    require(schemas, "capability_unavailable", 409)
    check(schemas[0], value)
    result = await execute_capability(executions, context, name, operation, value)
    check(schemas[1], result)
    return result


async def execute_capability(executions, context, name, operation, value):
    host, repo = executions.host, executions.repo
    instance, call = context["instance"], context["call"]
    require(isinstance(value, dict), "invalid_input", 400)
    if name in {"models.generate","files.store","sources.read","messages.present"}:
        return await host.domain_ports.host_port(context,name,operation,value)
    if name == "comics.files":
        from ..media.comics.plugin import capability as comic_capability

        return await comic_capability(host, context, operation)
    if name == "state.kv":
        require(operation in {"get", "compare_and_set", "delete"}, "capability_unavailable", 409)
        return await host.local(repo.state, instance, operation, value)
    if name == "http.client" and operation == "request":
        return await http_request(executions, context, value)
    if name == "content.discovery" and operation == "discover":
        return await discover_content(executions, context, value)
    if name == "secrets.read" and operation == "resolve":
        require(value.get("name") in context["secrets"], "not_found", 404)
        # This opaque handle is usable only as auth.secret_name on this instance's transport.
        return {"secret_ref": "secret:" + digest([instance, value["name"]]), "configured": True}
    if name == "jobs.manage":
        if operation == "checkpoint":
            require(
                call
                and isinstance(value.get("state"), dict)
                and len(canonical(value["state"]).encode()) <= 65536,
                "invalid_input",
                400,
            )
            current = await host.local(repo.call, call)
            checkpoint = current.get("checkpoint")
            expected = value.get(
                "expected_revision", checkpoint["revision"] if checkpoint else None
            )
            require(
                expected == (checkpoint["revision"] if checkpoint else None),
                "revision_conflict",
                409,
            )
            checkpoint = {
                "value": value["state"],
                "revision": (checkpoint["revision"] if checkpoint else 0) + 1,
            }
            await host.local(repo.update_call, call, checkpoint=checkpoint)
            return {
                "checkpoint_ref": call + ":" + str(checkpoint["revision"]),
                "revision": checkpoint["revision"],
            }
        if operation == "submit":
            require(call, "permission_denied", 403)
            return await host.domain_ports.host_port(context,name,operation,value)
        if operation in {"get", "cancel"}:
            identifier = value["invocation_id"]
            document = await host.local(repo.call, identifier)
            require(document and document["instance_id"] == instance, "not_found", 404)
            require(identifier != call or operation != "cancel", "invalid_input", 400)
            return (
                await executions.cancel(identifier)
                if operation == "cancel"
                else executions.public(document)
            )
        if operation in {"schedule.put", "schedule.remove"}:
            return await host.scheduler.manage(instance, operation, value)
    if name == "notifications.emit" and operation == "publish":
        require(
            isinstance(value.get("dedup_key"), str)
            and 1 <= len(value["dedup_key"]) <= 128
            and isinstance(value.get("title"), str)
            and len(value["title"]) <= 256
            and isinstance(value.get("body"), str)
            and len(value["body"]) <= 4096,
            "invalid_input",
            400,
        )
        identifier = digest([instance, value["dedup_key"]])
        old = await host.local(repo.get, "notifications", identifier)
        fingerprint = digest(value)
        if old:
            require(old["fingerprint"] == fingerprint, "idempotency_conflict", 409)
        else:
            await host.local(
                repo.put,
                "notifications",
                {
                    "id": identifier,
                    "instance_id": instance,
                    "title": value["title"],
                    "body": value["body"],
                    "severity": value.get("severity", "info"),
                    "invocation_id": call,
                    "fingerprint": fingerprint,
                    "created_at": time.time(),
                    "delivery": "delivered",
                    "channel": "in_app",
                },
            )
        return {"notification_ref": identifier, "delivery": "delivered"}
    raise Fault("capability_unavailable", 409)


async def discover_content(executions, context, value):
    host = executions.host
    require(host.content_discovery is not None and context["call"], "capability_unavailable", 409)
    document = await host.local(executions.repo.call, context["call"])

    async def current():
        instance = await host.instance(context["instance"])
        require(
            instance["desired_state"] == "enabled"
            and instance["revision"] == document["binding_revision"],
            "version_conflict",
            409,
        )
        authorize = executions.authorizers.get(context["call"])
        if authorize:
            require(await host.check_authorizer(authorize), "session_expired", 401)

    async def request(arguments):
        await current()
        require("http.client" in context["ports"], "capability_unavailable", 409)
        if context["secrets"].get("api_key"):
            arguments = {**arguments, "auth": {"secret_name": "api_key", "scheme": "bearer"}}
        answer = await http_request(executions, context, arguments)
        await current()
        return answer

    await current()
    answer = await host.content_discovery.discover(document["config"], value, request=request)
    # A read can race revocation. No data leaves this host after a stale grant.
    await current()
    return answer


async def http_request(executions, context, value):
    from dataclasses import replace
    from uuid import uuid4
    from ..execution_context import CURRENT_ACTION,CURRENT_EXECUTION
    from ..external import request,ProviderError
    action=CURRENT_ACTION.get()
    current=CURRENT_EXECUTION.get()
    if action is None and context.get("call"):
        action,attempt,_=await executions.host.domain_ports.actions._load(context["call"])
    elif current:attempt=current["attempt"]
    else:raise Fault("invocation_context_required",403)
    profile=executions.host.resource_profiles.get(context["instance"])
    if not profile or not profile.get("quota_group"):raise Fault("plugin_resource_not_configured",503)
    actual=replace(attempt,attempt_id=attempt.attempt_id+":http:"+str(uuid4()))
    async with executions.host.domain_ports.models.resource(request(action,actual,profile,"plugin.http",service_class=await executions.host.domain_ports.actions.execution_class(action.action_id))) as permit:
        await permit.submitted()
        result=await _http_request(executions,context,value)
        if result["status"] in {400,401,402,403,404,405,413,422,429}:
            await permit.rejected("plugin_http_rejected")
        elif result["status"]>=500:await permit.unknown("plugin_http_outcome_unknown")
        else:await permit.finish(result={"http_status":result["status"]})
        return result


async def _http_request(executions, context, value):
    method, url = value.get("method", "GET").upper(), value.get("url")
    require(
        method in {"GET", "HEAD", "POST", "PUT", "PATCH", 'DELETE'}
        and isinstance(url, str)
        and len(url) <= 4096,
        "invalid_input",
        400,
    )
    parsed = urlsplit(url)
    require(
        parsed.scheme in {"http", "https"}
        and parsed.hostname
        and not parsed.username
        and not parsed.password
        and not parsed.fragment,
        "invalid_input",
        400,
    )
    require(context["call"] or method in {"GET", "HEAD"}, "permission_denied", 403)
    headers = value.get("headers", {})
    require(
        isinstance(headers, dict)
        and not any(
            key.lower() in {"authorization", "cookie", "host", "proxy-authorization"}
            for key in headers
        ),
        "invalid_input",
        400,
    )
    auth = value.get("auth")
    if auth:
        name = auth["secret_name"]
        require(name in context["secrets"], "permission_denied", 403)
        # Authentication is only injected at the instance's configured base origin.
        call = (
            await executions.host.local(executions.repo.call, context["call"])
            if context["call"]
            else None
        )
        config = (
            call["config"]
            if call
            else (await executions.host.instance(context["instance"]))["config"]
        )
        base = urlsplit(config.get("base_url", ""))
        require(
            (base.scheme, base.hostname, base.port)
            == (parsed.scheme, parsed.hostname, parsed.port),
            "permission_denied",
            403,
        )
        secret = executions.host.vault.unseal(
            context["instance"], name, base64.b64decode(context["secrets"][name])
        )
        scheme = auth.get("scheme", "bearer")
        require(scheme in {"bearer", "x-api-key"}, "invalid_input", 400)
        headers = {
            **headers,
            "Authorization" if scheme == "bearer" else "X-API-Key": "Bearer " + secret
            if scheme == "bearer"
            else secret,
        }
    maximum = value.get("max_response_bytes", 512 * 1024)
    timeout = value.get("timeout_seconds", 25)
    require(
        type(maximum) is int
        and 1 <= maximum <= 768 * 1024
        and isinstance(timeout, (int, float))
        and 0 < timeout <= 60,
        "invalid_input",
        400,
    )
    write = method not in {"GET", "HEAD"}
    if write:
        from ..tracking import mark_submitted
        await mark_submitted()
        await executions.host.local(executions.repo.update_call, context["call"], dispatch="sent")
    try:
        async with aiohttp.ClientSession(trust_env=False) as session:
            async with session.request(
                method,
                url,
                headers=headers,
                json=value.get("body"),
                timeout=aiohttp.ClientTimeout(total=timeout),
                allow_redirects=False,
            ) as response:
                raw = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    raw.extend(chunk)
                    require(len(raw) <= maximum, "budget_exceeded", 413)
                try:
                    body = json.loads(raw)
                except (ValueError, UnicodeError):
                    body = raw.decode("utf-8", errors="replace")
                return {
                    "status": response.status,
                    "headers": {"content-type": response.headers.get("Content-Type", "")},
                    "body": body,
                }
    except (aiohttp.ClientError, TimeoutError, OSError):
        error = Fault("outcome_unknown" if write else "dependency_unavailable", 503)
        error.effect = "unknown" if write else "none"
        raise error from None
