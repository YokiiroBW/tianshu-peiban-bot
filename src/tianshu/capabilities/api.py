"""Native /api/v1 adapters. Trusted identity is injected once by identity middleware."""

from datetime import datetime
import re
from uuid import uuid4
from urllib.parse import quote
from aiohttp import web
from tianshu.types import Action, SourceRef, MediaRef, ServiceClass, OriginKind
from tianshu.runtime.api import require_context, CommandResult, json_response, command_body
from tianshu.runtime.serialization import wire
from tianshu.sources.dependencies import unique_refs
from .contracts import Fault, digest
from .operations import READ_OPERATIONS
from .serialization import receipt_from

SECRET_KEYS = frozenset(
    {
        "api_key",
        "token",
        "password",
        "cookie",
        "secrets",
        "code",
        "refresh_token",
        "access_token",
        "browser_storage_state",
        "storage_state",
        "cookies",
    }
)


def contains_secret(value):
    if isinstance(value, dict):
        return any(
            (k in SECRET_KEYS and v is not None) or contains_secret(v) for k, v in value.items()
        )
    if isinstance(value, list):
        return any(contains_secret(x) for x in value)
    return False


async def invoke(service, domain, operation, body, context, *, capability_id=None):
    client_id = body.get("client_request_id")
    if not isinstance(client_id, str) or not 1 <= len(client_id) <= 128:
        raise Fault("client_request_id_required", 400)
    identifier = capability_id or domain + "." + operation.replace("/", ".")
    if domain == "images" and operation == "generate":
        identifier = "images.generate"
    if domain == "content" and operation == "video.observe":
        identifier = "video.observe"
    if domain == "devices" and operation == "control":
        identifier = "device.control"
    key = digest([context.principal_id, identifier, client_id])
    arguments = {
        key: value
        for key, value in body.items()
        if key
        not in {
            "client_request_id",
            "source_refs",
            "current_input_refs",
            "deadline_at",
            "expression_id",
            "activity_id",
            "matter_id",
            "actor_id",
            "person_id",
            "conversation_id",
        }
    }
    if contains_secret(arguments):
        arguments = await service.configuration.seal_command(context, key, arguments)
    refs = tuple(SourceRef(**x) for x in body.get("source_refs", []))
    if body.get("source"):
        refs = unique_refs(refs, (SourceRef(**body["source"]),))
    try:
        deadline = datetime.fromisoformat(body["deadline_at"]) if body.get("deadline_at") else None
    except (TypeError, ValueError):
        raise Fault("invalid_deadline", 400) from None
    if deadline and (deadline.tzinfo is None or deadline.utcoffset() is None):
        raise Fault("invalid_deadline", 400)
    action = Action(
        key,
        client_id,
        identifier,
        context.actor_id,
        context,
        arguments,
        context.origin_kind,
        source_refs=refs,
        expression_id=body.get("expression_id"),
        activity_id=body.get("activity_id"),
        matter_id=body.get("matter_id"),
        deadline_at=deadline,
    )
    # Long work and plugin lifecycle are durable worker work; the API never starts a host.
    background = domain in {
        "plugins",
        "images",
        "media",
        "books",
        "music",
        "comics",
        "infrastructure",
    } or operation in {
        "synthesize",
        "preview",
        "transcribe",
        "voices.clone",
        "index",
        "annotate",
        "video.observe",
    }
    if domain == "speech" and operation in {
        "connections.save",
        "profiles.save",
        "dictionaries.save",
        "connections.connect",
        "connections.complete",
        "connections.disconnect",
    }:
        background = False
    definition = (await service.actions.handler(action)).definition
    service_class = (
        ServiceClass.ELASTIC
        if definition.long_running or context.origin_kind != OriginKind.HUMAN
        else ServiceClass.INTERACTIVE
    )
    receipt = await service.actions.invoke(
        action, background=background, service_class=service_class
    )
    return CommandResult(
        client_id,
        data=receipt.result,
        revision=receipt.result.get("revision") if isinstance(receipt.result, dict) else None,
        action_id=receipt.action_id,
        attempt_id=receipt.attempt_id,
        receipt=receipt,
    )


