"""External protocol peers and native-owner bootstrap for AL12 acceptance."""

import copy
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

from native_autonomous_fixtures import ACTOR, OPERATIONS, read, require, write

GROUP = "910003"
AUTHOR = "910004"
TOPIC = "乌龙茶焙火"
PRIVATE = (
    "NATIVE_OBSERVER_PRIVATE 群里有人提到轻焙火保留清香，这只是他们正在讨论的内容。"
)
REFLECTION = (
    "NATIVE_OBSERVER_REFLECTION 我只是旁听到关于乌龙茶焙火的讨论，还需实际阅读再查证。"
)
FIRST_GOAL = "NATIVE_OBSERVER_GOAL 登记乌龙茶待查证问题"
SECOND_GOAL = "NATIVE_INDEPENDENT_GOAL 整理自己的阅读环境"
QUEUE = "actor:observation"
TABLES = (
    "owner_heads",
    "sources",
    "scopes",
    "groups",
    "records",
    "history",
    "physical_sources",
    "source_admissions",
    "memory_materials",
    "memory_material_aliases",
    "memory_segments",
    "memory_segment_claims",
    "memory_segment_items",
    "suppression",
    "profile_shares",
    "profile_approvals",
    "profile_approval_authorities",
    "memory_documents",
    "memory_batches",
    "memory_summaries",
    "memory_summary_sources",
    "memory_portraits",
    "memory_source_lifecycle",
    "memory_observation_heads",
)


def configure(options, schedule, helper, smoke, host_path, tokens, upstream):
    """Reuse native conversation fixtures; add only original observer ports."""
    from dataclasses import asdict

    from tianshu_gateway.config import ClientGrant

    state = schedule.configure(options, helper, smoke, host_path, tokens, upstream)
    config = read(host_path)
    modules, bindings = config["modules"], state["bindings"]
    platform_path = Path(modules["platform"]["settings"])
    platform = read(platform_path)
    for name in ("companion", "memory"):
        platform["principals"][name]["actions"] = list(
            dict.fromkeys(
                platform["principals"][name]["actions"]
                + ["actor.memory.authorize", "observation.verify"]
            )
        )
    platform["principals"]["companion"]["actions"].append("actor.capabilities")
    write(platform_path, platform)
    background_token = secrets.token_urlsafe(32)
    companion_path = Path(modules["companion"]["settings"])
    companion = read(companion_path)
    companion.update(bot_observation_enabled=True, automatic_memory_candidates=False)
    companion["roles"][ACTOR]["persona"] = (
        "Synthetic role interested in 乌龙茶焙火。旁听别人的讨论不代表自己参与或认同；先保留问题，实际阅读后再判断。"
    )
    companion["services"].update(
        actor_memory_authority={
            "url": bindings["origins"]["platform"],
            "token_env": "TS012_COMPANION",
        },
        gateway_background={
            "url": bindings["origins"]["gateway"],
            "token_env": "NATIVE_OBSERVER_BACKGROUND",
        },
    )
    modules["companion"]["env"]["NATIVE_OBSERVER_BACKGROUND"] = background_token
    write(companion_path, companion)
    gateway_path = Path(modules["gateway"]["settings"])
    gateway = read(gateway_path)
    gateway["secret_references"]["secret-ref:fixture/observer-background"] = (
        "NATIVE_OBSERVER_BACKGROUND"
    )
    gateway["clients"].append(
        asdict(
            ClientGrant(
                "companion_background",
                "secret-ref:fixture/observer-background",
                gateway["clients"][0]["provider_id"],
                7,
                True,
            )
        )
    )
    gateway["workload_bindings"] = {
        "bindings": {"companion": "interactive", "companion_background": "background"},
        "default_class": "interactive",
    }
    modules["gateway"]["env"]["NATIVE_OBSERVER_BACKGROUND"] = background_token
    write(gateway_path, gateway)
    memory_path = Path(modules["memory"]["settings"])
    memory = read(memory_path)
    memory["callers"]["companion"]["actor_memory_operations"] = OPERATIONS
    memory["callers"]["companion"]["operations"] = list(
        dict.fromkeys(
            memory["callers"]["companion"]["operations"]
            + ["observe_ingest", "observe_query"]
        )
    )
    memory["observation_source"] = {
        "verify_url": bindings["origins"]["platform"]
        + "/internal/v2/observation-source/verify",
        "verify_token": bindings["tokens"]["source_access"],
    }
    memory["actor_memory"] = {
        "database_path": str(options.output / "actor-memory.sqlite"),
        "contract_directory": str(
            options.memory_contracts.resolve().parents[1]
            / "actor-self-memory/candidate-v1"
        ),
        "authorities": {
            "actor_check": {
                "url": bindings["origins"]["platform"]
                + "/internal/v1/actor-self-memory/authority",
                "token": bindings["tokens"]["source_access"],
            },
            "producers": {
                "companion": {
                    "source_facts_url": bindings["origins"]["companion"]
                    + "/internal/v1/actor-self-memory/source",
                    "source_check_url": bindings["origins"]["companion"]
                    + "/internal/v1/actor-self-memory/source",
                    "token": bindings["tokens"]["source_facts"],
                }
            },
        },
    }
    write(memory_path, memory)
    write(host_path, config)
    return state


