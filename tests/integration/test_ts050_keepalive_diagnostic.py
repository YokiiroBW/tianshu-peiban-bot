"""Controlled transport interleaving, not a retry-until-green W5 acceptance test."""

import asyncio
import json
import time

import httpx
from ts050_source_support import SourceChain


class KeepAliveDiagnostic(SourceChain):
    async def test_idle_connection_expiry_after_checkout_fails_turn_before_memory(self):
        await self.submit(self.physical("第一轮建立真实连接。", "keepalive:first"))
        self.assert_sent_commits(await self.wait_commits(1))
        events, held = [], False
        marker = "连接临界复现"
        server_idle = self.memory_server.server.config.timeout_keep_alive
        self.assertEqual(server_idle, 5)

        async def request_hook(request):
            if request.url.path != "/internal/v1/memory/select":
                return
            body = json.loads(request.content)
            if body.get("query_text") != marker:
                return

            async def transport_trace(name, info):
                nonlocal held
                entry = {"event": name, "at": time.monotonic()}
                error = info.get("exception")
                if error is not None:
                    entry.update(exception_type=type(error).__name__, exception=str(error))
                events.append(entry)
                if name == "http11.send_request_headers.started" and not held:
                    held = True
                    # Supported HTTPcore trace hook: hold AFTER pooled-connection checkout.
                    # The original Uvicorn server's real idle timer expires; no socket, timer,
                    # product method, payload, origin or HTTP response is replaced.
                    await asyncio.sleep(server_idle + 0.2)
                    events.append(
                        {"event": "diagnostic.resume_after_real_idle_timer", "at": time.monotonic()}
                    )

            request.extensions["trace"] = transport_trace

        client = self.core.memory.client.client
        client.event_hooks["request"].append(request_hook)
        try:
            await self.submit(self.physical(marker, "keepalive:second"))
            commits = await self.wait_commits(2)
        finally:
            client.event_hooks["request"].remove(request_hook)
        self.assertTrue(held)
        self.assertFalse(
            any(e["event"] == "connection.connect_tcp.started" for e in events),
            "Reproduction must use an existing pooled connection",
        )
        errors = [
            e
            for e in events
            if e.get("exception_type") in {"RemoteProtocolError", "ReadError", "WriteError"}
        ]
        self.assertTrue(errors, events)
        failed = self.core.store.list("turns")[1]
        self.assertEqual(
            (failed["phase"], failed["failure"], failed["model_calls"]),
            ("failed", "dependency_unavailable", 0),
        )
        self.assertEqual(commits[1]["request"]["delivery_state"], "failed")
        self.assertEqual(commits[1]["response"]["state"], "accepted")
        self.assertFalse(
            any(
                r["owner"] == "memory"
                and r["path"].endswith("/memory/select")
                and (r["request"] or {}).get("query_text") == marker
                for r in self.wire
            )
        )
        self.assertEqual(len(self.model_requests), 1)
        self.assertEqual(len(self.channel_requests), 1)
        # The new diagnostic assertion must explain the actual failure instead of indexing
        # a nonexistent model request or waiting longer for a terminal failed turn.
        with self.assertRaisesRegex(AssertionError, "dependency_unavailable"):
            self.assert_sent_commits(commits)
        self.check(
            "deterministic_idle_close_interleaving_reproduced",
            transport_events=events,
            server_keepalive_seconds=server_idle,
            httpx_library_default_keepalive_seconds=httpx.Limits().keepalive_expiry,
            injected_delay_seconds=server_idle + 0.2,
            failed_turn={
                k: failed.get(k)
                for k in ("id", "phase", "failure", "model_calls", "scope_version", "timings")
            },
            accepted_failed_event=commits[1]["request"],
            consume=commits[1]["response"],
            natural_failure_exception_available=False,
            verdict="defect reproduced, not fixed; original W5 cause inferred from matching boundary/trace",
        )
        await self.submit(self.physical("控制轮：新连接可以正常工作。", "keepalive:control"))
        all_commits = await self.wait_commits(3)
        self.assert_sent_commits([all_commits[2]])
        self.assertEqual(len(self.model_requests), 2)
        self.assertEqual(len(self.channel_requests), 2)
        self.check(
            "fresh_connection_control",
            phase=self.core.store.list("turns")[2]["phase"],
            model_calls=2,
            channel_calls=2,
        )
