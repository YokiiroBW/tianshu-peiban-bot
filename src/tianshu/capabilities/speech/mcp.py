"""Fish Streamable HTTP MCP with discovered schemas and OAuth PKCE."""

import asyncio
import base64
import hashlib
import json
import secrets
import time
from urllib.parse import urlencode, urlsplit
import aiohttp
from jsonschema import Draft202012Validator, ValidationError
from ..contracts import Fault, canonical, digest
from ..external import ProviderError

ENDPOINT = "https://api.fish.audio/mcp"


class FishMCP:
    def __init__(self, token, *, endpoint=ENDPOINT):
        self.token, self.endpoint = token, endpoint
        self.session_id = None
        self.tools = {}

    async def rpc(self, method, params=None, *, notification=False):
        identifier = secrets.token_hex(8)
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notification:
            body["id"] = identifier
        headers = {
            "Authorization": "Bearer " + self.token,
            "Accept": "application/json,text/event-stream",
            "MCP-Protocol-Version": "2025-06-18",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
            async with session.post(
                self.endpoint, json=body, headers=headers, allow_redirects=False
            ) as response:
                self.session_id = response.headers.get("Mcp-Session-Id", self.session_id)
                if response.status == 401:
                    raise ProviderError("speech_oauth_expired", 401)
                if response.status not in {200, 202}:
                    raise ProviderError("speech_mcp_rejected", 502)
                if notification:
                    return None
                if response.content_type == "text/event-stream":
                    result = None
                    data = []
                    async for raw in response.content:
                        line = raw.decode().rstrip("\r\n")
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip())
                        elif not line and data:
                            value = json.loads("\n".join(data))
                            data = []
                            if value.get("id") == identifier:
                                result = value
                                break
                    if result is None:
                        raise ProviderError("speech_mcp_result_unknown", effect="unknown")
                else:
                    result = await response.json()
                if not isinstance(result, dict) or result.get("id") != identifier:
                    raise ProviderError("speech_mcp_result_unknown", effect="unknown")
                if "error" in result:
                    raise ProviderError("speech_mcp_error", 502)
                return result.get("result", {})

    async def initialize(self):
        await self.rpc(
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "tianshu", "version": "2"},
            },
        )
        await self.rpc("notifications/initialized", notification=True)
        cursor = None
        while True:
            page = await self.rpc("tools/list", {"cursor": cursor} if cursor else {})
            self.tools.update({tool["name"]: tool for tool in page.get("tools", [])})
            cursor = page.get("nextCursor")
            if not cursor:
                break
        return self.tools

    async def call(self, name, arguments):
        if name not in self.tools:
            raise ProviderError("speech_mcp_capability_missing", 409)
        try:
            Draft202012Validator(self.tools[name]["inputSchema"]).validate(arguments)
        except ValidationError:
            raise ProviderError("speech_mcp_parameter_unsupported", 400) from None
        result = await self.rpc("tools/call", {"name": name, "arguments": arguments})
        if result.get("isError"):
            raise ProviderError("speech_mcp_tool_failed", 502)
        if result.get("structuredContent") is not None:
            return result["structuredContent"]
        texts = [
            item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"
        ]
        parsed = []
        for text in texts:
            try:
                parsed.append(json.loads(text))
            except ValueError:
                parsed.append({"text": text})
        return parsed[0] if len(parsed) == 1 else {"parts": parsed}

    def compile(self, name, semantic):
        schema = self.tools.get(name, {}).get("inputSchema", {})
        properties = schema.get("properties", {})
        # Only fields actually advertised by this connected server are sent.
        args = {
            key: value for key, value in semantic.items() if key in properties and value is not None
        }
        required = schema.get("required", [])
        if any(key not in args for key in required):
            raise ProviderError("speech_mcp_parameter_unsupported", 409)
        return args

    async def upload(self, raw, mime, *, filename="recording", parameters=None):
        """Upload actual bytes using the connected server's temporary-audio slot."""
        semantic = {
            **(parameters or {}),
            "filename": filename,
            "file_name": filename,
            "content_type": mime,
            "mime_type": mime,
            "file_size": len(raw),
            "size": len(raw),
        }
        result = await self.call("get_upload_url", self.compile("get_upload_url", semantic))
        if isinstance(result, dict) and isinstance(result.get("data"), dict):
            result = result["data"]
        if not isinstance(result, dict):
            raise ProviderError("speech_upload_contract_invalid", 502)
        upload_url = result.get("upload_url") or result.get("presigned_url")
        file_url = (
            result.get("file_url")
            or result.get("download_url")
            or result.get("public_url")
            or result.get("audio_url")
            or result.get("url")
        )
        if (
            not upload_url
            or not file_url
            or any(urlsplit(x).scheme != "https" for x in (upload_url, file_url))
        ):
            raise ProviderError("speech_upload_contract_invalid", 502)
        method = result.get("method", "PUT").upper()
        if method not in {"PUT", "POST"}:
            raise ProviderError("speech_upload_method_unsupported", 409)
        headers = dict(result.get("headers") or {})
        headers.setdefault("Content-Type", mime)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=180)) as session:
            if method == "POST" and result.get("fields"):
                form = aiohttp.FormData()
                for key, value in result["fields"].items():
                    form.add_field(key, str(value))
                form.add_field("file", raw, filename=filename, content_type=mime)
                data, headers = form, {}
            else:
                data = raw
            async with session.request(
                method, upload_url, data=data, headers=headers, allow_redirects=False
            ) as response:
                if response.status not in {200, 201, 204}:
                    raise ProviderError("speech_upload_failed", 502)
        return {"url": file_url, "media_id": result.get("media_id") or result.get("id")}

    def recordings(self, name, uploads):
        properties = self.tools.get(name, {}).get("inputSchema", {}).get("properties", {})
        urls = [item["url"] for item in uploads]
        result = {"audio_urls": urls, "sample_urls": urls, "files": urls}
        for key in ("recordings", "samples", "audios"):
            schema = properties.get(key, {})
            if schema.get("items", {}).get("type") == "string":
                result[key] = urls
            elif schema:
                fields = schema.get("items", {}).get("properties", {})
                result[key] = [
                    {
                        field: value
                        for field, value in {
                            "url": item["url"],
                            "audio_url": item["url"],
                            "text": item.get("text", ""),
                            "transcript": item.get("text", ""),
                        }.items()
                        if field in fields
                    }
                    for item in uploads
                ]
        if len(urls) == 1:
            result.update(audio_url=urls[0], sample_url=urls[0])
        return {key: value for key, value in result.items() if key in properties}


