"""TS-050 positive real subchains and explicit full-chain blockers; no fake Memory authority."""

import asyncio
import copy
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import asdict

import httpx
from services.platform.contracts import Fault as PlatformFault
from tianshu_companion.clients import Gateway, Origins, command
from tianshu_companion.contracts import Contracts, Fault
from ts050_support import CONTRACT, RUNTIME, RealChain, sample, sha


class TS050Subchains(RealChain):
    async def test_shipped_memory_tls_gap_and_explicit_core_ca(self):
        contracts = Contracts(CONTRACT)
        origins = Origins(
            contracts,
            {
                "nonebot": (
                    "platform",
                    self.service_client(self.platform_tls_url, "COMPANION_RESOLVER"),
                )
            },
        )
        envelope = command({"assertion_ref": self.origin}, "tls-check", time.time())
        context = await origins.resolve("nonebot", envelope, time.time())
        self.assertEqual(context["issuer"], "platform")
        self.assertIsNone(context["allowed_scope"]["person_id"])
        self.assertIsNone(context["allowed_scope"]["conversation_id"])
        self.check(
            "core_client_real_https_with_isolated_ca",
            authenticated_service=context["authenticated_service"],
            audience_service=context["audience_service"],
            no_system_trust_change=True,
        )
        # Same actual endpoint/certificate fails with the product's default client trust store.
        from tianshu_companion.clients import JsonService

        untrusted = JsonService(self.platform_tls_url, self.tokens["COMPANION_RESOLVER"])
        self.resources.append(untrusted.close)
        with self.assertRaises(Fault) as error:
            await untrusted.call(
                "/internal/v1/origins/resolve",
                {
                    "schema_version": 1,
                    "request_id": "tls-negative",
                    "assertion_ref": self.origin,
                },
            )
        self.assertEqual(error.exception.code, "dependency_unavailable")
        await self.start_memory(port_auth=False)
        body = sample("register")
        body.update(command=envelope, account=self.account)
        response = await self.client.post(
            self.memory_url + "/internal/v1/identity/register",
            json=body,
            headers={"Authorization": self.bearer("MEMORY_API")},
        )
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["code"], "dependency_unavailable")
        self.check(
            "shipped_memory_authenticator_tls_reproduction",
            http_status=503,
            no_transport_or_sslcontext_argument=True,
            resolves_via_application_port=False,
            model_calls=0,
        )

    async def test_real_platform_gateway_and_core_gateway_client_bytes(self):
        wire = b' { "messages":[{"role":"user","content":"TS050 bytes"}], "model":"fixture-text-model", "stream":false, "temperature":0.3 }\n'
        response = await self.native_request("ts050:bytes", body=wire)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.content, self.response_bytes)
        self.assertEqual(self.model_requests[0]["request_utf8"].encode(), wire)
        receipt = await self.receipt("ts050:bytes")
        self.assertEqual(receipt["outcome"], "succeeded")
        self.assertEqual(receipt["config_version"], 7)
        self.check(
            "native_request_and_response_bytes_preserved",
            request_sha256=sha(wire),
            response_sha256=sha(response.content),
            response_bytes=len(response.content),
            receipt=receipt,
        )
        # Actual companion Gateway adapter, HTTP JsonService, real gateway and real platform.
        client = Gateway(
            Contracts(CONTRACT),
            self.service_client(self.gateway_tls_url, "GATEWAY_API"),
        )
        messages = [
            {
                "role": "user",
                "content": "TS050 direct adapter; not a scheduled Core turn",
            }
        ]
        segments, route = await client.generate(
            {"id": "ts050:adapter-turn", "config_version": 7}, messages
        )
        self.assertEqual(segments, ["TS050 recorded reply"])
        forwarded = json.loads(self.model_requests[1]["request_utf8"])
        self.assertEqual(
            forwarded,
            {"messages": messages, "stream": False, "model": "fixture-text-model"},
        )
        self.assertEqual(route["outcome"], "succeeded")
        self.check(
            "companion_gateway_adapter_actual_https",
            segments=segments,
            receipt=route,
            scheduler_covered=False,
            forwarded_body=forwarded,
        )
        self.publish(8)
        self.assertEqual(
            (await self.native_request("ts050:old-version", version=7)).status_code, 200
        )
        count = len(self.model_requests)
        self.platform.models.revoke(self.bearer("ADMIN"), 7)
        denied = await self.native_request("ts050:revoked", version=7)
        self.assertEqual(denied.status_code, 403, denied.text)
        self.set_env("TS050_PLATFORM_ORIGIN", self.origin)
        wrong_purpose = await self.native_request("ts050:purpose", version=8)
        self.assertEqual(wrong_purpose.status_code, 403, wrong_purpose.text)
        self.assertEqual(len(self.model_requests), count)
        self.check(
            "fixed_old_version_then_revoke_and_dialogue_purpose_denial",
            calls_before_denials=count,
            calls_after_denials=len(self.model_requests),
            denial_statuses=[403, 403],
        )

    async def test_gateway_unknown_no_replay_across_real_process_restart(self):
        self.model_mode = "disconnect"
        response = await self.native_request("ts050:unknown")
        self.assertGreaterEqual(response.status_code, 500)
        self.assertEqual(response.json()["execution_state"], "unknown")
        receipt = await self.receipt("ts050:unknown")
        self.assertEqual(receipt["outcome"], "unknown")
        self.assertEqual(len(self.model_requests), 1)
        repeat = await self.native_request("ts050:unknown")
        self.assertEqual(repeat.status_code, 409, repeat.text)
        self.assertEqual(len(self.model_requests), 1)
        await self.runners[id(self.gateway)].cleanup()
        settings_path = self.directory / "gateway-settings.json"
        settings_path.write_text(json.dumps(asdict(self.gateway_settings)), "utf-8")
        with socket.socket() as reserved:
            reserved.bind(("127.0.0.1", 0))
            port = reserved.getsockname()[1]
        self.ports.append(port)
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                "-m",
                "tianshu_gateway",
                "--settings",
                str(settings_path),
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--local-test",
            ],
            cwd=RUNTIME / "sources/model-gateway",
            env=os.environ.copy(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

        async def stop():
            if process.poll() is None:
                process.terminate()
                try:
                    await asyncio.to_thread(process.wait, 5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    await asyncio.to_thread(process.wait, 5)

        self.resources.append(stop)
        self.gateway_url = f"http://127.0.0.1:{port}"
        async with asyncio.timeout(8):
            while True:
                self.assertIsNone(process.poll(), "Gateway CLI exited during startup")
                try:
                    restored = await self.receipt("ts050:unknown")
                    break
                except httpx.ConnectError:
                    await asyncio.sleep(0.05)
        self.assertEqual(restored, receipt)
        repeat = await self.native_request("ts050:unknown")
        self.assertEqual(repeat.status_code, 409, repeat.text)
        self.assertEqual(len(self.model_requests), 1)
        self.check(
            "gateway_unknown_persisted_no_duplicate_upstream",
            receipt=restored,
            replay_statuses=[409, 409],
            upstream_calls=1,
            real_new_process=True,
            core_or_channel_unknown_covered=False,
        )
        await stop()
        self.assertIsNotNone(process.poll())


class TS050PartialCore(RealChain):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.start_memory(port_auth=True)

    async def test_two_active_preparation_slots_t3_waits_real_memory_response(self):
        await self.start_core(silence_ms=0)
        self.select_gate = asyncio.Event()

        # Ensure a failed assertion releases held real requests before ASGI shutdown.
        async def release():
            self.select_gate.set()

        self.resources.append(release)
        for index in range(1, 4):
            await self.ingest(self.ingest_body(f"overlap-{index}", f"合成短句 {index}"))
        await self.eventually(lambda: self.select_arrivals == 2)
        state = self.core_state()
        self.assertEqual([t["phase"] for t in state["turns"]], ["preparing", "preparing", "queued"])
        self.assertEqual(sum(t["model_calls"] for t in state["turns"]), 0)
        self.check(
            "two_active_preparation_slots",
            core=state,
            held_actual_memory_http_requests=2,
            t2_generation_overlap_covered=False,
            t3_waits_for_capacity=True,
        )
        self.select_gate.set()
        await self.eventually(lambda: self.select_arrivals >= 3)
        self.clock.advance(5.1)
        await self.eventually(
            lambda: all(t["phase"] == "failed" for t in self.core.store.list("turns"))
        )
        self.assertEqual(len(self.model_requests), 0)
        self.assertEqual(len(self.channel_requests), 0)
        self.check("all_turns_fail_closed_after_real_memory_503", core=self.core_state())

    async def test_first_identity_mapping_w0_and_missing_source_gate(self):
        await self.start_core(silence_ms=0)
        before = await self.resolved_scope()
        self.assertIsNone(before["person_id"])
        self.assertIsNone(before["conversation_id"])
        body = self.ingest_body("first", "我叫小明")
        first = await self.ingest(body)
        after = await self.resolved_scope()
        self.assertEqual(after["person_id"], first["person_id"])
        self.assertEqual(after["conversation_id"], first["conversation_id"])
        repeated = await self.ingest(body)
        self.assertTrue(repeated["deduplicated"])
        self.assertEqual(repeated["receipt_id"], first["receipt_id"])
        self.assertEqual(len(self.core.store.list("inbox")), 1)
        await self.eventually(
            lambda: any(r["path"].endswith("/memory/select") for r in self.memory_requests)
        )
        self.clock.advance(5.1)
        await self.eventually(lambda: self.core.store.list("turns")[0]["phase"] == "failed")
        state = self.core_state()
        self.assertEqual(state["turns"][0]["bundle"]["close_reason"], "immediate_submit")
        self.assertEqual(state["turns"][0]["model_calls"], 0)
        self.assertIsNone(state["turns"][0]["scope_version"])
        self.assertEqual(state["outbox"][0]["state"], "blocked_scope")
        self.assertEqual(state["outbox"][0]["attempts"], 0)
        self.assertEqual(state["replies"], 0)
        self.assertEqual(len(self.model_requests), 0)
        self.assertEqual(len(self.channel_requests), 0)
        self.check(
            "actual_identity_and_core_receipt_mapping",
            before=before,
            after=after,
            receipt=first,
            duplicate_receipt=repeated,
            auth_path="coordinator-approved same-process Platform application port",
        )
        self.check("w0_failed_closed_source_unconfigured", core=state, full_l0=False)
        health = await self.client.get(self.memory_url + "/health")
        self.assertEqual(health.status_code, 503)
        self.assertEqual(health.json()["source_backend"], "unconfigured")
        self.check("memory_health_truthful", health=health.json())

    async def test_w5_short_sentences_deadline_restart_and_no_false_generation(self):
        await self.start_core(silence_ms=5000)
        first = await self.ingest(self.ingest_body("name-1", "我叫"))
        original = self.core.store.list("collections")[0]
        self.clock.advance(2)
        second = await self.ingest(self.ingest_body("name-2", "小明"))
        self.assertEqual(first["collection_id"], second["collection_id"])
        current = self.core.store.list("collections")[0]
        self.assertEqual(current["revision"], 2)
        self.core.timer(original["id"], original["revision"], original["deadline"])
        self.assertEqual(self.core.store.list("turns"), [])
        self.clock.value = current["deadline"] - 0.001
        await self.core.tick()
        self.assertEqual(self.core.store.list("turns"), [])
        await self.core_server.close()
        await self.start_core(silence_ms=5000)
        restored = self.core.store.list("collections")[0]
        self.assertEqual(restored, current)
        self.clock.value = current["deadline"]
        self.core.timer(current["id"], current["revision"], current["deadline"])
        await self.eventually(lambda: len(self.core.store.list("turns")) == 1)
        turn = self.core.store.list("turns")[0]
        self.assertEqual(
            [m["parts"][0]["text"] for m in turn["bundle"]["messages"]],
            ["我叫", "小明"],
        )
        self.assertEqual(turn["bundle"]["close_reason"], "silence")
        await self.eventually(
            lambda: (
                len([r for r in self.memory_requests if r["path"].endswith("/memory/select")]) > 0
            )
        )
        self.clock.advance(5.1)
        await self.eventually(lambda: self.core.store.list("turns")[0]["phase"] == "failed")
        failed = self.core_state()
        await self.core_server.close()
        await self.start_core(silence_ms=5000)
        self.assertEqual(self.core_state(), failed)
        repeat_body = self.ingest_body("name-3", "我叫什么")
        await self.ingest(repeat_body)
        self.check(
            "w5_complete_group_and_persistent_collection",
            stale_timer_ignored=True,
            before_deadline_turn_count=0,
            exact_deadline_messages=["我叫", "小明"],
            restart="ASGI lifecycle and Core SQLite owner reopen; not process crash",
            core=failed,
            ordinary_name_followup_prompt="blocked_before_model_by_missing_source_authority",
        )
        self.assertEqual(len(self.model_requests), 0)
        self.assertEqual(len(self.channel_requests), 0)

    async def test_zero_full_budget_revise_forget_and_commit_all_fail_closed(self):
        await self.start_core(silence_ms=5000)
        first = await self.ingest(self.ingest_body("boundary", "上次的完整咖啡条件"))
        scope = await self.resolved_scope()
        for budget in ({"tokens": 0, "bytes": 0}, {"tokens": 2048, "bytes": 8192}):
            body = sample("select")
            body.update(
                query={
                    "schema_version": 1,
                    "request_id": "ts050:select",
                    "origin": {"assertion_ref": self.origin},
                },
                requested_scope=scope,
                query_text="上次的咖啡条件",
                budget=budget,
            )
            response = await self.client.post(
                self.memory_url + "/internal/v1/memory/select",
                json=body,
                headers={"Authorization": self.bearer("MEMORY_API")},
            )
            self.assertEqual(response.status_code, 503, response.text)
            self.assertNotIn("selected_units", response.json())
            self.check("select_source_gate", budget=budget, response=response.json())
        for kind in ("correct", "forget"):
            body = sample("revise")
            body["command"] = command({"assertion_ref": self.origin}, "ts050:" + kind, self.clock())
            body["revision_kind"] = kind
            if kind == "forget":
                body["replacement_statement"] = None
            response = await self.client.post(
                self.memory_url + "/internal/v1/memory/revise",
                json=body,
                headers={"Authorization": self.bearer("MEMORY_API")},
            )
            self.assertEqual(response.status_code, 503, response.text)
            self.check(
                "revision_source_gate",
                kind=kind,
                response=response.json(),
                existing_memory_mutation_covered=False,
            )
        event = sample("turn_committed")
        event.update(scope=scope, conversation_id=scope["conversation_id"])
        event["sources"] = [self.core.store.list("inbox")[0]["source"]]
        # Schema-valid negative probe, deliberately NOT asserted to be a Core committed event.
        for _ in range(2):
            response = await self.client.post(
                self.memory_url + "/internal/v1/memory/turn-commits",
                json=event,
                headers={"Authorization": self.bearer("MEMORY_API")},
            )
            self.assertEqual(response.status_code, 503, response.text)
            self.assertNotIn("candidate_job_ref", response.json())
        self.check(
            "candidate_gate_no_false_receipt",
            input="schema-valid diagnostic probe, not Core event",
            statuses=[503, 503],
            candidate_dedup_positive_covered=False,
            real_ingest_receipt=first,
        )
        self.assertEqual(len(self.model_requests), 0)

    async def test_platform_partial_authority_revision_tombstone_and_revocation(self):
        await self.start_core(silence_ms=5000)
        await self.ingest(self.ingest_body("source-1", "旧输入"))
        scope = await self.resolved_scope()
        event = sample("turn_committed")
        event.update(
            scope=scope,
            conversation_id=scope["conversation_id"],
            scope_version=913,
            input_revision=77,
        )
        source = self.core.store.list("inbox")[0]["source"]
        event["sources"] = [source]
        partial = self.platform.origins.verify_current_sources(self.bearer("COMPANION"), event)
        self.assertEqual(
            partial,
            {
                "current_sources": True,
                "archive_verified": False,
                "scope_version_verified": False,
            },
        )
        self.check(
            "platform_partial_flags_not_owner_facts",
            result=partial,
            input="diagnostic probe, not Core commit",
            unchecked_probe_scope_version=913,
            unchecked_probe_input_revision=77,
        )
        archived = copy.deepcopy(event)
        archived["sources"][0].update(
            archive_state="archived", locator="archive:ts050-diagnostic-probe"
        )
        with self.assertRaises(PlatformFault) as error:
            self.platform.origins.verify_current_sources(self.bearer("COMPANION"), archived)
        self.assertEqual(error.exception.status, 503)
        await self.ingest(self.ingest_body("source-1", "修订输入", revision=2, kind="edit"))
        with self.assertRaises(PlatformFault) as error:
            self.platform.origins.verify_current_sources(self.bearer("COMPANION"), event)
        self.assertEqual(error.exception.code, "scope_changed")
        await self.ingest(self.ingest_body("source-1", "", revision=3, kind="retract"))
        with self.assertRaises(PlatformFault) as error:
            self.platform.origins.verify_current_sources(self.bearer("COMPANION"), event)
        self.assertEqual(error.exception.code, "scope_changed")
        self.check(
            "old_platform_source_rejected",
            archived_status=503,
            edit_code="scope_changed",
            tombstone_code="scope_changed",
            memory_invalidation_covered=False,
        )
        self.platform.origins.revoke(self.bearer("ADMIN"), "entry", "chat")
        denied = await self.client.post(
            self.core_server.url + "/internal/v1/conversation/ingest",
            json=self.ingest_body("revoked", "不能发出"),
            headers={"Authorization": self.bearer("CORE_INGRESS")},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        self.assertEqual(len(self.core.store.list("inbox")), 3)
        self.check(
            "real_entry_revocation_blocks_core_ingress",
            status=403,
            inbox_count=3,
            model_calls=0,
            channel_calls=0,
        )
