"""Authorized AssetLink reads; no peer database, ACL replica or original-media promise."""

import asyncio
import ssl
from urllib.parse import urlsplit
from uuid import uuid4
import aiohttp
from tianshu.types import Action, Receipt, OriginKind
from tianshu.runtime.serialization import wire
from ..catalog import Availability, CapabilityDefinition
from ..contracts import Fault, require, canonical, loads, digest
from ..external import request as resource_request, ProviderError, execution_service_class
from ..execution_context import current_business_deadline
from . import asset_queries as query


class AssetRead:
    def __init__(self, owner, operation):
        self.owner, self.operation = owner, operation
        properties = {"connection_id": {"type": "string"}}
        if operation in {"library", "browse", "entry", "search"}:
            properties["library_id"] = {"type": "string"}
        if operation in {"browse", "search"}:
            properties.update(parent_relative_path={"type": "string"})
        if operation in {"libraries", "browse", "search"}:
            properties.update(
                page_size={"type": "integer", "minimum": 1, "maximum": 100},
                cursor={"type": "string"},
            )
        if operation == "libraries":
            properties["category"] = {"enum": list(query.CATEGORIES)}
        if operation == "browse":
            properties.update(
                sort_by={"enum": list(query.SORT_BY)},
                sort_direction={"enum": list(query.DIRECTIONS)},
                kind={"enum": list(query.KIND_FILTERS)},
                name_filter={"type": "string"},
                anchor_entry_id={"type": "string"},
            )
        if operation == "search":
            properties.update(query={"type": "string"}, scope={"enum": list(query.SEARCH_SCOPES)})
        if operation == "entry":
            properties["entry_id"] = {"type": "string"}
        required = (
            ["connection_id"]
            + (["library_id"] if operation in {"library", "browse", "entry"} else [])
            + (["query"] if operation == "search" else [])
            + (["entry_id"] if operation == "entry" else [])
        )
        self.definition = CapabilityDefinition(
            "resources.assets." + operation,
            "资产库 / " + operation,
            "只读取AssetLibrary授权索引元数据。没有原件预览/下载端口，不能称原件已可用。",
            {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            {"type": "object"},
            "read",
            permissions=frozenset({"asset.read"}),
            model_tool={"name": "resources_assets_" + operation},
        )

    async def availability(self, context):
        rows = await self.owner.connections(context)
        configured = any(
            row.get("enabled") and row.get("configured") and row.get("quota_group") for row in rows
        )
        return Availability(
            True, configured, reason=None if configured else "asset_connection_not_configured"
        )

    async def execute(self, action, attempt):
        return await self.owner.execute(self.operation, action, attempt)

    async def reconcile(self, action, attempt):
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown",
            "none",
            "not_requested",
            error_code="asset_read_result_unknown",
        )

    async def cancel(self, action, attempt):
        return await self.reconcile(action, attempt)