class OAuth:
    def __init__(self, database, configuration):
        self.database, self.configuration = database, configuration

    async def begin(self, context, connection_id, redirect_uri, *, client_id=None):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://api.fish.audio/.well-known/oauth-protected-resource/mcp",
                allow_redirects=False,
            ) as response:
                resource = await response.json()
            issuer = resource["authorization_servers"][0]
            if urlsplit(issuer).scheme != "https":
                raise Fault("oauth_issuer_invalid", 400)
            async with session.get(
                issuer.rstrip("/") + "/.well-known/oauth-authorization-server",
                allow_redirects=False,
            ) as response:
                metadata = await response.json()
            if client_id is None:
                registration = metadata.get("registration_endpoint")
                if not registration:
                    raise Fault("oauth_client_id_required", 409)
                async with session.post(
                    registration,
                    json={
                        "client_name": "TianShu",
                        "redirect_uris": [redirect_uri],
                        "grant_types": ["authorization_code", "refresh_token"],
                        "response_types": ["code"],
                        "token_endpoint_auth_method": "none",
                    },
                    allow_redirects=False,
                ) as response:
                    client = await response.json()
                    client_id = client["client_id"]
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        async with self.database.transaction() as conn:
            await conn.execute(
                "INSERT INTO capabilities.oauth_states(state,principal_id,connection_id,expires_at,body) VALUES(%s,%s,%s,now()+interval '10 minutes',%s::jsonb)",
                (
                    state,
                    context.principal_id,
                    connection_id,
                    canonical(
                        {
                            "verifier": verifier,
                            "client_id": client_id,
                            "redirect_uri": redirect_uri,
                            "token_endpoint": metadata["token_endpoint"],
                        }
                    ),
                ),
            )
        return {
            "authorization_url": metadata["authorization_endpoint"]
            + "?"
            + urlencode(
                {
                    "response_type": "code",
                    "client_id": client_id,
                    "redirect_uri": redirect_uri,
                    "state": state,
                    "code_challenge": challenge,
                    "code_challenge_method": "S256",
                    "resource": ENDPOINT,
                }
            ),
            "state": state,
        }

    async def complete(self, context, state, code):
        async with self.database.transaction() as conn:
            cursor = await conn.execute(
                "DELETE FROM capabilities.oauth_states WHERE state=%s AND principal_id=%s AND expires_at>now() RETURNING connection_id,body",
                (state, context.principal_id),
            )
            row = await cursor.fetchone()
        if not row:
            raise Fault("oauth_state_expired", 409)
        value = row["body"]
        async with aiohttp.ClientSession() as session:
            async with session.post(
                value["token_endpoint"],
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "client_id": value["client_id"],
                    "redirect_uri": value["redirect_uri"],
                    "code_verifier": value["verifier"],
                    "resource": ENDPOINT,
                },
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise Fault("oauth_exchange_failed", 409)
                tokens = await response.json()
        current = await self.configuration.get("speech_connection", row["connection_id"])
        tokens.update(
            token_endpoint=value["token_endpoint"],
            client_id=value["client_id"],
            expires_at=time.time() + tokens.get("expires_in", 3600),
        )
        saved = await self.configuration.save(
            "speech_connection",
            current["id"],
            {**current, "configured": True, "state": "connected"},
            current["revision"],
            secret=tokens,
        )
        async with self.database.transaction(control=True) as conn:
            await conn.execute(
                "DELETE FROM capabilities.oauth_refresh_leases WHERE connection_id=%s",
                (current["id"],),
            )
        return saved

    async def token(self, connection):
        deadline = time.monotonic() + 30
        while True:
            current = await self.configuration.get("speech_connection", connection["id"])
            if not current or not current.get("enabled"):
                raise Fault("speech_connection_changed", 409)
            tokens = await self.configuration.secret("speech_connection", connection["id"])
            if tokens.get("expires_at", float("inf")) > time.time() + 60:
                token = tokens.get("access_token", tokens.get("api_key"))
                if not token:
                    raise Fault("speech_not_configured", 503)
                return token
            if not tokens.get("refresh_token"):
                raise Fault("speech_oauth_reconnect_required", 401)
            fingerprint = digest(tokens)
            owner = secrets.token_hex(16)
            async with self.database.transaction(control=True) as conn:
                claim = await (
                    await conn.execute(
                        "INSERT INTO capabilities.oauth_refresh_leases(connection_id,fingerprint,owner,state,expires_at) "
                        "VALUES(%s,%s,%s,'refreshing',now()+interval '30 seconds') ON CONFLICT(connection_id) "
                        "DO UPDATE SET fingerprint=excluded.fingerprint,owner=excluded.owner,state=excluded.state,expires_at=excluded.expires_at "
                        "WHERE capabilities.oauth_refresh_leases.fingerprint<>excluded.fingerprint RETURNING owner",
                        (connection["id"], fingerprint, owner),
                    )
                ).fetchone()
                if not claim:
                    lease = await (
                        await conn.execute(
                            "SELECT state,expires_at<=now() AS expired FROM capabilities.oauth_refresh_leases WHERE connection_id=%s",
                            (connection["id"],),
                        )
                    ).fetchone()
            if claim:
                try:
                    return await self.refresh_tokens(current, tokens)
                except BaseException:
                    # A rotating token submitted before a lost response must not be
                    # refreshed again by another process with the same old secret.
                    async with self.database.transaction(control=True) as conn:
                        await conn.execute(
                            "UPDATE capabilities.oauth_refresh_leases SET state='unknown' WHERE connection_id=%s AND owner=%s",
                            (connection["id"], owner),
                        )
                    raise
                finally:
                    # A changed encrypted credential or an explicit reconnect can
                    # supersede this lease, but this owner never deletes another one.
                    fresh = await self.configuration.secret("speech_connection", connection["id"])
                    if digest(fresh) != fingerprint:
                        async with self.database.transaction(control=True) as conn:
                            await conn.execute(
                                "DELETE FROM capabilities.oauth_refresh_leases WHERE connection_id=%s AND owner=%s",
                                (connection["id"], owner),
                            )
            if lease and (lease["state"] == "unknown" or lease["expired"]):
                raise Fault("speech_oauth_refresh_unknown_reconnect", 409)
            if time.monotonic() >= deadline:
                raise Fault("speech_oauth_refresh_in_progress", 409)
            await asyncio.sleep(0.1)

    async def refresh_tokens(self, connection, tokens):
        fingerprint = digest(tokens)
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20, connect=10)
        ) as session:
            async with session.post(
                tokens["token_endpoint"],
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": tokens["refresh_token"],
                    "client_id": tokens["client_id"],
                },
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise Fault("speech_oauth_refresh_failed", 401)
                new = await response.json()
        if not isinstance(new.get("access_token"), str) or not new["access_token"]:
            raise Fault("speech_oauth_refresh_invalid", 502)
        updated = {**tokens, **new, "expires_at": time.time() + new.get("expires_in", 3600)}
        for _ in range(3):
            current = await self.configuration.get("speech_connection", connection["id"])
            fresh = await self.configuration.secret("speech_connection", connection["id"])
            if digest(fresh) != fingerprint:
                if fresh.get("expires_at", 0) > time.time() + 60 and fresh.get("access_token"):
                    return fresh["access_token"]
                raise Fault("speech_connection_changed", 409)
            if any(current.get(key) != connection.get(key) for key in ("transport", "account_id")):
                raise Fault("speech_connection_changed", 409)
            try:
                await self.configuration.save(
                    "speech_connection",
                    connection["id"],
                    current,
                    current["revision"],
                    secret=updated,
                )
                return updated["access_token"]
            except Fault as error:
                if error.code != "revision_conflict":
                    raise
        raise Fault("speech_oauth_refresh_in_progress", 409)
