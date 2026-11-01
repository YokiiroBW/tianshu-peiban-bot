"""Actual QWeather readings; Life alone owns the actor's selected location."""

import asyncio
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4
import aiohttp
from tianshu.types import Action, Receipt, Coverage, OriginKind, ServiceClass, SourceRef
from tianshu.runtime.serialization import wire
from ..catalog import CapabilityDefinition, Availability
from ..configuration import Configuration
from ..contracts import Fault, digest, canonical, require
from ..external import request as resource_request, ProviderError
from ..tracking import TrackedBroker
from .protocol import host_url, location_view, current_view


class WeatherHandler:
    def __init__(self, service, operation):
        self.service, self.operation = service, operation
        field = "query" if operation == "search" else "location_id"
        self.definition = CapabilityDefinition(
            "weather." + operation,
            "搜索天气位置" if operation == "search" else "查看实际天气",
            "读取和风实际GeoAPI或当前天气，保留抓取时间、归因和来源。",
            {
                "type": "object",
                "properties": {field: {"type": "string", "minLength": 1, "maxLength": 100}},
                "required": [field],
            },
            {"type": "object"},
            "read",
            model_tool={"name": "weather_" + operation},
        )

    async def availability(self, context):
        connection = await self.service.configuration.get("weather_connection", "qweather")
        configured = bool(
            connection
            and connection.get("enabled")
            and connection.get("configured")
            and connection.get("quota_group")
        )
        return Availability(
            True, configured, reason=None if configured else "weather_not_configured"
        )

    async def execute(self, action, attempt):
        return await self.service.execute(self.operation, action, attempt)

    async def reconcile(self, action, attempt):
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown",
            "none",
            "not_requested",
            error_code="weather_read_result_unknown",
        )

    async def cancel(self, action, attempt):
        return await self.reconcile(action, attempt)


