"""Real pinned applications plus explicitly recorded external model/channel substitutes."""

import asyncio
import copy
import hashlib
import ipaddress
import json
import os
import secrets
import socket
import ssl
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import uvicorn
from aiohttp import web
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from services.platform.contracts import Fault as PlatformFault
from services.platform.contracts import utc
from services.platform.server import create_app as platform_app
from services.platform.service import Platform
from tianshu_companion.app import create_app as core_app
from tianshu_companion.clients import (
    Gateway,
    JsonService,
    Memory,
    Origins,
    Sender,
    command,
)
from tianshu_companion.contracts import Contracts as CoreContracts
from tianshu_companion.core import Core, Policy
from tianshu_companion.store import Store as CoreStore
from tianshu_gateway.config import ClientGrant
from tianshu_gateway.server import GATEWAY, Settings
from tianshu_gateway.server import create_app as gateway_app
from tianshu_memory.app import configured_app
from tianshu_memory.app import create_app as memory_app
from tianshu_memory.auth import Authenticator
from tianshu_memory.contracts import Contracts as MemoryContracts
from tianshu_memory.domain import Fault as MemoryFault
from tianshu_memory.domain import now, parse_time, require
from tianshu_memory.service import MemoryService
from tianshu_memory.store import Store as MemoryStore

RUNTIME = Path(os.environ["TS050_RUNTIME"])
CONTRACT = Path(os.environ["TS050_CONTRACTS"])
DOCUMENTS = {
    item["id"]: item["document"]
    for item in json.loads((CONTRACT / "examples/documents.json").read_text("utf-8"))
}


def sample(name):
    return copy.deepcopy(DOCUMENTS[name])


def sha(data):
    return hashlib.sha256(data).hexdigest()


def ca_contexts(directory):
    """Temporary CA/server cert, localhost SAN; no system or certifi modifications."""
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "TS050 isolated test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path, cert_path, key_path = [
        Path(directory) / n for n in ("ca.pem", "server.pem", "key.pem")
    ]
    ca_path.write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    client_context = ssl.create_default_context(cafile=str(ca_path))
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(cert_path, key_path)
    return client_context, server_context, cert_path, key_path


class AppServer:
    """ASGI real TCP listener in the owning event loop, with bounded shutdown."""

    async def start(self, app, cert=None, key=None, *, sock=None):
        self.sock = socket.socket() if sock is None else sock
        if sock is None:
            self.sock.bind(("127.0.0.1", 0))
        self.url = f"{'https' if cert else 'http'}://127.0.0.1:{self.sock.getsockname()[1]}"
        config = uvicorn.Config(
            app,
            access_log=False,
            log_level="error",
            lifespan="on",
            ws="none",
            ssl_certfile=str(cert) if cert else None,
            ssl_keyfile=str(key) if key else None,
            timeout_graceful_shutdown=2,
        )
        self.server = uvicorn.Server(config)
        self.task = asyncio.create_task(self.server.serve(sockets=[self.sock]))
        async with asyncio.timeout(5):
            while not self.server.started:
                if self.task.done():
                    await self.task
                    raise RuntimeError("ASGI server exited during startup")
                await asyncio.sleep(0.01)
        return self

    async def close(self):
        if not hasattr(self, "task"):
            if hasattr(self, "sock"):
                self.sock.close()
            return
        if not self.task.done():
            self.server.should_exit = True
            try:
                await asyncio.wait_for(asyncio.shield(self.task), 5)
            except TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task, return_exceptions=True)
        self.sock.close()


