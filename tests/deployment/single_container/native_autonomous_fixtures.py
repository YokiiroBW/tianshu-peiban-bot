"""Synthetic external peers and existing-domain bootstrap for native AL acceptance.

These helpers are test fixtures. Product clients, owner apps and installed
modules are never substituted. The check driver records this file's digest.
"""

import asyncio
import hashlib
import io
import json
import os
import time
from pathlib import Path
from uuid import uuid4

ACTOR = "actor:a"


MARKER = "NATIVE_AUTONOMY 乌龙茶的焙火香气"


UNDERSTANDING = "NATIVE_UNDERSTANDING 原文说明轻焙火与香气的关系；只针对实际读取区间。"


PREFERENCE = "NATIVE_PREFERENCE 我想继续了解乌龙茶的焙火方式。"


OPERATIONS = [
    "append_episode",
    "get_episode",
    "check_episode",
    "get_receipt",
    "append_derived",
    "query",
    "check_memory",
    "withdraw_episode",
    "withdraw_memory",
    "apply_source_revision",
    "interests",
    "manage_list",
    "memory_impact",
    "revise_memory",
    "forget_memory",
    "maintain",
]


def require(condition, label):
    if not condition:
        raise AssertionError(label)


def read(path):
    return json.loads(Path(path).read_bytes())


def write(path, document):
    Path(path).write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )


class Sources:
    """The search response contains a locator; only the article holds original text."""

    def __init__(self):
        self.calls = []
        self.runner = None
        self.article = (
            "<html><h1>" + MARKER + "</h1><p>轻焙火可以保留部分清香。"
            "不同茶叶和制作工艺仍会影响结果。这是隔离验证文章，不是用户的经历。"
            "</p><p>后文讨论不同温度和加工时间。这些后续段落必须实际读取后才能形成判断。"
            "本验收只读取开头的有限区间，后面的材料保持未读，不据此推断整件理解。"
            "</p></html>"
        ).encode()

    async def open(self):
        from aiohttp import web

        async def directory(request):
            require(
                request.query.get("format") == "json"
                and request.query.get("q") == "乌龙茶",
                "original builtin uses configured SearXNG JSON search",
            )
            self.calls.append(
                {"path": "/search", "query": dict(request.query), "at": time.time()}
            )
            return web.json_response(
                {
                    "results": [
                        {
                            "title": MARKER,
                            "url": self.origin + "/article",
                            "content": "Synthetic article locator; fetch the original before interpretation.",
                        }
                    ]
                }
            )

        async def article(request):
            self.calls.append(
                {
                    "path": "/article",
                    "at": time.time(),
                    "sha256": hashlib.sha256(self.article).hexdigest(),
                }
            )
            return web.Response(
                body=self.article, content_type="text/html", charset="utf-8"
            )

        app = web.Application()
        app.router.add_get("/search", directory)
        app.router.add_get("/article", article)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        await web.TCPSite(self.runner, "127.0.0.1", 0).start()
        self.origin = "http://127.0.0.1:" + str(self.runner.addresses[0][1])
        return self.origin

    async def close(self):
        if self.runner:
            await self.runner.cleanup()


