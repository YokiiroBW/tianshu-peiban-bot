"""Five actual TLS owners; only the paid upstream is synthetic.

Run with the existing Platform test interpreter (no dependency install):
  python -B tests/integration/test_life_autonomy_joint.py --platform <checkout>
    --companion <checkout> --memory <fixed checkout> --gateway <fixed checkout>
    --contracts <LF contracts/text-dialogue/v1>
    --memory-contracts <deployed Memory contracts/text-dialogue/v1>
    --runtime <owned runtime>

Reuse CompleteJoint's temporary CA, role grants, independent Knowledge, native
Gateway process, login, restart and cleanup. Calendar time is injected through
the existing product constructors; no production debug endpoint or SQL assertions.
"""

import argparse
import asyncio
import copy
import hashlib
import json
import os
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from unittest.mock import patch


def configure_sources():
    if __name__ != "__main__" and "TS_LIFE_PLATFORM" not in os.environ:
        raise unittest.SkipTest(
            "Run the joint fixture with its explicit isolated source paths"
        )
    if __name__ == "__main__":
        parser = argparse.ArgumentParser(description=__doc__)
        for name in (
            "platform",
            "companion",
            "memory",
            "gateway",
            "contracts",
            "memory_contracts",
            "runtime",
        ):
            parser.add_argument("--" + name.replace("_", "-"), type=Path, required=True)
        values = parser.parse_args()
        for name, value in vars(values).items():
            os.environ["TS_LIFE_" + name.upper()] = str(value.resolve())
    paths = {
        name: Path(os.environ["TS_LIFE_" + name.upper()]).resolve()
        for name in (
            "platform",
            "companion",
            "memory",
            "gateway",
            "contracts",
            "memory_contracts",
            "runtime",
        )
    }
    paths["runtime"].mkdir(parents=True, exist_ok=True)
    os.environ.update(
        TS050_RUNTIME=str(paths["runtime"]),
        TS050_CONTRACTS=str(paths["contracts"]),
        TS012_CONTRACT_DIR=str(paths["contracts"]),
        TS_ROLE_GATEWAY_ROOT=str(paths["gateway"]),
        TIANSHU_CORE_REPO=str(paths["companion"]),
        TS_C4_PLATFORM_ROOT=str(paths["platform"]),
        PYTHONDONTWRITEBYTECODE="1",
    )
    source_paths = [
        paths["platform"],
        paths["platform"] / "tests/backend",
        paths["companion"] / "src",
        paths["companion"] / "tests",
        paths["memory"] / "src",
        paths["gateway"] / "src",
        Path(__file__).resolve().parent,
        paths["memory"] / ".runtime/c5-clean-install/venv/Lib/site-packages",
    ]
    for source in reversed(source_paths):
        if not source.is_dir():
            raise RuntimeError("Explicit joint fixture source/dependency is missing")
        sys.path.insert(0, str(source))
    os.environ["PYTHONPATH"] = os.pathsep.join(map(str, source_paths))
    sys.dont_write_bytecode = True


configure_sources()

import httpx  # noqa: E402
from aiohttp import web  # noqa: E402
import test_c4_joint as fixture  # noqa: E402
from services.platform.service import Platform  # noqa: E402
from tianshu_companion.core import Core  # noqa: E402
from tianshu_memory.service import MemoryService  # noqa: E402
from ts050_support import ControlledClock  # noqa: E402


SCHEDULE = [
    {"minute": minute, "activity": "joint-stage-" + str(number), "controls": {}}
    for number, minute in enumerate((0, 420, 540, 720, 840, 1080, 1200, 1380))
]
BOOK = "Synthetic original: the cyan leaf keeps a quiet record of the rain."


