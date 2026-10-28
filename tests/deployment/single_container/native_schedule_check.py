"""Opt-in native Companion/Memory/schedule closure using isolated synthetic state.

The installed six-owner BusinessHost and product stores/clients are unchanged.
Only the two external ports are fixtures: a loopback OpenAI-compatible provider
and a loopback OneBot WebSocket peer. No real QQ or model service is contacted.
The controller uses two fresh interpreters, retaining the same isolated stores,
because the native NoneBot singleton deliberately cannot be reinitialized.

Requires native_host_smoke.py and native_memory_check.py from the coordinated
validation checkout, its Platform fixtures, and the exact R7/R11 contracts and
complete synthetic Memory fixture. --postgres-url is opt-in, loopback plus a
Unix socket only; it must identify a dedicated disposable database. The tool
retains its randomly namespaced PG schemas for inspection and never drops them.
"""

import argparse
import asyncio
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4


sys.dont_write_bytecode = True
ACTOR = "actor:a"
ROOMS = ("910001", "910002")
SEED_A = "NATIVE_SEED_A 我喜欢乌龙茶，这是一条隔离的合成原材料。"
SEED_B = "NATIVE_SEED_B 我喜欢薄荷茶，这是另一会话的合成原材料。"
TERMINAL = {"sent", "failed", "cancelled", "observed", "closed_unknown"}


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--host-source", type=Path, default=Path("/opt/tianshu/application")
    )
    parser.add_argument(
        "--platform-fixtures",
        type=Path,
        default=Path("/validation/platform/tests/backend"),
    )
    parser.add_argument("--memory-helper", type=Path)
    parser.add_argument(
        "--processing-candidate",
        type=Path,
        help="Explicit local-only observation_processing candidate; recorded as source overlay, never installed-wheel acceptance",
    )
    parser.add_argument(
        "--contracts",
        type=Path,
        default=Path("/verification/contracts/text-dialogue/v1"),
    )
    parser.add_argument(
        "--memory-contracts",
        type=Path,
        default=Path("/verification/memory-fixture/contracts/text-dialogue/v1"),
    )
    parser.add_argument(
        "--diagnostics-contracts",
        type=Path,
        default=Path("/verification/contracts/diagnostics/v1"),
    )
    parser.add_argument(
        "--memory-fixture", type=Path, default=Path("/verification/memory-fixture")
    )
    parser.add_argument(
        "--postgres-url",
        help="Dedicated disposable PG database, localhost plus explicit Unix socket",
    )
    parser.add_argument(
        "--output", type=Path, required=True, help="New isolated retained directory"
    )
    parser.add_argument(
        "--timeout", type=float, default=60, help="Per-assertion deadline in seconds"
    )
    parser.add_argument(
        "--learning-timeout",
        type=float,
        default=180,
        help="Real 120-second segment quiet window plus native extraction/commit",
    )
    parser.add_argument(
        "--phase",
        choices=("controller", "initial", "resume"),
        default="controller",
        help=argparse.SUPPRESS,
    )
    options = parser.parse_args()
    options.output = options.output.resolve()
    options.host_source = options.host_source.resolve()
    options.memory_helper = (
        options.memory_helper
        or options.host_source
        / "tests/deployment/single_container/native_memory_check.py"
    ).resolve()
    options.web_access = options.http_web_access = False
    require(10 <= options.timeout <= 180, "bounded assertion timeout")
    require(
        150 <= options.learning_timeout <= 300,
        "bounded real-time memory segment deadline",
    )
    if options.postgres_url:
        parsed = urlsplit(options.postgres_url)
        socket_host = parse_qs(parsed.query).get("host", [])
        require(
            parsed.scheme in {"postgresql", "postgres"}
            and parsed.hostname == "localhost",
            "PG must use explicit localhost hostname",
        )
        require(
            len(socket_host) == 1
            and socket_host[0].startswith("/")
            and Path(socket_host[0]).is_dir(),
            "PG requires an existing explicit isolated Unix socket directory",
        )
        require(
            parsed.path not in {"", "/", "/postgres", "/template0", "/template1"},
            "PG requires a dedicated disposable database",
        )
    return options


def require(value, label):
    if not value:
        raise AssertionError(label)


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def load_helper(options):
    spec = importlib.util.spec_from_file_location(
        "phase2_native_memory_helper", options.memory_helper
    )
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    return helper, helper.load_smoke(options)


async def wait_for(check, label, timeout, *, poll=0.2):
    started = time.monotonic()
    until = time.monotonic() + timeout
    next_notice = started + 20
    last = None
    while time.monotonic() < until:
        last = await check()
        if last:
            return last
        if time.monotonic() >= next_notice:
            print(
                json.dumps(
                    {
                        "waiting": label,
                        "elapsed_seconds": round(time.monotonic() - started),
                    }
                ),
                flush=True,
            )
            next_notice += 20
        await asyncio.sleep(poll)
    raise AssertionError("deadline: " + label)


