"""Stable voices and per-expression speech; no implicit billing fallback."""

import re
from uuid import uuid4
from datetime import UTC, datetime
import aiohttp
from tianshu.types import Receipt, MediaRef
from ..catalog import CapabilityDefinition, Availability
from ..contracts import Fault, digest, canonical
from ..external import request, ProviderError
from .native import NativeFish, MODELS
from .mcp import FishMCP, OAuth
from .compatible import EdgeSpeech, CompatibleSpeech
from .voices import voice_page

INPUT = {
    "type": "object",
    "properties": {
        "text": {"type": "string", "minLength": 1},
        "profile_id": {"type": "string"},
        "profile_revision": {"type": "integer", "minimum": 1},
        "connection_id": {"type": "string"},
        "model": {"type": "string"},
        "voice_id": {"type": "string"},
    },
    "required": ["text"],
}


class Speech:
    definition = CapabilityDefinition(
        "speech.synthesize",
        "说话",
        "按稳定角色音色生成公开台词。",
        INPUT,
        {"type": "object"},
        "write",
    )

    def __init__(self, configuration, broker, artifacts):
        self.config, self.broker, self.artifacts = configuration, broker, artifacts
        self.oauth = OAuth(configuration.database, configuration)

    async def availability(self, context):
        connections = await self.config.list("speech_connection")
        ready = any(item.get("enabled") and item.get("configured") for item in connections)
        if not ready:
            return Availability(True, False, reason="speech_not_configured")
        selected = await self.expression_profile(context)
        if selected is None:
            return Availability(True, False, reason="voice_profile_not_configured")
        profile = await self.config.get("voice_profile", selected["profile_id"])
        ready = bool(profile and profile.get("voice_id") and any(
            item["id"] == profile.get("connection_id") and item.get("enabled") and item.get("configured") for item in connections))
        return Availability(True, ready, reason=None if ready else "speech_profile_connection_unavailable")

    async def view(self, context):
        return {
            "connections": await self.config.list("speech_connection"),
            "profiles": [
                p
                for p in await self.config.list("voice_profile")
                if not context.actor_id or p["actor_id"] == context.actor_id
            ],
            "dictionaries": await self.config.list("dictionary"),
            "default_route": None,
        }

    async def save_connection(self, body):
        transport = body.get("transport")
        identifier = body.get("id") or str(uuid4())
        if transport not in {"mcp", "native", "edge", "compatible"}:
            raise Fault("invalid_transport", 400)
        if not body.get("account_id") or not body.get("quota_group"):
            raise Fault("account_capacity_required", 400)
        current = await self.config.get("speech_connection", identifier)
        configured = (
            transport == "edge"
            or (
                transport == "compatible"
                and bool(body.get("endpoint"))
                and not body.get("api_key_required", True)
            )
            or bool(body.get("api_key"))
            or bool(current and current.get("configured"))
        )
        value = {
            key: body.get(key)
            for key in ("account_id", "team_id", "workspace_id", "quota_group", "endpoint")
        }
        value.update(
            transport=transport,
            enabled=body.get("enabled", True),
            configured=configured,
            billing_source="plus"
            if transport == "mcp"
            else "free"
            if transport == "edge"
            else "api",
            capabilities={
                "tts": True,
                "asr": transport in {"native", "mcp"},
                "stream": transport in {"native", "edge"},
                "timestamps": transport in {"native", "edge"},
                "voices": transport in {"native", "mcp", "edge"},
                "clone": transport in {"native", "mcp"},
                "dictionaries": transport == "native",
            },
            state="configured" if configured else "not_configured",
            reason=None
            if configured
            else "oauth_required"
            if transport == "mcp"
            else "api_key_required",
        )
        return await self.config.save(
            "speech_connection",
            identifier,
            value,
            body.get("expected_revision", 0),
            secret={"api_key": body["api_key"]} if body.get("api_key") else None,
        )

    async def expression_profile(self, context):
        if not context.actor_id:
            return None
        rows = [
            p
            for p in await self.config.list("voice_profile")
            if p.get("actor_id") == context.actor_id
        ]
        profile = next(
            (p for p in rows if p["id"] == context.actor_id), rows[0] if len(rows) == 1 else None
        )
        if profile is None:
            return None
        return {
            "profile_id": profile["id"],
            "revision": profile["revision"],
            "output_mode": profile.get("output_mode", "natural"),
        }

    async def save_profile(self, body, context):
        actor = body.get("actor_id", context.actor_id)
        if context.actor_id and actor != context.actor_id:
            raise Fault("actor_mismatch", 403)
        if not body.get("voice_id"):
            raise Fault("voice_required", 400)
        if body.get("output_mode", "natural") not in {"text", "audio", "both", "natural"}:
            raise Fault("speech_output_mode_invalid", 400)
        connection = await self.config.get("speech_connection", body["connection_id"])
        if not connection:
            raise Fault("connection_not_found", 404)
        if not body.get("model") or (
            connection["transport"] in {"native", "mcp"} and body["model"] not in MODELS
        ):
            raise Fault("speech_model_invalid", 400)
        value = {
            key: body.get(key)
            for key in (
                "connection_id",
                "voice_id",
                "voice_name",
                "model",
                "language",
                "output_mode",
                "dictionary_ids",
                "vocal_defaults",
            )
        }
        value.update(
            actor_id=actor,
            language=body.get("language", "zh-CN"),
            output_mode=body.get("output_mode", "natural"),
            dictionary_ids=body.get("dictionary_ids", []),
            vocal_defaults=body.get("vocal_defaults", {}),
        )
        return await self.config.save(
            "voice_profile", body.get("id") or actor, value, body.get("expected_revision", 0)
        )

    async def save_dictionary(self, body):
        entries = body.get("entries", [])
        keys = set()
        for item in entries:
            key = item.get("text", "").casefold()
            if not key or key in keys or not item.get("pronunciation"):
                raise Fault("dictionary_entry_invalid", 400)
            keys.add(key)
        return await self.config.save(
            "dictionary",
            body.get("id") or str(uuid4()),
            {
                "name": body["name"],
                "entries": entries,
                "provider_id": body.get("provider_id"),
                "provider_version": body.get("provider_version"),
            },
            body.get("expected_revision", 0),
        )

    async def selected(self, action):
        args = action.arguments
        profile = (
            await self.config.get("voice_profile", args.get("profile_id", action.actor_id)) or {}
        )
        if "profile_revision" in args and profile.get("revision") != args["profile_revision"]:
            raise Fault("profile_revision_changed", 409)
        if profile.get("actor_id", action.actor_id) != action.actor_id:
            raise Fault("actor_mismatch", 403)
        connection = await self.config.get(
            "speech_connection", args.get("connection_id", profile.get("connection_id"))
        )
        if not connection or not connection.get("enabled") or not connection.get("configured"):
            raise Fault("speech_not_configured", 503)
        model = args.get("model", profile.get("model", "s2.1-pro"))
        if connection["transport"] in {"native", "mcp"} and model not in MODELS:
            raise Fault("speech_model_invalid", 400)
        return profile, connection, model

    async def adapter(self, connection):
        if connection["transport"] == "edge":
            return EdgeSpeech()
        if connection["transport"] == "compatible":
            try:
                secret = await self.config.secret("speech_connection", connection["id"])
            except Fault as error:
                if error.code != "not_configured":
                    raise
                secret = {}
            return CompatibleSpeech(connection["endpoint"], secret.get("api_key", ""))
        token = await self.oauth.token(connection)
        if connection["transport"] == "native":
            return NativeFish(token, connection.get("endpoint") or "https://api.fish.audio")
        return FishMCP(token)

    async def compile(self, action, profile, model):
        text = action.arguments["text"]
        # Directions are separate from public text and are never parsed from user brackets.
        direction = action.arguments.get("vocal_direction") or profile.get("vocal_defaults", {})
        labels = [str(direction[key]) for key in ("tone", "pace") if direction.get(key)]
        synthesis = ("[" + ", ".join(labels) + "] " if labels else "") + text
        body = {
            "text": synthesis,
            "reference_id": action.arguments.get("voice_id", profile.get("voice_id")),
            "format": "mp3",
            "language": profile.get("language", "zh-CN"),
            "prosody": {"speed": direction.get("speed", 1), "volume": 0},
        }
        dictionaries = []
        versions = []
        for identifier in profile.get("dictionary_ids", [])[:3]:
            value = await self.config.get("dictionary", identifier)
            if value is None:
                raise Fault("dictionary_not_found", 409)
            versions.append([identifier, value["revision"]])
            if value.get("provider_id"):
                if not value.get("provider_version"):
                    raise Fault("dictionary_version_required", 409)
                dictionaries.append(
                    {"id": value["provider_id"], "version": value["provider_version"]}
                )
            else:
                dictionaries.append(
                    {
                        "items": [
                            {
                                "key": x["text"],
                                "value": x["pronunciation"],
                                "case_sensitive": x.get("case_sensitive", False),
                            }
                            for x in value["entries"]
                        ]
                    }
                )
        if dictionaries:
            if any("id" in x for x in dictionaries) and any("items" in x for x in dictionaries):
                raise Fault("dictionary_modes_mixed", 409)
            body["pronunciation_dictionary"] = dictionaries
        if action.arguments.get("references"):
            body.pop("reference_id", None)
            body["references"] = []
            for item in action.arguments["references"]:
                raw, _ = await self.artifacts.resolve(action.context, MediaRef(**item["media"]))
                body["references"].append({"audio": raw, "text": item.get("text", "")})
        return body, text, versions

    async def execute(self, action, attempt):
        profile, connection, model = await self.selected(action)
        if not action.arguments.get("voice_id", profile.get("voice_id")):
            raise Fault("voice_profile_not_configured", 503)
        body, spoken, versions = await self.compile(action, profile, model)
        cache_key = digest(
            [
                action.context.principal_id,
                action.context.actor_id,
                action.context.scope_ids,
                connection["id"],
                profile,
                model,
                {
                    **body,
                    "references": [
                        {"media": x["media"], "text": x.get("text", "")}
                        for x in action.arguments.get("references", [])
                    ],
                },
                versions,
            ]
        )
        async with self.config.database.transaction(read_only=True) as conn:
            cursor = await conn.execute(
                "SELECT result FROM capabilities.speech_cache WHERE key=%s", (cache_key,)
            )
            cached = await cursor.fetchone()
        if cached:
            from ..serialization import artifact_from

            artifacts = tuple(
                [
                    await self.artifacts.reference(action.context, x["media"]["media_id"])
                    for x in cached["result"]["artifacts"]
                ]
            )
            for artifact in artifacts:
                async with self.artifacts.open_media(action.context, artifact.media):
                    pass
            return Receipt(
                str(uuid4()),
                action.action_id,
                attempt.attempt_id,
                "succeeded",
                "confirmed",
                "not_requested",
                artifacts=artifacts,
                result={**cached["result"], "cache": "hit"},
            )
        adapter = await self.adapter(connection)
        artifacts = []
        alignment = []
        effective_model = model
        if not isinstance(adapter, (NativeFish, FishMCP)):
            body["text"] = spoken
        async with self.broker.resource(
            request(
                action,
                attempt,
                connection,
                "speech.tts",
                units={"utf8_bytes": len(body["text"].encode())},
            )
        ) as permit:
            if isinstance(adapter, FishMCP):
                await adapter.initialize()
                semantic = {
                    **action.arguments.get("mcp_parameters", {}),
                    "text": body["text"],
                    "voice_id": body.get("reference_id"),
                    "reference_id": body.get("reference_id"),
                    "language": body["language"],
                    "model": model,
                }
                if body.get("pronunciation_dictionary"):
                    semantic["pronunciation_dictionary"] = body["pronunciation_dictionary"]
                if body.get("references"):
                    raise ProviderError("speech_mcp_reference_unsupported", 409)
                params = adapter.compile("text_to_speech", semantic)
                effective_model = model if "model" in params else "provider_selected"
                if body.get(
                    "pronunciation_dictionary"
                ) and "pronunciation_dictionary" not in adapter.tools["text_to_speech"][
                    "inputSchema"
                ].get("properties", {}):
                    raise ProviderError("mcp_dictionary_unsupported", 409)
            elif not isinstance(adapter, NativeFish) and (
                body.get("pronunciation_dictionary") or body.get("references")
            ):
                raise ProviderError("speech_native_feature_unsupported", 409)
            await permit.submitted()
            if not isinstance(adapter, FishMCP):
                raw, alignment = await adapter.tts(
                    body, model, mode=action.arguments.get("transport_mode", "sse")
                )
                artifacts.append(
                    await self.artifacts.put(
                        action.context,
                        raw,
                        "audio/mpeg",
                        source_refs=action.source_refs,
                        metadata={
                            "spoken_text": spoken,
                            "voice_id": body.get("reference_id"),
                            "model": model,
                            "alignment": alignment,
                        },
                    )
                )
            else:
                result = await adapter.call("text_to_speech", params)
                urls = audio_urls(result)
                if not urls:
                    raise ProviderError("speech_mcp_audio_missing", 502, effect="unknown")
                async with aiohttp.ClientSession() as session:
                    for url in urls:
                        async with session.get(url, allow_redirects=False) as response:
                            if response.status != 200:
                                raise ProviderError(
                                    "speech_artifact_unavailable", 502, effect="unknown"
                                )
                            raw = bytearray()
                            async for chunk in response.content.iter_chunked(65536):
                                raw.extend(chunk)
                                if len(raw) > 64 * 1024 * 1024:
                                    raise ProviderError(
                                        "speech_artifact_too_large", 413, effect="unknown"
                                    )
                            artifacts.append(
                                await self.artifacts.put(
                                    action.context,
                                    raw,
                                    response.content_type,
                                    source_refs=action.source_refs,
                                    metadata={
                                        "spoken_text": spoken,
                                        "model": "provider_selected"
                                        if "model" not in params
                                        else model,
                                    },
                                )
                            )
            await permit.finish(result={"artifacts": len(artifacts)})
        from tianshu.runtime.serialization import wire

        result = {
            "artifacts": wire(artifacts),
            "alignment": alignment,
            "spoken_text": spoken,
            "model": effective_model,
            "billing_source": "free"
            if model == "s2.1-pro-free" and connection["transport"] == "native"
            else connection["billing_source"],
            "usage": None,
            "cache": "miss",
        }
        async with self.config.database.transaction() as conn:
            from ..withdrawal import validate_sources, depend

            if not await validate_sources(
                conn, action.context, action.source_refs, self.artifacts.sources
            ):
                raise Fault("source_revoked", 410)
            await depend(conn, "speech_cache", cache_key, action.source_refs)
            await conn.execute(
                "INSERT INTO capabilities.speech_cache(key,result) VALUES(%s,%s::jsonb) ON CONFLICT DO NOTHING",
                (cache_key, canonical(result)),
            )
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "succeeded",
            "confirmed",
            "not_requested",
            artifacts=tuple(artifacts),
            result=result,
        )

    async def transcribe(self, action, attempt):
        _, connection, _ = await self.selected(action)
        if connection["transport"] not in {"native", "mcp"}:
            raise ProviderError("asr_not_supported", 409)
        adapter = await self.adapter(connection)
        media = MediaRef(**action.arguments["media"])
        raw, mime = await self.artifacts.resolve(action.context, media)
        async with self.broker.resource(
            request(
                action,
                attempt,
                connection,
                "speech.asr",
                units={"audio_seconds": media.duration_ms / 1000}
                if media.duration_ms is not None
                else {},
            )
        ) as permit:
            if isinstance(adapter, FishMCP):
                await adapter.initialize()
            await permit.submitted()
            if isinstance(adapter, NativeFish):
                result = await adapter.asr(
                    raw,
                    language=action.arguments.get("language"),
                    diarize=action.arguments.get("diarize", False),
                )
            else:
                upload = await adapter.upload(
                    raw, mime, parameters=action.arguments.get("upload_parameters")
                )
                result = await adapter.call(
                    "speech_to_text",
                    adapter.compile(
                        "speech_to_text",
                        {
                            **action.arguments.get("mcp_parameters", {}),
                            "audio_url": upload["url"],
                            "url": upload["url"],
                            "model": "pro",
                            "language": action.arguments.get("language"),
                        },
                    ),
                )
            await permit.finish(result={"transcription": True})
        return {
            "media": action.arguments["media"],
            "transcript": result,
            "model": "transcribe-1-pro" if isinstance(adapter, NativeFish) else "provider_pro",
            "source_event_id": action.arguments.get("source_event_id"),
            "uncertainty": result.get("uncertainty"),
        }

    async def voices(self, action, attempt):
        _, connection, _ = await self.selected(action)
        adapter = await self.adapter(connection)
        cursor = action.arguments.get("cursor")
        offset_pagination = isinstance(adapter, NativeFish)
        if offset_pagination:
            try:
                offset = int(cursor or 0)
            except (TypeError, ValueError):
                raise Fault("speech_voice_cursor_invalid", 400) from None
            if offset < 0:
                raise Fault("speech_voice_cursor_invalid", 400)
        async with self.broker.resource(
            request(action, attempt, connection, "speech.voices")
        ) as permit:
            if isinstance(adapter, FishMCP):
                await adapter.initialize()
            await permit.submitted()
            if isinstance(adapter, EdgeSpeech):
                import edge_tts

                voices = await edge_tts.list_voices()
                query = action.arguments.get("query", "").casefold()
                answer = {
                    "items": [
                        {"id": x["ShortName"], "name": x["FriendlyName"]}
                        for x in voices
                        if query in (x["ShortName"] + x["FriendlyName"]).casefold()
                    ]
                }
            elif isinstance(adapter, NativeFish):
                answer = await adapter.voices(
                    action.arguments.get("query", ""),
                    owned=action.arguments.get("owned", False),
                    offset=offset,
                )
            elif isinstance(adapter, FishMCP):
                parameters = adapter.voice_search_arguments(
                    action.arguments.get("query", ""),
                    owned=action.arguments.get("owned", False),
                    cursor=cursor,
                    parameters=action.arguments.get("mcp_parameters"),
                )
                properties = adapter.tools["search_voices"]["inputSchema"].get("properties", {})
                offset_pagination = "cursor" not in properties and any(
                    key in properties for key in ("offset", "page_number", "page")
                )
                answer = await adapter.call("search_voices", parameters)
            else:
                raise ProviderError("voices_not_supported", 409)
            await permit.finish()
        return voice_page(answer, cursor=cursor, offset_pagination=offset_pagination)

    async def clone(self, action, attempt):
        _, connection, _ = await self.selected(action)
        adapter = await self.adapter(connection)
        recordings = []
        for item in action.arguments.get("recordings", []):
            raw, mime = await self.artifacts.resolve(action.context, MediaRef(**item["media"]))
            recordings.append((raw, mime, item.get("text", "")))
        if not recordings:
            raise Fault("voice_recordings_required", 400)
        async with self.broker.resource(
            request(
                action,
                attempt,
                connection,
                "speech.clone",
                units={
                    "audio_seconds": sum(
                        x["media"]["duration_ms"] / 1000 for x in action.arguments["recordings"]
                    )
                }
                if all(
                    x.get("media", {}).get("duration_ms") is not None
                    for x in action.arguments["recordings"]
                )
                else {},
            )
        ) as permit:
            if isinstance(adapter, FishMCP):
                await adapter.initialize()
            await permit.submitted()
            if isinstance(adapter, NativeFish):
                result = await adapter.clone(
                    action.arguments["name"],
                    recordings,
                    description=action.arguments.get("description", ""),
                )
            elif isinstance(adapter, FishMCP):
                properties = (
                    adapter.tools.get("create_voice_clone", {})
                    .get("inputSchema", {})
                    .get("properties", {})
                )
                if "visibility" not in properties and "private" not in properties:
                    raise ProviderError("speech_private_clone_unsupported", 409)
                uploads = []
                for index, (raw, mime, text) in enumerate(recordings):
                    uploaded = await adapter.upload(raw, mime, filename="recording-" + str(index))
                    uploads.append({**uploaded, "text": text})
                files = adapter.recordings("create_voice_clone", uploads)
                if not files:
                    raise ProviderError("speech_clone_recordings_unsupported", 409)
                params = adapter.compile(
                    "create_voice_clone",
                    {
                        **action.arguments.get("mcp_parameters", {}),
                        **files,
                        "name": action.arguments["name"],
                        "title": action.arguments["name"],
                        "description": action.arguments.get("description", ""),
                        "visibility": "private",
                        "private": True,
                    },
                )
                result = await adapter.call("create_voice_clone", params)
            else:
                raise ProviderError("voice_clone_not_supported", 409)
            await permit.finish(result={"voice_created": True})
        return {"voice": result, "visibility": "private", "connection_id": connection["id"]}

    async def refresh(self, action, attempt):
        connection = await self.config.get("speech_connection", action.arguments["connection_id"])
        if not connection:
            raise Fault("connection_not_found", 404)
        adapter = await self.adapter(connection)
        async with self.broker.resource(
            request(action, attempt, connection, "speech.account")
        ) as permit:
            if isinstance(adapter, FishMCP):
                await adapter.initialize()
                await permit.submitted()
                account = await adapter.call(
                    "get_credit_balance",
                    adapter.compile(
                        "get_credit_balance", action.arguments.get("mcp_parameters", {})
                    ),
                )
                tools = list(adapter.tools)
            else:
                await permit.submitted()
                account = None
                tools = []
                if isinstance(adapter, NativeFish):
                    await adapter.voices(limit=1)
            await permit.finish()
        value = {k: v for k, v in connection.items() if k not in {"id", "revision"}}
        value.update(
            state="connected",
            reason=None,
            last_verified_at=datetime.now(UTC).isoformat(),
            tools=tools,
            account=account,
        )
        return await self.config.save(
            "speech_connection", connection["id"], value, connection["revision"]
        )

    async def reconcile(self, action, attempt):
        return Receipt(
            str(uuid4()),
            action.action_id,
            attempt.attempt_id,
            "unknown",
            "unknown",
            "not_requested",
            error_code="provider_has_no_query_handle",
        )

    async def cancel(self, action, attempt):
        return await self.reconcile(action, attempt)


def audio_urls(value):
    urls = []

    def walk(item):
        if isinstance(item, dict):
            for key, part in item.items():
                if (
                    key in {"audio_url", "url", "download_url"}
                    and isinstance(part, str)
                    and part.startswith("https://")
                ):
                    urls.append(part)
                else:
                    walk(part)
        elif isinstance(item, list):
            for part in item:
                walk(part)
        elif isinstance(item, str):
            urls.extend(re.findall(r"https://[^\s<>]+\.(?:mp3|wav|opus)(?:\?[^\s<>]*)?", item))

    walk(value)
    return list(dict.fromkeys(urls))