def initialize_actor(host_path):
    from tianshu_memory.actor_memory.store import Store

    module = read(host_path)["modules"]["memory"]
    prior = dict(os.environ)
    try:
        os.environ.clear()
        os.environ.update(module["env"])
        result = Store.initialize(
            read(module["settings"]),
            Path(host_path).parent / "actor-initialization.json",
        )
        require(not result.get("already_installed"), "fresh independent actor domain")
    finally:
        os.environ.clear()
        os.environ.update(prior)


def peer_fixture(schedule):
    """Real reverse WS peer; unexpected outbound sends are evidence of failure."""

    class Peer(schedule.OneBotPeer):
        async def respond(self):
            async for raw in self.peer:
                request = json.loads(raw)
                self.calls.append(
                    {"action": request["action"], "observed_at": time.time()}
                )
                if request["action"] == "get_login_info":
                    data, code = (
                        {"user_id": 10001, "nickname": "Synthetic observer peer"},
                        0,
                    )
                else:
                    data, code = {}, 100
                await self.peer.send(
                    json.dumps(
                        {
                            "status": "ok" if code == 0 else "failed",
                            "retcode": code,
                            "data": data,
                            "echo": request["echo"],
                        }
                    )
                )

        async def observed_message(self):
            await self.peer.send(
                json.dumps(
                    {
                        "time": int(time.time()),
                        "self_id": 10001,
                        "post_type": "message",
                        "message_type": "group",
                        "sub_type": "normal",
                        "group_id": int(GROUP),
                        "message_id": 991001,
                        "user_id": int(AUTHOR),
                        "anonymous": None,
                        "message": [{"type": "text", "data": {"text": PRIVATE}}],
                        "raw_message": PRIVATE,
                        "font": 0,
                        "sender": {
                            "user_id": int(AUTHOR),
                            "nickname": "Synthetic observer author",
                            "card": "",
                        },
                    },
                    ensure_ascii=False,
                )
            )

    return Peer()


async def enroll_observe_only(host, schedule, peer, state):
    from fixtures import bearer
    from services.platform import group_policy

    from deploy.single_container.applications import app_platform

    module = host.config["modules"]["onebot"]
    port = int(urlsplit(module["origin"]).port)
    await peer.open(port, state["tokens"]["sdk"])

    def access(app):
        from tianshu_nonebot.storage import read_access_key

        return read_access_key(
            Path(module["data_directory"]) / "adapter.sqlite3",
            read(module["settings"]),
            environ=host.modules["onebot"].environment,
        )

    key = await host.exchange.invoke("onebot", access)
    instance = await host.exchange.invoke(
        "onebot", lambda app: app.state.lifecycle.instance_id
    )
    address = "http://127.0.0.1:" + str(port)

    async def online(app):
        value = await app_platform(app).bot_adapters._plugin(
            {
                "id": "observer-owned-probe",
                "instance_id": instance,
                "address": address,
                "access_key": key,
                "pins": None,
                "ca_pem": None,
            },
            "/capabilities",
            {},
        )
        return bool(value["accounts"])

    await schedule.wait_for(
        lambda: host.exchange.invoke("platform", online),
        "synthetic observation SDK account online",
        15,
    )

    async def enroll(app):
        owner = app_platform(app).bot_observation
        initial = await owner.local_enroll_default(
            bearer("ADMIN"),
            {
                "adapter": "onebot",
                "address": address,
                "access_key": key,
                "allow_private_http": True,
                "ca_pem": None,
                "account_id": "10001",
                "name": "Synthetic AL12 observation only",
            },
        )
        row = owner.get(initial["connection"]["id"])
        require(
            row["state"] == "ready" and row["enabled"], "real SDK observation enrolled"
        )
        updated = await owner.policy(
            {
                "id": row["id"],
                "expected_revision": row["revision"],
                "group_policy": {
                    "version": 2,
                    "default": group_policy.rule("observe", ACTOR),
                    "groups": {},
                },
                "private_policy": {
                    "observe": False,
                    "mode": "observe_only",
                    "list": [],
                    "actor_id": None,
                },
                "client_id": "observer-policy:" + uuid4().hex,
            }
        )
        require(
            updated["state"] == "ready" and updated["pending"] is None,
            "observe-only policy applied in actual SDK",
        )
        frame = {
            "instance_id": instance,
            "self_id": "10001",
            "conversation_id": "group:" + GROUP,
            "actor_id": ACTOR,
        }
        return {
            "id": updated["id"],
            "frame": frame,
            "policy_version": group_policy.revision(updated, "group:" + GROUP),
        }

    return await host.exchange.invoke("platform", enroll)


