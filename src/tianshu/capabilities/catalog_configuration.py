"""Actor switches and data-only discovery feed the one executable Catalog."""

import asyncio
import hashlib
import re
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import urlsplit
import aiohttp
from tianshu.types import SourceRef
from tianshu.runtime.serialization import wire
from .catalog import Availability, CapabilityDefinition
from .contracts import Fault, canonical, digest, loads, require
from .execution_context import current_generation
from .external import request as resource_request, ProviderError


class CatalogConfiguration:
    def __init__(self, catalog, configuration, models, sources, authority):
        self.catalog, self.configuration, self.models, self.sources, self.authority = (
            catalog,
            configuration,
            models,
            sources,
            authority,
        )

    def actor(self, context):
        if not context.actor_id:
            raise Fault("actor_required", 400)
        return context.actor_id

    async def policy(self, context):
        if not context.actor_id:
            return {"revision": 0, "capabilities": {}}
        return await self.configuration.get("catalog_actor", context.actor_id) or {
            "revision": 0,
            "capabilities": {},
        }

    async def configured_sources(self, context):
        if not context.actor_id:
            return []
        async with self.configuration.database.transaction(read_only=True) as conn:
            rows = await (
                await conn.execute(
                    "SELECT body FROM capabilities.configuration WHERE kind='catalog_source' AND body->>'actor_id'=%s "
                    "AND ARRAY(SELECT jsonb_array_elements_text(coalesce(body->'scope_ids','[]'::jsonb)))<@%s::text[] "
                    "AND ARRAY(SELECT jsonb_array_elements_text(coalesce(body->'audience','[]'::jsonb)))<@%s::text[] ORDER BY id LIMIT 17",
                    (context.actor_id, list(context.scope_ids), list(context.audience)),
                )
            ).fetchall()
        if len(rows) > 16:
            raise Fault("catalog_source_limit", 409)
        values = [dict(row["body"]) for row in rows]
        groups = tuple(
            tuple(SourceRef(**item) for item in value.get("source_refs", ())) for value in values
        )
        allowed = await self.sources.check_many(context, groups) if groups else ()
        return [
            value
            if permitted
            else {**value, "entries": [], "source_refs": [], "state": "source_not_authorized"}
            for value, permitted in zip(values, allowed)
        ]

    async def availability(self, context, handler, availability):
        policy = await self.policy(context)
        enabled = policy.get("capabilities", {}).get(handler.definition.id, {}).get("enabled", True)
        return replace(
            availability,
            enabled=availability.enabled and enabled,
            reason="capability_disabled" if not enabled else availability.reason,
        )

    async def project(self, context, rows):
        policy = await self.policy(context)
        sources = await self.configured_sources(context)
        by_id = {row["definition"].id: row for row in rows}
        for row in rows:
            enabled = (
                policy.get("capabilities", {}).get(row["definition"].id, {}).get("enabled", True)
            )
            availability = replace(
                row["availability"],
                enabled=row["availability"].enabled and enabled,
                reason="capability_disabled" if not enabled else row["availability"].reason,
            )
            row.update(
                availability=availability,
                can_execute=row.get("can_execute", True) and availability.can_execute,
                enabled=enabled,
                source_ids=[],
            )
            semantic = row.get("semantic_availability")
            if semantic is not None:
                row["semantic_availability"] = replace(
                    semantic,
                    enabled=semantic.enabled and enabled,
                    reason="capability_disabled" if not enabled else semantic.reason,
                )
        for source in sources:
            for item in source.get("entries", []):
                identifier = item["id"]
                if identifier in by_id:
                    by_id[identifier]["source_ids"].append(source["source_id"])
                    continue
                enabled = policy.get("capabilities", {}).get(identifier, {}).get("enabled", False)
                definition = CapabilityDefinition(
                    identifier,
                    item.get("title", identifier),
                    item.get("description", ""),
                    item.get("input_schema", {"type": "object"}),
                    item.get("output_schema", {"type": "object"}),
                )
                availability = Availability(
                    False,
                    False,
                    enabled=enabled and source.get("enabled", False),
                    reason="adapter_not_installed",
                )
                row = {
                    "definition": definition,
                    "availability": availability,
                    "can_execute": False,
                    "enabled": enabled,
                    "source_ids": [source["source_id"]],
                }
                rows.append(row)
                by_id[identifier] = row
        return rows

    async def view(self, context):
        self.actor(context)
        policy = await self.policy(context)
        rows = await self.catalog.discover(context)
        sources = await self.configured_sources(context)
        return {
            "actor_id": context.actor_id,
            "revision": policy["revision"],
            "items": rows,
            "sources": sources,
        }

    async def save_policy(self, context, body):
        actor = self.actor(context)
        changes = body.get("capabilities")
        require(isinstance(changes, dict) and len(changes) <= 512, "invalid_input", 400)
        current = await self.policy(context)
        known = {row["definition"].id: row for row in await self.catalog.discover(context)}
        updated = dict(current.get("capabilities", {}))
        for identifier, setting in changes.items():
            require(
                identifier in known
                and isinstance(setting, dict)
                and set(setting) == {"enabled"}
                and type(setting["enabled"]) is bool,
                "catalog_capability_invalid",
                400,
            )
            definition = known[identifier]["definition"]
            if identifier in self.catalog.handlers and not (
                definition.model_tool or definition.public_command
            ):
                raise Fault("catalog_management_capability_not_switchable", 400)
            updated[identifier] = {"enabled": setting["enabled"]}
        return await self.configuration.save(
            "catalog_actor",
            actor,
            {"actor_id": actor, "capabilities": updated},
            body.get("expected_revision", 0),
        )

    async def save_source(self, context, body):
        actor = self.actor(context)
        source_id = body.get("source_id")
        require(
            isinstance(source_id, str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", source_id),
            "catalog_source_id_invalid",
            400,
        )
        address = body.get("manifest_url")
        parsed = urlsplit(address or "")
        require(
            parsed.scheme in {"http", "https"}
            and bool(parsed.hostname)
            and not parsed.username
            and not parsed.password
            and not parsed.fragment
            and not parsed.query,
            "catalog_source_url_invalid",
            400,
        )
        identifier = digest([actor, source_id])
        old = await self.configuration.get("catalog_source", identifier) or {}
        if not old and len(await self.configured_sources(context)) >= 16:
            raise Fault("catalog_source_limit", 409)
        checksum = body.get("expected_sha256")
        require(
            checksum is None
            or isinstance(checksum, str)
            and re.fullmatch(r"[0-9a-f]{64}", checksum),
            "catalog_source_digest_invalid",
            400,
        )
        value = {
            **old,
            "actor_id": actor,
            "source_id": source_id,
            "name": body.get("name", source_id),
            "manifest_url": address,
            "enabled": body.get("enabled", True),
            "expected_sha256": checksum,
            "quota_group": body.get("quota_group", old.get("quota_group")),
            "provider_id": "capability-catalog",
            "account_id": source_id,
            "scope_ids": list(context.scope_ids),
            "audience": list(context.audience),
            "credential_configured": bool(body.get("token") or old.get("credential_configured")),
            "state": "configured" if body.get("enabled", True) else "disabled",
        }
        secret = {"token": body["token"]} if body.get("token") else None
        if old.get("manifest_url") != address:
            value.update(entries=[], source_refs=[], content_sha256=None, last_refreshed_at=None)
        refs = tuple(SourceRef(**item) for item in value.get("source_refs", ()))
        async with self.configuration.database.transaction() as conn:
            if refs and not await self.sources.validate_persistence(conn, context, refs):
                value.update(
                    entries=[], source_refs=[], content_sha256=None, state="source_revoked"
                )
            return await self.configuration.save(
                "catalog_source",
                identifier,
                value,
                body.get("expected_revision", 0),
                secret=secret,
                connection=conn,
            )

    async def refresh(self, context, body, action, attempt):
        try:
            return await self._refresh(context, body, action, attempt)
        except Fault as error:
            identifier = digest([self.actor(context), body["source_id"]])
            current = await self.configuration.get("catalog_source", identifier)
            if current is not None:
                refs = tuple(SourceRef(**item) for item in current.get("source_refs", ()))
                async with self.configuration.database.transaction() as conn:
                    valid = await self.sources.validate_persistence(conn, context, refs)
                    state = {
                        **current,
                        "last_refreshed_at": datetime.now(UTC).isoformat(),
                        "error_code": error.code,
                        "state": "unreachable" if isinstance(error, ProviderError) else "invalid",
                    }
                    if not valid:
                        state.update(entries=[], source_refs=[])
                    try:
                        await self.configuration.save(
                            "catalog_source",
                            identifier,
                            state,
                            current["revision"],
                            connection=conn,
                        )
                    except Fault as conflict:
                        if conflict.code != "revision_conflict":
                            raise
            raise

    async def _refresh(self, context, body, action, attempt):
        actor = self.actor(context)
        identifier = digest([actor, body["source_id"]])
        source = await self.configuration.get("catalog_source", identifier)
        if not source or not source.get("enabled"):
            raise Fault("catalog_source_disabled", 409)
        token = (
            (await self.configuration.secret("catalog_source", identifier)).get("token")
            if source.get("credential_configured")
            else None
        )
        headers = {"Authorization": "Bearer " + token} if token else {}
        async with self.models.resource(
            resource_request(action, attempt, source, "catalog.sources.refresh")
        ) as permit:
            await permit.submitted()
            try:
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=10), trust_env=False
                ) as client:
                    async with client.get(
                        source["manifest_url"], headers=headers, allow_redirects=False
                    ) as response:
                        if response.status != 200:
                            raise ProviderError(
                                "catalog_source_unavailable",
                                503,
                                effect="unknown" if response.status >= 500 else "none",
                            )
                        raw = bytearray()
                        async for chunk in response.content.iter_chunked(8192):
                            raw.extend(chunk)
                            if len(raw) > 65536:
                                raise ProviderError(
                                    "catalog_source_too_large", 413, effect="unknown"
                                )
                await permit.finish(result={"bytes": len(raw)})
            except (aiohttp.ClientError, OSError, TimeoutError):
                raise ProviderError("catalog_source_unavailable", 503, effect="unknown") from None
        checksum = hashlib.sha256(raw).hexdigest()
        if source.get("expected_sha256") and checksum != source["expected_sha256"]:
            raise Fault("catalog_source_digest_mismatch", 409)
        manifest = loads(raw)
        require(
            isinstance(manifest, dict)
            and set(manifest) <= {"version", "capabilities"}
            and isinstance(manifest.get("capabilities"), list)
            and len(manifest["capabilities"]) <= 256,
            "catalog_manifest_invalid",
            400,
        )
        require(
            manifest.get("version") is None
            or isinstance(manifest["version"], str)
            and len(manifest["version"]) <= 100
            or type(manifest.get("version")) is int,
            "catalog_manifest_invalid",
            400,
        )
        entries = []
        seen = set()
        from jsonschema import Draft202012Validator

        for item in manifest["capabilities"]:
            require(
                isinstance(item, dict)
                and set(item) <= {"id", "title", "description", "input_schema", "output_schema"},
                "catalog_manifest_invalid",
                400,
            )
            name = item.get("id")
            require(
                isinstance(name, str)
                and re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", name)
                and name not in seen,
                "catalog_manifest_invalid",
                400,
            )
            for field in ("title", "description"):
                require(
                    field not in item or isinstance(item[field], str) and len(item[field]) <= 2000,
                    "catalog_manifest_invalid",
                    400,
                )
            for field in ("input_schema", "output_schema"):
                from jsonschema.exceptions import SchemaError

                try:
                    Draft202012Validator.check_schema(item.get(field, {"type": "object"}))
                except SchemaError:
                    raise Fault("catalog_manifest_invalid", 400) from None
            seen.add(name)
            entries.append(item)
        evidence = await self.sources.capture(
            context,
            locator={
                "kind": "capability_manifest",
                "source_id": source["source_id"],
                "url": source["manifest_url"],
                "sha256": checksum,
            },
            raw=bytes(raw),
            mime_type="application/json",
            generation=current_generation(),
        )
        async with self.configuration.database.transaction() as conn:
            from .withdrawal import depend

            if not await self.sources.validate_persistence(conn, context, (evidence,)):
                raise Fault("source_revoked", 410)
            await depend(conn, "catalog_source", identifier, (evidence,))
            return await self.configuration.save(
                "catalog_source",
                identifier,
                {
                    **source,
                    "entries": entries,
                    "source_version": manifest.get("version"),
                    "content_sha256": checksum,
                    "state": "ready",
                    "error_code": None,
                    "last_refreshed_at": datetime.now(UTC).isoformat(),
                    "source_refs": wire((evidence,)),
                },
                source["revision"],
                connection=conn,
            )