def configure(options, schedule, helper, smoke, host_path, tokens, upstream, sources):
    """Extend the established native fixture; never reserialize frozen contracts."""
    state = schedule.configure(options, helper, smoke, host_path, tokens, upstream)
    config = read(host_path)
    modules, bindings = config["modules"], state["bindings"]
    platform_path = Path(modules["platform"]["settings"])
    platform = read(platform_path)
    for name in ("companion", "memory"):
        platform["principals"][name]["actions"] = list(
            dict.fromkeys(
                platform["principals"][name]["actions"] + ["actor.memory.authorize"]
            )
        )
    platform["principals"]["companion"]["actions"].append("actor.capabilities")
    write(platform_path, platform)

    companion_path = Path(modules["companion"]["settings"])
    companion = read(companion_path)
    companion["roles"][ACTOR]["persona"] = (
        "Synthetic role interested in 乌龙茶的焙火与香气。使用已发现能力寻找实际原件，读后形成有依据的看法并选择后续目标。"
    )
    companion["services"].update(
        {
            "actor_memory_authority": {
                "url": bindings["origins"]["platform"],
                "token_env": "TS012_COMPANION",
            },
            "knowledge": {
                "url": bindings["origins"]["knowledge"],
                "token_env": "NATIVE_KNOWLEDGE_COMPANION",
            },
        }
    )
    modules["companion"]["env"]["NATIVE_KNOWLEDGE_COMPANION"] = bindings["tokens"][
        "knowledge_companion"
    ]
    write(companion_path, companion)

    memory_path = Path(modules["memory"]["settings"])
    memory = read(memory_path)
    memory["callers"]["companion"]["actor_memory_operations"] = OPERATIONS
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
    knowledge_path = Path(modules["knowledge"]["settings"])
    knowledge = read(knowledge_path)
    knowledge["knowledge_content"] = {"trusted_urls": [sources.origin + "/article"]}
    write(knowledge_path, knowledge)
    write(host_path, config)
    return state


async def bootstrap(host_path, sources):
    """Use existing synthetic registration helpers and real maintenance/install ports."""
    from services.platform.service import Platform
    from tianshu_memory.actor_memory.store import Store as ActorStore

    config = read(host_path)
    modules = config["modules"]
    original = dict(os.environ)
    platform = None
    try:
        os.environ.clear()
        os.environ.update(modules["platform"]["env"])
        platform = Platform(read(modules["platform"]["settings"]))
        await platform.plugins.start()
        operation = await platform.plugins.install(
            {"type": "builtin", "id": "content-discovery"}, "native-autonomy:install"
        )
        until = time.monotonic() + 60
        while (
            operation["execution"] in {"accepted", "running"}
            and time.monotonic() < until
        ):
            await asyncio.sleep(0.05)
            operation = platform.plugins.repository.get("operations", operation["id"])
        require(operation["execution"] == "succeeded", "actual search plugin installed")
        instance = await platform.plugins.create(
            operation["package_id"],
            "Synthetic autonomous content discovery",
            "native-autonomy:instance",
        )
        instance = await platform.plugins.configure(
            instance["id"], {"base_url": sources.origin}, {}, instance["revision"]
        )
        instance = await platform.plugins.state(
            instance["id"], True, instance["revision"]
        )
        binding = {
            "kind": "binding",
            "schema_version": 1,
            "binding_id": str(uuid4()),
            "instance_id": instance["id"],
            "revision": 1,
            "enabled": True,
            "scope": {"kind": "character", "character_id": ACTOR},
            "overrides": {},
            "priority": 0,
            "entries": [{"kind": "action", "id": "discover"}],
        }
        await platform.plugins.bindings(instance["id"], [binding], instance["revision"])
    finally:
        if platform:
            await platform.plugins.close()
            platform.close()
            await asyncio.to_thread(platform.local_work.executor.shutdown, wait=True)
        os.environ.clear()
        os.environ.update(original)
    try:
        os.environ.clear()
        os.environ.update(modules["memory"]["env"])
        memory = read(modules["memory"]["settings"])
        initialized = ActorStore.initialize(
            memory, Path(host_path).parent / "actor-initialization.json"
        )
        require(
            not initialized.get("already_installed"),
            "fresh independent actor memory domain",
        )
    finally:
        os.environ.clear()
        os.environ.update(original)


async def register_actor(host):
    """The existing fixture registers confirmed peers; it is not UI acceptance."""
    from test_actor_capabilities import seed_roles

    from deploy.single_container.applications import app_platform

    def registered(app):
        platform = app_platform(app)
        seed_roles(platform)
        role = platform.role_runtime.get(ACTOR)
        role["capabilities"] = ["memory.read", "memory.write"]
        platform.role_runtime._put(role)
        return {
            key: role[key] for key in ("actor_id", "version", "state", "capabilities")
        }

    return await host.exchange.invoke("platform", registered)