def register_routes(app, service):
    async def operation(request):
        service.ready()
        domain = request.match_info["domain"]
        op = request.match_info["operation"]
        context = require_context(request)
        try:
            body = await request.json()
        except ValueError:
            raise Fault("invalid_json", 400) from None
        if not isinstance(body, dict):
            raise Fault("invalid_input", 400)
        await service.authority.check(context)
        # actor/person/conversation selectors are resolved by Identity middleware
        # from the query. They are routing metadata, never model arguments.
        body = {
            key: value
            for key, value in body.items()
            if key not in {"actor_id", "person_id", "conversation_id", "current_input_refs"}
        }
        if domain == "system":
            require_context(request, "config.publish")
            diagnostics = service.runtime.diagnostics
            if diagnostics is None:
                raise Fault("runtime_diagnostics_unavailable", 503)
            if op == "tasks":
                value = await diagnostics.tasks(limit=body.get("limit", 50))
            elif op == "resources":
                value = await diagnostics.resources()
            elif op == "logs":
                value = await diagnostics.logs(
                    start_ns=body["start_ns"],
                    end_ns=body["end_ns"],
                    role=body.get("role"),
                    limit=body.get("limit", 100),
                )
            elif op == "view":
                value = {
                    "tasks": await diagnostics.tasks(limit=20),
                    "resources": await diagnostics.resources(),
                }
            else:
                raise Fault("not_found", 404)
            await service.authority.check(context)
            return json_response(value)
        if domain == "images" and op in {"cancel", "reconcile"}:
            if not body.get("client_request_id"):
                raise Fault("client_request_id_required", 400)
            receipt = await getattr(service.actions, op)(
                body["action_id"],
                context,
                **({"client_request_id": body["client_request_id"]} if op == "resume" else {}),
            )
            return json_response(
                CommandResult(
                    body["client_request_id"],
                    data=receipt.result,
                    action_id=receipt.action_id,
                    attempt_id=receipt.attempt_id,
                    receipt=receipt,
                )
            )
        if domain == "capabilities":
            if op == "catalog/view":
                return json_response(await service.operate(domain, op, body, context))
            if op in {"catalog/settings.save", "catalog/sources.save", "catalog/sources.refresh"}:
                return json_response(await invoke(service, domain, op, body, context))
            if op == "discover":
                return json_response({"items": await service.discover(context)})
            if op == "get":
                return json_response(await service.actions.get(body["action_id"], context))
            if op == "find":
                return json_response(
                    await service.actions.find(
                        body["capability_id"], body["client_request_id"], context
                    )
                )
            if op in {"cancel", "reconcile", "resume"}:
                if not body.get("client_request_id"):
                    raise Fault("client_request_id_required", 400)
                receipt = await getattr(service.actions, op)(
                    body["action_id"],
                    context,
                    **({"client_request_id": body["client_request_id"]} if op == "resume" else {}),
                )
                return json_response(
                    CommandResult(
                        body["client_request_id"],
                        data=receipt.result,
                        action_id=receipt.action_id,
                        attempt_id=receipt.attempt_id,
                        receipt=receipt,
                    )
                )
            if op == "invoke":
                supplied = body["capability_id"]
                domain_hint = (
                    supplied.split(".", 1)[0] if not supplied.startswith("plugin:") else "plugins"
                )
                return json_response(
                    await invoke(
                        service,
                        domain_hint,
                        "invoke",
                        {
                            **body.get("arguments", {}),
                            "client_request_id": body["client_request_id"],
                            "source_refs": body.get("source_refs", []),
                        },
                        context,
                        capability_id=supplied,
                    )
                )
            raise Fault("not_found", 404)
        if domain == "plugins" and op in {"invoke", "cancel", "reconcile", "resume"}:
            if not body.get("client_request_id"):
                raise Fault("client_request_id_required", 400)
            result = await service.operate(domain, op, body, context)
            receipt = wire(result)
            return json_response(
                CommandResult(
                    body.get("client_request_id", ""),
                    data=receipt.get("result", receipt),
                    action_id=receipt.get("action_id"),
                    attempt_id=receipt.get("attempt_id"),
                    receipt=receipt_from(receipt) if "receipt_id" in receipt else None,
                )
            )
        if op in READ_OPERATIONS.get(domain, ()) and not (
            domain == "plugins" and op == "schedules" and "operation" in body
        ):
            return json_response(await service.operate(domain, op, body, context))
        return json_response(await invoke(service, domain, op, body, context))

    async def media(request):
        context = require_context(request)
        identifier = request.match_info["media_id"]
        if identifier.startswith("artifact:"):
            artifact = await service.artifacts.reference(context, identifier)
            ref = artifact.media
            source_refs = artifact.source_refs
        elif identifier.startswith("source:"):
            try:
                source_id, revision = identifier[7:].rsplit(":", 1)
                source = SourceRef(source_id, int(revision))
            except (ValueError, TypeError):
                raise Fault("not_found", 404) from None
            projection = await service.sources.read(
                context, source, {"position": 0, "max_characters": 1}
            )
            if not projection.get("media"):
                raise Fault("not_found", 404)
            ref = MediaRef(**projection["media"])
            source_refs = (source,)
        else:
            raise Fault("media_owner_required", 404)
        await service.authority.check(context, source_refs)
        start = 0
        length = ref.size_bytes
        status = 200
        headers = {
            "Cache-Control": "private, no-store",
            "Accept-Ranges": "bytes",
            "ETag": '"' + ref.sha256 + '"',
        }
        etag = headers["ETag"]
        if request.headers.get("If-None-Match") in {etag, "*"}:
            return web.Response(
                status=304, headers={"ETag": etag, "Cache-Control": "private, no-store"}
            )
        requested = (
            request.headers.get("Range") if request.headers.get("If-Range", etag) == etag else None
        )
        if requested:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", requested)
            if not match or length is None or not any(match.groups()):
                raise web.HTTPRequestRangeNotSatisfiable(
                    headers={"Content-Range": f"bytes */{ref.size_bytes}"}
                )
            if match[1]:
                start = int(match[1])
                end = min(int(match[2]) if match[2] else length - 1, length - 1)
            else:
                suffix = int(match[2])
                start = max(0, length - suffix)
                end = length - 1
            if not 0 <= start <= end < length:
                raise web.HTTPRequestRangeNotSatisfiable(
                    headers={"Content-Range": f"bytes */{ref.size_bytes}"}
                )
            headers["Content-Range"] = f"bytes {start}-{end}/{length}"
            length = end - start + 1
            status = 206
        if length is not None:
            headers["Content-Length"] = str(length)
        if request.method == "HEAD":
            return web.Response(status=status, headers={**headers, "Content-Type": ref.mime_type})
        response = web.StreamResponse(status=status, headers=headers)
        response.content_type = ref.mime_type.split(";", 1)[0]
        await response.prepare(request)
        async for chunk in service.runtime.files.iter_async(ref, start=start, length=length):
            await service.authority.check(context, source_refs)
            await response.write(chunk)
        await response.write_eof()
        return response

    async def upload(request):
        service.ready()
        context = require_context(request)
        await service.authority.check(context)
        multipart = await request.multipart()
        field = await multipart.next()
        if field is None or field.name != "client_request_id":
            raise Fault("client_request_id_required", 400)
        client_id = await field.text()
        if not 1 <= len(client_id) <= 128:
            raise Fault("client_request_id_required", 400)
        field = await multipart.next()
        if field is None or field.name != "file" or not field.filename:
            raise Fault("upload_file_required", 400)
        filename = field.filename
        if len(filename) > 512 or any(ord(x) < 32 for x in filename):
            raise Fault("upload_filename_invalid", 400)

        async def chunks():
            while raw := await field.read_chunk(1024 * 1024):
                yield raw

        reference = await service.runtime.files.put_async(
            chunks(),
            media_type=field.headers.get("Content-Type", "application/octet-stream"),
            max_bytes=service.settings.get("max_upload_bytes", 512 * 1024 * 1024),
        )
        if await multipart.next() is not None:
            raise Fault("single_upload_required", 400)
        source = await service.sources.register_media(
            context,
            reference,
            locator={"kind": "upload", "filename": filename, "client_request_id": client_id},
            origin_kind=OriginKind.HUMAN,
        )
        return json_response(
            await invoke(
                service,
                "capabilities",
                "upload",
                {"client_request_id": client_id, "filename": filename, "source": wire(source)},
                context,
            )
        )

    async def plugin_page(request):
        context = require_context(request)
        raw = await service.web_plugins.page(request.match_info["ticket"], context)
        return web.Response(
            body=raw.encode() if isinstance(raw, str) else raw,
            content_type="text/html",
            headers={
                "Content-Security-Policy": service.web_plugins.view_policy,
                "Cache-Control": "no-store",
            },
        )

    async def library_file(request):
        context = require_context(request, "media.read")
        await service.authority.check(context)
        domain = request.match_info["domain"]
        parts = request.match_info["path"].split("/")
        if domain == "books" and len(parts) == 2:
            path, name, mime = await service.media.books.file(*parts)
        elif domain == "comics":
            path, mime = await service.media.comics.file(parts)
            name = path.name
        else:
            raise Fault("not_found", 404)
        await service.authority.check(context)
        return web.FileResponse(
            path,
            headers={
                "Content-Type": mime,
                "Content-Disposition": "inline; filename*=UTF-8''" + quote(name),
                "Cache-Control": "private, no-store",
            },
        )

    app.router.add_post("/api/v1/capabilities/upload", upload)
    app.router.add_get("/api/v1/capabilities/media/{media_id}", media)
    app.router.add_get("/api/v1/plugins/page/{ticket}", plugin_page)
    app.router.add_get("/api/v1/{domain:books|comics}/files/{path:.*}", library_file)
    app.router.add_post(
        "/api/v1/{domain:capabilities|plugins|content|images|speech|reactions|media|books|music|comics|devices|infrastructure|game|weather|resources|projects|system}/{operation:.*}",
        operation,
    )
