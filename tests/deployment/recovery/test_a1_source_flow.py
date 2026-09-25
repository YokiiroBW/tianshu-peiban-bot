"""Offline boundary checks for the parameterized source entry."""

import json
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from ops.recovery import a1_source_flow
from ops.recovery.safety import RecoveryError


def put(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


class SourceFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        parent = Path(self.temp.name)
        scope = parent / "scope-a1-new-offline"
        source = scope / "deployments/source"
        reports = source / "reports/a1-new-offline"
        reports.mkdir(parents=True)
        put(scope / ".recovery-scope.json", {"scope_id": str(uuid.uuid4())})
        put(source / "deployment.json", {"project_name": "tianshu-qa-a1-source-new-offline"})
        for label in ("forget-success-v3", "source-revoke-success-v3"):
            put(reports / f"register-{label}.json", {"entry_id": "web-input-source",
                "input": {"message_key": {"revision": 1}, "kind": "message",
                          "parts": [{"kind": "text", "text": "fictional"}]}})
        self.source = source
        self.reports = reports
        self.config = {"scope_parent": str(parent), "scope_name": scope.name,
            "scope_id": json.loads((scope / ".recovery-scope.json").read_text())["scope_id"],
            "source_project": "tianshu-qa-a1-source-new-offline",
            "run_label": "a1-new-offline", "docker": str(parent / "docker"),
            "python": str(parent / "python"), "code_root": str(parent / "tooling")}
        Path(self.config["code_root"]).mkdir()

    def test_scope_binding_and_one_attempt_marker(self):
        runner = a1_source_flow.Source(self.config)
        self.assertEqual(runner.root, self.source)
        runner.save("a1-source-attempt.json", {"scope_id": self.config["scope_id"]})
        with self.assertRaises(FileExistsError):
            runner.save("a1-source-attempt.json", {})
        put(self.source / ".recovery-registration.json", {"scope_id": self.config["scope_id"]})
        with self.assertRaises(RecoveryError):
            a1_source_flow.Source(self.config)

    def test_unknown_requires_exactly_two_closed_unknown_replies(self):
        history = [{"turn": {"turn_sequence": i, "turn_id": f"turn:{i}",
                    "phase": "closed_unknown", "delivery_state": "unknown"},
                    "replies": [{"reply_id": f"reply:{i}", "state": "unknown"}]}
                   for i in (1, 2)]
        result = {"http_status": 200, "body": {"history": history}}
        self.assertEqual(len(a1_source_flow._unknown(result)["turns"]), 2)
        history[1]["replies"].append({"reply_id": "duplicate", "state": "unknown"})
        with self.assertRaises(RecoveryError):
            a1_source_flow._unknown(result)

    def test_product_api_uses_only_configured_docker_and_private_receipt(self):
        runner = a1_source_flow.Source(self.config)
        response = b'{"http_status":410,"body":{"code":"forbidden"}}'
        with patch.object(runner, "command", return_value=response) as call:
            value = runner.api("gateway", "https://platform.internal:8443/internal/v1/model-config/snapshot",
                "TS_GATEWAY_PLATFORM", {"config_version": 3}, "model-revoked")
        self.assertEqual(value["http_status"], 410)
        self.assertEqual(call.call_args.args[0][0], self.config["docker"])
        self.assertEqual(json.loads((self.reports / "model-revoked.json").read_text()), value)

    def test_command_failure_keeps_private_diagnostic(self):
        runner = a1_source_flow.Source(self.config)
        class Completed:
            returncode, stdout, stderr = 1, b"", b"fixture failure"
        with patch("ops.recovery.a1_source_flow.subprocess.run", return_value=Completed()):
            with self.assertRaises(RecoveryError):
                runner.command([self.config["docker"], "ps"])
        diagnostic = json.loads((self.reports / "command-error-private.json").read_text())
        self.assertEqual(diagnostic["stderr_tail"], "fixture failure")

    def test_full_ordered_source_flow_with_offline_product_fixture(self):
        """Exercise every orchestration branch without a NAS, database, or model."""
        runner = a1_source_flow.Source(self.config)
        scope = self.source.parent.parent
        put(scope / "inputs/model-publication-template.json", {
            "config_version": 1, "status": "published", "providers": [{
                "provider_id": "provider-synthetic", "model_id": "synthetic-recorded-text",
                "capability_verification": "fixture_only",
                "base_url": "https://gateway.internal:9443/v1"}]})
        put(self.source / "config/platform/settings.json", {
            "principals": {"gateway": {"config_versions": [1, 3]}}})
        put(self.source / "config/gateway/settings.json", {"clients": [{
            "service": "companion", "provider_id": "provider-synthetic",
            "internal": True, "allowed_versions": [1, 3]}]})
        put(self.source / "config/companion/settings.json", {
            "automatic_memory_candidates": True, "config_version": 3})
        (self.source / "tools").mkdir()
        (self.source / "tools/container_probe.py").write_text("fixture code")
        observed = []
        exact_scope = {"actor_id": "actor:a1-source", "person_id": "person:fixture",
                       "audience": "self_private", "conversation_id": "conv:fixture"}
        account = {"namespace": "web", "immutable_account_id": "fixture"}
        history = [{"turn": {"turn_sequence": index, "turn_id": f"turn:{index}",
                    "phase": "closed_unknown", "delivery_state": "unknown"},
                    "replies": [{"reply_id": f"reply:{index}", "state": "unknown"}]}
                   for index in (1, 2)]
        events = [{"turn_id": f"turn:{index}", "phase": "closed_unknown",
                   "committed_event": {"turn_sequence": index, "reality": "fictional",
                       "scope": exact_scope, "sources": [f"source:{index}"],
                       "event_id": f"event:{index}"}}
                  for index in (1, 2)]

        def compose(_self, owner, *args, **_kwargs):
            observed.append(("compose", owner, args))
            if "migrate-profiles" in args or "migrate-sources" in args:
                name = "profiles" if "migrate-profiles" in args else "sources"
                return json.dumps({"schema": 2 if name == "profiles" else 3,
                    "backup": f"/srv/tianshu/first-install.pre-{name}.sqlite",
                    "unverified_admissions": 0}).encode()
            return b""

        def platform(_self, action, name, document):
            observed.append(("platform", action, name))
            if action == "register-input":
                return {"assertion_ref": "source-input:fixture"}
            if action.startswith("revoke-"):
                return {"revoked": True}
            return {"outcomes": []}

        def dispatch(_self, label):
            observed.append(("dispatch", label))
            accepted = {"state": "accepted", "admission": {"scope": exact_scope},
                        "receipt": {"collection_key": {"author": account}}}
            return "source-input:" + label, {"conversation_id": "conv:fixture"}, accepted

        def memory(_self, stage, _origin, _scope, expected):
            observed.append(("memory", stage))
            return {key: [{"record_id": key + ":record"}] if count else []
                    for key, count in expected.items()}

        def reader(_self, _method, name, _request):
            observed.append(("reader", name))
            return {"http_status": 200, "body": {"turns": events}}

        def api(_self, _owner, _url, _env, _body, name, **_kwargs):
            observed.append(("api", name))
            if name == "model-revoked-api-result":
                return {"http_status": 410, "body": {"code": "forbidden"}}
            return {"http_status": 503, "body": {"status": "unknown"}}

        def commit(_root, path, **_kwargs):
            return {"state": "committed", "record_ids": [
                "record:" + row["field_key"] for row in json.loads(path.read_text())["drafts"]]}

        def identity(_self):
            put(self.source / "reports/runtime-identity.json", {"fixture": True})

        def register(_self):
            put(self.source / ".recovery-registration.json",
                {"scope_id": self.config["scope_id"]})

        class Offline:
            returncode, stdout = 1, b"container_probe_failed\n"

        with patch.object(a1_source_flow.Source, "compose", compose), \
             patch.object(a1_source_flow.Source, "initial_permissions", lambda *_args: None), \
             patch.object(a1_source_flow.Source, "wait_owners", lambda *_args: None), \
             patch.object(a1_source_flow.Source, "platform", platform), \
             patch.object(a1_source_flow.Source, "origin", lambda _self, name, entry=None: "origin:" + name), \
             patch.object(a1_source_flow.Source, "dispatch", dispatch), \
             patch.object(a1_source_flow.Source, "memory", memory), \
             patch.object(a1_source_flow.Source, "reader", reader), \
             patch.object(a1_source_flow.Source, "web", lambda _self, *args: {"http_status": 200, "body": {"history": history}}), \
             patch.object(a1_source_flow.Source, "api", api), \
             patch.object(a1_source_flow.Source, "runtime_identity", identity), \
             patch.object(a1_source_flow.Source, "register", register), \
             patch("ops.recovery.a1_source_flow.subprocess.run", return_value=Offline()), \
             patch("ops.recovery.a1_clone_prepare._evidence", return_value={"usage": {"counts": {"total": 4}}}), \
             patch("ops.recovery.a1_source_flow.time.sleep"), \
             patch.multiple(a1_source_flow.product,
                bind_synthetic_classification=lambda *_args: None,
                enable_synthetic_memory_candidates=lambda *_args: None,
                bind_synthetic_model_version=lambda *_args: None,
                set_gateway_origin=lambda *_args: None,
                bind_memory_scopes=lambda *_args: {"status": "bound"},
                start_synthetic_model=lambda *_args, **_kwargs: {"status": "ready", "fixture_only": True},
                publish_synthetic_config=lambda *_args, **_kwargs: {"action": "publish", "receipt": {"config_version": 3, "published": True}},
                trusted_memory_commit=commit,
                run_local_user_action=lambda *_args, **_kwargs: {"consumed": False, "binding_version": 1},
                post_memory_revision=lambda *_args, **_kwargs: {"http_status": 200, "body": {"authoritative_state": "tombstoned"}},
                diagnose_platform_publication=lambda *_args, **_kwargs: {"valid": True},
                run_platform_cli=lambda *_args, **_kwargs: {"action": "publish", "receipt": {"config_version": 4, "published": True}},
                _update_bundle=lambda *_args: None):
            result = runner.run()
        self.assertEqual(result["status"], "source_complete")
        self.assertEqual([row[1] for row in observed if row[0] == "dispatch"],
                         ["forget-success-v3", "source-revoke-success-v3"])
        self.assertEqual([row[1] for row in observed if row[0] == "memory"],
                         ["baseline", "after-forget", "after-source-revoke", "after-retract"])
        self.assertEqual(json.loads((self.reports / "gateway-usage-readback.json").read_text()),
                         {"counts": {"total": 4}})
        self.assertTrue((self.reports / "a1-source-complete.json").is_file())


if __name__ == "__main__":
    unittest.main()