def audiovisual():
    """Small original with both real video and real audio, no numpy required."""
    from fractions import Fraction

    import av
    from PIL import Image

    output = io.BytesIO()
    with av.open(output, "w", format="mp4") as container:
        video = container.add_stream("mpeg4", rate=4)
        video.width, video.height, video.pix_fmt = 64, 48, "yuv420p"
        audio = container.add_stream("aac", rate=16000)
        audio.layout = "mono"
        for index in range(16):
            frame = av.VideoFrame.from_image(
                Image.new("RGB", (64, 48), (index * 12, 10, 90))
            )
            frame.pts = index
            for packet in video.encode(frame):
                container.mux(packet)
            sound = av.AudioFrame(format="s16", layout="mono", samples=4000)
            sound.planes[0].update(b"\x10\x01" * 4000)
            sound.sample_rate = 16000
            sound.pts, sound.time_base = index * 4000, Fraction(1, 16000)
            for packet in audio.encode(sound):
                container.mux(packet)
        for stream in (video, audio):
            for packet in stream.encode():
                container.mux(packet)
    return output.getvalue()


async def media_checks(host, helper, state):
    """Real Knowledge decoder routes; these synthetic uploads are not actor episodes."""
    import base64

    import httpx

    token = state["bindings"]["tokens"]["knowledge_companion"]
    path = "/internal/v1/knowledge/content/"

    def principal():
        return {
            "kind": "actor",
            "request_id": "native-media:" + uuid4().hex,
            "actor_id": ACTOR,
            "operation_ref": "native-media:decode-only",
        }

    async def post(operation, payload):
        result = await helper.json_call(
            host, "companion", "knowledge", path + operation, token, payload
        )
        require(
            result["status"] == 200, "real Knowledge " + operation + ": " + str(result)
        )
        return result["body"]

    async def acquire(raw, filename, media):
        owner = principal()
        descriptor = await post(
            "uploads",
            {
                "principal": owner,
                "filename": filename,
                "media_type": media,
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )

        async def upload(app):
            async with httpx.AsyncClient(
                transport=host.modules["companion"].ports.async_transport("knowledge"),
                base_url=host.config["modules"]["knowledge"]["origin"],
                trust_env=False,
            ) as client:
                response = await client.put(
                    path + "uploads/" + descriptor["upload_id"],
                    content=raw,
                    headers={
                        "Authorization": "Bearer " + token,
                        "Content-Type": "application/octet-stream",
                        "X-Tianshu-Actor-Id": ACTOR,
                        "X-Tianshu-Operation-Ref": owner["operation_ref"],
                    },
                )
                require(
                    response.status_code == 200, "actual uploaded original accepted"
                )

        await host.exchange.invoke("companion", upload)
        result = await post(
            "acquire",
            {
                "principal": principal(),
                "source": {"kind": "upload", "upload_id": descriptor["upload_id"]},
                "purpose": "read",
            },
        )
        require(
            result["content_ref"]["sha256"] == hashlib.sha256(raw).hexdigest(),
            "acquired original digest",
        )
        return result["content_ref"]

    subtitle = b"WEBVTT\n\n00:00:00.000 --> 00:00:20.000\nNATIVE_CAPTION_FIRST\n\n00:00:35.000 --> 00:00:45.000\nNATIVE_CAPTION_LATER\n"
    caption = await acquire(subtitle, "synthetic.vtt", "text/vtt")
    first = await post(
        "read",
        {
            "principal": principal(),
            "content_ref": caption,
            "range": {"unit": "seconds", "start": 0, "end": 45},
            "budget_bytes": 8192,
        },
    )
    require(
        caption["kind"] == "text" and first["coverage"]["unit"] == "seconds",
        "caption retains time coverage",
    )
    require(
        first["coverage"]["end"] <= 30
        and not first["complete"]
        and "range_truncated" in first["gaps"],
        "long subtitle request returns actual bounded range",
    )
    require(
        "NATIVE_CAPTION_FIRST" in first["text"]
        and "NATIVE_CAPTION_LATER" not in first["text"],
        "caption text matches actual returned interval",
    )
    later = await post(
        "read",
        {
            "principal": principal(),
            "content_ref": caption,
            "range": {"unit": "seconds", "start": 30, "end": 45},
            "budget_bytes": 8192,
        },
    )
    require(
        "NATIVE_CAPTION_LATER" in later["text"],
        "subtitle continuation reads remaining real cue",
    )
    original = audiovisual()
    video = await acquire(original, "synthetic-audiovisual.mp4", "video/mp4")
    viewed = await post(
        "read",
        {
            "principal": principal(),
            "content_ref": video,
            "range": {"unit": "seconds", "start": 0, "end": 4},
            "budget_bytes": 1048576,
        },
    )
    kinds = {row["kind"] for row in viewed["representations"]}
    require(
        {"audio_clip", "video_frame"} <= kinds and not viewed["complete"],
        "actual audio and frames supplied without claiming full understanding",
    )
    require(
        1
        <= sum(row["kind"] == "video_frame" for row in viewed["representations"])
        <= 8,
        "bounded genuine frame sampling",
    )
    for row in viewed["representations"]:
        require(
            row["source_sha256"] == video["sha256"]
            and hashlib.sha256(
                base64.b64decode(row["data_base64"], validate=True)
            ).hexdigest()
            == row["sha256"],
            "each representation is pinned to original and actual bytes",
        )
    return {
        "caption": {key: first[key] for key in ("coverage", "complete", "gaps")},
        "caption_continuation": later["coverage"],
        "video": {key: viewed[key] for key in ("coverage", "complete", "gaps")},
        "representation_kinds": sorted(kinds),
        "representation_count": len(viewed["representations"]),
    }


def tool_results(messages):
    calls, results = {}, []
    for message in messages:
        if message["role"] == "assistant":
            calls.update(
                {
                    row["id"]: row["function"]["name"]
                    for row in message.get("tool_calls", [])
                }
            )
        elif message["role"] == "tool":
            name = calls.get(message["tool_call_id"])
            envelope = json.loads(message["content"])
            require(
                envelope.get("state") == "completed",
                "actual autonomous tool completed: " + str(name) + ": " + str(envelope),
            )
            results.append((name, envelope["result"]))
    return results


def provider_fixture(schedule, token, sources):
    """A recorded external model emits tools only from genuine preceding results."""

    class Provider(schedule.Provider):
        async def serve(self, request):
            try:
                return await self.recorded_response(request)
            except Exception as error:
                body = await request.json()
                self.calls.append(
                    {
                        "label": "provider-failure",
                        "messages": body.get("messages"),
                        "model": body.get("model"),
                        "stream": body.get("stream"),
                        "failure": {
                            "type": type(error).__name__,
                            "assertion": str(error)
                            if isinstance(error, AssertionError)
                            else None,
                        },
                        "started": time.time(),
                        "finished": time.time(),
                    }
                )
                raise

        async def recorded_response(self, request):
            from aiohttp import web

            require(
                request.headers.get("Authorization") == "Bearer " + self.token,
                "provider authenticates original Gateway credential",
            )
            body = await request.json()
            messages = body["messages"]
            system = "\n".join(
                str(row.get("content", ""))
                for row in messages
                if row["role"] == "system"
            )
            names = {row["function"]["name"] for row in body.get("tools", [])}
            if system.startswith("Choose one concrete next personal goal"):
                captured = json.loads(messages[1]["content"])
                basis = json.dumps(captured["actor_self_memory"], ensure_ascii=False)
                successor = UNDERSTANDING in basis and PREFERENCE in basis
                value = {
                    "decision": "new",
                    "concern_id": None,
                    "title": "NATIVE_NEXT_GOAL 乌龙茶后续探索" if successor else MARKER,
                    "goal": "发现实际原件并读取有限区间，形成有依据的理解与偏好；随后继续查证。",
                    "reason": "由实际自身经历与偏好选择后续目标"
                    if successor
                    else "由人格中的乌龙茶兴趣选择首个目标",
                    "delay_seconds": 600,
                }
                message = {
                    "role": "assistant",
                    "content": json.dumps(value, ensure_ascii=False),
                }
                label = "autonomous-next-goal" if successor else "autonomous-first-goal"
            elif "platform_discover" in names:
                require(
                    {
                        "platform_discover",
                        "platform_invoke",
                        "platform_get",
                        "actor_memory_recall",
                        "actor_memory_remember",
                        "life_content_acquire",
                        "life_reading_open",
                        "life_reading_read",
                        "life_activity_save",
                    }
                    <= names,
                    "real background registry exposes required tools",
                )
                require(
                    not {"memory_propose", "memory_recall"}.intersection(names),
                    "no user-memory tool in autonomous registry",
                )
                captured = json.loads(messages[1]["content"])
                activity = captured["activity"]
                results = tool_results(messages)
                rounds = sum(
                    row["role"] == "assistant" and bool(row.get("tool_calls"))
                    for row in messages
                )
                by_name = {name: result for name, result in results}
                if activity["title"].startswith("NATIVE_NEXT_GOAL"):
                    calls = [
                        (
                            "life_activity_save",
                            {
                                "expected_version": activity["version"],
                                "value": {
                                    **{
                                        key: activity[key]
                                        for key in (
                                            "id",
                                            "title",
                                            "checkpoint",
                                            "resume_condition",
                                            "result_refs",
                                        )
                                    },
                                    "state": "paused",
                                    "next_due_at": time.time() + 3600,
                                },
                            },
                        )
                    ]
                elif rounds == 0:
                    calls = [("platform_discover", {})]
                elif rounds == 1:
                    capabilities = by_name["platform_discover"]["capabilities"]
                    selected = [
                        row
                        for row in capabilities
                        if row["binding"]["action_id"] == "discover"
                        and row["availability"] == "ready"
                    ]
                    require(
                        len(selected) == 1,
                        "one actually discovered ready search action",
                    )
                    calls = [
                        (
                            "platform_invoke",
                            {
                                "binding": selected[0]["binding"],
                                "arguments": {
                                    "query": "乌龙茶",
                                    "sources": ["web"],
                                    "limit": 1,
                                },
                                "intent": "native-discover:" + activity["id"],
                            },
                        )
                    ]
                elif rounds == 2:
                    # The recorded model waits for its external directory response,
                    # rather than assuming the asynchronous plugin already finished.
                    until = time.monotonic() + 10
                    while (
                        not any(row["path"] == "/search" for row in sources.calls)
                        and time.monotonic() < until
                    ):
                        await asyncio.sleep(0.02)
                    require(
                        any(row["path"] == "/search" for row in sources.calls),
                        "actual plugin queried loopback directory",
                    )
                    await asyncio.sleep(0.1)
                    calls = [
                        (
                            "platform_get",
                            {
                                "invocation_id": by_name["platform_invoke"][
                                    "invocation_id"
                                ]
                            },
                        )
                    ]
                elif rounds == 3:
                    result = by_name["platform_get"]
                    require(
                        result["execution"] == "succeeded",
                        "genuine content discovery invocation settled",
                    )
                    candidates = result["result"]["candidates"]
                    require(
                        len(candidates) == 1
                        and candidates[0]["source"] == "web"
                        and candidates[0]["read_locator"]["owner"] == "knowledge"
                        and candidates[0]["read_locator"]["kind"] == "webpage"
                        and candidates[0]["read_locator"]["url"]
                        == sources.origin + "/article",
                        "locator comes from actual discovered candidate",
                    )
                    calls = [
                        (
                            "life_content_acquire",
                            {
                                "expected_version": 0,
                                "value": {
                                    "source": {
                                        "kind": "url",
                                        "url": candidates[0]["read_locator"]["url"],
                                    },
                                    "purpose": "read",
                                },
                            },
                        )
                    ]
                elif rounds == 4:
                    reference = by_name["life_content_acquire"]["content_ref"]
                    require(
                        reference["sha256"]
                        == hashlib.sha256(sources.article).hexdigest(),
                        "acquired real original bytes",
                    )
                    reading_id = (
                        "native-reading:"
                        + hashlib.sha256(activity["id"].encode()).hexdigest()[:32]
                    )
                    calls = [
                        (
                            "life_reading_open",
                            {
                                "expected_version": 0,
                                "value": {
                                    "id": reading_id,
                                    "content_ref": reference,
                                    "mode": "solo",
                                    "participants": [],
                                },
                            },
                        ),
                        (
                            "life_reading_read",
                            {
                                "expected_version": 1,
                                "value": {
                                    "id": reading_id,
                                    "range": {
                                        "unit": "characters",
                                        "start": 0,
                                        "end": 64,
                                    },
                                },
                            },
                        ),
                    ]
                elif rounds == 5:
                    received = by_name["life_reading_read"]["actual_content"]
                    reference = by_name["life_content_acquire"]["content_ref"]
                    # complete describes this requested interval. The owner can
                    # completely serve [0,64) while the 141-character original
                    # still has unread material after position 64.
                    require(
                        MARKER in received["text"]
                        and len(received["text"]) == 64
                        and received["coverage"]
                        == {"unit": "characters", "start": 0, "end": 64}
                        and received["complete"] is True
                        and not received["gaps"]
                        and reference["coverage"]["total"] > 64,
                        "real finite interval precedes self recall",
                    )
                    calls = [
                        (
                            "actor_memory_recall",
                            {"text": "乌龙茶", "kinds": ["episode"], "limit": 8},
                        )
                    ]
                elif rounds == 6:
                    material = by_name["actor_memory_recall"]
                    refs = [
                        ref
                        for row in material["items"]
                        if row["kind"] == "episode"
                        for ref in row["episode_refs"]
                    ]
                    require(
                        refs,
                        "actual Memory receipt and recall supply episode revisions",
                    )
                    calls = [
                        (
                            "actor_memory_remember",
                            {
                                "kind": kind,
                                "text": text,
                                "episode_refs": refs,
                                "importance": "normal",
                                "retention": "medium",
                            },
                        )
                        for kind, text in (
                            ("understanding", UNDERSTANDING),
                            ("preference", PREFERENCE),
                        )
                    ]
                else:
                    require(
                        rounds == 7
                        and sum(name == "actor_memory_remember" for name, _ in results)
                        == 2,
                        "both grounded derived commands precede completion",
                    )
                    value = {
                        key: activity[key]
                        for key in ("id", "title", "resume_condition", "result_refs")
                    }
                    value.update(
                        state="completed",
                        checkpoint={
                            "step": 1,
                            "position": 64,
                            "unit": "characters",
                            "note": "实际读取区间和基于episode的理解偏好已保存；未读完整原件。",
                        },
                        next_due_at=None,
                    )
                    calls = [
                        (
                            "life_activity_save",
                            {"expected_version": activity["version"], "value": value},
                        )
                    ]
                require(
                    all(name in names for name, _ in calls),
                    "recorded model selects only genuinely offered tools",
                )
                message = {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "native:" + str(rounds) + ":" + str(index),
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(value, ensure_ascii=False),
                            },
                        }
                        for index, (name, value) in enumerate(calls)
                    ],
                }
                label = "autonomous-activity"
            else:
                return await super().serve(request)
            self.calls.append(
                {
                    "label": label,
                    "messages": messages,
                    "model": body["model"],
                    "stream": bool(body.get("stream")),
                    "reply": message,
                    "started": time.time(),
                    "finished": time.time(),
                }
            )
            finish = "tool_calls" if message.get("tool_calls") else "stop"
            usage = {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}
            if body.get("stream"):
                response = web.StreamResponse(
                    headers={"Content-Type": "text/event-stream"}
                )
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
                        (
                            "data: " + json.dumps(frame, ensure_ascii=False) + "\n\n"
                        ).encode()
                    )
                await response.write(b"data: [DONE]\n\n")
                return response
            return web.json_response(
                {
                    "id": "synthetic:" + uuid4().hex,
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": message,
                            "finish_reason": finish,
                        }
                    ],
                    "usage": usage,
                }
            )

    return Provider(token)