class Provider:
    """Recorded external provider. All authentication, dispatch and receipts stay real."""

    def __init__(self, token):
        self.token = token
        self.calls = []
        self.hold_marker = None
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self.runner = None

    async def open(self, port=0):
        from aiohttp import web

        app = web.Application()
        app.router.add_post("/v1/chat/completions", self.serve)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", port)
        await site.start()
        self.port = self.runner.addresses[0][1]
        self.origin = "http://127.0.0.1:" + str(self.port) + "/v1"
        return self.origin

    async def serve(self, request):
        from aiohttp import web

        require(
            request.headers.get("Authorization") == "Bearer " + self.token,
            "provider uses its own credential",
        )
        body = await request.json()
        system = "\n".join(
            str(item.get("content"))
            for item in body["messages"]
            if item["role"] == "system"
        )
        contents = json.dumps(body["messages"], ensure_ascii=False)
        if "Organize this bounded conversation segment" in system:
            material = json.loads(body["messages"][-1]["content"])
            window = material["segment_window"]
            members = {row["member_id"]: row for row in window["members"]}
            label = "memory-segment"
            text = json.dumps(
                {
                    "extraction_schema": 6,
                    "topics": [],
                    "results": [
                        {
                            "subject_person_id": target["scope"]["person_id"],
                            "covered_member_ids": target["new_member_ids"],
                            "memories": [
                                {
                                    "text": "\n".join(
                                        members[key]["text"]
                                        for key in target["new_member_ids"]
                                    ),
                                    "kind": "experience",
                                    "state": "described",
                                    "valid_until": None,
                                    "source_member_ids": target["new_member_ids"],
                                    "prior_refs": [],
                                    "operation": "append",
                                    "importance": "normal",
                                    "retention": "medium",
                                }
                            ],
                            "portrait": None,
                        }
                        for target in window["targets"]
                    ],
                },
                ensure_ascii=False,
            )
        elif "Organize a useful, concise experience memory" in system:
            material = json.loads(body["messages"][-1]["content"])
            label = "memory-summary"
            text = json.dumps(
                {
                    "subject_person_id": material["subject"]["person_id"],
                    "summary": "\n".join(
                        row["text"] for row in material["subject_messages"]
                    )[:6000],
                    "portrait": None,
                    "importance": "normal",
                },
                ensure_ascii=False,
            )
        elif "Extract role-life intentions from this accepted dialogue" in system:
            label, text = "life-influence", '{"intentions":[]}'
        elif "Create today's fictional intentions" in system:
            material = json.loads(body["messages"][-1]["content"])
            label = "life-plan"
            text = json.dumps(
                {
                    "entries": [
                        {
                            "minute": row["minute"],
                            "activity": row["activity"],
                            "detail": "隔离合成的虚构日常意图。",
                        }
                        for row in material["schedule"]
                    ]
                },
                ensure_ascii=False,
            )
        elif "Write a concrete short first-person fictional experience" in system:
            label, text = "life-stage", "我在合成的虚构房间里读了一页书，心情平静。"
        elif "memories" in system and ("JSON" in system or "output_schema" in contents):
            label, text = "memory-extraction", '{"memories":[]}'
        elif "Decide whether this sourced actor motive" in system:
            label, text = (
                "motive",
                '{"send":true,"text":"NATIVE_MOTIVE_REPLY 这是根据已登记动机的合成主动联系。"}',
            )
        else:
            labels = [
                "SECOND_A",
                "OTHER_B",
                "FIRST_A",
                "NATIVE_SEED_B",
                "NATIVE_SEED_A",
            ]
            label = next((name for name in labels if name in contents), "dialogue")
            text = "NATIVE_REPLY_" + label + "。"
        row = {
            "label": label,
            "started": time.time(),
            "messages": body["messages"],
            "stream": body.get("stream"),
            "model": body.get("model"),
        }
        self.calls.append(row)
        if label == self.hold_marker and not self.held.is_set():
            self.held.set()
            await self.release.wait()
        if not body.get("stream"):
            row["finished"] = time.time()
            return web.json_response(
                {
                    "id": "synthetic:" + uuid4().hex,
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": text},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                }
            )
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        try:
            for frame in (
                {
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": text},
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                    "usage": {
                        "prompt_tokens": 1,
                        "completion_tokens": 1,
                        "total_tokens": 2,
                    },
                },
            ):
                await response.write(
                    ("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode()
                )
            await response.write(b"data: [DONE]\n\n")
            row["finished"] = time.time()
        except (ConnectionResetError, RuntimeError):
            row["connection_closed"] = True
        return response

    async def close(self):
        self.release.set()
        if self.runner:
            await self.runner.cleanup()


class OneBotPeer:
    """Actual reverse WS protocol peer; missing message_id creates a real unknown."""

    def __init__(self):
        self.calls = []
        self.peer = self.task = None
        self.native_sequence = 880000

    async def open(self, port, token):
        from websockets.asyncio.client import connect

        self.peer = await connect(
            "ws://127.0.0.1:" + str(port) + "/onebot/v11/ws",
            additional_headers={
                "Authorization": "Bearer " + token,
                "X-Self-ID": "10001",
            },
        )
        self.task = asyncio.create_task(self.respond())

    async def respond(self):
        async for raw in self.peer:
            request = json.loads(raw)
            action = request["action"]
            if action == "get_login_info":
                data = {"user_id": 10001, "nickname": "Synthetic phase2 peer"}
            else:
                require(
                    action == "send_private_msg",
                    "only synthetic private send is expected",
                )
                params = request["params"]
                require(
                    str(params["user_id"]) in ROOMS,
                    "outbound destination stays in the synthetic fixture",
                )
                text = params["message"]
                if isinstance(text, list):
                    text = "".join(
                        item.get("data", {}).get("text", "") for item in text
                    )
                row = {
                    "action": action,
                    "room": str(params["user_id"]),
                    "text": text,
                    "observed_at": time.time(),
                }
                self.calls.append(row)
                if "NATIVE_UNKNOWN" in text:
                    # The actual SDK received a response without a message ID.
                    # Its existing durable AdapterService must call this unknown.
                    row["missing_message_id"] = True
                    data = {}
                else:
                    self.native_sequence += 1
                    data = {"message_id": self.native_sequence}
                    row["message_id"] = str(self.native_sequence)
            await self.peer.send(
                json.dumps(
                    {
                        "status": "ok",
                        "retcode": 0,
                        "data": data,
                        "echo": request["echo"],
                    }
                )
            )

    async def inbound(self, room, marker, sequence):
        require(room in ROOMS, "synthetic inbound room")
        await self.peer.send(
            json.dumps(
                {
                    "time": int(time.time()),
                    "self_id": 10001,
                    "post_type": "message",
                    "message_type": "private",
                    "sub_type": "friend",
                    "message_id": sequence,
                    "user_id": int(room),
                    "message": [{"type": "text", "data": {"text": marker}}],
                    "raw_message": marker,
                    "font": 0,
                    "sender": {"user_id": int(room), "nickname": "Synthetic " + room},
                }
            )
        )

    async def close(self):
        if self.peer:
            await self.peer.close()
        if self.task:
            await self.task


def configure(options, helper, smoke, host_path, tokens, upstream):
    """Extend the original complete fixture, without changing any contract bytes."""
    from fixtures import ENV

    bindings, _ = helper.configure(options, smoke, host_path, tokens)
    host = read(host_path)
    modules = host["modules"]
    platform_path = Path(modules["platform"]["settings"])
    platform = read(platform_path)
    for action in ("bot.manage", "qq.admin.view", "qq.admin.manage"):
        if action not in platform["principals"]["admin"]["actions"]:
            platform["principals"]["admin"]["actions"].append(action)
    for action in ("dialogue.send", "qq.admin.check", "observation.verify"):
        if action not in platform["principals"]["companion"]["actions"]:
            platform["principals"]["companion"]["actions"].append(action)
    platform["bot_connections"] = {"principal": "connector", "slots": {}}
    platform["entries"]["config-entry"]["ttl_seconds"] = 300
    platform["bot_adapter_self_service"] = {
        "directory": str(options.output / "private-adapters"),
        "allowed_cidrs": ["127.0.0.0/8"],
        "actors": [{"id": ACTOR, "label": "Synthetic native role A"}],
    }
    smoke.write(platform_path, platform)
    companion_path = Path(modules["companion"]["settings"])
    companion = read(companion_path)
    companion["services"].update(
        {
            "platform_sender": {
                "url": bindings["origins"]["platform"],
                "token_env": "TS012_COMPANION",
            },
            "qq_admin": {
                "url": bindings["origins"]["platform"],
                "token_env": "TS012_COMPANION",
            },
            "gateway": {
                "url": bindings["origins"]["gateway"],
                "token_env": "SYNTHETIC_CLIENT",
            },
        }
    )
    companion["policy"] = {"silence_ms": 100, "delivery_reconcile_timeout_ms": 3000}
    companion["bot_binding_management_enabled"] = True
    # Existing static config version is the genuine published Gateway binding.
    # No independently authenticated background selector is invented here.
    companion["life_config_version"] = 7
    modules["companion"]["env"].update(
        SYNTHETIC_CLIENT=tokens["client"], **{"TS012_COMPANION": ENV["TS012_COMPANION"]}
    )
    smoke.write(companion_path, companion)
    # The original complete fixture was generated in /verification. Normalize
    # only its copied configuration paths when running on another mount/OS.
    copied = options.output / "complete-memory"
    for path in copied.rglob("*.json"):
        if path.is_relative_to(copied / "contracts"):
            continue

        def rebase(value):
            if isinstance(value, str):
                return value.replace("/verification/memory-fixture", str(copied))
            if isinstance(value, list):
                return [rebase(item) for item in value]
            if isinstance(value, dict):
                return {key: rebase(item) for key, item in value.items()}
            return value

        smoke.write(path, rebase(read(path)))
    for name in ("memory", "knowledge"):
        # smoke.prepare may have copied module paths that still use the original
        # /verification prefix; only the isolated copy is an allowed state root.
        for key in ("settings", "contracts", "log_directory"):
            if key in modules[name]:
                modules[name][key] = str(modules[name][key]).replace(
                    "/verification/memory-fixture", str(copied)
                )
    namespace = "phase2_" + uuid4().hex
    if options.postgres_url:
        for name, module in modules.items():
            document = read(module["settings"])
            document["storage_backends"] = {
                "postgresql_url": options.postgres_url,
                "namespace": namespace + "_" + name,
            }
            smoke.write(Path(module["settings"]), document)
            module["env"]["TIANSHU_STORAGE_NAMESPACE"] = namespace + "_" + name
    smoke.write(Path(host_path), host)
    return {
        "bindings": bindings,
        "tokens": tokens,
        "host_path": str(host_path),
        "provider_origin": upstream,
        "namespace": namespace,
    }


async def core_snapshot(host):
    def take(app):
        core = app.state.core
        return {
            "backend": core.store.backend,
            "turns": core.store.list("turns"),
            "outbox": core.store.list("outbox"),
            "subscriptions": core.store.list("proactive_subscriptions"),
            "candidates": core.store.list("proactive_candidates"),
            "attempts": core.store.list("proactive_attempts"),
            "reminders": core.store.list("proactive_reminders"),
            "contacts": core.store.list("delivery_contacts"),
            "learning": core.store.list("metadata", "memory:learning"),
        }

    return await host.exchange.invoke("companion", take)


def seed_platform(host_path, upstream):
    """Use the real offline fixture owner before Gateway probes its immutable v7."""
    from fixtures import bearer, config
    from services.platform.service import Platform

    host = read(host_path)
    module = host["modules"]["platform"]
    prior = dict(os.environ)
    platform = None
    try:
        os.environ.clear()
        os.environ.update(module["env"])
        platform = Platform(read(module["settings"]))
        require(
            platform.models.publish(bearer("ADMIN"), config(upstream=upstream))[
                "published"
            ],
            "real Platform model fixture published before native probes",
        )
        issued = platform.origins.issue(bearer("ADMIN"), "config-entry")
        host["modules"]["gateway"]["env"]["SYNTHETIC_ORIGIN"] = issued["assertion_ref"]
        write(host_path, host)
        return issued
    finally:
        if platform:
            platform.close()
        os.environ.clear()
        os.environ.update(prior)


async def queue_snapshot(host):
    from deploy.single_container.applications import app_platform

    def take(app):
        platform = app_platform(app)
        db = platform.bots._db()
        try:
            return {
                "expressions": [
                    dict(row)
                    for row in db.execute(
                        "SELECT id,scope,channel,final,cancelled FROM expressions ORDER BY id"
                    )
                ],
                "replies": [
                    dict(row)
                    for row in db.execute(
                        "SELECT reply_id,conversation_id,actor_id,turn_id,state,channel_message_ids,request FROM replies ORDER BY reply_id"
                    )
                ],
            }
        finally:
            db.close()

    return await host.exchange.invoke("platform", take)


def matching_turn(snapshot, marker):
    return next(
        (
            turn
            for turn in snapshot["turns"]
            if any(
                marker in str(part.get("text", ""))
                for message in turn["bundle"]["messages"]
                for part in message["parts"]
            )
        ),
        None,
    )


async def await_turn(host, marker, timeout, terminal=True):
    async def check():
        turn = matching_turn(await core_snapshot(host), marker)
        return turn if turn and (not terminal or turn["phase"] in TERMINAL) else None

    turn = await wait_for(check, "turn " + marker, timeout)
    if terminal:
        require(
            turn["phase"] == "sent",
            "actual turn is sent: "
            + marker
            + " phase="
            + turn["phase"]
            + " failure="
            + str(turn.get("failure")),
        )
        require(turn["model_calls"] == 1, "one actual model dispatch for " + marker)
    return turn


async def candidate(host, subject_id, state, timeout):
    async def check():
        snapshot = await core_snapshot(host)
        rows = [
            item
            for item in snapshot["candidates"]
            if item["subject_id"] == subject_id and item["state"] == state
        ]
        return rows[0] if len(rows) == 1 else None

    return await wait_for(check, "candidate " + subject_id + " " + state, timeout)


async def register_subscription(host, turn):
    def register(app):
        scope = turn["scope"]
        return app.state.core.proactive.register_subscription(
            **scope,
            channel=turn["bundle"]["messages"][0]["message_key"]["channel"],
            consent={
                "registered_by": "admin:synthetic-phase2",
                "basis": "explicit_admin_registration",
                "evidence_ref": "synthetic:phase2-opt-in",
            },
            timezone_name="+00:00",
            quiet=("00:00", "00:00"),
            cooldown_seconds=0,
            daily_limit=24,
            unanswered_limit=24,
            expiry_seconds=300,
        )

    return await host.exchange.invoke("companion", register)


async def register_reminder(host, subscription, name, delay, marker):
    def register(app):
        core = app.state.core
        core.proactive.put_template(
            "phase2-reminder", 1, body="NATIVE_REMINDER {summary} {due_time}。"
        )
        return core.proactive.register_reminder(
            name,
            actor_id=ACTOR,
            subscription_id=subscription["id"],
            summary=marker,
            due_at=core.clock() + delay,
            template_id="phase2-reminder",
            template_version=1,
            registered_by="admin:synthetic-phase2",
            evidence_ref="synthetic:phase2-registration",
        )

    return await host.exchange.invoke("companion", register)


async def create_connections(host, peer, tokens, state):
    from deploy.single_container.applications import app_platform

    port = int(urlsplit(host.config["modules"]["onebot"]["origin"]).port)
    await peer.open(port, tokens["sdk"])

    def access(app):
        from tianshu_nonebot.storage import read_access_key

        module = host.config["modules"]["onebot"]
        return read_access_key(
            Path(module["data_directory"]) / "adapter.sqlite3",
            read(module["settings"]),
            environ=host.modules["onebot"].environment,
        )

    key = await host.exchange.invoke("onebot", access)
    instance = await host.exchange.invoke(
        "onebot", lambda app: app.state.lifecycle.instance_id
    )

    async def connected(app):
        return bool(
            (
                await app_platform(app).bot_adapters._plugin(
                    {
                        "id": "phase2-owned-probe",
                        "instance_id": instance,
                        "address": "http://127.0.0.1:" + str(port),
                        "access_key": key,
                        "pins": None,
                        "ca_pem": None,
                    },
                    "/capabilities",
                    {},
                )
            )["accounts"]
        )

    await wait_for(
        lambda: host.exchange.invoke("platform", connected),
        "synthetic OneBot account online",
        10,
    )
    if "connections" not in state:
        rows = []
        for room in ROOMS:

            async def create(app, room=room):
                manager = app_platform(app).bot_adapters
                session = {}
                draft = await manager.probe(
                    {
                        "adapter": "onebot",
                        "address": "http://127.0.0.1:" + str(port),
                        "access_key": key,
                        "allow_private_http": True,
                        "ca_pem": None,
                    },
                    session,
                )
                created = await manager.create(
                    {
                        "draft_id": draft["draft_id"],
                        "name": "Synthetic " + room,
                        "account_id": "10001",
                        "conversation": {"kind": "private", "id": room},
                        "allowed_authors": [room],
                        "actor_id": ACTOR,
                        "client_id": str(uuid4()),
                    },
                    session,
                )
                require(
                    created["state"] == "disabled",
                    "native binding create agrees in SDK and Companion",
                )
                enabled = await manager.change(
                    "enable",
                    {
                        "id": created["id"],
                        "expected_revision": created["revision"],
                        "client_id": str(uuid4()),
                    },
                )
                require(
                    enabled["state"] == "ready" and enabled["enabled"],
                    "native binding enable agrees in SDK and Companion",
                )
                return {
                    "id": enabled["id"],
                    "room": room,
                    "revision": enabled["revision"],
                }

            rows.append(await host.exchange.invoke("platform", create))
        state["connections"] = rows
    else:

        async def ready(app):
            manager = app_platform(app).bot_adapters
            return all(
                (row := manager.catalog.get(saved["id"]))["state"] == "ready"
                and row["enabled"]
                for saved in state["connections"]
            )

        await wait_for(
            lambda: host.exchange.invoke("platform", ready),
            "same persisted native bindings reconcile after restart",
            30,
        )


async def initial_checks(options, host, helper, provider, peer, state, checks):
    await helper.role_grant(host, state["bindings"], 0, True, "phase2:static-role")
    checks["published_model_before_boot"] = state["model_origin"]["expires_at"]
    await create_connections(host, peer, state["tokens"], state)
    frames = [
        {
            "channel": {
                "namespace": "qq",
                "binding_id": "binding:" + row["id"],
                "channel_conversation_id": "private:" + row["room"],
                "thread_id": None,
            },
            "actor_id": ACTOR,
        }
        for row in state["connections"]
    ]
    processing = await helper.json_call(
        host,
        "companion",
        "platform",
        "/internal/v2/observation-source/processing",
        host.config["modules"]["companion"]["env"]["TS012_COMPANION"],
        {
            "schema_version": 1,
            "request_id": "processing:phase2-native",
            "frames": frames,
        },
    )
    checks["two_managed_conversation_processing"] = processing
    require(
        processing["status"] == 200
        and len(processing["body"]["frames"]) == 2
        and all(row["processing_enabled"] for row in processing["body"]["frames"]),
        "each exact native managed conversation remains eligible for automatic Memory processing",
    )
    await peer.inbound(ROOMS[0], SEED_A, 501)
    seed_a = await await_turn(host, SEED_A, options.timeout)
    await peer.inbound(ROOMS[1], SEED_B, 502)
    seed_b = await await_turn(host, SEED_B, options.timeout)

    async def materialized():
        snapshot = await core_snapshot(host)
        return (
            snapshot
            if len(
                [
                    row
                    for row in snapshot["outbox"]
                    if row["state"] == "delivered" and row.get("receipt")
                ]
            )
            >= 2
            else None
        )

    await wait_for(
        materialized, "actual Memory material receipts for both rooms", options.timeout
    )

    async def learned():
        snapshot = await core_snapshot(host)
        rows = [
            row
            for row in snapshot["learning"]
            if row.get("scope", {}).get("conversation_id")
            in {seed_a["conversation_id"], seed_b["conversation_id"]}
            and row["state"] == "completed"
        ]
        return rows if len(rows) == 2 else None

    seed_learning = await wait_for(
        learned,
        "actual asynchronous sourced Memory summary receipts (original segment quiet window)",
        options.learning_timeout,
        poll=1,
    )
    require(
        all(
            row.get("receipt")
            and row.get("summary_state", row["receipt"].get("summary_state"))
            == "committed"
            for row in seed_learning
        ),
        "both grounded summaries have actual committed Memory receipts",
    )
    checks["automatic_memory_summaries"] = [
        {
            "id": row["id"],
            "turn_id": row.get("turn_id"),
            "segment_id": row.get("segment_id"),
            "extraction_schema": row["extraction_schema"],
            "state": row["state"],
            "receipt": row.get("receipt"),
        }
        for row in seed_learning
    ]
    subscriptions = [
        await register_subscription(host, turn) for turn in (seed_a, seed_b)
    ]
    checks["seeds"] = {
        "turn_ids": [seed_a["id"], seed_b["id"]],
        "scopes": [seed_a["scope"], seed_b["scope"]],
        "real_material_receipts": True,
    }
    require(
        seed_a["scope"]["conversation_id"] != seed_b["scope"]["conversation_id"]
        and seed_a["scope"]["person_id"] != seed_b["scope"]["person_id"],
        "two actual identities and rooms",
    )

    provider.hold_marker = "FIRST_A"
    await peer.inbound(ROOMS[0], "FIRST_A 乌龙茶前序阻塞验证。", 503)
    await wait_for(
        lambda: asyncio.sleep(0, result=provider.held.is_set()),
        "real first-room provider dispatch held",
        options.timeout,
    )
    first = await await_turn(host, "FIRST_A", options.timeout, terminal=False)
    # This exact natural-language dependency uses Core's original supported
    # predecessor rule. Independent ordinary turns may generate concurrently.
    await peer.inbound(ROOMS[0], "SECOND_A 按你刚才的方案继续。", 504)
    second = await await_turn(host, "SECOND_A", options.timeout, terminal=False)
    await peer.inbound(ROOMS[1], "OTHER_B 薄荷茶独立会话。", 505)
    other = await await_turn(host, "OTHER_B", options.timeout)
    require(
        not provider.release.is_set(),
        "other room finishes while first provider remains blocked",
    )
    require(
        not any(row["label"] == "SECOND_A" for row in provider.calls),
        "same-room successor cannot dispatch before predecessor",
    )
    await register_reminder(host, subscriptions[0], "phase2:due-a", 1, "NATIVE_DUE_A")
    await register_reminder(host, subscriptions[1], "phase2:due-b", 1, "NATIVE_DUE_B")
    due_b = await candidate(host, "phase2:due-b", "sent", options.timeout)
    due_a = await candidate(host, "phase2:due-a", "deferred", options.timeout)
    require(
        due_a["decision"] == "conversation_active" and not provider.release.is_set(),
        "only busy destination reminder waits",
    )
    checks["independent_rooms"] = {
        "held_turn": first["id"],
        "queued_successor": second["id"],
        "other_sent_turn": other["id"],
        "other_reminder_sent": due_b["id"],
        "busy_reminder_defer": due_a["decision"],
    }
    provider.release.set()
    first = await await_turn(host, "FIRST_A", options.timeout)
    second = await await_turn(host, "SECOND_A", options.timeout)
    due_a = await candidate(host, "phase2:due-a", "sent", options.timeout)
    require(
        due_a["delivered"]
        and due_b["delivered"]
        and due_a["delivery_evidence"]
        == due_b["delivery_evidence"]
        == "published_contract_receipt",
        "due reminders have actual contract delivery evidence",
    )
    require(
        SEED_A in json.dumps(first["memory_context"]["evidence"], ensure_ascii=False),
        "actual Memory context includes first-room source episode",
    )
    require(
        SEED_B not in json.dumps(first["memory_context"], ensure_ascii=False),
        "Memory context does not cross room/person scope",
    )
    second_calls = [row for row in provider.calls if row["label"] == "SECOND_A"]
    require(
        len(second_calls) == 1
        and "NATIVE_REPLY_FIRST_A" in json.dumps(second_calls[0]["messages"]),
        "successor actual model input contains committed predecessor reply",
    )
    checks["memory_model_order"] = {
        "first": first["id"],
        "second": second["id"],
        "first_model_calls": first["model_calls"],
        "second_model_calls": second["model_calls"],
        "same_scope_episode_selected": True,
        "committed_reply_in_successor": True,
    }

    def motive(app):
        core = app.state.core
        source = core.store.get("proactive_reminders", "phase2:due-a")
        return core.proactive.register_motive(
            ACTOR,
            {
                "id": "phase2:motive",
                "subscription_id": subscriptions[0]["id"],
                "summary": "NATIVE_MOTIVE 乌龙茶的合成后续联系",
                "sources": [
                    {
                        "owner": "companion",
                        "object_id": source["id"],
                        "version": source["version"],
                    }
                ],
                "content_refs": [],
                "due_at": core.clock() + 1,
                "expires_at": core.clock() + 120,
                "weight": 1.0,
            },
        )

    await host.exchange.invoke("companion", motive)
    active = await candidate(host, "phase2:motive", "sent", options.timeout)
    motive_calls = [row for row in provider.calls if row["label"] == "motive"]
    require(
        active["delivered"]
        and active["delivery_evidence"] == "published_contract_receipt"
        and active["expression_receipt"]["outcome"] == "succeeded",
        "proactive expression has real Gateway receipt and send receipt",
    )
    require(
        len(motive_calls) == 1
        and "current_person_context" in json.dumps(motive_calls[0]["messages"])
        and SEED_A in json.dumps(motive_calls[0]["messages"], ensure_ascii=False),
        "proactive actual provider input contains Memory-selected person context",
    )
    checks["sourced_motive"] = {
        "candidate_id": active["id"],
        "gateway_receipt": active["expression_receipt"],
        "verified_delivery": True,
    }

    await register_reminder(
        host, subscriptions[1], "phase2:unknown", 1, "NATIVE_UNKNOWN"
    )
    unknown = await candidate(host, "phase2:unknown", "unknown", options.timeout)
    require(
        not unknown["delivered"] and unknown["attempt_count"] == 1,
        "SDK missing ID is durably unknown, never claimed delivered",
    )
    await register_reminder(
        host, subscriptions[0], "phase2:cancelled", 4, "NATIVE_CANCELLED"
    )
    await host.exchange.invoke(
        "companion",
        lambda app: app.state.core.proactive.cancel_subject(
            "reminder", "phase2:cancelled", reason="synthetic explicit cancellation"
        ),
    )
    await register_reminder(
        host, subscriptions[1], "phase2:persisted", 12, "NATIVE_PERSISTED"
    )
    state["subscriptions"] = subscriptions
    state["unknown_candidate"] = unknown["id"]
    state["before_restart_queue"] = await queue_snapshot(host)
    checks["before_restart"] = {
        "unknown_candidate": unknown["id"],
        "unknown_attempts": unknown["attempt_count"],
        "unknown_sdk_sends": len(
            [row for row in peer.calls if "NATIVE_UNKNOWN" in row["text"]]
        ),
        "cancelled_schedule": True,
        "future_schedule_persisted": True,
    }
    require(
        checks["before_restart"]["unknown_sdk_sends"] == 1,
        "exactly one actual SDK send before uncertain receipt",
    )


async def resumed_checks(options, host, peer, state, checks):
    await create_connections(host, peer, state["tokens"], state)
    persisted = await candidate(host, "phase2:persisted", "sent", options.timeout)
    require(
        persisted["delivered"]
        and persisted["delivery_evidence"] == "published_contract_receipt"
        and persisted["attempt_count"] == 1,
        "persisted due schedule sends exactly once after process restart",
    )
    # Poll actual receipt recovery multiple times, including the old unknown.
    for _ in range(3):
        await host.exchange.invoke(
            "companion", lambda app: app.state.core.proactive.work()
        )
        await asyncio.sleep(0.2)
    snapshot = await core_snapshot(host)
    old = next(
        row for row in snapshot["candidates"] if row["id"] == state["unknown_candidate"]
    )
    require(
        old["state"] == "unknown"
        and old["attempt_count"] == 1
        and not old["delivered"],
        "restart and reconciliation preserve unknown without a retry",
    )
    cancelled = next(
        row for row in snapshot["reminders"] if row["id"] == "phase2:cancelled"
    )
    require(cancelled["state"] == "cancelled", "explicit cancellation remains durable")
    require(
        not any(
            "NATIVE_UNKNOWN" in row["text"] or "NATIVE_CANCELLED" in row["text"]
            for row in peer.calls
        ),
        "neither unknown nor cancelled schedule is sent on restart",
    )
    require(
        len([row for row in peer.calls if "NATIVE_PERSISTED" in row["text"]]) == 1,
        "one actual persisted-schedule SDK send",
    )
    queue = await queue_snapshot(host)
    before = {row["reply_id"]: row for row in state["before_restart_queue"]["replies"]}
    after = {row["reply_id"]: row for row in queue["replies"]}
    require(
        before.keys() <= after.keys(), "pre-restart durable output rows are retained"
    )
    require(
        all(
            after[key]["state"] == value["state"]
            and after[key]["channel_message_ids"] == value["channel_message_ids"]
            for key, value in before.items()
        ),
        "existing terminal receipts are not reissued/replaced",
    )
    checks["restart"] = {
        "same_bindings_reconciled": True,
        "old_reply_rows_preserved": len(before),
        "new_reply_rows": len(after) - len(before),
        "unknown_attempts": old["attempt_count"],
        "unknown_no_resend": True,
        "cancelled_no_send": True,
        "persisted_once": persisted["id"],
    }


def installed_modules():
    return {
        name: str(Path(importlib.import_module(name).__file__).resolve())
        for name in (
            "services.platform.service",
            "tianshu_companion.app",
            "tianshu_memory.app",
            "tianshu_gateway.server",
            "tianshu_nonebot.onebot",
        )
    }


def storage_bindings(root):
    rows = []
    for path in root.rglob(".tianshu-storage-*.json"):
        document = read(path)
        rows.append(
            {
                "path": str(path.relative_to(root)),
                "service": document["service"],
                "backend": document["backend"],
                "namespace": document["namespace"],
                "state": document["state"],
                "schemas": document["databases"],
            }
        )
    return rows


async def worker(options):
    helper, smoke = load_helper(options)
    smoke.installed_wheels()
    if options.processing_candidate:
        candidate_path = options.processing_candidate.resolve()
        spec = importlib.util.spec_from_file_location(
            "services.platform.observation_processing", candidate_path
        )
        candidate_module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = candidate_module
        spec.loader.exec_module(candidate_module)
    state_file = options.output / "schedule-state.json"
    state = read(state_file) if options.phase == "resume" else None
    token = state["tokens"]["upstream"] if state else secrets.token_urlsafe(32)
    provider, peer = Provider(token), OneBotPeer()
    if options.phase == "resume":
        peer.native_sequence = 890000
    host = None
    recorder = helper.Recorder()
    summary = {
        "ok": False,
        "phase": options.phase,
        "installed_modules": installed_modules(),
        "processing_candidate": {
            "path": str(options.processing_candidate.resolve()),
            "sha256": hashlib.sha256(
                options.processing_candidate.read_bytes()
            ).hexdigest(),
        }
        if options.processing_candidate
        else None,
        "checks": {},
        "limitations": [
            "Only synthetic external provider and OneBot protocol peers are substituted; no real QQ/paid model or production state is accessed.",
            "Internal six-owner communication is the real bounded HTTP/JSON/SSE byte exchange, not internal TCP/TLS.",
            "Reminders render registered templates; only dialogue and the explicit sourced motive claim model generation.",
            "Memory raw source material, asynchronous grounded schema5/6 experience memories and scoped retrieval are required. The original 120-second segment quiet window is not shortened. Recorded memories do not claim independent atomic facts, relationship changes or autonomous diary generation.",
            "Reminder/subscription/motive/cancellation registration uses the original domain management ports. Browser management authentication and HTTP/CAS workflows are not claimed.",
            "Restart is a clean BusinessHost shutdown followed by a fresh interpreter; cancellation is explicit withdrawal before the due time. Abrupt process-kill recovery is not claimed.",
            "Web login, production PostgreSQL and deployed observability/Loki acceptance are outside this tool.",
        ],
    }
    original = None
    try:
        upstream = await provider.open(
            int(urlsplit(state["provider_origin"]).port) if state else 0
        )
        if state is None:
            host_path, tokens, _, original = smoke.prepare(options, upstream)
            tokens["upstream"] = token
            # Update only the fixture's external provider credential before boot.
            config = read(host_path)
            config["modules"]["gateway"]["env"]["SYNTHETIC_UPSTREAM"] = token
            write(host_path, config)
            state = configure(options, helper, smoke, host_path, tokens, upstream)
            state["model_origin"] = seed_platform(host_path, upstream)
            state["source_databases"] = original
        else:
            original = state["source_databases"]
            require(
                upstream == state["provider_origin"],
                "same synthetic provider origin on restart",
            )
        from deploy.single_container.host import BusinessHost, load_settings

        host = BusinessHost(load_settings(state["host_path"]))
        recorder.attach(host)
        recorder.stage(options.phase + "-startup")
        await host.open()
        owners = {
            name: await host.exchange.invoke(name, lambda app: threading.get_ident())
            for name in host.modules
        }
        require(
            len(set(owners.values())) == 6
            and threading.get_ident() not in owners.values(),
            "six distinct native owner threads",
        )
        summary["owners"] = owners
        recorder.stage(options.phase + "-checks")
        if options.phase == "initial":
            await initial_checks(
                options, host, helper, provider, peer, state, summary["checks"]
            )
        else:
            await resumed_checks(options, host, peer, state, summary["checks"])
        snapshot = await core_snapshot(host)
        summary["checks"]["companion_backend"] = snapshot["backend"]
        require(
            snapshot["backend"] == ("postgresql" if options.postgres_url else "sqlite"),
            "Companion actual backend matches the requested backend",
        )
        summary["core_snapshot"] = snapshot
        summary["queue_snapshot"] = await queue_snapshot(host)
        rows = recorder.snapshot()
        summary["checks"]["dispatch_owner_threads"] = all(
            row["thread"] == owners[row["owner"]] for row in rows
        )
        require(
            summary["checks"]["dispatch_owner_threads"],
            "all observed dispatches run on their genuine owner threads",
        )
        summary["ok"] = True
    except BaseException as error:
        summary["failure"] = {
            "type": type(error).__name__,
            "code": getattr(error, "code", None),
            "stage": recorder.phase,
        }
        if isinstance(error, AssertionError):
            summary["failure"]["assertion"] = str(error)
        if host and host.started:
            try:
                summary["failure_snapshot"] = await core_snapshot(host)
                summary["failure_queue"] = await queue_snapshot(host)
            except BaseException as snapshot_error:
                summary["failure"]["snapshot_error_type"] = type(
                    snapshot_error
                ).__name__
        raise
    finally:
        recorder.stage(options.phase + "-shutdown")
        provider.release.set()
        try:
            await peer.close()
            if host is not None:
                summary["closed"] = await host.close(timeout=30)
                summary["owner_threads_remaining"] = [
                    thread.name
                    for thread in threading.enumerate()
                    if thread.name.startswith("tianshu-")
                ]
                require(
                    summary["closed"] and not summary["owner_threads_remaining"],
                    "all native owners drain and terminate",
                )
        except BaseException as error:
            summary["ok"] = False
            summary["shutdown_error_type"] = type(error).__name__
            raise
        finally:
            await provider.close()
            if options.output.is_dir():
                summary["storage_bindings"] = storage_bindings(options.output)
                if options.postgres_url:
                    bindings = summary["storage_bindings"]
                    summary["checks"]["all_pg_bindings_active"] = (
                        bool(bindings)
                        and {row["service"] for row in bindings}
                        >= {"platform", "companion", "memory", "gateway"}
                        and all(
                            row["backend"] == "postgresql"
                            and row["state"] == "active"
                            and row["namespace"].startswith(state["namespace"])
                            for row in bindings
                        )
                    )
                    summary["ok"] = (
                        summary["ok"] and summary["checks"]["all_pg_bindings_active"]
                    )
                if original is not None:
                    unchanged = all(
                        hashlib.sha256(
                            (options.memory_fixture / relative).read_bytes()
                        ).hexdigest()
                        == digest
                        for relative, digest in original.items()
                    )
                    summary["checks"]["source_fixture_databases_unchanged"] = unchanged
                    summary["ok"] = summary["ok"] and unchanged
                write(
                    options.output / ("schedule-" + options.phase + "-requests.json"),
                    recorder.snapshot(),
                )
                write(
                    options.output / ("schedule-" + options.phase + "-provider.json"),
                    provider.calls,
                )
                write(
                    options.output / ("schedule-" + options.phase + "-onebot.json"),
                    peer.calls,
                )
                write(
                    options.output / ("schedule-" + options.phase + "-validation.json"),
                    summary,
                )
                if state and options.phase == "initial" and summary["ok"]:
                    write(state_file, state)
            print(
                json.dumps(
                    {
                        "ok": summary["ok"],
                        "phase": options.phase,
                        "checks": summary["checks"],
                        "failure": summary.get("failure"),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                flush=True,
            )
    require(summary["ok"], "native schedule validation did not close")


def controller(options):
    require(not options.output.exists(), "output must be a new isolated directory")
    for phase in ("initial", "resume"):
        command = [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            *sys.argv[1:],
            "--phase",
            phase,
        ]
        subprocess.run(
            command,
            check=True,
            timeout=480,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
    initial = read(options.output / "schedule-initial-validation.json")
    resumed = read(options.output / "schedule-resume-validation.json")
    provider = read(options.output / "schedule-initial-provider.json") + read(
        options.output / "schedule-resume-provider.json"
    )
    sends = read(options.output / "schedule-initial-onebot.json") + read(
        options.output / "schedule-resume-onebot.json"
    )
    require(initial["ok"] and resumed["ok"], "both fresh native processes pass")
    require(
        len([row for row in sends if "NATIVE_UNKNOWN" in row["text"]]) == 1,
        "unknown exactly once across native process restart",
    )
    require(
        not any("NATIVE_CANCELLED" in row["text"] for row in sends),
        "cancelled reminder never leaves native SDK",
    )
    summary = {
        "ok": True,
        "backend": initial["checks"]["companion_backend"],
        "fresh_native_processes": 2,
        "real_sdk_sends": len(sends),
        "recorded_provider_requests": len(provider),
        "unknown_sdk_sends": 1,
        "cancelled_sdk_sends": 0,
        "retained_state": str(options.output),
        "source": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "processing_candidate": initial["processing_candidate"],
        "checks": {"initial": initial["checks"], "restart": resumed["checks"]},
        "limitations": initial["limitations"],
    }
    write(options.output / "native-schedule-validation.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    options = arguments()
    if options.phase == "controller":
        controller(options)
    else:
        asyncio.run(worker(options))
