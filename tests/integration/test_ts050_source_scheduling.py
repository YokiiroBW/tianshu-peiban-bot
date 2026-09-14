"""Real source barriers around timed collection, concurrent model calls and ordered delivery."""

import asyncio
import json
import time

from ts050_source_support import SourceChain


class OrderedSourceChain(SourceChain):
    async def test_two_slots_t3_and_A_plan_B_interruption_A_continuation(self):
        gate = self.model_gates["actor:a"] = asyncio.Event()
        first = await self.submit(self.physical("请给我一个周末旅行方案。", "plan-a"))
        await self.eventually(lambda: len(self.model_requests) == 1)
        await self.submit(
            self.physical("我插一句，今天晴天。", "interruption-b"), actors=("actor:b",)
        )
        await self.eventually(
            lambda: (
                len(self.model_requests) == 2
                and self.core.store.list("turns")[1]["phase"] == "ready_to_send"
            )
        )
        await self.submit(self.physical("按你刚才的方案继续。", "continuation-a"))
        before = self.core_state()
        self.assertEqual(
            [t["phase"] for t in before["turns"]], ["generating", "ready_to_send", "queued"]
        )
        self.assertEqual(len(self.channel_requests), 0)
        gate.set()
        await self.wait_commits(3)
        self.assertEqual(len(self.model_requests), 3)
        self.assertEqual(
            [r["actor_id"] for r in self.channel_requests], ["actor:a", "actor:b", "actor:a"]
        )
        self.assertEqual([r["turn_sequence"] for r in self.channel_requests], [1, 2, 3])
        turns = self.core.store.list("turns")
        prompt = json.loads(self.model_requests[2]["body"]["messages"][-1]["content"])
        dependencies = prompt["delivered_dependencies"]
        self.assertEqual([d["turn_id"] for d in dependencies], [turns[0]["id"]])
        self.assertNotIn(turns[1]["id"], [d["turn_id"] for d in dependencies])
        self.assertTrue(self.model_requests[1]["completed"] < self.model_requests[0]["completed"])
        self.assertEqual([t["model_calls"] for t in turns], [1, 1, 1])
        self.check(
            "actual_two_slots_global_order_and_same_scope_plan",
            before_release=before,
            first_admission=first["outcomes"][0]["admission"],
            dependencies=dependencies,
            channel_order=[(r["turn_sequence"], r["actor_id"]) for r in self.channel_requests],
        )


class DebouncedSourceChain(SourceChain):
    silence_ms = 5000

    async def asyncSetUp(self):
        await super().asyncSetUp()
        events = self.trace["memory_transport"] = []

        async def request_hook(request):
            body = json.loads(request.content) if request.content else {}
            envelope = body.get("query", body.get("command", body))
            request_id = envelope.get("request_id", body.get("event_id"))

            async def trace(name, info):
                record = {
                    "request_id": request_id,
                    "path": request.url.path,
                    "event": name,
                    "at": time.monotonic(),
                }
                error = info.get("exception")
                if error is not None:
                    record.update(exception_type=type(error).__name__, exception=str(error))
                if name == "http11.receive_response_headers.complete":
                    record["http_status"] = info["return_value"][1]
                events.append(record)  # Passive only: no delay, retry, socket or payload change.

            request.extensions["trace"] = trace

        self.core.memory.client.client.event_hooks["request"].append(request_hook)

    async def test_w5_short_sentences_and_ordinary_name_followup(self):
        first = await self.submit(self.physical("我叫", "short-1"))
        await asyncio.sleep(0.2)
        second = await self.submit(self.physical("小明。", "short-2"))
        a, b = first["outcomes"][0]["receipt"], second["outcomes"][0]["receipt"]
        self.assertEqual(a["collection_id"], b["collection_id"])
        self.assertEqual(self.core.store.list("turns"), [])
        self.assertEqual(len(self.model_requests), 0)
        self.assert_sent_commits(await self.wait_commits(1))
        turn = self.core.store.list("turns")[0]
        self.assertEqual(turn["bundle"]["close_reason"], "silence")
        self.assertEqual(
            [m["parts"][0]["text"] for m in turn["bundle"]["messages"]], ["我叫", "小明。"]
        )
        await self.submit(self.physical("我叫什么？", "name-followup"))
        self.assert_sent_commits(await self.wait_commits(2))
        self.assertEqual(
            len(self.model_requests), 2, "Two successful turns require two model requests"
        )
        prompt = json.loads(self.model_requests[1]["body"]["messages"][-1]["content"])
        self.assertTrue(prompt["recent_dialogue"])
        recent = json.dumps(prompt["recent_dialogue"], ensure_ascii=False)
        self.assertIn("我叫", recent)
        self.assertIn("小明", recent)
        self.assertEqual(self.channel_requests[1]["text"], "你叫小明。")
        self.assertEqual(len(self.model_requests), 2)
        self.check(
            "real_W5_and_recent_name_context",
            first_turn=turn["bundle"],
            recent_dialogue=prompt["recent_dialogue"],
            recorded_answer=self.channel_requests[1]["text"],
            clock="real wall time; no virtual Core clock",
            model="deterministic external recorder requires name in actual prompt",
        )


class UnknownSourceChain(SourceChain):
    reconcile_ms = 2000

    async def test_unknown_core_restart_no_resend_or_duplicate_candidate(self):
        self.send_states = ["unknown", "sent"]
        physical = self.physical("这是一条回执不确定的本人输入。", "unknown-input")
        result = await self.submit(physical, key="same:unknown-input")
        await self.eventually(
            lambda: (
                self.core.store.list("turns")
                and self.core.store.list("turns")[0]["phase"] == "reconciling"
            )
        )
        before = self.core_state()
        await self.restart_core()
        commits = await self.wait_commits(1)
        self.assertEqual(self.core.store.list("turns")[0]["phase"], "closed_unknown")
        self.assertEqual(commits[0]["request"]["delivery_state"], "unknown")
        repeat = await self.submit(physical, key="same:unknown-input")
        self.assertEqual(
            repeat["outcomes"][0]["receipt"]["receipt_id"],
            result["outcomes"][0]["receipt"]["receipt_id"],
        )
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.channel_requests), 1)
        await self.submit(self.physical("后续轮次可以继续。", "after-unknown"))
        await self.wait_commits(2)
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.channel_requests), 2)
        self.assertEqual(len(await asyncio.to_thread(self.workflow.jobs)), 2)
        self.assertNotEqual(
            self.channel_requests[0]["reply_id"], self.channel_requests[1]["reply_id"]
        )
        self.check(
            "unknown_survives_Core_owner_restart_without_replay",
            before=before,
            replay=repeat,
            channel_receipts=self.channel_receipts,
            restart="actual Core ASGI lifecycle and SQLite owner reopen; not hard process crash",
        )