class LifeAutonomyJoint(fixture.CompleteJoint):
    async def asyncSetUp(self):
        self.clock = ControlledClock()
        self.native_mode = "checkpoint"
        self.upstream_calls = []
        self.native_results = []
        self.native_request_ids = set()
        self.fail_actor_once = None
        self.injected_failures = []
        with (
            patch("ts050_source_support.Platform", partial(Platform, clock=self.clock)),
            patch(
                "tianshu_memory.app.MemoryService",
                partial(MemoryService, clock=self.memory_now),
            ),
            patch("tianshu_memory.app.now", self.memory_now),
            patch(
                "tianshu_memory.knowledge_content_runtime.MemoryService",
                partial(MemoryService, clock=self.memory_now),
            ),
            patch("tianshu_memory.knowledge_content_runtime.now", self.memory_now),
        ):
            await super().asyncSetUp()
        self.trace["slice"] = "actual_life_autonomy_five_TLS_owners"
        self.trace["calendar_basis"] = (
            "injected product clock; real network and Gateway process"
        )
        self.trace["contract_inputs"] = {
            owner: os.environ["TS_LIFE_" + package]
            for owner, package in (
                ("platform_companion", "CONTRACTS"),
                ("memory_knowledge", "MEMORY_CONTRACTS"),
            )
        }
        self.day_start = datetime.fromtimestamp(self.clock(), timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ) + timedelta(days=1)
        self.clock.value = self.day_start.timestamp() + 2

    def memory_now(self):
        return datetime.fromtimestamp(self.clock(), timezone.utc)

    def platform_settings(self):
        settings = super().platform_settings()
        static = self.directory / "static"
        static.mkdir()
        (static / "index.html").write_text(
            "<!doctype html><title>Joint fixture</title>"
        )
        settings["web"]["static_directory"] = str(static)
        return settings

    def make_core_config(self):
        config = super().make_core_config()
        config.pop("life_writing", None)  # Exercise the autonomous-role default.
        config["life_timezone"] = "UTC"
        return config

    def make_memory_config(self):
        config = super().make_memory_config()
        config["contract_directory"] = os.environ["TS_LIFE_MEMORY_CONTRACTS"]
        return config

    async def start_core(self):
        with patch("tianshu_companion.app.Core", partial(Core, clock=self.clock)):
            await super().start_core()

    async def autonomy(self, resource="routine", object_id=None):
        return await self.web(
            "life/autonomy/read",
            {
                "actor_id": self.actor,
                "resource": resource,
                "object_id": object_id,
                "limit": 50,
                "after": None,
            },
        )

    async def configure(self, *, autonomous=True, diary=True):
        routine = (await self.autonomy())["items"][0]
        return await self.web(
            "life/autonomy/manage",
            {
                "actor_id": self.actor,
                "client_id": str(uuid.uuid4()),
                "operation": "routine.configure",
                "expected_version": routine["version"],
                "value": {
                    "autonomous": autonomous,
                    "diary_enabled": diary,
                    "schedule": copy.deepcopy(SCHEDULE),
                },
            },
        )

    async def drive(self, predicate, *, attempts=40):
        for _ in range(attempts):
            await self.core.life.work()
            result = await predicate()
            if result:
                return result
            await asyncio.sleep(0.05)
        routine = (await self.autonomy())["items"][0]
        activities = (await self.read("activities"))["items"]
        receipts = []
        for request_id in sorted(self.native_request_ids):
            receipts.append(
                await self.core.gateway.client.call(
                    "/internal/v1/model-requests/" + request_id
                )
            )
        self.fail(
            "Bounded joint work did not reach its persisted HTTP result: "
            + json.dumps(
                {
                    "routine_current": routine["current"],
                    "activities": [
                        {key: row[key] for key in ("id", "state", "resume_condition")}
                        for row in activities
                    ],
                    "upstream": self.upstream_calls,
                    "gateway_receipts": receipts,
                },
                ensure_ascii=False,
            )
        )

    @staticmethod
    def activity_value(activity, *, note, references=()):
        return {
            "id": activity["id"],
            "title": activity["title"],
            "state": "completed",
            "checkpoint": {
                "step": activity["checkpoint"]["step"] + 1,
                "position": len(note),
                "unit": "characters",
                "note": note,
            },
            "next_due_at": None,
            "resume_condition": None,
            "result_refs": list(references),
        }

    @staticmethod
    def tool_message(name, value, expected=0):
        return {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call_" + uuid.uuid4().hex,
                    "type": "function",
                    "function": {
                        "name": name,
                        "arguments": json.dumps(
                            {
                                "expected_version": expected,
                                "value": value,
                            },
                            ensure_ascii=False,
                        ),
                    },
                }
            ],
        }

    @staticmethod
    def response(message, body):
        document = {
            "id": "synthetic-autonomy-" + uuid.uuid4().hex,
            "object": "chat.completion",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "message": message,
                    "finish_reason": "tool_calls"
                    if message.get("tool_calls")
                    else "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20,
            },
        }
        if not body.get("stream"):
            return web.json_response(document)
        delta = copy.deepcopy(message)
        for index, call in enumerate(delta.get("tool_calls", [])):
            call["index"] = index
        initial = {
            **document,
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
        }
        terminal = {
            **initial,
            "choices": [
                {
                    "index": 0,
                    "delta": {},
                    "finish_reason": document["choices"][0]["finish_reason"],
                }
            ],
        }
        events = [json.dumps(item, ensure_ascii=False) for item in (initial, terminal)]
        return web.Response(
            text="".join("data: " + event + "\n\n" for event in events)
            + "data: [DONE]\n\n",
            content_type="text/event-stream",
        )

    async def record_model(self, request):
        self.assertEqual(request.headers.get("Authorization"), self.bearer("MODEL"))
        body = await request.json()
        self.native_request_ids.update(self.core.gateway.active_requests.values())
        messages = body["messages"]
        instruction = messages[0]["content"]
        users = [
            json.loads(message["content"])
            for message in messages
            if message["role"] == "user" and isinstance(message.get("content"), str)
        ]
        material = users[0]
        if instruction.startswith("Create today's fictional intentions"):
            kind = "plan"
            content = json.dumps(
                {
                    "entries": [
                        {
                            "minute": entry["minute"],
                            "activity": entry["activity"],
                            "detail": "Synthetic independent intention",
                        }
                        for entry in material["schedule"]
                    ]
                }
            )
            message = {"role": "assistant", "content": content}
        elif "diary draft" in instruction and "activity" not in material:
            kind = "diary"
            if self.native_mode == "reading":
                self.assertIn(BOOK, json.dumps(material, ensure_ascii=False))
            message = {
                "role": "assistant",
                "content": "Synthetic diary: "
                + (
                    BOOK
                    if self.native_mode == "reading"
                    else "I kept the actual fictional checkpoints."
                ),
            }
        else:
            kind = "activity"
            activity = material["activity"]
            actor = activity["actor_id"]
            self.upstream_calls.append((actor, activity["id"]))
            if (
                actor == self.fail_actor_once
                and activity["title"] == SCHEDULE[0]["activity"]
            ):
                self.fail_actor_once = None
                self.injected_failures.append(
                    {"actor_id": actor, "activity_id": activity["id"]}
                )
                return web.json_response(
                    {"error": {"message": "synthetic unavailable"}}, status=503
                )
            results = [
                json.loads(message["content"])
                for message in messages
                if message["role"] == "tool"
            ]
            for result in results:
                self.assertEqual(result["state"], "completed", result)
            if (
                self.native_mode == "reading"
                and activity["id"] == "activity:original-autonomy-joint"
            ):
                message = self.reading_message(body, activity, results)
            elif not results:
                message = self.tool_message(
                    "life_activity_save",
                    self.activity_value(activity, note="Actual synthetic checkpoint"),
                    activity["version"],
                )
            else:
                message = {
                    "role": "assistant",
                    "content": "The checkpoint tool actually completed.",
                }
        self.model_requests.append({"kind": kind, "body": body})
        return self.response(message, body)

    def reading_message(self, body, activity, results):
        step = len(results)
        reading_id = "reading:autonomy-joint"
        if step == 0:
            return self.tool_message(
                "life_content_acquire",
                {
                    "source": {"kind": "upload", "upload_id": self.upload_id},
                    "purpose": "read",
                },
            )
        reference = results[0]["result"]["content_ref"]
        if step == 1:
            return self.tool_message(
                "life_reading_open",
                {
                    "id": reading_id,
                    "content_ref": reference,
                    "mode": "solo",
                    "participants": [],
                },
            )
        if step == 2:
            return self.tool_message(
                "life_reading_read",
                {
                    "id": reading_id,
                    "range": {"unit": "characters", "start": 0, "end": len(BOOK)},
                },
                results[1]["result"]["version"],
            )
        self.assertIn(BOOK, json.dumps(results[2], ensure_ascii=False))
        if step == 3:
            names = {tool["function"]["name"] for tool in body["tools"]}
            self.assertIn("life_read", names)
            return {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_" + uuid.uuid4().hex,
                        "type": "function",
                        "function": {
                            "name": "life_read",
                            "arguments": json.dumps(
                                {
                                    "resource": "activity",
                                    "id": activity["id"],
                                }
                            ),
                        },
                    }
                ],
            }
        if step == 4:
            return self.tool_message(
                "life_concern_save",
                {
                    "id": "concern:autonomy-joint",
                    "title": "Understanding the original",
                    "goal": "Keep one sourced observation",
                    "state": "completed",
                    "fragments": [
                        {
                            "id": "fragment:original",
                            "expected_version": 0,
                            "text": BOOK,
                            "certainty": "observed",
                            "expires_at": None,
                        }
                    ],
                    "next_due_at": None,
                    "expires_at": None,
                    "result_refs": [reference],
                },
            )
        if step == 5:
            observed = results[3]["result"]
            self.native_results.append(
                {"original": BOOK, "activity_id": activity["id"]}
            )
            return self.tool_message(
                "life_activity_save",
                self.activity_value(activity, note=BOOK, references=[reference]),
                observed.get("version", activity["version"]),
            )
        return {
            "role": "assistant",
            "content": "The original was read and its checkpoint saved.",
        }

    async def test_eight_phases_next_day_two_actor_fairness_and_restart(self):
        actors = []
        for number in range(2):
            actor = await self.add_role()
            if number == 0:
                self.fail_actor_once = actor
            await self.configure()
            actors.append(actor)
        completed = {actor: set() for actor in actors}
        for stage, entry in enumerate(SCHEDULE):
            self.clock.value = self.day_start.timestamp() + entry["minute"] * 60 + 2
            for actor in actors:
                self.actor = actor

                async def persisted():
                    rows = (await self.read("activities"))["items"]
                    for row in rows:
                        if row["state"] == "completed" and row["title"].startswith(
                            "joint-stage-"
                        ):
                            completed[actor].add(row["title"])
                    return entry["activity"] in completed[actor]

                if stage == 0 and actor == actors[0]:
                    continue  # Its transient failure must not block the other actor.
                await self.drive(persisted)
            if stage == 0:
                self.assertIn("joint-stage-0", completed[actors[1]])
                self.assertEqual(len(self.injected_failures), 1)
                self.assertEqual(self.injected_failures[0]["actor_id"], actors[0])
                self.actor = actors[0]
                failed = (
                    await self.read(
                        "activities", self.injected_failures[0]["activity_id"]
                    )
                )["items"][0]
                self.assertEqual(failed["state"], "paused")
                self.assertEqual(failed["checkpoint"]["step"], 0)
                await self.restart_core()
        self.assertEqual(
            completed[actors[1]], {entry["activity"] for entry in SCHEDULE}
        )
        self.actor = actors[1]
        previous = await self.web("life/today", {"actor_id": self.actor})
        previous_ids = {row["id"] for row in (await self.read("activities"))["items"]}
        self.clock.value = self.day_start.timestamp() + 86400 + 2

        async def next_day():
            today = await self.web("life/today", {"actor_id": self.actor})
            activities = (await self.read("activities"))["items"]
            return (
                today
                if today["day"] > str(self.day_start.date())
                and today["plan"]["generation_state"] == "completed"
                and today["plan"]["plan_id"] != previous["plan"]["plan_id"]
                and any(
                    row["id"] not in previous_ids
                    and row["title"] == SCHEDULE[0]["activity"]
                    and row["state"] == "completed"
                    for row in activities
                )
                else None
            )

        today = await self.drive(next_day)
        self.assertNotEqual(today["plan"]["plan_id"], None)
        self.check(
            "eight_stages_then_next_day",
            completed_stages=8,
            actors=2,
            good_actor_not_blocked=True,
            restarted=True,
            next_day=today["day"],
        )

    async def test_native_original_checkpoint_diary_publish_authorized_read(self):
        await self.add_role()
        await self.configure(autonomous=False)
        activity_id = "activity:original-autonomy-joint"
        await self.manage(
            "activity.save",
            {
                "id": activity_id,
                "title": "Read the actor-owned synthetic original",
                "state": "paused",
                "checkpoint": {
                    "step": 0,
                    "position": 0,
                    "unit": "step",
                    "note": "Not yet read",
                },
                "next_due_at": None,
                "resume_condition": "Waiting for actual original upload",
                "sources": [],
                "result_refs": [],
                "scope": None,
            },
        )
        header = {"Authorization": "Bearer " + os.environ["TS_C4_KNOWLEDGE_ADMIN"]}
        raw = BOOK.encode()
        # This internal caller has no browser session cookie (localhost ports share cookies).
        knowledge_client = httpx.AsyncClient(verify=str(self.ca))
        self.addAsyncCleanup(knowledge_client.aclose)
        descriptor = await knowledge_client.post(
            self.knowledge_url + "/internal/v1/knowledge/content/uploads",
            headers=header,
            json={
                "principal": {
                    "kind": "actor",
                    "actor_id": self.actor,
                    "request_id": "upload:autonomy",
                    "operation_ref": activity_id,
                },
                "filename": "actor-original.txt",
                "media_type": "text/plain",
                "size": len(raw),
                "sha256": hashlib.sha256(raw).hexdigest(),
            },
        )
        self.assertEqual(descriptor.status_code, 200, descriptor.text)
        self.upload_id = descriptor.json()["upload_id"]
        uploaded = await knowledge_client.put(
            self.knowledge_url
            + "/internal/v1/knowledge/content/uploads/"
            + self.upload_id,
            headers={
                **header,
                "Content-Type": "application/octet-stream",
                "X-Tianshu-Actor-Id": self.actor,
                "X-Tianshu-Operation-Ref": activity_id,
                "X-Tianshu-Request-Id": "upload:autonomy-bytes",
            },
            content=raw,
        )
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        self.native_mode = "reading"
        await self.configure(autonomous=True)
        await self.manage("activity.resume", {"id": activity_id}, 1)

        async def completed():
            rows = (await self.read("activities", activity_id))["items"]
            return rows[0] if rows and rows[0]["state"] == "completed" else None

        activity = await self.drive(completed)
        self.assertEqual(activity["checkpoint"]["note"], BOOK)
        self.assertEqual(activity["checkpoint"]["step"], 1)
        concerns = (await self.read("open_work", "concern:autonomy-joint"))["items"]
        self.assertEqual(concerns[0]["fragments"][0]["text"], BOOK)
        await self.restart_core()
        self.clock.advance(86400)
        await self.login()

        async def draft():
            rows = (await self.autonomy("diaries"))["items"]
            return next((row for row in rows if row["state"] == "draft"), None)

        diary = await self.drive(draft)
        revision = (await self.autonomy("diary_revision", diary["current_revision"]))[
            "items"
        ][0]
        self.assertIn(BOOK, revision["content"])
        published_before = await self.web(
            "life/diaries", {"actor_id": self.actor, "limit": 20, "after": None}
        )
        self.assertNotIn(
            diary["id"], {row["diary_id"] for row in published_before["items"]}
        )
        published = await self.manage(
            "diary.publish", {"id": diary["id"]}, diary["version"]
        )
        self.assertEqual(published["result"]["state"], "published")
        diaries = await self.web(
            "life/diaries", {"actor_id": self.actor, "limit": 20, "after": None}
        )
        row = next(row for row in diaries["items"] if row["diary_id"] == diary["id"])
        read = await self.web(
            "life/revision",
            {
                "actor_id": self.actor,
                "diary_id": row["diary_id"],
                "revision_id": row["published_revision_id"],
                "expected_diary_version": row["version"],
            },
        )
        self.assertIn(BOOK, read["content"])
        self.check(
            "native_original_to_published_diary",
            original_sha256=hashlib.sha256(raw).hexdigest(),
            actual_native_tool_query=True,
            checkpoint_step=1,
            actual_diary_draft=True,
            published_pointer=True,
            reader_authorized=True,
            restarted=True,
        )


# This file owns only its new acceptance scenarios, not inherited fixture tests.
for _name in dir(fixture.CompleteJoint):
    if _name.startswith("test_"):
        setattr(LifeAutonomyJoint, _name, None)


if __name__ == "__main__":
    unittest.main(argv=[sys.argv[0]], verbosity=2)