async def emit_model_response(request, body, message):
    from aiohttp import web

    finish = "tool_calls" if message.get("tool_calls") else "stop"
    usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
    if not body.get("stream"):
        return web.json_response(
            {
                "id": "synthetic:" + uuid4().hex,
                "object": "chat.completion",
                "created": 1,
                "model": body["model"],
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": usage,
            }
        )
    response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
    await response.prepare(request)
    delta = {"role": "assistant"}
    if message.get("content") is not None:
        delta["content"] = message["content"]
    if message.get("tool_calls"):
        delta["tool_calls"] = [
            dict(index=index, **call)
            for index, call in enumerate(message["tool_calls"])
        ]
    for frame in (
        {"choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
        {
            "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
            "usage": usage,
        },
    ):
        await response.write(
            ("data: " + json.dumps(frame, ensure_ascii=False) + "\n\n").encode()
        )
    await response.write(b"data: [DONE]\n\n")
    return response


def provider_fixture(schedule, token):
    class Provider(schedule.Provider):
        def __init__(self, credential):
            super().__init__(credential)
            self.observation_goal_chosen = False

        async def serve(self, request):
            try:
                return await self.observer_response(request)
            except Exception as error:
                body = await request.json()
                self.calls.append(
                    {
                        "label": "provider-failure",
                        "messages": body.get("messages"),
                        "failure": {
                            "type": type(error).__name__,
                            "assertion": str(error)
                            if isinstance(error, AssertionError)
                            else None,
                        },
                    }
                )
                raise

        async def observer_response(self, request):
            require(
                request.headers.get("Authorization") == "Bearer " + self.token,
                "original Gateway provider credential",
            )
            body = await request.json()
            messages = body["messages"]
            system = "\n".join(
                str(row.get("content", ""))
                for row in messages
                if row["role"] == "system"
            )
            if "actually archived same-group messages" in system:
                captured = json.loads(messages[1]["content"])
                facts = captured["source_facts"]
                require(
                    facts
                    and all(
                        row["relation"] == "observer"
                        and row["provenance"] == "observation"
                        and not row["content_reads"]
                        and PRIVATE in row["summary"]
                        for row in facts
                    ),
                    "real archived owner facts reach relevance judgement",
                )
                value = {
                    "retain": [row["source"]["object_id"] for row in facts],
                    "topic": TOPIC,
                    "reflection": REFLECTION,
                }
                label = "observer-relevance"
                message = {
                    "role": "assistant",
                    "content": json.dumps(value, ensure_ascii=False),
                }
            elif system.startswith("Choose one concrete next personal goal"):
                captured = json.loads(messages[1]["content"])
                retained = REFLECTION in json.dumps(
                    captured["actor_self_memory"], ensure_ascii=False
                )
                if retained and not self.observation_goal_chosen:
                    value = {
                        "decision": "new",
                        "concern_id": None,
                        "title": FIRST_GOAL,
                        "goal": "把实际旁听后仍需查证的问题登记为自己的后续探索事项；不声称已经阅读资料。",
                        "reason": "来自真实旁听及已保存反思",
                        "delay_seconds": 60,
                        "observation_inspired": True,
                    }
                    self.observation_goal_chosen = True
                    label = "observer-inspired-goal"
                elif self.observation_goal_chosen:
                    value = {
                        "decision": "new",
                        "concern_id": None,
                        "title": SECOND_GOAL,
                        "goal": "之后整理自己的阅读环境，目前只登记计划。",
                        "reason": "独立于先前群话题的个人安排",
                        "delay_seconds": 600,
                        "observation_inspired": False,
                    }
                    label = "independent-goal"
                else:
                    value = {
                        "decision": "rest",
                        "concern_id": None,
                        "title": "等待实际材料",
                        "goal": "目前没有新的实际观察",
                        "reason": "不凭空制造经历",
                        "delay_seconds": 600,
                    }
                    label = "pre-observation-rest"
                message = {
                    "role": "assistant",
                    "content": json.dumps(value, ensure_ascii=False),
                }
            elif "Continue this actor's independent activity" in system:
                activity = json.loads(messages[1]["content"])["activity"]
                require(
                    activity["title"] in {FIRST_GOAL, SECOND_GOAL},
                    "only sourced observer or declared independent goal executes",
                )
                value = {
                    key: activity[key]
                    for key in ("id", "title", "resume_condition", "result_refs")
                }
                value.update(
                    state="paused",
                    next_due_at=None,
                    checkpoint={
                        "step": 0,
                        "position": 0,
                        "unit": "step",
                        "note": "已登记待查证问题；没有阅读新资料或完成外部探索。",
                    },
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "observer-register-plan",
                            "type": "function",
                            "function": {
                                "name": "life_activity_save",
                                "arguments": json.dumps(
                                    {
                                        "expected_version": activity["version"],
                                        "value": value,
                                    },
                                    ensure_ascii=False,
                                ),
                            },
                        }
                    ],
                }
                label = (
                    "pause-observer-plan"
                    if activity["title"] == FIRST_GOAL
                    else "pause-independent-plan"
                )
            else:
                return await super().serve(request)
            self.calls.append(
                {
                    "label": label,
                    "messages": copy.deepcopy(messages),
                    "reply": message,
                    "model": body["model"],
                    "stream": bool(body.get("stream")),
                    "started": time.time(),
                }
            )
            return await emit_model_response(request, body, message)

    return Provider(token)


