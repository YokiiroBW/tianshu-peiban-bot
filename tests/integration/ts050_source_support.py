"""Actual source-sync owners over TLS; only external model/channel/extraction are recorded."""

import asyncio
import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path

import httpx
from aiohttp import web
from services.platform.server import create_app as platform_app
from services.platform.service import Platform
from tianshu_companion.app import create_app as companion_app
from tianshu_companion.clients import command, utc
from tianshu_companion.contracts import Contracts as CoreContracts
from tianshu_gateway.config import ClientGrant
from tianshu_gateway.server import Settings
from tianshu_memory.app import configured_app
from tianshu_memory.contracts import Contracts as MemoryContracts
from tianshu_memory.source_authority import SourceAuthority
from tianshu_memory.store import Store as MemoryStore
from tianshu_memory.workflow import TrustedWorkflow
from ts050_support import CONTRACT, RUNTIME, AppServer, ca_contexts, sample


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def scrub(value):
    """Keep equality evidence for capability refs without publishing usable credentials."""
    if isinstance(value, dict):
        return {
            ("assertion_digest" if key == "assertion_ref" else key): (
                digest(item.encode()) if key == "assertion_ref" else scrub(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [scrub(item) for item in value]
    return value


def reserve(port=0):
    sock = socket.socket()
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("127.0.0.1", port))
    return sock


class WireASGI:
    """Transparent ASGI wire recorder: no altered requests, responses, auth or product state."""

    def __init__(self, app, owner, records):
        self.app, self.owner, self.records = app, owner, records

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_body, response_body = bytearray(), bytearray()
        record = {
            "owner": self.owner,
            "path": scope["path"],
            "transport": scope["scheme"],
            "started": time.monotonic(),
            "status": None,
        }

        async def read():
            message = await receive()
            if message["type"] == "http.request":
                request_body.extend(message.get("body", b""))
            return message

        async def write(message):
            if message["type"] == "http.response.start":
                record["status"] = message["status"]
            if message["type"] == "http.response.body":
                response_body.extend(message.get("body", b""))
            await send(message)

        try:
            return await self.app(scope, read, write)
        finally:
            record.update(
                ended=time.monotonic(),
                request_sha256=digest(request_body),
                response_sha256=digest(response_body),
            )
            record["request"] = json.loads(request_body) if request_body else None
            record["response"] = json.loads(response_body) if response_body else None
            self.records.append(record)


class SourceChain(unittest.IsolatedAsyncioTestCase):
    silence_ms = 0
    reconcile_ms = 500

    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="source-run-", dir=RUNTIME)
        self.directory = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.resources, self.ports, self.wire, self.runners = [], [], [], {}
        self.trace = {"scenario": self._testMethodName, "slice": "real_source_sync", "checks": []}
        self.tokens, self.previous_env = {}, {}
        for role in (
            "ADMIN",
            "CONNECTOR",
            "CORE_PLATFORM",
            "MEMORY_PLATFORM",
            "CORE_MEMORY",
            "MEMORY_CORE",
            "INGRESS",
            "GATEWAY_PLATFORM",
            "GATEWAY_CORE",
            "MODEL",
            "CHANNEL",
        ):
            self.tokens[role] = "synthetic-source-" + secrets.token_urlsafe(30)
            self.set_env("TS050_SOURCE_" + role, self.tokens[role])
        self.addCleanup(self.restore_env)
        self.addAsyncCleanup(self.cleanup)
        self.tls, self.server_tls, self.cert, self.key = ca_contexts(self.directory)
        self.ca = self.directory / "ca.pem"
        self.client = httpx.AsyncClient(
            verify=self.tls,
            trust_env=False,
            timeout=20,
            limits=httpx.Limits(max_keepalive_connections=0),
        )
        self.resources.append(self.client.aclose)
        self.core_contracts = CoreContracts(CONTRACT)
        self.model_requests, self.channel_requests, self.channel_receipts = [], [], []
        self.model_gates, self.send_states = {}, []
        self.admissions, self.fanouts = {}, []
        self.memory_records = self.wire
        self.core_socket, self.memory_socket = reserve(), reserve()
        self.resources.extend([self.close_core_socket, self.close_memory_socket])
        self.core_url = f"https://127.0.0.1:{self.core_socket.getsockname()[1]}"
        self.memory_url = f"https://127.0.0.1:{self.memory_socket.getsockname()[1]}"
        model = web.Application()
        model.router.add_post("/v1/chat/completions", self.record_model)
        self.model_url = await self.start_aio(model)
        channel = web.Application()
        channel.router.add_post("/internal/v1/conversation/send", self.record_channel)
        self.channel_url = await self.start_aio(channel)
        self.settings = self.platform_settings()
        self.platform = Platform(self.settings)
        app = self.make_platform_app()
        self.platform_url = await self.start_aio(app)
        self.platform_runner = self.runners[id(app)]
        source_ref = self.platform.origins.issue(self.bearer("ADMIN"), "model-config")[
            "assertion_ref"
        ]
        self.set_env("TS050_SOURCE_CONFIG_ORIGIN", source_ref)
        config = sample("config")
        config.update(published_at=utc(time.time() - 1), usable_until=utc(time.time() + 1800))
        config["providers"][0]["base_url"] = self.model_url + "/v1"
        self.platform.models.publish(self.bearer("ADMIN"), config)
        await self.start_gateway()
        self.memory_config_path = self.directory / "memory-config.json"
        self.memory_config = self.make_memory_config()
        self.write_memory_config()
        contracts = MemoryContracts(CONTRACT)
        contracts.load_sources()
        store = MemoryStore(
            self.memory_config["database_path"],
            recovery_path=self.memory_config["source_sync"]["recovery_path"],
        )
        store.migrate_profiles(self.directory / "backups/schema1.sqlite")
        store.migrate_sources(self.directory / "backups/schema2.sqlite", contracts)
        self.set_env("TIANSHU_MEMORY_CONFIG", str(self.memory_config_path))
        self.memory_app = configured_app()
        self.memory = self.memory_app.state.memory
        self.assertIsInstance(self.memory.source_authority, SourceAuthority)
        self.workflow = TrustedWorkflow(self.memory)
        self.memory_server = await self.start_asgi(self.memory_app, "memory", self.memory_socket)
        self.core_config = self.make_core_config()
        self.core_config_path = self.directory / "core-config.json"
        await self.start_core()

    async def close_core_socket(self):
        self.core_socket.close()

    async def close_memory_socket(self):
        self.memory_socket.close()

    def set_env(self, name, value):
        self.previous_env.setdefault(name, os.environ.get(name))
        os.environ[name] = value

    def restore_env(self):
        for name, value in self.previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def bearer(self, role):
        return "Bearer " + self.tokens[role]

    def make_platform_app(self):
        app = platform_app(self.platform)

        @web.middleware
        async def record(request, handler):
            started = time.monotonic()
            raw = await request.read()
            response = await handler(request)
            self.wire.append(
                {
                    "owner": "platform",
                    "path": request.path,
                    "transport": request.scheme,
                    "started": started,
                    "ended": time.monotonic(),
                    "status": response.status,
                    "request": json.loads(raw),
                    "response": json.loads(response.body),
                    "request_sha256": digest(raw),
                    "response_sha256": digest(response.body),
                }
            )
            return response

        app.middlewares.insert(0, record)
        return app

    async def start_aio(self, app, *, port=0):
        runner = web.AppRunner(app, access_log=None, shutdown_timeout=3)
        await runner.setup()
        self.runners[id(app)] = runner
        self.resources.append(runner.cleanup)
        await web.TCPSite(runner, "127.0.0.1", port, ssl_context=self.server_tls).start()
        port = runner.addresses[0][1]
        self.ports.append(port)
        return f"https://127.0.0.1:{port}"

    async def start_asgi(self, app, owner, sock):
        server = AppServer()
        self.resources.append(server.close)
        await server.start(WireASGI(app, owner, self.wire), self.cert, self.key, sock=sock)
        self.ports.append(server.sock.getsockname()[1])
        return server

    async def start_core(self):
        self.core_config_path.write_text(json.dumps(self.core_config), "utf-8")
        self.set_env("TIANSHU_COMPANION_CONFIG", str(self.core_config_path))
        self.core_app = companion_app()
        self.core = self.core_app.state.core
        self.core_server = await self.start_asgi(self.core_app, "core", self.core_socket)

    async def restart_core(self, *, database_path=None):
        port = self.core_socket.getsockname()[1]
        await self.core_server.close()
        self.core_socket = reserve(port)
        if database_path is not None:
            self.core_config["database_path"] = str(database_path)
        await self.start_core()

    async def start_gateway(self):
        settings = Settings(
            str(CONTRACT),
            str(self.directory / "gateway.sqlite"),
            self.platform_url,
            "secret-ref:source/platform",
            "TS050_SOURCE_CONFIG_ORIGIN",
            {
                "secret-ref:source/platform": "TS050_SOURCE_GATEWAY_PLATFORM",
                "secret-ref:source/core": "TS050_SOURCE_GATEWAY_CORE",
                "secret-ref:fixture/provider-a": "TS050_SOURCE_MODEL",
            },
            [
                {"base_url": url, "addresses": ["127.0.0.1"], "allow_private_http": False}
                for url in (self.platform_url, self.model_url + "/v1")
            ],
            [ClientGrant("companion", "secret-ref:source/core", "provider-fixture", 7, True)],
            config_refresh_seconds=0,
        )
        path = self.directory / "gateway-settings.json"
        path.write_text(json.dumps(asdict(settings)), "utf-8")
        sock = reserve()
        port = sock.getsockname()[1]
        sock.close()
        self.ports.append(port)
        self.gateway_url = f"https://127.0.0.1:{port}"
        env = dict(os.environ, SSL_CERT_FILE=str(self.ca))
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "tianshu_gateway",
                "--settings",
                str(path),
                "--port",
                str(port),
                "--tls-cert",
                str(self.cert),
                "--tls-key",
                str(self.key),
            ],
            cwd=RUNTIME / "sources/model-gateway",
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.gateway_process = process

        async def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)

        self.resources.append(stop)
        async with asyncio.timeout(10):
            while True:
                self.assertIsNone(process.poll(), "Gateway exited before TLS startup")
                try:
                    response = await self.client.get(
                        self.gateway_url + "/internal/v1/model-requests/startup-probe",
                        headers={"Authorization": self.bearer("GATEWAY_CORE")},
                    )
                    self.assertEqual(response.status_code, 404)
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)

    def platform_settings(self):
        principals = {}

        def principal(name, role, service, actions, **extra):
            principals[name] = {
                "kind": "service",
                "service": service,
                "token_env": "TS050_SOURCE_" + role,
                "actions": actions,
                **extra,
            }

        principal(
            "operator",
            "ADMIN",
            "platform",
            [
                "origin.issue",
                "entry.revoke",
                "origin.revoke",
                "principal.revoke",
                "config.publish",
                "config.revoke",
            ],
            kind="operator",
            account={"namespace": "web", "immutable_account_id": "source-synthetic-operator"},
        )
        principal(
            "channel",
            "CONNECTOR",
            "nonebot",
            ["source.register", "source.dispatch", "mapping.prepare", "origin.issue"],
        )
        principal(
            "core",
            "CORE_PLATFORM",
            "companion",
            ["source.input", "origin.resolve", "mapping.confirm"],
            resolver={"caller": "nonebot", "purpose": "dialogue"},
        )
        principal(
            "memory",
            "MEMORY_PLATFORM",
            "memory",
            ["source.current", "origin.resolve"],
            resolver={"caller": "companion", "purpose": "dialogue"},
        )
        principal(
            "gateway", "GATEWAY_PLATFORM", "gateway", ["config.snapshot"], config_versions=[7]
        )
        self.account = {"namespace": "qq", "immutable_account_id": "source-synthetic-author"}
        self.channels = {
            audience: {
                "namespace": "qq",
                "binding_id": "source:" + audience,
                "channel_conversation_id": "source:" + audience,
                "thread_id": None,
            }
            for audience in ("self_private", "group")
        }
        entries, inputs = {}, {}
        for audience, channel in self.channels.items():
            actors = []
            for actor in ("actor:a", "actor:b"):
                name = audience + ":" + actor
                entries[name] = {
                    "kind": "trusted_application",
                    "owner": "channel",
                    "account": self.account,
                    "channel": channel,
                    "actor_id": actor,
                    "audience": audience,
                    "ttl_seconds": 1800,
                    "routes": [
                        {"caller": "nonebot", "receiver": "companion", "purpose": "dialogue"},
                        {"caller": "companion", "receiver": "memory", "purpose": "dialogue"},
                    ],
                }
                actors.append(name)
            inputs[audience] = {
                "owner": "channel",
                "account": self.account,
                "channel": channel,
                "audience": audience,
                "ttl_seconds": 1800,
                "actor_entries": actors,
                "default_actor_ids": ["actor:a"],
                "routing_version": 1,
            }
        entries["model-config"] = {
            "kind": "local_operator",
            "owner": "operator",
            "account": principals["operator"]["account"],
            "channel": {
                "namespace": "web",
                "binding_id": "source:config",
                "channel_conversation_id": "config",
                "thread_id": None,
            },
            "actor_id": "source:config",
            "audience": "self_private",
            "ttl_seconds": 1800,
            "routes": [{"caller": "gateway", "receiver": "platform", "purpose": "config.snapshot"}],
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
            base_url=self.model_url + "/v1",
            model_ids=[provider["model_id"]],
            reviewed_addresses=["127.0.0.1"],
            allow_private_http=False,
        )
        return {
            "mode": "service_https",
            "storage": "sqlite_local",
            "database_path": str(self.directory / "platform.sqlite"),
            "contract_directory": str(CONTRACT),
            "source_contract_directory": str(CONTRACT.parent.parent / "source-sync/v1"),
            "principals": principals,
            "entries": entries,
            "input_entries": inputs,
            "providers": {provider["provider_id"]: registration},
            "core": {
                "base_url": self.core_url,
                "token_env": "TS050_SOURCE_INGRESS",
                "ca_file": str(self.ca),
                "timeout_seconds": 15,
            },
            "tls": {"certificate_file": str(self.cert), "private_key_file": str(self.key)},
        }

    def make_memory_config(self):
        retained = self.directory / "retained"
        retained.mkdir()
        return {
            "mode": "source_sync",
            "database_path": str(self.directory / "memory/data.sqlite"),
            "contract_directory": str(CONTRACT),
            "source_sync": {
                "recovery_path": str(retained / "source-guard.json"),
                "core": {
                    "url": self.core_url + "/internal/v1/source-facts/read",
                    "token": self.tokens["MEMORY_CORE"],
                    "ca_file": str(self.ca),
                },
                "platform": {
                    "url": self.platform_url + "/internal/v1/source-access/read",
                    "token": self.tokens["MEMORY_PLATFORM"],
                    "ca_file": str(self.ca),
                },
            },
            "callers": {
                "companion": {
                    "token": self.tokens["CORE_MEMORY"],
                    "issuer": "platform",
                    "issuer_url": self.platform_url + "/internal/v1/origins/resolve",
                    "issuer_token": self.tokens["MEMORY_PLATFORM"],
                    "issuer_ca_file": str(self.ca),
                    "allowed_actors": ["actor:a", "actor:b"],
                    "operations": [
                        "resolve",
                        "register",
                        "select",
                        "select_profiles",
                        "consume",
                        "revise",
                        "check_sources",
                    ],
                    "event_scopes": [],
                }
            },
        }

    def write_memory_config(self):
        self.memory_config_path.write_text(json.dumps(self.memory_config), "utf-8")

    def make_core_config(self):
        services = {}
        for name, url, role in (
            ("platform", self.platform_url, "CORE_PLATFORM"),
            ("memory", self.memory_url, "CORE_MEMORY"),
            ("gateway", self.gateway_url, "GATEWAY_CORE"),
            ("nonebot", self.channel_url, "CHANNEL"),
        ):
            services[name] = {
                "url": url,
                "token_env": "TS050_SOURCE_" + role,
                "ca_file": str(self.ca),
            }
        return {
            "contracts_path": str(CONTRACT),
            "database_path": str(self.directory / "core.sqlite"),
            "config_version": 7,
            "policy": {
                "silence_ms": self.silence_ms,
                "delivery_reconcile_timeout_ms": self.reconcile_ms,
            },
            "roles": {
                actor: {"version": 1, "persona": actor + " | synthetic local companion"}
                for actor in ("actor:a", "actor:b")
            },
            "bindings": {
                channel["binding_id"]: {
                    "namespace": "qq",
                    "service": "nonebot",
                    "audience": audience,
                    "actor_ids": ["actor:a", "actor:b"],
                    "classification": {
                        "value": "real",
                        "basis": "registered_input_mode",
                        "policy_ref": "input-mode:synthetic-rehearsal",
                        "policy_version": 1,
                    },
                }
                for audience, channel in self.channels.items()
            },
            "callers": {
                "nonebot": {
                    "token_env": "TS050_SOURCE_INGRESS",
                    "issuer": "platform",
                    "origin_service": "platform",
                },
                "memory": {"token_env": "TS050_SOURCE_MEMORY_CORE"},
            },
            "services": services,
        }

    async def record_model(self, request):
        self.assertEqual(request.headers.get("Authorization"), self.bearer("MODEL"))
        raw = await request.read()
        body = json.loads(raw)
        actor = body["messages"][0]["content"].split(" | ", 1)[0]
        record = {
            "actor": actor,
            "body": body,
            "request_bytes": len(raw),
            "request_sha256": digest(raw),
            "started": time.monotonic(),
            "completed": None,
        }
        self.model_requests.append(record)
        gate = self.model_gates.get(actor)
        if gate is not None:
            await gate.wait()
        text = f"{actor} recorded reply {len(self.model_requests)}"
        prompt = body["messages"][-1]["content"]
        if "我叫什么" in prompt and "小明" in prompt:
            text = "你叫小明。"  # Recording substitute requires the actual prompt's context.
        response = {
            "id": "source-recorded",
            "object": "chat.completion",
            "created": 1,
            "model": "fixture-text-model",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": text},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        payload = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode()
        record.update(
            completed=time.monotonic(),
            response_bytes=len(payload),
            response_sha256=digest(payload),
            response=response,
        )
        return web.Response(body=payload, content_type="application/json")

    async def record_channel(self, request):
        self.assertEqual(request.headers.get("Authorization"), self.bearer("CHANNEL"))
        body = await request.json()
        self.core_contracts.check("conversation#send_request", body)
        self.channel_requests.append(body)
        index = len(self.channel_requests)
        state = self.send_states[index - 1] if len(self.send_states) >= index else "sent"
        receipt = {
            "schema_version": 1,
            "request_id": body["command"]["request_id"],
            "reply_id": body["reply_id"],
            "segment_sequence": body["segment_sequence"],
            "attempt_id": f"recorded-attempt:{index}",
            "state": state,
            "channel_message_ids": [f"recorded-channel:{index}"] if state == "sent" else [],
            "observed_at": utc(),
            "retry_safe": False,
        }
        self.core_contracts.check("conversation#send_receipt", receipt)
        self.channel_receipts.append(receipt)
        return web.json_response(receipt)

    def physical(self, text, message_id, *, audience="self_private", revision=1, kind="message"):
        return {
            "message_key": {
                "channel": self.channels[audience],
                "message_id": message_id,
                "revision": revision,
            },
            "author": self.account,
            "sent_at": utc(),
            "kind": kind,
            "parts": [] if kind == "retract" else [{"kind": "text", "text": text}],
            "reply_refs": [],
            "mentioned_accounts": [],
        }

    async def submit(self, physical, *, actors=("actor:a",), key=None):
        audience = next(
            a for a, c in self.channels.items() if c == physical["message_key"]["channel"]
        )
        proof = self.platform.sources.register_input(self.bearer("CONNECTOR"), audience, physical)
        request = {
            "schema_version": 1,
            "command": command(
                {"assertion_ref": proof["assertion_ref"]},
                key or "source-command:" + secrets.token_hex(8),
                time.time(),
            ),
            "input": physical,
            "target_actor_ids": list(actors),
        }
        result = await self.platform.sources.dispatch(self.bearer("CONNECTOR"), request)
        self.fanouts.append({"request": request, "response": result})
        scopes = self.memory_config["callers"]["companion"]["event_scopes"]
        for outcome in result["outcomes"]:
            if outcome["admission"] is None:
                continue
            admission = outcome["admission"]
            self.admissions[(audience, outcome["actor_id"])] = admission
            if admission["scope"] not in scopes:
                scopes.append(admission["scope"])
        self.write_memory_config()
        return result

    async def eventually(self, predicate, timeout=15):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if predicate():
                return
            await asyncio.sleep(0.03)
        self.fail(
            "Timed out; actual Core state: " + json.dumps(self.core_state(), ensure_ascii=False)
        )

    def core_state(self):
        return {
            "turns": [
                {
                    k: t.get(k)
                    for k in (
                        "id",
                        "scope",
                        "sequence",
                        "phase",
                        "failure",
                        "model_calls",
                        "scope_version",
                        "bundle",
                        "short_context",
                    )
                }
                for t in self.core.store.list("turns")
            ],
            "outbox": [
                {
                    k: item.get(k)
                    for k in ("id", "state", "attempts", "last_error", "event", "receipt")
                }
                for item in self.core.store.list("outbox")
            ],
            "replies": self.core.store.list("replies"),
        }

    async def wait_commits(self, count):
        await self.eventually(lambda: len(self.commits()) >= count)
        return self.commits()

    def commits(self):
        events = {}
        for record in self.wire:
            if (
                record["owner"] == "memory"
                and record["path"].endswith("/turn-commits")
                and record["status"] == 200
            ):
                events.setdefault(record["request"]["event_id"], record)
        return list(events.values())

    async def facts(self, selectors=(), turn_ids=(), *, head=False):
        body = {
            "schema_version": 1,
            "request_id": "test-facts:" + secrets.token_hex(8),
            "mode": "head" if head else "snapshot",
            "selectors": list(selectors),
            "turn_ids": list(turn_ids),
            "include_content": False,
        }
        response = await self.client.post(
            self.core_url + "/internal/v1/source-facts/read",
            json=body,
            headers={"Authorization": self.bearer("MEMORY_CORE")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def current(self, admissions=()):
        body = {
            "schema_version": 1,
            "request_id": "test-access:" + secrets.token_hex(8),
            "operation": "current",
            "admissions": list(admissions),
            "viewer": None,
        }
        response = await self.client.post(
            self.platform_url + "/internal/v1/source-access/read",
            json=body,
            headers={"Authorization": self.bearer("MEMORY_PLATFORM")},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def profiles(self, admission):
        return await self.core.memory.profiles(
            admission["accepted_origin"],
            admission["scope"],
            {"kind": "person", "person_id": admission["scope"]["person_id"]},
            "咖啡",
            ["interest"],
            {"tokens": 0, "bytes": 0},
        )

    async def select(self, admission, query_text, *, budget=None, known=None):
        body = {
            "query": {
                "schema_version": 1,
                "request_id": "selection:" + secrets.token_hex(8),
                "origin": admission["accepted_origin"],
            },
            "requested_scope": admission["scope"],
            "query_text": query_text,
            "selection": ["evidence"],
            "known_scope_version": known,
            "budget": budget or {"tokens": 2048, "bytes": 8192},
        }
        return await self.client.post(
            self.memory_url + "/internal/v1/memory/select",
            json=body,
            headers={"Authorization": self.bearer("CORE_MEMORY")},
        )

    async def commit_draft(self, commit, statements):
        event, receipt = commit["request"], commit["response"]
        draft = {
            "scope": event["scope"],
            "category": "evidence",
            "field_key": "synthetic_fact",
            "item_key": None,
            "relationship_delta": None,
            "units": [
                {
                    "statement": statement,
                    "conditions": [],
                    "negations": [],
                    "valid_time": "current synthetic statement",
                    "uncertainty": "uncertain",
                    "reality": "real",
                    "sources": event["sources"],
                }
                for statement in statements
            ],
        }
        request = {"job_id": receipt["candidate_job_ref"], "drafts": [draft]}
        result = await asyncio.to_thread(self.workflow.commit_candidate, request)
        return request, result

    def check(self, name, **facts):
        self.trace["checks"].append({"name": name, **facts})

    async def asyncTearDown(self):
        if hasattr(self, "core"):
            self.trace["core"] = self.core_state()
        self.trace.update(
            wire=self.wire,
            fanouts=self.fanouts,
            models=self.model_requests,
            channel_requests=self.channel_requests,
            channel_receipts=self.channel_receipts,
        )

    async def cleanup(self):
        for gate in getattr(self, "model_gates", {}).values():
            gate.set()
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
                failures.append(f"listener-open:{port}")
        self.trace["cleanup"] = {
            "listeners_checked": len(self.ports),
            "failures": failures,
            "gateway_process_stopped": not hasattr(self, "gateway_process")
            or self.gateway_process.poll() is not None,
        }
        serialized = json.dumps(scrub(self.trace), ensure_ascii=False, indent=2) + "\n"
        for token in self.tokens.values():
            self.assertNotIn(token, serialized)
        destination = RUNTIME / "results"
        destination.mkdir(exist_ok=True)
        (destination / (self._testMethodName + ".json")).write_text(serialized, "utf-8")
        self.assertEqual(failures, [])