class Resources:
    def __init__(self, configuration, models, sources, authority, actions):
        self.configuration, self.models, self.sources, self.authority, self.actions = (
            configuration,
            models,
            sources,
            authority,
            actions,
        )
        for operation in ("libraries", "library", "browse", "entry", "search"):
            actions.catalog.register(AssetRead(self, operation))

    def authorize(self, context):
        if "*" not in context.permissions and "asset.read" not in context.permissions:
            raise Fault("forbidden", 403)

    async def connections(self, context):
        rows = await self.configuration.list("asset_connection")
        return [
            row
            for row in rows
            if (
                not row.get("allowed_principals")
                or context.principal_id in row["allowed_principals"]
            )
            and (not row.get("actor_ids") or context.actor_id in row["actor_ids"])
        ]

    async def connection(self, context, identifier):
        self.authorize(context)
        await self.authority.check(context)
        row = next(
            (item for item in await self.connections(context) if item["id"] == identifier), None
        )
        if not row or not row.get("enabled") or not row.get("configured"):
            raise Fault("connection_not_allowed", 403)
        return row

    async def view(self, context):
        self.authorize(context)
        await self.authority.check(context)
        rows = await self.connections(context)
        return {
            "available": True,
            "enabled": bool(rows),
            "code": "connection_required" if rows else "no_asset_connections",
            "connection": None,
            "connections": [
                {
                    "connection_id": row["id"],
                    "label": row.get("name", row["id"]),
                    "credential_registered": row.get("configured", False),
                    "revision": row["revision"],
                    "configured": bool(row.get("configured") and row.get("quota_group")),
                    "reason": None
                    if row.get("configured") and row.get("quota_group")
                    else "asset_connection_not_configured",
                }
                for row in rows
            ],
            "preview": query.PREVIEW,
            "page_size": {"default": query.DEFAULT_PAGE, "maximum": query.MAX_PAGE},
            "sort": {"fields": list(query.SORT_BY), "directions": list(query.DIRECTIONS)},
            "kinds": list(query.KIND_FILTERS),
            "scopes": list(query.PAGE_SCOPES),
            "search_scopes": list(query.SEARCH_SCOPES),
            "reselect": "clear_and_refetch",
            "cancel": "abort_in_flight",
            "reads": [],
            "can_manage": "*" in context.permissions or "external.manage" in context.permissions,
            "configurations": rows
            if "*" in context.permissions or "external.manage" in context.permissions
            else [],
        }

    async def save(self, context, body):
        if "*" not in context.permissions and "external.manage" not in context.permissions:
            raise Fault("forbidden", 403)
        identifier = query.text(body["id"], 128)
        address = body["endpoint"]
        parsed = urlsplit(address)
        require(
            parsed.scheme == "https"
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and parsed.path == "/assetlink/v1/control"
            and not parsed.query
            and not parsed.fragment,
            "asset_endpoint_invalid",
            400,
        )
        old = await self.configuration.get("asset_connection", identifier) or {}
        secret = {"token": body["token"]} if body.get("token") else None
        if body.get("credential_ref"):
            raise Fault("asset_credential_must_be_written", 400)
        if secret is not None:
            require(
                isinstance(secret["token"], str) and 1 <= len(secret["token"]) <= 16384,
                "asset_credential_invalid",
                400,
            )
        for field in ("allowed_principals", "actor_ids"):
            values = body.get(field, old.get(field, []))
            require(
                isinstance(values, list)
                and len(values) <= 128
                and all(isinstance(item, str) and 1 <= len(item) <= 256 for item in values),
                "asset_connection_scope_invalid",
                400,
            )
        value = {
            "name": body.get("name", identifier),
            "endpoint": address,
            "enabled": body.get("enabled", True),
            "configured": bool(secret or old.get("configured")),
            "quota_group": body.get("quota_group", old.get("quota_group")),
            "provider_id": "assetlink",
            "account_id": identifier,
            "ca_file": body.get("ca_file", old.get("ca_file")),
            "allowed_principals": body.get("allowed_principals", old.get("allowed_principals", [])),
            "actor_ids": body.get("actor_ids", old.get("actor_ids", [])),
        }
        if value["ca_file"]:
            from pathlib import Path

            require(Path(value["ca_file"]).is_absolute(), "asset_ca_invalid", 400)
        if body.get("clear_credential"):
            secret = {}
            value.update(configured=False)
        return await self.configuration.save(
            "asset_connection", identifier, value, body.get("expected_revision", 0), secret=secret
        )

    async def read(self, context, operation, body):
        self.authorize(context)
        identifier = str(uuid4())
        receipt = await self.actions.invoke(
            Action(
                identifier,
                identifier,
                "resources.assets." + operation,
                context.actor_id,
                context,
                body,
                OriginKind.OBSERVATION,
                deadline_at=current_business_deadline(context),
            ),
            background=False,
            service_class=execution_service_class(context),
        )
        if receipt.execution != "succeeded":
            raise Fault(receipt.error_code or "asset_read_unavailable", 503)
        return dict(receipt.result)

    async def execute(self, operation, action, attempt):
        body = dict(action.arguments)
        identifier = body.pop("connection_id")
        connection = await self.connection(action.context, identifier)
        scope = query.Scope(
            digest(
                [
                    action.context.principal_id,
                    action.context.actor_id,
                    list(action.context.scope_ids),
                    action.context.access_epoch,
                ]
            ),
            identifier,
            operation,
        )
        options = None
        if operation == "libraries":
            payload, options = query.libraries_request(body, scope)
            native = "libraries.list"
        elif operation == "browse":
            payload, options = query.browse_request(body, scope)
            native = "entries.browse"
        elif operation == "search":
            payload, options = query.search_request(body, scope)
            native = "assets.search"
        elif operation == "entry":
            payload = query.entry_request(body)
            native = "entries.get"
        else:
            require(set(body) == {"library_id"}, "invalid_input", 400)
            payload = {"library_id": query.text(body["library_id"], 256)}
            native = "libraries.get"
        request_id = str(uuid4())
        data = canonical(
            {
                "message_type": "control.request",
                "request_id": request_id,
                "operation": native,
                "body": payload,
                "timeout_ms": 5000,
            }
        ).encode()
        require(len(data) <= 65536, "budget_exceeded", 413)
        token = (await self.configuration.secret("asset_connection", identifier)).get("token")
        if not token:
            raise Fault("asset_credential_not_configured", 503)
        tls = ssl.create_default_context(cafile=connection.get("ca_file"))
        tls.minimum_version = ssl.TLSVersion.TLSv1_2
        async with self.models.resource(
            resource_request(
                action, attempt, connection, "resources.assets.read", units={"requests": 1}
            )
        ) as permit:
            await permit.submitted()
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=5),
                    trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    auto_decompress=False,
                ) as client:
                    async with client.post(
                        connection["endpoint"],
                        data=data,
                        ssl=tls,
                        allow_redirects=False,
                        headers={
                            "Authorization": "Bearer " + token,
                            "Content-Type": "application/json",
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        if response.content_type != "application/json" or response.headers.get(
                            "Content-Encoding"
                        ):
                            raise ProviderError("invalid_upstream", 502, effect="unknown")
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(65536):
                            raw.extend(chunk)
                            if len(raw) > 1048576:
                                raise ProviderError("budget_exceeded", 413, effect="unknown")
                        result = loads(raw)
                        if response.status != 200:
                            code = (
                                (result.get("error") or {}).get("code", "upstream_error")
                                if isinstance(result, dict)
                                else "upstream_error"
                            )
                            code, status = query.failure(code, response.status)
                            raise ProviderError(
                                code, status, effect="unknown" if response.status >= 500 else "none"
                            )
                        require(
                            isinstance(result, dict)
                            and result.get("request_id") == request_id
                            and result.get("message_type") == "control.result"
                            and result.get("ok") is True
                            and isinstance(result.get("body"), dict),
                            "invalid_upstream",
                            502,
                        )
                await permit.finish(
                    result={"representation": "index_metadata", "request_id": request_id}
                )
            except (aiohttp.ClientError, OSError, TimeoutError):
                raise ProviderError("dependency_unavailable", 503, effect="unknown") from None
        current = await self.connection(action.context, identifier)
        if current["revision"] != connection["revision"]:
            raise Fault("connection_revision_changed", 409)
        found = self.project(operation, result["body"], payload, scope, options)
        source = await self.sources.capture(
            action.context,
            locator={
                "kind": "asset_index",
                "connection_id": identifier,
                "operation": native,
                "request_id": request_id,
                "representation": "index_metadata",
                "original_available": "not_verified",
            },
            raw=canonical(result["body"]).encode(),
            mime_type="application/json",
        )
        found.update(
            source_refs=wire((source,)),
            representation="index_metadata",
            original_available="not_verified",
        )
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "none",
            "not_requested",
            result=found,
        )

    def project(self, operation, result, payload, scope, options):
        found = {
            "operation": {
                "libraries": "libraries.list",
                "library": "libraries.get",
                "browse": "entries.browse",
                "entry": "entries.get",
                "search": "assets.search",
            }[operation],
            "connection": scope.connection_id,
            "preview": dict(query.PREVIEW),
        }
        if operation == "libraries":
            listed = query.slice_page(result, payload["page_size"], scope, options, query.library)
            return {**found, "libraries": listed["items"], "page": listed["page"]}
        if operation == "library":
            library = query.library(result.get("library", result))
            require(library["library_id"] == payload["library_id"], "invalid_upstream", 502)
            return {**found, "library": library}
        if operation == "browse":
            library = query.library(result.get("library"))
            require(library["library_id"] == payload["library_id"], "invalid_upstream", 502)
            listed = query.slice_page(result, payload["page_size"], scope, options, query.entry)
            require(
                all(item["library_id"] == payload["library_id"] for item in listed["items"]),
                "invalid_upstream",
                502,
            )
            return {
                **found,
                "library": library,
                "entries": listed["items"],
                "page": listed["page"],
                "parent_relative_path": query.echo(
                    result.get("parent_relative_path"), payload["parent_relative_path"]
                ),
                "options": {
                    key: payload.get(key, default)
                    for key, default in (
                        ("sort_by", "name"),
                        ("sort_direction", "asc"),
                        ("kind", "all"),
                        ("name_filter", ""),
                    )
                },
            }
        if operation == "entry":
            library, entry = query.library(result.get("library")), query.entry(result.get("entry"))
            require(
                library["library_id"] == entry["library_id"] == payload["library_id"]
                and entry["entry_id"] == payload["entry_id"],
                "invalid_upstream",
                502,
            )
            return {**found, "library": library, "entry": entry}
        listed = query.slice_page(result, payload["page_size"], scope, options, query.hit)
        require(
            payload.get("library_id") is None
            or all(
                item["library"]["library_id"] == payload["library_id"] for item in listed["items"]
            ),
            "invalid_upstream",
            502,
        )
        return {
            **found,
            "hits": listed["items"],
            "page": listed["page"],
            "query": payload["query"],
            "scope": payload["scope"],
            "library_id": payload.get("library_id"),
            "parent_relative_path": payload.get("parent_relative_path"),
        }
