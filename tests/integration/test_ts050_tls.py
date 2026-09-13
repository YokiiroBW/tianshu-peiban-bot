"""Targeted TS-032 integration: shipped Memory factory -> actual Platform HTTPS resolver."""

import copy
import json
import os
import time

from tianshu_companion.clients import command
from ts050_support import RealChain, ca_contexts, sample


class TS050MemoryHTTPS(RealChain):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        await self.start_memory(port_auth=False, configured_tls=True)
        self.assertIsNone(self.memory_service.source_authority)
        self.assertIsNone(self.memory_contracts.profile_version)
        self.trace.update(
            slice="memory_https_followup",
            memory_factory="tianshu_memory.app.configured_app",
            memory_authenticator="shipped Authenticator; no B resolver adapter",
            profile_contract_loaded=False,
        )

    def write_caller(self, caller):
        self.auth_config["callers"]["companion"] = copy.deepcopy(caller)
        self.auth_path.write_text(json.dumps(self.auth_config), "utf-8")

    def identity_body(self, *, register=False):
        if register:
            return {
                "command": command({"assertion_ref": self.origin}, "tls:register", time.time()),
                "account": self.account,
            }
        return {
            "query": {
                "schema_version": 1,
                "request_id": "tls:resolve",
                "origin": {"assertion_ref": self.origin},
            },
            "account": self.account,
        }

    async def memory_post(self, path, body, *, token=None):
        return await self.client.post(
            self.memory_url + "/internal/v1/" + path,
            json=body,
            headers={"Authorization": self.bearer("MEMORY_API") if token is None else token},
        )

    async def test_configured_https_first_identity_mapping_and_w0_gate(self):
        await self.start_core(silence_ms=0)
        before = await self.resolved_scope()
        self.assertIsNone(before["person_id"])
        self.assertIsNone(before["conversation_id"])
        body = self.ingest_body("tls:first", "我叫小明")
        start = len(self.platform_requests)
        first = await self.ingest(body)
        # No harness resolve calls between start and this point: these are product HTTP requests.
        resolver_calls = [
            r for r in self.platform_requests[start:] if r["credential_role"] == "MEMORY_RESOLVER"
        ]
        self.assertGreaterEqual(len(resolver_calls), 2)
        for request in resolver_calls:
            self.assertEqual((request["transport"], request["status"]), ("https", 200))
            self.assertEqual(request["resolved"]["authenticated_service"], "companion")
            self.assertEqual(request["resolved"]["audience_service"], "memory")
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
        self.assertEqual(state["turns"][0]["model_calls"], 0)
        self.assertIsNone(state["turns"][0]["scope_version"])
        self.assertEqual(state["outbox"][0]["state"], "blocked_scope")
        self.assertEqual(state["outbox"][0]["attempts"], 0)
        self.assertEqual(state["replies"], 0)
        self.assertEqual(len(self.model_requests), 0)
        self.assertEqual(len(self.channel_requests), 0)
        self.check(
            "shipped_factory_real_platform_https_identity",
            before=before,
            after=after,
            receipt=first,
            product_resolver_requests=resolver_calls,
            remaining_application_ports=["prepare_mapping", "confirm_mapping", "observe_source"],
        )
        self.check(
            "w0_source_none_fails_closed_after_real_https_identity",
            core=state,
            full_l0=False,
            duplicate_receipt=repeated,
        )

    async def test_untrusted_ca_default_trust_and_false_rejected_then_recovery(self):
        original = copy.deepcopy(self.auth_config["callers"]["companion"])
        other_directory = self.directory / "other-ca"
        other_directory.mkdir()
        ca_contexts(other_directory)
        cases = [
            ("default_trust", {k: v for k, v in original.items() if k != "issuer_ca_file"}),
            ("untrusted_ca", {**original, "issuer_ca_file": str(other_directory / "ca.pem")}),
            ("false_is_not_tls_bypass", {**original, "issuer_ca_file": False}),
        ]
        for label, caller in cases:
            with self.subTest(label):
                self.write_caller(caller)
                count = len(self.platform_requests)
                response = await self.memory_post(
                    "identity/register", self.identity_body(register=True)
                )
                self.assertEqual(response.status_code, 503, response.text)
                self.assertEqual(response.json()["code"], "dependency_unavailable")
                self.assertEqual(len(self.platform_requests), count)
                self.check(
                    label,
                    http_status=503,
                    resolver_http_requests=0,
                    note="false rejected before TLS; other cases fail TLS trust",
                )
        self.write_caller(original)
        start = len(self.platform_requests)
        registered = await self.memory_post("identity/register", self.identity_body(register=True))
        self.assertEqual(registered.status_code, 200, registered.text)
        self.assertTrue(registered.json()["created"])
        resolved = await self.memory_post("identity/resolve", self.identity_body())
        self.assertEqual(resolved.status_code, 200, resolved.text)
        self.assertEqual(resolved.json()["person_id"], registered.json()["person_id"])
        calls = self.platform_requests[start:]
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(r["transport"] == "https" and r["status"] == 200 for r in calls))
        self.check(
            "trusted_ca_restored_through_live_deployment_config",
            register=registered.json(),
            resolve=resolved.json(),
            resolver_http_requests=calls,
        )

    async def test_real_platform_identity_boundaries_expiry_and_revocation(self):
        original = copy.deepcopy(self.auth_config["callers"]["companion"])
        registered = await self.memory_post("identity/register", self.identity_body(register=True))
        self.assertEqual(registered.status_code, 200, registered.text)
        count = len(self.platform_requests)
        denied = await self.memory_post(
            "identity/resolve", self.identity_body(), token="Bearer synthetic-invalid-api"
        )
        self.assertEqual(denied.status_code, 401, denied.text)
        self.assertEqual(len(self.platform_requests), count)
        self.check("invalid_memory_api_credential", http_status=401, resolver_http_requests=0)
        config_cases = [
            ("wrong_resolver_credential", {"issuer_token": "synthetic-invalid-resolver-token"}),
            ("wrong_receiver_and_caller", {"issuer_token": self.tokens["COMPANION_RESOLVER"]}),
            ("wrong_issuer", {"issuer": "nonebot"}),
            ("actor_not_allowed", {"allowed_actors": ["actor-other"]}),
        ]
        for label, change in config_cases:
            with self.subTest(label):
                self.write_caller({**original, **change})
                start = len(self.platform_requests)
                response = await self.memory_post("identity/resolve", self.identity_body())
                self.assertEqual(response.status_code, 403, response.text)
                calls = self.platform_requests[start:]
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["transport"], "https")
                self.check(label, http_status=403, resolver_http_requests=calls)
        self.write_caller(original)
        for label in ("configuration_origin_wrong_purpose", "unissued_origin", "wrong_account"):
            with self.subTest(label):
                body = self.identity_body()
                if label == "wrong_account":
                    body["account"] = {
                        **self.account,
                        "immutable_account_id": "synthetic-other-person",
                    }
                else:
                    body["query"]["origin"]["assertion_ref"] = (
                        os.environ["TS050_PLATFORM_ORIGIN"]
                        if label.startswith("configuration")
                        else "synthetic-unissued-origin"
                    )
                start = len(self.platform_requests)
                response = await self.memory_post("identity/resolve", body)
                self.assertEqual(response.status_code, 403, response.text)
                self.check(
                    label, http_status=403, resolver_http_requests=self.platform_requests[start:]
                )
        original_clock = self.platform.origins.clock
        try:
            self.platform.origins.clock = lambda: original_clock() + 301
            response = await self.memory_post("identity/resolve", self.identity_body())
            self.assertEqual(response.status_code, 403, response.text)
            self.check(
                "expired_real_platform_origin",
                http_status=403,
                clock_fault_injection="Platform issuer clock +301 seconds",
            )
        finally:
            self.platform.origins.clock = original_clock
        restored = await self.memory_post("identity/resolve", self.identity_body())
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()["person_id"], registered.json()["person_id"])
        self.platform.origins.revoke(self.bearer("ADMIN"), "entry", "chat")
        revoked = await self.memory_post("identity/resolve", self.identity_body())
        self.assertEqual(revoked.status_code, 403, revoked.text)
        await self.start_core(silence_ms=0)
        core_denial = await self.client.post(
            self.core_server.url + "/internal/v1/conversation/ingest",
            json=self.ingest_body("tls:revoked", "拒绝的合成输入"),
            headers={"Authorization": self.bearer("CORE_INGRESS")},
        )
        self.assertEqual(core_denial.status_code, 403, core_denial.text)
        self.assertEqual(self.core.store.list("inbox"), [])
        self.check(
            "real_entry_revocation_denies_memory_and_core",
            memory_status=403,
            core_status=403,
            core_inbox_count=0,
        )

    async def test_source_none_remains_503_after_shipped_https_identity(self):
        await self.start_core(silence_ms=5000)
        await self.ingest(self.ingest_body("tls:scope", "上次的完整条件"))
        scope = await self.resolved_scope()
        for budget in ({"tokens": 0, "bytes": 0}, {"tokens": 2048, "bytes": 8192}):
            body = sample("select")
            body.update(
                query=self.identity_body()["query"],
                requested_scope=scope,
                query_text="上次的完整条件",
                budget=budget,
            )
            start = len(self.platform_requests)
            response = await self.memory_post("memory/select", body)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertNotIn("selected_units", response.json())
            calls = self.platform_requests[start:]
            self.assertEqual([(r["transport"], r["status"]) for r in calls], [("https", 200)])
            self.check(
                "source_none_select",
                budget=budget,
                response=response.json(),
                actual_origin_resolution=calls,
            )
        for kind in ("correct", "forget"):
            body = sample("revise")
            body["command"] = command({"assertion_ref": self.origin}, "tls:" + kind, time.time())
            body["revision_kind"] = kind
            if kind == "forget":
                body["replacement_statement"] = None
            response = await self.memory_post("memory/revise", body)
            self.assertEqual(response.status_code, 503, response.text)
            self.check("source_none_revision", kind=kind, response=response.json())
        event = sample("turn_committed")
        event.update(scope=scope, conversation_id=scope["conversation_id"])
        event["sources"] = [self.core.store.list("inbox")[0]["source"]]
        response = await self.memory_post("memory/turn-commits", event)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertNotIn("candidate_job_ref", response.json())
        self.check(
            "source_none_consume",
            response=response.json(),
            input="schema-valid negative probe, not an authoritative Core committed_event",
        )
        health = await self.client.get(self.memory_url + "/health")
        self.assertEqual(health.status_code, 503, health.text)
        self.assertEqual(health.json()["source_backend"], "unconfigured")
        self.assertIsNone(self.memory_service.source_authority)
        self.assertEqual(len(self.model_requests), 0)
        self.assertEqual(len(self.channel_requests), 0)
        self.check("source_backend_stays_unconfigured", health=health.json(), full_l0=False)