async def user_memory_snapshot(host):
    """Read logical user-memory data on its own owner, excluding raw archive/QQ identities."""

    def inspect(app):
        service = app.state.memory
        with service.store.transaction() as db:
            tables = {
                table: sorted(
                    [list(row) for row in db.execute("SELECT * FROM " + table)],
                    key=lambda value: json.dumps(
                        value, ensure_ascii=False, sort_keys=True
                    ),
                )
                for table in TABLES
            }
        return {
            "sha256": hashlib.sha256(
                json.dumps(tables, sort_keys=True, ensure_ascii=False).encode()
            ).hexdigest(),
            "counts": {table: len(rows) for table, rows in tables.items()},
        }

    return await host.exchange.invoke("memory", inspect)


async def snapshot(host):
    def inspect(app):
        core = app.state.core
        return {
            "backend": core.store.backend,
            "turns": core.store.list("turns"),
            "outbox": core.store.list("outbox"),
            "actors": core.store.list("life_actors"),
            "jobs": core.store.list("metadata", QUEUE),
            "activities": core.store.list("life_activities"),
            "user_learning": core.store.list("metadata", "memory:learning"),
            "automatic_memory_candidates": core.automatic_memory_candidates,
            "reading": core.store.list("life_reading"),
        }

    return await host.exchange.invoke("companion", inspect)


async def platform_policy(host, enrollment):
    from contextlib import closing

    from services.platform import group_policy

    from deploy.single_container.applications import app_platform

    def inspect(app):
        owner = app_platform(app).bot_observation
        row = owner.get(enrollment["id"])
        with closing(owner._db()) as db:
            counts = {
                name: db.execute("SELECT count(*) FROM " + name).fetchone()[0]
                for name in ("reply_connections", "reply_authors")
            }
        return {
            "rule": group_policy.effective(row["group_policy"], "group:" + GROUP),
            "generation": group_policy.revision(row, "group:" + GROUP),
            "counts": counts,
        }

    return await host.exchange.invoke("platform", inspect)
