"""New fixed Core: real W5 plus one query retry without closing another in-flight request."""

import asyncio
import hashlib
import json
import time

import test_ts050_source_scheduling as scheduling
from ts050_source_support import SourceChain


class FixedW5(scheduling.DebouncedSourceChain):
    pass


class FixedQueryRetry(SourceChain):
    async def test_same_idle_interleaving_recovers_query_and_preserves_second_slot(self):
        await self.submit(self.physical("先建立真实复用连接。", "retry:baseline"))
        self.assert_sent_commits(await self.wait_commits(1))
        target_marker, parallel_marker = "受控查询重试", "并行槽查询"
        hold_started, parallel_held, retry_received = (
            asyncio.Event(),
            asyncio.Event(),
            asyncio.Event(),
        )
        release_parallel = asyncio.Event()
        ids, events, attempts = {}, [], []
        held = False
        client = self.core.memory.client.client
        original_client = client

        async def hook(request):
            nonlocal held
            if request.url.path != "/internal/v1/memory/select":
                return
            body = json.loads(request.content)
            role = {target_marker: "target", parallel_marker: "parallel"}.get(
                body.get("query_text")
            )
            if role is None:
                return
            rid = body["query"]["request_id"]
            ids.setdefault(role, rid)
            if ids[role] != rid:
                return
            attempts.append(
                {
                    "role": role,
                    "request_id": rid,
                    "body_sha256": hashlib.sha256(request.content).hexdigest(),
                }
            )

            async def trace(name, info):
                nonlocal held
                entry = {"role": role, "event": name, "at": time.monotonic()}
                if info.get("exception") is not None:
                    entry["exception_type"] = type(info["exception"]).__name__
                if name == "http11.receive_response_headers.complete":
                    entry["status"] = info["return_value"][1]
                events.append(entry)
                if role == "target" and name == "http11.send_request_headers.started" and not held:
                    held = True
                    hold_started.set()
                    await asyncio.sleep(self.memory_server.server.config.timeout_keep_alive + 0.2)
                if role == "target" and name == "http11.receive_response_headers.complete":
                    retry_received.set()
                if role == "parallel" and name == "http11.receive_response_headers.started":
                    parallel_held.set()
                    await release_parallel.wait()

            request.extensions["trace"] = trace

        client.event_hooks["request"].append(hook)
        try:
            await self.submit(self.physical(target_marker, "retry:target"))
            await asyncio.wait_for(hold_started.wait(), 5)
            # Keep B in flight during A's failed socket/retry, but below B's own idle timeout.
            await asyncio.sleep(4)
            await self.submit(self.physical(parallel_marker, "retry:parallel"), actors=("actor:b",))
            await asyncio.wait_for(parallel_held.wait(), 5)
            await asyncio.wait_for(retry_received.wait(), 5)
            self.assertIs(self.core.memory.client.client, original_client)
            self.assertFalse(client.is_closed)
            release_parallel.set()
            self.assert_sent_commits(await self.wait_commits(3))
        finally:
            release_parallel.set()
            client.event_hooks["request"].remove(hook)
        target_attempts = [a for a in attempts if a["role"] == "target"]
        self.assertEqual(len(target_attempts), 2)
        self.assertEqual(target_attempts[0], target_attempts[1])
        target_events = [e for e in events if e["role"] == "target"]
        self.assertEqual(
            sum(e["event"] == "connection.connect_tcp.started" for e in target_events), 1
        )
        self.assertEqual(
            sum(e.get("exception_type") == "RemoteProtocolError" for e in target_events), 1
        )
        self.assertEqual(sum(e.get("status") == 200 for e in target_events), 1)
        received = [
            r
            for r in self.wire
            if r["owner"] == "memory"
            and r["path"].endswith("/memory/select")
            and (r["request"] or {}).get("query", {}).get("request_id") == ids["target"]
        ]
        self.assertEqual(len(received), 1)
        self.assertEqual([t["model_calls"] for t in self.core.store.list("turns")], [1, 1, 1])
        self.assertEqual(len(self.model_requests), 3)
        self.assertEqual(len(self.channel_requests), 3)
        forbidden = {
            "/v1/chat/completions",
            "/internal/v1/conversation/send",
            "/internal/v1/identity/register",
            "/internal/v1/memory/revise",
            "/internal/v1/memory/turn-commits",
        }
        self.assertFalse(forbidden.intersection(self.core.memory.client.QUERY_PATHS))
        self.check(
            "fixed_query_retry_real_idle_boundary",
            attempts=attempts,
            transport=events,
            shared_client_preserved=True,
            models=3,
            sends=3,
            only_query_whitelisted=True,
            previous_failure_evidence_unchanged=True,
        )