class WeatherService:
    def __init__(self, runtime, *, authority, sources, configuration=None, models=None):
        self.runtime, self.authority, self.sources = runtime, authority, sources
        self.configuration = configuration or Configuration(runtime.database, runtime.credentials)
        self.models = models or TrackedBroker(runtime.models)
        self.actions = None
        self.handlers = tuple(WeatherHandler(self, name) for name in ("search", "current"))

    def bind_actions(self, actions):
        self.actions = actions
        for handler in self.handlers:
            actions.catalog.register(handler)

    async def view(self, context):
        await self.authority.check(context)
        connection = await self.configuration.get("weather_connection", "qweather")
        value = connection or {
            "id": "qweather",
            "revision": 0,
            "enabled": True,
            "configured": False,
        }
        return {
            "connection": value,
            "configured": bool(value.get("configured") and value.get("quota_group")),
            "reason": None
            if value.get("configured") and value.get("quota_group")
            else "weather_not_configured",
            "server_time": datetime.now(UTC),
        }

    async def save(self, context, body):
        if "*" not in context.permissions and "external.manage" not in context.permissions:
            raise Fault("forbidden", 403)
        old = await self.configuration.get("weather_connection", "qweather") or {}
        secret = None
        if body.get("api_key") is not None:
            key = body["api_key"]
            require(
                isinstance(key, str)
                and 8 <= len(key) <= 4096
                and all(33 <= ord(char) <= 126 for char in key),
                "weather_credential_invalid",
                400,
            )
            secret = {"api_key": key}
        value = {
            "host": host_url(body.get("host", old.get("host", ""))),
            "provider_id": "qweather",
            "quota_group": body.get("quota_group", old.get("quota_group")),
            "account_id": body.get("account_id", old.get("account_id", "qweather")),
            "enabled": body.get("enabled", True),
            "configured": bool(secret or old.get("configured")),
        }
        if body.get("clear_credential"):
            secret, value["configured"] = {}, False
        return await self.configuration.save(
            "weather_connection", "qweather", value, body.get("expected_revision", 0), secret=secret
        )

    async def search(self, context, query):
        return await self._read(context, "search", {"query": query})

    async def observe(self, context, location):
        if not location:
            return None
        state = await self.view(context)
        if not state["configured"]:
            return None
        try:
            result = await self._read(context, "current", {"location_id": location})
        except Fault as error:
            result = {
                "location": None,
                "weather": None,
                "description": "",
                "observed_at": None,
                "fetched_at": None,
                "expires_at": None,
                "stale": False,
                "code": error.code,
                "source_refs": [],
            }
            async with self.runtime.database.transaction(read_only=True) as conn:
                rows = await (
                    await conn.execute(
                        "SELECT receipt FROM capabilities.actions WHERE principal_id=%s AND kind='weather.current' "
                        "AND action->>'actor_id'=%s AND action->'arguments'->>'location_id'=%s "
                        "AND receipt->>'execution'='succeeded' ORDER BY created_at DESC LIMIT 4",
                        (context.principal_id, context.actor_id, location),
                    )
                ).fetchall()
            for row in rows:
                previous = row["receipt"]["result"]
                if not previous.get("weather"):
                    continue
                refs = tuple(SourceRef(**item) for item in previous.get("source_refs", ()))
                try:
                    await self.authority.check(context, refs)
                except Exception as denied:
                    if getattr(denied, "status", None) in {403, 404, 410}:
                        continue
                    raise
                result = {**previous, "stale": True, "code": error.code}
                break
        return {
            **result,
            "fetched_at": datetime.fromisoformat(result["fetched_at"])
            if result["fetched_at"]
            else None,
            "expires_at": datetime.fromisoformat(result["expires_at"])
            if result["expires_at"]
            else None,
        }

    async def _read(self, context, operation, arguments):
        if self.actions is None:
            raise Fault("weather_actions_unbound", 503)
        connection = await self.configuration.get("weather_connection", "qweather") or {}
        bucket = int(datetime.now(UTC).timestamp() // 300)
        key = digest(
            [
                context.principal_id,
                context.actor_id,
                context.conversation_id,
                context.access_epoch,
                list(context.scope_ids),
                list(context.audience),
                sorted(context.permissions),
                connection.get("revision", 0),
                operation,
                arguments,
                bucket,
            ]
        )
        receipt = await self.actions.invoke(
            Action(
                key,
                key,
                "weather." + operation,
                context.actor_id,
                context,
                arguments,
                OriginKind.OBSERVATION,
            ),
            background=False,
            service_class=ServiceClass.INTERACTIVE,
        )
        if receipt.execution != "succeeded":
            raise Fault(receipt.error_code or "weather_unavailable", 503)
        return dict(receipt.result)

    async def request(self, connection, secret, path, query):
        try:
            async with aiohttp.ClientSession(
                trust_env=False, timeout=aiohttp.ClientTimeout(total=8)
            ) as client:
                async with client.get(
                    host_url(connection["host"]) + path,
                    params={**query, "lang": "zh"},
                    headers={"X-QW-Api-Key": secret["api_key"]},
                    allow_redirects=False,
                ) as response:
                    raw = bytearray()
                    async for chunk in response.content.iter_chunked(8192):
                        raw.extend(chunk)
                        if len(raw) > 131072:
                            raise ProviderError("weather_response_budget", 502, effect="unknown")
                    if response.status in {401, 403}:
                        raise ProviderError("weather_unauthorized", 503)
                    if response.status != 200:
                        raise ProviderError(
                            "weather_unavailable",
                            503,
                            effect="unknown" if response.status >= 500 else "none",
                        )
                    answer = json.loads(raw)
                    if not isinstance(answer, dict):
                        raise ProviderError("weather_invalid_response", 502, effect="unknown")
                    if answer.get("code") in {"401", "402", "403"}:
                        raise ProviderError("weather_unauthorized", 503)
                    if answer.get("code") == "404":
                        raise ProviderError("weather_location_not_found", 404)
                    if path.startswith("/geo/") and answer.get("code") != "200":
                        raise ProviderError("weather_unavailable", 503)
                    return answer
        except (aiohttp.ClientError, OSError, TimeoutError, ValueError):
            raise ProviderError("weather_unavailable", 503, effect="unknown") from None

    async def execute(self, operation, action, attempt):
        connection = await self.configuration.get("weather_connection", "qweather")
        if not connection or not connection.get("enabled") or not connection.get("configured"):
            raise Fault("weather_not_configured", 503)
        secret = await self.configuration.secret("weather_connection", "qweather")
        query = (
            action.arguments.get("query")
            if operation == "search"
            else action.arguments.get("location_id")
        )
        require(isinstance(query, str) and 1 <= len(query.strip()) <= 100, "invalid_input", 400)
        if operation == "current":
            require(
                re.fullmatch(r"[A-Za-z0-9_-]{1,64}", query) is not None,
                "weather_location_invalid",
                400,
            )
        async with self.models.resource(
            resource_request(
                action,
                attempt,
                connection,
                "weather." + operation,
                units={"requests": 1 if operation == "search" else 2},
            )
        ) as permit:
            await permit.submitted()
            answer = await self.request(
                connection, secret, "/geo/v2/city/lookup", {"location": query, "number": "10"}
            )
            items = answer.get("location")
            try:
                require(
                    isinstance(items, list) and len(items) <= 20, "weather_invalid_response", 502
                )
                locations = [location_view(item) for item in items]
            except Fault:
                await permit.finish(
                    result={"provider": "qweather", "error_code": "weather_invalid_response"}
                )
                raise
            if operation == "search":
                raw = answer
                result = {"items": locations}
            else:
                location = next((item for item in locations if item["id"] == query), None)
                if location is None:
                    await permit.rejected("weather_location_not_found")
                    raise Fault("weather_location_not_found", 404)
                answer = await self.request(
                    connection,
                    secret,
                    f"/weather/v1/current/{location['latitude']:.2f}/{location['longitude']:.2f}",
                    {},
                )
                try:
                    weather = current_view(answer)
                except Fault:
                    await permit.finish(
                        result={"provider": "qweather", "error_code": "weather_invalid_response"}
                    )
                    raise
                raw = {"location": location, "current": answer}
                now = datetime.now(UTC)
                result = {
                    "location": location,
                    "weather": weather,
                    "description": weather["text"] + " " + weather["temp"] + "°C",
                    "observed_at": None,
                    "fetched_at": now.isoformat(),
                    "expires_at": (now + timedelta(seconds=300)).isoformat(),
                    "stale": False,
                    "code": "ready",
                }
            await permit.finish(
                result={
                    "provider": "qweather",
                    "representation": "geo" if operation == "search" else "current_v1",
                }
            )
        source = await self.sources.capture(
            action.context,
            locator={
                "kind": "weather_lookup" if operation == "search" else "weather_current",
                "provider": "qweather",
                "host": connection["host"],
                "query": query,
                "acquired_at": datetime.now(UTC).isoformat(),
            },
            raw=canonical(raw).encode(),
            mime_type="application/json",
        )
        result["source_refs"] = wire((source,))
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "none",
            "not_requested",
            # This acquired response, rather than every location or a whole day's weather.
            coverage=Coverage(
                True,
                total=len(locations) if operation == "search" else 1,
                representation=(
                    "weather_lookup_response" if operation == "search" else "weather_current_response"
                ),
            ),
            result=result,
        )
