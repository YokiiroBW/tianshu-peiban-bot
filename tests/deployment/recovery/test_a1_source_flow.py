"""Offline boundary checks for the parameterized source entry."""

import copy
from contextlib import nullcontext
import json
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ops.recovery import a1_source_flow
from ops.recovery.safety import RecoveryError, read_json


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

    def test_initial_wait_rejects_failed_turn_without_final_read(self):
        runner = a1_source_flow.Source(self.config)
        history = [{"turn": {"turn_sequence": index, "turn_id": f"turn:{index}",
                    "phase": "closed_unknown" if index == 1 else "failed",
                    "delivery_state": "unknown" if index == 1 else "failed"},
                    "replies": [{"reply_id": "reply:1", "state": "unknown"}]
                    if index == 1 else []} for index in (1, 2)]
        snapshot = {"http_status": 200, "body": {"conversation_id": "conv:fixture",
            "history": history, "active_turns": [], "collectors": []}}
        with patch.object(runner, "web", return_value=snapshot) as web:
            with self.assertRaisesRegex(RecoveryError,
                    "a1_source_initial_turn_terminal_mismatch"):
                runner.await_initial_unknown("origin:fixture", "conv:fixture")
        self.assertEqual(web.call_count, 1)
        self.assertEqual(web.call_args.args[0], "web-initial-wait-001")

    def test_initial_wait_times_out_on_unfinished_turn(self):
        runner = a1_source_flow.Source(self.config)
        history = [{"turn": {"turn_sequence": index, "turn_id": f"turn:{index}",
                    "phase": "preparing", "delivery_state": "not_started"},
                    "replies": []} for index in (1, 2)]
        snapshot = {"http_status": 200, "body": {"conversation_id": "conv:fixture",
            "history": history, "active_turns": [{"turn_sequence": 2}],
            "collectors": []}}
        tick = [0.0]
        def pause(seconds):
            tick[0] += seconds
        with patch.object(runner, "web", return_value=snapshot) as web:
            with self.assertRaisesRegex(RecoveryError,
                    "a1_source_initial_turn_timeout"):
                runner.await_initial_unknown("origin:fixture", "conv:fixture",
                    timeout_seconds=2, clock=lambda: tick[0], pause=pause)
        self.assertEqual(web.call_count, 3)
        self.assertEqual(tick[0], 2)

    def test_initial_web_reads_have_distinct_request_ids(self):
        runner = a1_source_flow.Source(self.config)
        snapshot = {"http_status": 200, "body": {"conversation_id": "conv:fixture",
            "history": [], "active_turns": [], "collectors": []}}
        with patch.object(a1_source_flow.product, "read_companion_web_snapshot",
                          return_value=snapshot):
            runner.web("web-initial-wait-001", "origin:fixture", "conv:fixture")
            runner.web("web-snapshot-initial", "origin:fixture", "conv:fixture")
        first = read_json(self.reports / "web-initial-wait-001-request.json")
        final = read_json(self.reports / "web-snapshot-initial-request.json")
        self.assertNotEqual(first["query"]["request_id"],
                            final["query"]["request_id"])

    def test_initial_wait_rejects_changed_final_snapshot(self):
        runner = a1_source_flow.Source(self.config)
        history = [{"turn": {"turn_sequence": index, "turn_id": f"turn:{index}",
                    "phase": "closed_unknown", "delivery_state": "unknown"},
                    "replies": [{"reply_id": f"reply:{index}", "state": "unknown"}]}
                   for index in (1, 2)]
        complete = {"http_status": 200, "body": {"conversation_id": "conv:fixture",
            "history": history, "active_turns": [], "collectors": []}}
        changed = copy.deepcopy(complete)
        changed["body"]["collectors"] = [{"state": "processing"}]
        with patch.object(runner, "web", side_effect=[complete, changed]) as web:
            with self.assertRaisesRegex(RecoveryError,
                    "a1_source_initial_turn_changed"):
                runner.await_initial_unknown("origin:fixture", "conv:fixture")
        self.assertEqual([call.args[0] for call in web.call_args_list],
                         ["web-initial-wait-001", "web-snapshot-initial"])

    def test_failed_async_turn_stops_run_before_memory_rebuild(self):
        runner = a1_source_flow.Source(self.config)
        observed = []
        exact_scope = {"actor_id": "actor:a1-source", "person_id": "person:fixture",
                       "audience": "self_private", "conversation_id": "conv:fixture"}
        account = {"namespace": "web", "immutable_account_id": "fixture"}
        history = [{"turn": {"turn_sequence": index, "turn_id": f"turn:{index}",
                    "phase": "closed_unknown" if index == 1 else "failed",
                    "delivery_state": "unknown" if index == 1 else "failed"},
                    "replies": [{"reply_id": "reply:1", "state": "unknown"}]
                    if index == 1 else []} for index in (1, 2)]
        def compose(_self, owner, *args, **_kwargs):
            observed.append(("compose", owner, args))
            if "migrate-profiles" in args or "migrate-sources" in args:
                name = "profiles" if "migrate-profiles" in args else "sources"
                return json.dumps({"schema": 2 if name == "profiles" else 3,
                    "backup": f"/srv/tianshu/first-install.pre-{name}.sqlite",
                    "unverified_admissions": 0}).encode()
            return b""
        def dispatch(_self, label):
            observed.append(("dispatch", label))
            outcome = {"state": "accepted", "admission": {"scope": exact_scope},
                       "receipt": {"collection_key": {"author": account}}}
            return "source-input:" + label, {"conversation_id": "conv:fixture"}, outcome
        def web(_self, name, _origin, conversation):
            observed.append(("web", name))
            return {"http_status": 200, "body": {"conversation_id": conversation,
                "history": history, "active_turns": [], "collectors": []}}
        def bind(_root, _document):
            observed.append(("bind",))
            return {"status": "bound"}
        with patch.object(a1_source_flow.Source, "compose", compose), \
             patch.object(a1_source_flow.Source, "initial_permissions",
                          lambda *_args: None), \
             patch.object(a1_source_flow.Source, "wait_owners", lambda *_args: None), \
             patch.object(a1_source_flow.Source, "origin",
                          lambda _self, *_args: "origin:fixture"), \
             patch.object(a1_source_flow.Source, "dispatch", dispatch), \
             patch.object(a1_source_flow.Source, "web", web), \
             patch.multiple(a1_source_flow.product,
                bind_synthetic_classification=lambda *_args: None,
                enable_synthetic_memory_candidates=lambda *_args: None,
                bind_synthetic_model_version=lambda *_args: None,
                set_gateway_origin=lambda *_args: None,
                start_synthetic_model=lambda *_args, **_kwargs:
                    {"status": "ready", "fixture_only": True},
                publish_synthetic_config=lambda *_args, **_kwargs:
                    {"action": "publish", "receipt": {"config_version": 3,
                                                       "published": True}},
                bind_memory_scopes=bind):
            with self.assertRaisesRegex(RecoveryError,
                    "a1_source_initial_turn_terminal_mismatch"):
                runner.run()
        self.assertEqual([row[1] for row in observed if row[0] == "dispatch"],
                         ["forget-success-v3", "source-revoke-success-v3"])
        self.assertEqual([row[1] for row in observed if row[0] == "web"],
                         ["web-initial-wait-001"])
        self.assertNotIn(("bind",), observed)
        self.assertFalse(any(row[0] == "compose" and "--force-recreate" in row[2]
                             for row in observed))

    def test_real_memory_reader_accepts_current_bound_ids_only(self):
        scope = {"actor_id": "actor:a1-source",
            "person_id": "person:" + uuid.uuid4().hex,
            "audience": "self_private", "conversation_id": "conv:" + uuid.uuid4().hex}
        put(self.source / "config/memory/settings.json", {"callers": {
            "companion": {"event_scopes": [scope]}}, "local_users": {
            "a1-local-owner": {"revision_scopes": [scope]}}})
        request = {"query": {"schema_version": 1, "request_id": "new-scope",
                    "origin": {"assertion_ref": "origin:new"}},
                   "requested_scope": scope, "query_text": "绿色",
                   "selection": ["identity"]}
        path = self.reports / "memory-reader-request.json"
        put(path, request)
        output = SimpleNamespace(returncode=0,
            stdout=b'{"http_status":200,"body":{"selected_units":[]}}', stderr=b"")
        with patch.object(a1_source_flow.product, "_imports",
                          return_value=(lambda _root: None, None, None, read_json)), \
             patch.object(a1_source_flow.product.subprocess, "run", return_value=output) as run:
            result = a1_source_flow.product.read_memory_selection(
                self.source, path, docker_executable=self.config["docker"])
            self.assertEqual(result["http_status"], 200)
            self.assertEqual(run.call_args.args[0][0], self.config["docker"])
            request["requested_scope"] = dict(scope, conversation_id="conv:other")
            put(path, request)
            with self.assertRaises(ValueError):
                a1_source_flow.product.read_memory_selection(
                    self.source, path, docker_executable=self.config["docker"])
            self.assertEqual(run.call_count, 1)

    def test_cpuset_comes_from_locked_profile_and_both_compose_files(self):
        profile = {"kind": "nas-cpuset-qa-v1", "cpus": [6, 7],
                   "pid_limit": "unsupported"}
        path = Path(self.temp.name) / "resource-profile.json"
        put(path, profile)
        self.config["resource_profile"] = str(path)
        put(self.source / "deployment.json", {"project_name": self.config["source_project"],
            "compose_inputs": {"resource_profile": profile}})
        put(self.source / "compose.json", {"services": {
            owner: {"cpuset": "6,7"} for owner in a1_source_flow.CORE}})
        put(self.source / "observability/compose.yaml", {"services": {
            owner: {"cpuset": "6,7"} for owner in a1_source_flow.OBS}})
        runner = a1_source_flow.Source(self.config)
        with patch.object(a1_source_flow, "deploy_module",
                          return_value=SimpleNamespace(validate=lambda value: value)):
            self.assertEqual(runner.expected_cpuset(), "6,7")
            obs = read_json(self.source / "observability/compose.yaml")
            obs["services"]["obs-loki"]["cpuset"] = "0,1"
            put(self.source / "observability/compose.yaml", obs)
            with self.assertRaises(RecoveryError):
                runner.expected_cpuset()

    def test_runtime_identity_checks_observed_allocated_cpu_pair(self):
        runner = a1_source_flow.Source(self.config)
        project = self.config["source_project"]
        owners = (*a1_source_flow.CORE, *a1_source_flow.OBS)
        names = {owner: f"{project}{'-obs' if owner in a1_source_flow.OBS else ''}-{owner}-1"
                 for owner in owners}
        memory = {"obs-vector": 805306368, "obs-loki": 1610612736,
                  "obs-grafana": 536870912, "obs-prometheus": 536870912,
                  "obs-guard": 268435456}
        containers = []
        for owner in owners:
            containers.append({"Name": "/" + names[owner],
                "Config": {"Labels": {"com.docker.compose.project":
                    project + ("-obs" if owner in a1_source_flow.OBS else ""),
                    "com.docker.compose.service": owner,
                    "com.docker.compose.oneoff": "False"}, "User": "10001:10001"},
                "State": {"Status": "running", "OOMKilled": False, "Pid": 123,
                          "Health": {"Status": "healthy"}},
                "RestartCount": 0, "HostConfig": {"CpusetCpus": "6,7",
                    "ReadonlyRootfs": True, "CapDrop": ["ALL"],
                    "Memory": 1073741824 if owner in a1_source_flow.CORE else memory[owner]},
                "Mounts": [], "Image": "sha256:" + owner, "Id": "container:" + owner})

        def command(_self, argv, **_kwargs):
            if argv[1:3] == ["ps", "-a"]:
                selected = (a1_source_flow.OBS if any(
                    item == "label=com.docker.compose.project=" + project + "-obs"
                    for item in argv) else a1_source_flow.CORE)
                return ("\n".join(names[owner] for owner in selected) + "\n").encode()
            if argv[1] == "inspect":
                return json.dumps(containers).encode()
            if argv[1:3] == ["image", "inspect"]:
                return json.dumps([{"Id": argv[3], "Os": "linux",
                    "Architecture": "amd64", "RepoDigests": []}]).encode()
            raise AssertionError(argv)

        def save(_root, **_kwargs):
            put(self.source / "reports/runtime-identity.json", {"observed": True})

        module = SimpleNamespace(lifecycle_lease=lambda _root: nullcontext(), save=save)
        with patch.object(a1_source_flow.Source, "expected_cpuset", return_value="6,7"), \
             patch.object(a1_source_flow.Source, "command", command), \
             patch.object(a1_source_flow, "deploy_module", return_value=module), \
             patch.object(Path, "read_text", return_value="Uid:\t10001\nGid:\t10001\n"):
            runner.runtime_identity()
            self.assertTrue((self.source / "reports/runtime-identity.json").is_file())
            containers[-1]["HostConfig"]["CpusetCpus"] = "0,1"
            with self.assertRaises(RecoveryError):
                runner.runtime_identity()

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

        def web(_self, name, _origin, conversation):
            observed.append(("web", name))
            if name == "web-initial-wait-001":
                pending = copy.deepcopy(history)
                pending[1]["turn"].update(phase="preparing",
                                          delivery_state="not_started")
                pending[1]["replies"] = []
                return {"http_status": 200, "body": {"conversation_id": conversation,
                    "history": pending, "active_turns": [{"turn_sequence": 2}],
                    "collectors": []}}
            if name == "web-initial-wait-002":
                return {"http_status": 200, "body": {"conversation_id": conversation,
                    "history": history, "active_turns": [],
                    "collectors": [{"state": "processing"}]}}
            return {"http_status": 200, "body": {"conversation_id": conversation,
                "history": history, "active_turns": [], "collectors": []}}

        def bind(_root, _document):
            observed.append(("bind_memory_scopes",))
            return {"status": "bound"}

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
             patch.object(a1_source_flow.Source, "web", web), \
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
                bind_memory_scopes=bind,
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
        final = observed.index(("web", "web-snapshot-initial"))
        restart = next(index for index, row in enumerate(observed)
            if row[0] == "compose" and "--force-recreate" in row[2])
        self.assertLess(observed.index(("web", "web-initial-wait-003")), final)
        self.assertLess(final, observed.index(("bind_memory_scopes",)))
        self.assertLess(final, restart)
        self.assertEqual([row[1] for row in observed if row[0] == "memory"],
                         ["baseline", "after-forget", "after-source-revoke", "after-retract"])
        self.assertEqual(json.loads((self.reports / "gateway-usage-readback.json").read_text()),
                         {"counts": {"total": 4}})
        self.assertTrue((self.reports / "a1-source-complete.json").is_file())


if __name__ == "__main__":
    unittest.main()
