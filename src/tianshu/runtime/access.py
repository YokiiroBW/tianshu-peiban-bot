"""Actual HTTP/HTTPS/proxy access settings, applied at the next interactive start."""

import asyncio
from copy import deepcopy
from dataclasses import replace
import ipaddress
import re
import ssl
from urllib.parse import urlsplit

from aiohttp import web
from psycopg.types.json import Jsonb

from .api import CommandResult, command_body, json_response, require_context
from .commands import body_hash
from .errors import AppError


def canonical_origin(value):
    try:
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 300
            or not value.isascii()
            or any(character.isspace() for character in value)
            or "\\" in value
        ):
            raise ValueError()
        parts = urlsplit(value)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.path
            or parts.query
            or parts.fragment
            or value != parts.scheme + "://" + parts.netloc.lower()
        ):
            raise ValueError()
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError()
        try:
            ipaddress.ip_address(parts.hostname)
        except ValueError:
            if len(parts.hostname) > 253 or not all(
                re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in parts.hostname.split(".")
            ):
                raise ValueError()
        return parts
    except (ValueError, TypeError):
        raise AppError("invalid_access_origin") from None


def certificate_context(tls, hostname):
    """Validate key, validity and SAN offline, preserving the existing TLS behavior."""
    try:
        server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        server_context.minimum_version = ssl.TLSVersion.TLSv1_2
        server_context.load_cert_chain(tls["certificate_file"], tls["key_file"])
        client_context = ssl.create_default_context(cafile=tls["certificate_file"])
        client_context.verify_flags |= ssl.VERIFY_X509_PARTIAL_CHAIN
        server_in, server_out, client_in, client_out = (ssl.MemoryBIO() for _ in range(4))
        server = server_context.wrap_bio(server_in, server_out, server_side=True)
        client = client_context.wrap_bio(client_in, client_out, server_hostname=hostname)
        completed = set()
        for _ in range(16):
            for name, peer in (("server", server), ("client", client)):
                if name not in completed:
                    try:
                        peer.do_handshake()
                        completed.add(name)
                    except ssl.SSLWantReadError:
                        pass
            if client_out.pending:
                server_in.write(client_out.read())
            if server_out.pending:
                client_in.write(server_out.read())
            if len(completed) == 2:
                return server_context
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise AppError("access_certificate_invalid", status=422)


class AccessService:
    def __init__(self, database, commands, settings):
        self.database, self.commands, self.settings = database, commands, settings
        self.config = settings.get("http", {}).get("access")
        self.active = None

    def validate(self, value, *, check_certificate=True):
        if not self.config:
            raise AppError("access_settings_unavailable", status=503)
        if not isinstance(value, dict) or set(value) != {"mode", "origin", "certificate"}:
            raise AppError("invalid_access_settings")
        if not isinstance(value["mode"], str) or not (
            value["certificate"] is None or isinstance(value["certificate"], str)
        ):
            raise AppError("invalid_access_settings")
        parts = canonical_origin(value["origin"])
        if (
            value["mode"] not in {"http", "https", "proxy"}
            or value["mode"] != "proxy"
            and value["mode"] != parts.scheme
        ):
            raise AppError("invalid_access_mode")
        if value["mode"] == "https":
            certificate = self.config.get("certificates", {}).get(value["certificate"])
            if certificate is None:
                raise AppError("access_certificate_not_installed", status=422)
            if check_certificate:
                certificate_context(certificate, parts.hostname)
        else:
            if value["certificate"] is not None:
                raise AppError("access_certificate_not_applicable")
        return value

    async def read_on(self, conn):
        row = await (
            await conn.execute("SELECT revision,value FROM runtime.access_settings WHERE singleton")
        ).fetchone()
        return row or {"revision": 0, "value": deepcopy(self.config["initial"])}

    async def load(self):
        if not self.config:
            return self.settings
        async with self.database.transaction(read_only=True) as conn:
            saved = await self.read_on(conn)
        await asyncio.to_thread(self.validate, saved["value"])
        self.active = deepcopy(saved["value"])
        document = deepcopy(self.settings.document)
        http = document["http"]
        http["public_mode"], http["public_origin"] = self.active["mode"], self.active["origin"]
        tls = http.setdefault("tls", {})
        tls["enabled"] = self.active["mode"] == "https"
        if tls["enabled"]:
            tls.update(self.config["certificates"][self.active["certificate"]])
        document.setdefault("identity", {})["cookie_secure"] = (
            canonical_origin(self.active["origin"]).scheme == "https"
        )
        return replace(self.settings, document=document)

    async def view_on(self, conn, context):
        if not self.config:
            return {"available": False, "can_manage": False}
        saved = await self.read_on(conn)
        return {
            "available": True,
            "can_manage": "config.publish" in context.permissions or "*" in context.permissions,
            "active": self.active,
            "saved": saved["value"],
            "revision": saved["revision"],
            "restart_required": saved["value"] != self.active,
            "certificates": sorted(self.config.get("certificates", {})),
            "listener_port": self.settings.get("http", {}).get("port", 18446),
            "https_listener_port": self.settings.get("http", {}).get("tls", {}).get("port", 18443),
        }

    async def save(self, context, body, *, identity):
        if "config.publish" not in context.permissions and "*" not in context.permissions:
            raise AppError("forbidden", status=403)
        await asyncio.to_thread(self.validate, body.get("value"))
        expected = body.get("expected_revision")
        if type(expected) is not int or expected < 0:
            raise AppError("expected_revision_required")

        async def apply(conn):
            await identity.check_context(context, connection=conn)
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('runtime.access',0))")
            saved = await self.read_on(conn)
            if saved["revision"] != expected:
                raise AppError("revision_conflict", status=409)
            await conn.execute(
                "INSERT INTO runtime.access_settings(singleton,revision,value) VALUES(true,%s,%s) ON CONFLICT(singleton) DO UPDATE SET revision=excluded.revision,value=excluded.value",
                (expected + 1, Jsonb(body["value"])),
            )
            return CommandResult(
                body["client_request_id"],
                data=await self.view_on(conn, context),
                revision=expected + 1,
            )

        async def replay(conn, original):
            await identity.check_context(context, connection=conn)
            return replace(original, data=await self.view_on(conn, context))

        return await self.commands.execute(
            context,
            "runtime.access.save",
            body["client_request_id"],
            body_hash(body),
            apply,
            on_replay=replay,
        )

    def register(self, app, _runtime, identity):
        async def view(request):
            context = require_context(request)
            async with self.database.transaction(read_only=True) as conn:
                return json_response(await self.view_on(conn, context))

        async def save(request):
            return json_response(
                await self.save(
                    require_context(request, "config.publish"),
                    await command_body(request),
                    identity=identity,
                )
            )

        @web.middleware
        async def public_transport(request, handler):
            if (
                self.active
                and self.active["mode"] == "https"
                and not request.secure
                and not request.path.startswith(("/health/", "/internal/", "/channels/"))
            ):
                return web.Response(
                    status=308, headers={"Location": self.active["origin"] + str(request.rel_url)}
                )
            return await handler(request)

        app.middlewares.insert(1, public_transport)
        app.router.add_get("/api/v1/access/view", view)
        app.router.add_post("/api/v1/access/view", view)
        app.router.add_post("/api/v1/access/save", save)