class RealChain(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.trace = {
            "scenario": self._testMethodName,
            "classification": "partial",
            "checks": [],
        }
        self.resources = []
        self.runners = {}
        self.ports = []
        self.temp = tempfile.TemporaryDirectory(prefix="run-", dir=RUNTIME)
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.tokens = {}
        self.previous_env = {}
        for name in (
            "ADMIN",
            "CONNECTOR",
            "COMPANION",
            "MEMORY",
            "MEMORY_RESOLVER",
            "COMPANION_RESOLVER",
            "GATEWAY",
            "UPSTREAM",
            "CORE_INGRESS",
            "MEMORY_API",
            "GATEWAY_API",
            "CHANNEL_API",
        ):
            self.tokens[name] = "synthetic-TS050-" + secrets.token_urlsafe(24)
            self.set_env("TS050_" + name, self.tokens[name])
        self.addCleanup(self.restore_env)
        self.addAsyncCleanup(self.cleanup_resources)
        self.tls, self.server_tls, self.cert, self.key = ca_contexts(self.directory)
        self.client = httpx.AsyncClient(
            verify=self.tls,
            trust_env=False,
            timeout=10,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self.resources.append(self.client.aclose)
        self.model_requests = []
        self.channel_requests = []
        self.platform_requests = []
        self.memory_requests = []
        self.mapping_receipts = []
        self.select_gate = None
        self.select_arrivals = 0
        self.model_mode = "normal"
        self.response_bytes = b' {"id":"ts050-recording","object":"chat.completion","created":1,"model":"fixture-text-model","choices":[{"index":0,"message":{"role":"assistant","content":"TS050 recorded reply"},"finish_reason":"stop"}],"usage":{"prompt_tokens":4,"completion_tokens":3,"total_tokens":7}}\n'
        model = web.Application()
        model.router.add_post("/v1/chat/completions", self.record_model)
        self.model_url, _ = await self.start_aiohttp(model)
        self.settings = self.platform_settings(self.model_url + "/v1")
        self.platform = Platform(self.settings)
        self.origin = self.platform.origins.issue(self.bearer("CONNECTOR"), "chat")["assertion_ref"]
        config_origin = self.platform.origins.issue(self.bearer("ADMIN"), "config")["assertion_ref"]
        self.set_env("TS050_PLATFORM_ORIGIN", config_origin)
        self.publish(7)

        @web.middleware
        async def record(request, handler):
            raw = await request.read()
            response = await handler(request)
            item = {
                "path": request.path,
                "transport": request.scheme,
                "request_sha256": sha(raw),
                "status": response.status,
                "credential_role": next(
                    (
                        role
                        for role in ("MEMORY_RESOLVER", "COMPANION_RESOLVER", "GATEWAY")
                        if request.headers.get("Authorization") == self.bearer(role)
                    ),
                    "unrecognized",
                ),
            }
            if response.status == 200 and request.path.endswith("/origins/resolve"):
                context = json.loads(response.body)["context"]
                item["resolved"] = {
                    key: context[key]
                    for key in (
                        "issuer",
                        "authenticated_service",
                        "audience_service",
                        "allowed_scope",
                    )
                }
            self.platform_requests.append(item)
            return response

        app = platform_app(self.platform)
        app.middlewares.insert(0, record)
        self.platform_url, self.platform_tls_url = await self.start_aiohttp(app, tls=True)
        self.gateway_settings = Settings(
            str(CONTRACT),
            str(self.directory / "gateway.sqlite"),
            self.platform_url,
            "secret-ref:ts050/platform",
            "TS050_PLATFORM_ORIGIN",
            {
                "secret-ref:ts050/platform": "TS050_GATEWAY",
                "secret-ref:ts050/core": "TS050_GATEWAY_API",
                "secret-ref:fixture/provider-a": "TS050_UPSTREAM",
            },
            [
                {
                    "base_url": url,
                    "addresses": ["127.0.0.1"],
                    "allow_private_http": True,
                }
                for url in (self.platform_url, self.model_url + "/v1")
            ],
            [
                ClientGrant(
                    "companion",
                    "secret-ref:ts050/core",
                    "provider-fixture",
                    7,
                    True,
                    (8,),
                )
            ],
            config_refresh_seconds=0,
        )
        self.gateway = gateway_app(self.gateway_settings)
        self.gateway_url, self.gateway_tls_url = await self.start_aiohttp(self.gateway, tls=True)
        # Reachable but no fake success is ever fed into Core; any send is recorded then fails.
        channel = web.Application()

        async def record_channel(request):
            self.channel_requests.append(await request.json())
            return web.json_response(
                {"error": "no real Core generation reached this recorder"}, status=503
            )

        channel.router.add_post("/internal/v1/conversation/send", record_channel)
        _, self.channel_url = await self.start_aiohttp(channel, tls=True)

    def set_env(self, name, value):
        if name not in self.previous_env:
            self.previous_env[name] = os.environ.get(name)
        os.environ[name] = value

    def restore_env(self):
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    async def cleanup_resources(self):
        failures = []
        for close in reversed(self.resources):
            try:
                await close()
            except Exception as error:
                failures.append(type(error).__name__)

        def port_open(port):
            with socket.socket() as probe:
                probe.settimeout(0.2)
                return probe.connect_ex(("127.0.0.1", port)) == 0

        for port in self.ports:
            if await asyncio.to_thread(port_open, port):
                failures.append(f"listener_still_open:{port}")
        self.trace["cleanup"] = {
            "listeners_checked": len(self.ports),
            "failures": failures,
        }
        result_path = RUNTIME / "results" / (self._testMethodName + ".json")
        if result_path.exists():
            result_path.write_text(
                json.dumps(self.trace, indent=2, ensure_ascii=False) + "\n", "utf-8"
            )
        self.assertEqual(failures, [], "Every server/client must be closed")

    async def asyncTearDown(self):
        self.trace["model_http_calls"] = len(self.model_requests)
        self.trace["channel_http_calls"] = len(self.channel_requests)
        self.trace["platform_http_requests"] = self.platform_requests
        self.trace["memory_http_requests"] = self.memory_requests
        self.trace["mapping_receipts"] = self.mapping_receipts
        self.trace["model_wire"] = self.model_requests
        self.trace["gateway_active"] = self.gateway[GATEWAY].active
        self.assertEqual(self.gateway[GATEWAY].active, 0)
        encoded = json.dumps(self.trace, indent=2, ensure_ascii=False) + "\n"
        for value in [
            *self.tokens.values(),
            self.origin,
            os.environ["TS050_PLATFORM_ORIGIN"],
        ]:
            self.assertNotIn(value, encoded, "Evidence must not publish credentials or origin refs")
        result_dir = RUNTIME / "results"
        result_dir.mkdir(exist_ok=True)
        (result_dir / (self._testMethodName + ".json")).write_text(encoded, "utf-8")

    def check(self, name, **evidence):
        self.trace["checks"].append({"name": name, **evidence})

    def bearer(self, name):
        return "Bearer " + self.tokens[name]

    def platform_settings(self, upstream):
        principals = {}

        def principal(name, service, actions, **extra):
            principals[name.lower()] = {
                "kind": "service",
                "service": service,
                "token_env": "TS050_" + name,
                "actions": actions,
                **extra,
            }

        principal(
            "ADMIN",
            "platform",
            [
                "origin.issue",
                "origin.revoke",
                "entry.revoke",
                "config.publish",
                "config.revoke",
            ],
            kind="operator",
            account={
                "namespace": "web",
                "immutable_account_id": "ts050-local-operator",
            },
        )
        principal(
            "CONNECTOR",
            "nonebot",
            ["origin.issue", "mapping.prepare", "source.observe"],
        )
        principal(
            "COMPANION",
            "companion",
            ["mapping.prepare", "mapping.confirm", "source.verify"],
        )
        principal("MEMORY", "memory", ["mapping.confirm"])
        principal(
            "MEMORY_RESOLVER",
            "memory",
            ["origin.resolve"],
            resolver={"caller": "companion", "purpose": "dialogue"},
        )
        principal(
            "COMPANION_RESOLVER",
            "companion",
            ["origin.resolve"],
            resolver={"caller": "nonebot", "purpose": "dialogue"},
        )
        principal("GATEWAY", "gateway", ["config.snapshot"], config_versions=[7, 8])
        self.account = {
            "namespace": "qq",
            "immutable_account_id": "ts050-synthetic-person",
        }
        self.channel = {
            "namespace": "qq",
            "binding_id": "ts050-local-recorder",
            "channel_conversation_id": "ts050-private",
            "thread_id": None,
        }
        entries = {
            "chat": {
                "kind": "rehearsal_connector",
                "owner": "connector",
                "account": self.account,
                "channel": self.channel,
                "actor_id": "actor-fixture",
                "audience": "self_private",
                "ttl_seconds": 300,
                "routes": [
                    {
                        "caller": "nonebot",
                        "receiver": "companion",
                        "purpose": "dialogue",
                    },
                    {
                        "caller": "companion",
                        "receiver": "memory",
                        "purpose": "dialogue",
                    },
                ],
            },
            "config": {
                "kind": "local_operator",
                "owner": "admin",
                "account": principals["admin"]["account"],
                "channel": {
                    "namespace": "web",
                    "binding_id": "ts050-console",
                    "channel_conversation_id": "config",
                    "thread_id": None,
                },
                "actor_id": "ts050-config",
                "audience": "self_private",
                "ttl_seconds": 300,
                "routes": [
                    {
                        "caller": "gateway",
                        "receiver": "platform",
                        "purpose": "config.snapshot",
                    }
                ],
            },
        }
        provider = sample("config")["providers"][0]
        registration = {
            k: provider[k]
            for k in (
                "credential_ref",
                "credential_namespace",
                "capability_verification",
                "verified_capabilities",
            )
        }
        registration.update(
            base_url=upstream,
            model_ids=[provider["model_id"]],
            reviewed_addresses=["127.0.0.1"],
            allow_private_http=True,
        )
        return {
            "mode": "local_rehearsal",
            "storage": "sqlite_local",
            "database_path": str(self.directory / "platform.sqlite"),
            "contract_directory": str(CONTRACT),
            "principals": principals,
            "entries": entries,
            "providers": {provider["provider_id"]: registration},
        }

    def publish(self, version):
        config = sample("config")
        config.update(
            config_version=version,
            published_at=utc(time.time() - 1),
            usable_until=utc(time.time() + 300),
        )
        config["providers"][0]["base_url"] = self.model_url + "/v1"
        self.platform.models.publish(self.bearer("ADMIN"), config)
        return config

    async def start_aiohttp(self, app, tls=False):
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        self.runners[id(app)] = runner
        self.resources.append(runner.cleanup)
        plain = web.TCPSite(runner, "127.0.0.1", 0)
        await plain.start()
        self.ports.append(runner.addresses[0][1])
        url = f"http://127.0.0.1:{runner.addresses[0][1]}"
        secure_url = None
        if tls:
            secure = web.TCPSite(runner, "127.0.0.1", 0, ssl_context=self.server_tls)
            await secure.start()
            self.ports.append(runner.addresses[-1][1])
            secure_url = f"https://127.0.0.1:{runner.addresses[-1][1]}"
        return url, secure_url

    async def start_asgi(self, app):
        server = AppServer()
        # Register cleanup before awaiting startup so a failed bind/start does not leak.
        self.resources.append(server.close)
        await server.start(app, self.cert, self.key)
        self.ports.append(server.sock.getsockname()[1])
        return server

    def service_client(self, url, token_name):
        client = JsonService(
            url,
            self.tokens[token_name],
            transport=httpx.AsyncHTTPTransport(
                verify=self.tls,
                trust_env=False,
                limits=httpx.Limits(max_keepalive_connections=0),
            ),
        )
        self.resources.append(client.close)
        return client

    async def record_model(self, request):
        raw = await request.read()
        self.assertEqual(request.headers.get("Authorization"), self.bearer("UPSTREAM"))
        self.model_requests.append(
            {
                "request_bytes": len(raw),
                "request_sha256": sha(raw),
                "request_utf8": raw.decode(),
                "independent_upstream_auth": True,
            }
        )
        if self.model_mode == "disconnect":
            request.transport.abort()
            return web.Response()
        return web.Response(body=self.response_bytes, content_type="application/json")

    async def native_request(self, request_id, *, body=None, version=7):
        body = (
            body
            or b' { "messages" : [{"role":"user","content":"synthetic TS050"}], "model":"fixture-text-model", "stream":false }\n'
        )
        return await self.client.post(
            self.gateway_url + "/v1/chat/completions",
            content=body,
            headers={
                "Authorization": self.bearer("GATEWAY_API"),
                "Content-Type": "application/json",
                "X-Request-ID": request_id,
                "X-Tianshu-Config-Version": str(version),
                "X-Tianshu-Turn-ID": "turn:" + request_id,
                "X-Tianshu-Workload": "companion.text",
            },
        )

    async def receipt(self, request_id):
        response = await self.client.get(
            self.gateway_url + "/internal/v1/model-requests/" + request_id,
            headers={"Authorization": self.bearer("GATEWAY_API")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.platform.contracts.check("model#route_receipt", response.json())
        return response.json()

    async def eventually(self, predicate, timeout=5):
        async with asyncio.timeout(timeout):
            while not predicate():
                await asyncio.sleep(0.02)

    async def start_memory(self, *, port_auth=True, configured_tls=False):
        if port_auth and configured_tls:
            raise ValueError("Configured TLS must use the shipped Authenticator")
        self.auth_path = self.directory / "memory-auth.json"
        self.auth_config = {
            "mode": "explicit_application_port_composition" if port_auth else "deployed",
            "contract_directory": str(CONTRACT),
            "database_path": str(self.directory / "memory.sqlite"),
            "callers": {
                "companion": {
                    "token": self.tokens["MEMORY_API"],
                    "issuer": "platform",
                    "issuer_url": self.platform_tls_url + "/internal/v1/origins/resolve",
                    "issuer_token": self.tokens["MEMORY_RESOLVER"],
                    "allowed_actors": ["actor-fixture"],
                    "operations": [
                        "resolve",
                        "register",
                        "select",
                        "consume",
                        "revise",
                    ],
                    "event_scopes": [],
                }
            },
        }
        if configured_tls:
            self.auth_config["callers"]["companion"]["issuer_ca_file"] = str(
                self.directory / "ca.pem"
            )
        self.auth_path.write_text(json.dumps(self.auth_config), "utf-8")
        if configured_tls:
            self.set_env("TIANSHU_MEMORY_CONFIG", str(self.auth_path))
            app = configured_app()
            self.memory_service = app.state.memory
            self.memory_contracts = self.memory_service.contracts
        else:
            self.memory_contracts = MemoryContracts(CONTRACT)
            self.memory_service = MemoryService(
                MemoryStore(self.directory / "memory.sqlite"),
                self.memory_contracts,
                source_authority=None,
            )
            shipped_auth = Authenticator(self.auth_path, self.memory_contracts, now)
            auth = (
                PlatformPortAuthenticator(shipped_auth, self.platform)
                if port_auth
                else shipped_auth
            )
            app = memory_app(service=self.memory_service, auth=auth)

        @app.middleware("http")
        async def record(request, call_next):
            raw = await request.body()
            if request.url.path.endswith("/memory/select") and self.select_gate is not None:
                self.select_arrivals += 1
                await self.select_gate.wait()  # Actual request delay; never supplies a response.
            response = await call_next(request)
            body = json.loads(raw) if raw else {}
            item = {
                "path": request.url.path,
                "status": response.status_code,
                "request_sha256": sha(raw),
            }
            if "budget" in body:
                item.update(budget=body["budget"], query_text=body["query_text"])
            self.memory_requests.append(item)
            return response

        self.memory_server = await self.start_asgi(app)
        self.memory_url = self.memory_server.url

    async def start_core(self, silence_ms=0):
        self.clock = getattr(self, "clock", ControlledClock())
        self.core_policy = Policy(silence_ms=silence_ms)
        self.core_contracts = CoreContracts(CONTRACT)
        origins = Origins(
            self.core_contracts,
            {
                "nonebot": (
                    "platform",
                    self.service_client(self.platform_tls_url, "COMPANION_RESOLVER"),
                )
            },
        )
        memory_transport = MappingClient(
            self.service_client(self.memory_url, "MEMORY_API"),
            self.platform,
            self.bearer("COMPANION"),
            self.bearer("MEMORY"),
            self.mapping_receipts,
        )
        self.core = Core(
            CoreStore(self.directory / "core.sqlite"),
            self.core_contracts,
            origins,
            Memory(self.core_contracts, memory_transport),
            Gateway(
                self.core_contracts,
                self.service_client(self.gateway_tls_url, "GATEWAY_API"),
            ),
            Sender(
                self.core_contracts,
                self.service_client(self.channel_url, "CHANNEL_API"),
            ),
            bindings={
                self.channel["binding_id"]: {
                    "service": "nonebot",
                    "namespace": "qq",
                    "audience": "self_private",
                    "actor_ids": ["actor-fixture"],
                }
            },
            roles={"actor-fixture": {"persona": "TS050 local rehearsal companion"}},
            config_version=7,
            policy=self.core_policy,
            clock=self.clock,
        )
        self.core_server = await self.start_asgi(
            core_app(self.core, {"nonebot": self.tokens["CORE_INGRESS"]})
        )

    def ingest_body(self, message_id, text, *, revision=1, kind="message"):
        body = sample("ingest")
        body.update(
            command=command(
                {"assertion_ref": self.origin},
                message_id + ":" + str(revision),
                self.clock(),
            ),
            author=self.account,
            sent_at=utc(self.clock()),
            message_key={
                "channel": self.channel,
                "message_id": message_id,
                "revision": revision,
            },
            kind=kind,
            parts=[{"kind": "text", "text": text}] if kind != "retract" else [],
        )
        return body

    async def ingest(self, body, expected=200):
        ticket = self.platform.origins.prepare_mapping(self.bearer("CONNECTOR"), "ingest", body)
        response = await self.client.post(
            self.core_server.url + "/internal/v1/conversation/ingest",
            json=body,
            headers={"Authorization": self.bearer("CORE_INGRESS")},
        )
        self.assertEqual(response.status_code, expected, response.text)
        if expected == 200:
            self.platform.origins.confirm_mapping(self.bearer("COMPANION"), ticket, response.json())
            self.platform.origins.observe_source(
                self.bearer("CONNECTOR"),
                "chat",
                body["message_key"],
                tombstone=body["kind"] == "retract",
            )
            self.mapping_receipts.append({"kind": "ingest", "response": response.json()})
        return response.json()

    async def resolved_scope(self):
        response = await self.client.post(
            self.platform_tls_url + "/internal/v1/origins/resolve",
            json={
                "schema_version": 1,
                "request_id": "ts050:scope",
                "assertion_ref": self.origin,
            },
            headers={"Authorization": self.bearer("MEMORY_RESOLVER")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["context"]["allowed_scope"]

    def core_state(self):
        # Observability only, never an authority adapter or a product-to-product database query.
        # Uses the owning Core's repository API and never writes state.
        return {
            "turns": [
                {
                    "id": t["id"],
                    "sequence": t["sequence"],
                    "phase": t["phase"],
                    "failure": t["failure"],
                    "model_calls": t["model_calls"],
                    "scope_version": t["scope_version"],
                    "bundle": t["bundle"],
                }
                for t in self.core.store.list("turns")
            ],
            "collections": [
                {
                    "id": c["id"],
                    "state": c["state"],
                    "revision": c["revision"],
                    "deadline": c["deadline"],
                    "source_context_revision": c["source_context_revision"],
                }
                for c in self.core.store.list("collections")
            ],
            "outbox": [
                {
                    "id": e["id"],
                    "state": e["state"],
                    "attempts": e["attempts"],
                    "scope_version": e["event"]["scope_version"],
                }
                for e in self.core.store.list("outbox")
            ],
            "replies": len(self.core.store.list("replies")),
        }


class ControlledClock:
    """Core's existing clock injection; virtual deadline boundaries, not latency measurements."""

    def __init__(self):
        self.value = time.time()

    def __call__(self):
        return self.value

    def advance(self, seconds):
        self.value += seconds


class PlatformPortAuthenticator:
    """Coordinator-approved B adapter. Shipped HTTPS resolve is NOT used by this adapter.

    Credential/operation configuration remains Memory Authenticator's real implementation.
    Context comes exclusively from the actual platform's authenticated public application port.
    There is no dictionary origin ledger or synthetic permission/scope success.
    """

    def __init__(self, shipped, platform):
        self.shipped, self.platform = shipped, platform

    def authenticate(self, header):
        return self.shipped.authenticate(header)

    def resolve(self, service, caller, assertion_ref, request_id):
        try:
            response = self.platform.origins.resolve(
                "Bearer " + caller["issuer_token"],
                {
                    "schema_version": 1,
                    "request_id": request_id,
                    "assertion_ref": assertion_ref,
                },
            )
        except PlatformFault as error:
            raise MemoryFault(error.code, error.status) from None
        self.shipped.contracts.validate("common#origin_resolve_response", response)
        context = response["context"]
        require(response["request_id"] == request_id and context["issuer"] == caller["issuer"])
        require(
            context["authenticated_service"] == service and context["audience_service"] == "memory"
        )
        require(context["assertion_ref"] == assertion_ref and not context["revoked"])
        require(parse_time(context["expires_at"]) > self.shipped.clock())
        require(context["allowed_scope"]["actor_id"] in caller["allowed_actors"])
        return context


class MappingClient:
    """Trusted receipt adapter around actual Memory HTTP responses, no extra wire endpoint."""

    def __init__(self, client, platform, prepare_auth, confirm_auth, receipts):
        self.client, self.platform = client, platform
        self.prepare_auth, self.confirm_auth, self.receipts = (
            prepare_auth,
            confirm_auth,
            receipts,
        )

    async def call(self, path, body=None, headers=None):
        kind = {
            "/internal/v1/identity/resolve": "identity_resolve",
            "/internal/v1/identity/register": "identity",
        }.get(path)
        ticket = (
            self.platform.origins.prepare_mapping(self.prepare_auth, kind, body) if kind else None
        )
        response = await self.client.call(path, body, headers)
        if kind:
            self.platform.origins.confirm_mapping(self.confirm_auth, ticket, response)
            self.receipts.append({"kind": kind, "response": response})
        return response
