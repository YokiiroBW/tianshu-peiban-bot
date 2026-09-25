"""Isolated preparation -> placeholder -> driver-entry rehearsal."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from ops.recovery import a1_acceptance, a1_clone_prepare, a1_once, a1_prepare
from ops.recovery.a1_code import deploy_module
from ops.recovery.safety import read_json
import test_a1_once

put = test_a1_once.put


class PreparationTests(unittest.TestCase):
    def setUp(self):
        test_a1_once.Fixture.setUp(self)
        home = Path(self.temp.name)
        self.parent = home
        (self.parent / "preparations").mkdir(parents=True)
        self.contracts = home / "contracts"
        self.contracts.mkdir()
        self.obs = Path(__file__).resolve().parents[3]
        self.projects = Path("C:/YOKI/Codex/tianshu-peiban-bot/projects")
        if not self.projects.is_dir():
            self.skipTest("fixed product Git repositories unavailable locally")
        self.gateway = home / "gateway-import"
        self.gateway.mkdir()
        (self.gateway / "module.py").write_text("fixed gateway source\n")
        self.profile = home / "resource-profile.json"
        put(self.profile, {"kind": "fixture-only"})
        self.original = Path(__file__).parent / "fixtures/a1-fixed-original-manifest.json"
        for name in ("a1_prepare.py", "a1_clone_prepare.py"):
            (self.code / "ops/recovery" / name).write_text("fixture pinned module\n")
        self.prep = {
            "schema_version": "a1-preparation/1", "scope_parent": str(self.parent),
            "scope_name": "scope-a1-new-fixture", "scope_id": str(uuid.uuid4()),
            "code_root": str(self.code), "code_tree_sha256": a1_once._code_tree(self.code),
            "code_lock_sha256": "", "python": str(self.python), "docker": str(self.docker),
            "original_manifest": str(self.original),
            "original_manifest_sha256": a1_once.file_hash(self.original),
            "contracts_root": str(self.contracts),
            "resource_profile": str(self.profile),
            "resource_profile_sha256": a1_once.file_hash(self.profile),
            "observability_repository": str(self.obs),
            "observability_repository_sha256": a1_prepare._git_commit_digest(
                self.obs, "65b88a6d1c2b5047ca6bfb2f7f7484749eb14154"),
            "projects_root": str(self.projects),
            "source_project": "tianshu-qa-a1-source-new-fixture",
            "clone_project": "tianshu-qa-a1-clone-new-fixture",
            "networks": {"source": self.nets[:5], "clone": self.nets[5:]},
            "ports": {"source": self.ports[:6], "clone": self.ports[6:]},
            "run_label": "a1-new-fixture", "restored_name": "restored",
            "clone_name": "clone", "clone_inputs_name": "clone-inputs",
            "backup_name": "backup", "permit_name": "permit", "receipt_name": "receipts",
            "gateway_pythonpath": {str(self.gateway): a1_prepare._tree(self.gateway)},
        }
        self.lock = self.parent / "preparations/scope-a1-new-fixture.code-lock.json"
        put(self.lock, {"schema_version": "a1-code-lock/1",
            "tree_sha256": self.prep["code_tree_sha256"],
            "files": dict(a1_once._code_files(self.code))})
        self.prep["code_lock_sha256"] = a1_once.file_hash(self.lock)
        self.prep_path = self.parent / "preparations/scope-a1-new-fixture.json"
        put(self.prep_path, self.prep)

    def _initializer(self, scope, manifest_path, c):
        scope.mkdir()
        put(scope / ".recovery-scope.json", {"scope_id": c["scope_id"]})
        tls = scope / "inputs/tls"
        tls.parent.mkdir()
        a1_prepare.make_tls(tls)
        source = scope / "deployments/source"
        source.mkdir(parents=True)
        put(source / "release-manifest.json", json.loads(manifest_path.read_text()))
        return source

    def _synthetic(self):
        def configure(source, **kwargs):
            self.assertEqual(kwargs["api_ports"], dict(zip(
                ("companion", "memory", "gateway"), self.ports[1:4])))
            put(source / "config/platform/settings.json", {
                "input_entries": {"web-input-source": {
                    "default_actor_ids": ["actor:a1-source"],
                    "channel": {"namespace": "web", "binding_id": "web-source",
                                "channel_conversation_id": "a1-fixture"},
                    "account": {"id": "fixture-account"}}}})
            return {"status": "prepared", "synthetic_only": True,
                    "publication": {"config_version": 1, "providers": [{
                        "provider_id": "provider-synthetic",
                        "base_url": "https://gateway.internal:9443/v1"}]}}
        def inputs(source, output):
            with patch("ops.recovery.a1_acceptance._imports",
                       return_value=(lambda root: None, None, None, read_json)):
                return a1_acceptance.build_synthetic_inputs(source, output)
        return configure, inputs

    def _source(self):
        self.assertEqual(a1_prepare.load(self.prep_path), self.prep)
        result = a1_prepare.prepare_source(self.prep, initializer=self._initializer,
            observability=lambda source, settings, repo, projects:
                {"status": "observability_configured"},
            synthetic=self._synthetic())
        self.assertEqual(result["status"], "static_source_prepared")
        scope = self.parent / self.prep["scope_name"]
        return scope, scope / "deployments/source"

    def _semantic_fixture(self, scope, source):
        """Fixture-only product state after simulated source exercise/registration."""
        put(scope / "inputs/model-publication-template.json", {
            "config_version": 1, "providers": [{"provider_id": "provider-synthetic",
                "base_url": "https://gateway.internal:9443/v1"}]})
        put(source / "deployment.json", {"project_name": self.prep["source_project"]})
        put(source / ".recovery-registration.json", {
            "scope_id": self.prep["scope_id"],
            "authority_id": str(uuid.uuid4()),
            "runtime_identity": {"sha256": "5" * 64}})
        old = {"PLATFORM": "source-platform", "MEMORY": "source-memory",
               "CORE": "source-core", "GATEWAY": "source-gateway",
               "SINK": "source-sink", "LOCAL": "source-local"}
        envs = {
            "platform": {"TS_MEMORY_PLATFORM": old["PLATFORM"]},
            "companion": {"TS_CORE_MEMORY": old["MEMORY"],
                "TS_MEMORY_CORE": old["CORE"], "TS_PLATFORM_CORE": "source-platform-core",
                "TS_CORE_GATEWAY": old["GATEWAY"],
                "TIANSHU_DIAGNOSTICS_TOKEN": "source-diagnostics",
                "TS_A1_SINK_TOKEN": old["SINK"]},
            "memory": {"TIANSHU_LOCAL_OWNER_CREDENTIAL": old["LOCAL"]},
            "gateway": {"TS_A1_SINK_TOKEN": old["SINK"],
                "TS_GATEWAY_PLATFORM": "source-gateway-platform",
                "TS_GATEWAY_ORIGIN": "origin:source-placeholder"},
        }
        for owner, values in envs.items():
            path = source / "private" / (owner + ".env")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("".join(f"{key}='{value}'\n" for key, value in values.items()))
        put(source / "config/platform/settings.json", {
            "providers": {"provider-synthetic": {"reviewed_addresses": ["10.205.48.13"]}},
            "web": {"origin": "https://console.synthetic.test:22001"},
            "principals": {"gateway": {"config_versions": [1, 3]}}})
        put(source / "config/gateway/settings.json", {
            "targets": [{"addresses": ["10.205.48.10", "10.205.48.13"]}],
            "clients": [{"service": "companion", "provider_id": "provider-synthetic",
                         "internal": True, "allowed_versions": [3]}]})
        put(source / "config/companion/settings.json", {"config_version": 3})
        put(source / "config/memory/settings.json", {
            "local_users": {"a1-local-owner": {"credential_sha256": "source"}},
            "callers": {"companion": {"token": old["MEMORY"],
                "issuer_token": old["PLATFORM"],
                "event_scopes": [{"conversation_id": "fixture-conversation"}]}},
            "source_sync": {"core": {"token": old["CORE"]},
                            "platform": {"token": old["PLATFORM"]}}})
        put(source / "observability-input/settings.json", {
            "network_subnets": {"observe": self.nets[3], "storage": self.nets[4]},
            "ports": {"grafana": self.ports[4], "query": self.ports[5]}})
        for name in a1_clone_prepare.TLS_MAP:
            path = source / "observability-input/tls" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"source tls")
        for name in ("writer_token", "query_token", "metrics_token",
                     "grafana_admin_password"):
            path = source / "observability-input/secrets" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("source secret\n")
        for owner in ("platform", "companion", "memory", "gateway"):
            for name in ("ca.pem", "server.pem", "server.key"):
                path = source / "config" / owner / "tls" / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"source tls")
        for i in range(100):
            path = source / "config/extra" / str(i)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"source fixture {i}\n")
        def compose(name, nets, service_names, port_names):
            return {"name": name, "networks": {network: {
                "internal": True, "ipam": {"config": [{"subnet": subnet}]}}
                for network, subnet in nets.items()},
                "services": {}}
        core = compose(self.prep["source_project"],
            dict(zip(("core", "egress", "frontend"), self.nets[:3])),
            (), ())
        for i, owner in enumerate(("platform", "companion", "memory", "gateway")):
            core["services"][owner] = {"networks": {
                "core": {"ipv4_address": str(__import__("ipaddress").ip_network(
                    self.nets[0]).network_address + 10 + i)}},
                "ports": [f"127.0.0.1:{self.ports[i]}:8000"], "volumes": []}
        obs = compose(self.prep["source_project"] + "-obs",
            dict(zip(("observe", "storage"), self.nets[3:5])), (), ())
        for owner in ("obs-vector", "obs-loki", "obs-grafana", "obs-prometheus",
                      "obs-guard"):
            port = (self.ports[4] if owner == "obs-grafana" else
                    self.ports[5] if owner == "obs-guard" else None)
            obs["services"][owner] = {"networks": ["observe"],
                "ports": [f"127.0.0.1:{port}:8000"] if port else [],
                "volumes": []}
        put(source / "compose.json", core)
        put(source / "observability/compose.yaml", obs)
        owners = set(core["services"]) | set(obs["services"])
        put(source / "reports/runtime-identity.json", {
            "projects": {"core": {"compose_json": core}, "observability": {
                "compose_json": obs}},
            "services": {name: {"image_id": "sha256:" + str(i) * 64}
                         for i, name in enumerate(sorted(owners), 1)}})
        registration = json.loads((source / ".recovery-registration.json").read_text())
        registration["runtime_identity"]["sha256"] = a1_once.file_hash(
            source / "reports/runtime-identity.json")
        put(source / ".recovery-registration.json", registration)
        report = source / "reports" / self.prep["run_label"]
        turns = [{"turn_id": f"turn-{i}", "phase": "closed_unknown",
                  "committed_event": {"reality": "fictional", "turn_sequence": i}}
                 for i in (1, 2)]
        put(report / "source-facts.json", {"body": {"schema_version": 1,
            "turns": turns}})
        unknown = [{"turn_id": f"turn-{i}", "sequence": i,
            "phase": "closed_unknown", "delivery_state": "unknown",
            "replies": [{"reply_id": f"reply-{i}", "state": "unknown"}]}
            for i in (1, 2)]
        put(report / "unknown-after-v4-readback.json", {"after": {"turns": unknown}})
        put(report / "web-snapshot-initial.json", {"body": {"history": [
            {"turn": {"turn_id": f"turn-{i}", "turn_sequence": i,
                      "phase": "closed_unknown", "delivery_state": "unknown"},
             "replies": [{"reply_id": f"reply-{i}", "state": "unknown"}]}
            for i in (1, 2)]}})
        inventory = {p.relative_to(source).as_posix(): a1_once.file_hash(p)
                     for p in source.rglob("*") if p.is_file()}
        put(source / "bundle-integrity.json", {"schema_version": "1.0.0",
                                               "files": inventory})

    def _usage(self, source, c, since, until):
        day = datetime.fromisoformat(since[:-1] + "+00:00")
        ids = ["model:" + "a" * 32, "model:" + "b" * 32,
               c["run_label"] + "-offline-control-5",
               c["run_label"] + "-offline-control-6"]
        attempts = [{"request_id": name,
                     "reason": "completed" if name.startswith("model:") else
                               "transport_unknown",
                     "outcome": "succeeded" if name.startswith("model:") else "unknown",
                     "completed_at": since}
                    for name in ids]
        window = {"since": since, "until": until,
                  "since_ms": int(day.timestamp() * 1000),
                  "until_ms": int((day + timedelta(days=1)).timestamp() * 1000),
                  "limit": 500, "offset": 0}
        report = {"schema_version": 1, "key_space": "chat",
            "identity": {"service": "companion"},
            "counts": {"total": 4, "succeeded": 2, "failed": 0,
                       "cancelled": 0, "unknown": 2},
            "coverage": {"matching": 4, "scanned": 4,
                         "truncated": False, "unmetered_total": 0},
            "window": window, "attempts": attempts}
        return report, json.dumps(report).encode()

    def test_manifest_is_exact_single_field_derivation(self):
        self.assertEqual(a1_prepare.load(self.prep_path), self.prep)
        original = json.loads(self.original.read_text())
        derived = a1_prepare.derive_manifest(self.prep)
        original["features"][0]["enabled"] = False
        self.assertEqual(derived, original)
        self.assertEqual(a1_once.file_hash(self.original),
                         a1_prepare.FIXED_ORIGINAL_MANIFEST_SHA256)

    def test_tooling_must_be_the_scope_parent_locked_tree(self):
        elsewhere = self.parent / "elsewhere"
        shutil.copytree(self.code, elsewhere)
        self.prep["code_root"] = str(elsewhere)
        self.prep["code_tree_sha256"] = a1_once._code_tree(elsewhere)
        put(self.lock, {"schema_version": "a1-code-lock/1",
            "tree_sha256": self.prep["code_tree_sha256"],
            "files": dict(a1_once._code_files(elsewhere))})
        self.prep["code_lock_sha256"] = a1_once.file_hash(self.lock)
        put(self.prep_path, self.prep)
        with self.assertRaises(Exception):
            a1_prepare.load(self.prep_path)

    def test_cached_deployment_module_from_other_tree_is_rejected(self):
        fake = type("Cached", (), {"__file__": str(self.gateway / "module.py")})()
        with patch.dict(sys.modules, {"bundle": fake}):
            with self.assertRaises(Exception):
                deploy_module(self.code, "bundle")

    def test_fixed_product_git_objects_are_required(self):
        self.prep["projects_root"] = str(self.parent / "missing-projects")
        put(self.prep_path, self.prep)
        with self.assertRaises(Exception):
            a1_prepare.load(self.prep_path)

    def test_missing_fixed_git_commit_is_rejected_before_scope_creation(self):
        with patch("ops.recovery.a1_prepare.subprocess.run",
                   return_value=subprocess.CompletedProcess([], 1, b"", b"")):
            with self.assertRaises(Exception):
                a1_prepare.load(self.prep_path)
        self.assertFalse((self.parent / self.prep["scope_name"]).exists())

    def test_source_api_ports_must_match_fixed_candidate(self):
        self.prep["ports"]["source"][1] = 21999
        put(self.prep_path, self.prep)
        with self.assertRaises(Exception):
            a1_prepare.load(self.prep_path)
        self.assertFalse((self.parent / self.prep["scope_name"]).exists())

    def test_public_source_plan_rejects_wrong_loaded_tree_without_write(self):
        result = subprocess.run([sys.executable, "-B", "-m", "ops.recovery.a1_prepare",
            "--config", str(self.prep_path)], cwd=Path(__file__).resolve().parents[3],
            capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "a1_entry_code_root_mismatch")
        self.assertFalse((self.parent / self.prep["scope_name"]).exists())

    def test_source_preparation_parameter_generation(self):
        scope, source = self._source()
        self.assertTrue((scope / "inputs/a1-observability/settings.json").is_file())
        settings = json.loads((scope / "inputs/a1-observability/settings.json").read_text())
        self.assertEqual(settings["ports"], {"grafana": self.ports[4],
                                             "query": self.ports[5]})
        self.assertEqual(settings["network_subnets"], {"observe": self.nets[3],
                                                        "storage": self.nets[4]})
        self.assertEqual(len(list((source / "reports/a1-new-fixture").glob("register-*.json"))), 9)

    def test_failed_observability_step_preserves_partial_scope_and_rejects_retry(self):
        self.assertEqual(a1_prepare.load(self.prep_path), self.prep)
        with self.assertRaises(RuntimeError):
            a1_prepare.prepare_source(self.prep, initializer=self._initializer,
                observability=lambda source, settings, repo, projects:
                    (_ for _ in ()).throw(RuntimeError("fixture observability failure")),
                synthetic=self._synthetic())
        scope = self.parent / self.prep["scope_name"]
        self.assertTrue((scope / "deployments/source/release-manifest.json").is_file())
        self.assertFalse((scope / "deployments/source/reports/a1-new-fixture").exists())
        with self.assertRaises(Exception):
            a1_prepare.load(self.prep_path)

    def _real_static_source(self):
        repository = Path(__file__).resolve().parents[3]
        manifest = json.loads(self.original.read_text())
        candidates = [repository / "contracts",
                      Path("C:/YOKI/Codex/tianshu-peiban-bot/contracts")]
        contracts = next((root for root in candidates if root.is_dir() and all(
            (root / contract["id"] / entry["path"]).is_file() and
            a1_once.file_hash(root / contract["id"] / entry["path"]) == entry["sha256"]
            for contract in manifest["contracts"] for entry in contract["files"])), None)
        if contracts is None:
            self.skipTest("fixed original contract bytes are unavailable locally")
        profile = {"kind": "nas-cpuset-qa-v1", "cpus": [0, 1],
                   "pid_limit": "unsupported"}
        put(self.profile, profile)
        self.prep["resource_profile_sha256"] = a1_once.file_hash(self.profile)
        shutil.copytree(repository / "ops", self.code / "ops", dirs_exist_ok=True)
        shutil.copytree(repository / "deploy", self.code / "deploy", dirs_exist_ok=True)
        # Each real CLI is a fresh interpreter. Emulate that boundary for only
        # the copied top-level deployment modules in this in-process test.
        for path in (self.code / "deploy/tianshu").glob("*.py"):
            sys.modules.pop(path.stem, None)
        self.prep["code_tree_sha256"] = a1_once._code_tree(self.code)
        put(self.lock, {"schema_version": "a1-code-lock/1",
            "tree_sha256": self.prep["code_tree_sha256"],
            "files": dict(a1_once._code_files(self.code))})
        self.prep["code_lock_sha256"] = a1_once.file_hash(self.lock)
        self.prep["contracts_root"] = str(contracts)
        put(self.prep_path, self.prep)
        self.assertEqual(a1_prepare.load(self.prep_path), self.prep)
        planned = subprocess.run([sys.executable, "-B", "-m", "ops.recovery.a1_prepare",
            "--config", str(self.prep_path)], cwd=self.code, capture_output=True,
            text=True, timeout=20)
        self.assertEqual(planned.returncode, 0, planned.stderr)
        self.assertEqual(json.loads(planned.stdout)["status"], "planned")
        release = a1_prepare.deploy_module(self.code, "observability_release")
        with patch.object(release, "apply_runtime_permissions") as linux_permissions:
            result = a1_prepare.prepare_source(self.prep)
        linux_permissions.assert_called_once()
        self.assertEqual(result["status"], "static_source_prepared")
        scope = self.parent / self.prep["scope_name"]
        return scope, scope / "deployments/source"

    def test_real_bundle_initializer_in_isolated_directory(self):
        scope, source = self._real_static_source()
        self.assertTrue((source / "bundle-integrity.json").is_file())
        self.assertFalse((source / "INCOMPLETE").exists())
        observed = json.loads((source / "observability/compose.yaml").read_text())
        self.assertEqual(set(observed["networks"]), {"observe", "storage"})
        self.assertEqual(len(list((source / "reports" / self.prep["run_label"]).glob(
            "register-*.json"))), 9)

    def _real_semantic_receipts_fixture(self, scope, source):
        """Fixture-only source business state; product CLIs are not invoked."""
        event_scope = {"actor_id": "actor:a1-source", "person_id": "fixture-person",
                       "audience": "self_private", "conversation_id": "fixture-conversation"}
        a1_acceptance.bind_memory_scopes(source, {"scopes": [event_scope] * 3,
            "account": {"namespace": "fixture", "immutable_account_id": "fixture-account"}})
        a1_acceptance.set_gateway_origin(source, "origin:fixture-source")
        platform = read_json(source / "config/platform/settings.json")
        gateway = read_json(source / "config/gateway/settings.json")
        companion = read_json(source / "config/companion/settings.json")
        memory = read_json(source / "config/memory/settings.json")
        platform["principals"]["gateway"]["config_versions"] = [1, 3]
        gateway["clients"][0]["allowed_versions"] = [1, 3]
        companion["config_version"] = 3
        a1_acceptance._update_bundle(source, {
            "config/platform/settings.json": a1_acceptance._raw(platform),
            "config/gateway/settings.json": a1_acceptance._raw(gateway),
            "config/companion/settings.json": a1_acceptance._raw(companion),
            "config/memory/settings.json": a1_acceptance._raw(memory)})
        core = read_json(source / "compose.json")
        obs = read_json(source / "observability/compose.yaml")
        owners = set(core["services"]) | set(obs["services"])
        put(source / "reports/runtime-identity.json", {
            "projects": {"core": {"compose_json": core},
                         "observability": {"compose_json": obs}},
            "services": {name: {"image_id": "sha256:" + str(i) * 64}
                         for i, name in enumerate(sorted(owners), 1)}})
        put(source / ".recovery-registration.json", {
            "scope_id": self.prep["scope_id"], "authority_id": str(uuid.uuid4()),
            "runtime_identity": {"sha256": a1_once.file_hash(
                source / "reports/runtime-identity.json")}})
        report = source / "reports" / self.prep["run_label"]
        turns = [{"turn_id": f"turn-{i}", "phase": "closed_unknown",
                  "committed_event": {"reality": "fictional", "turn_sequence": i}}
                 for i in (1, 2)]
        put(report / "source-facts.json", {"body": {"schema_version": 1,
            "turns": turns}})
        unknown = [{"turn_id": f"turn-{i}", "sequence": i,
            "phase": "closed_unknown", "delivery_state": "unknown",
            "replies": [{"reply_id": f"reply-{i}", "state": "unknown"}]}
            for i in (1, 2)]
        put(report / "unknown-after-v4-readback.json", {"after": {"turns": unknown}})
        put(report / "web-snapshot-initial.json", {"body": {"history": [
            {"turn": {"turn_id": f"turn-{i}", "turn_sequence": i,
                      "phase": "closed_unknown", "delivery_state": "unknown"},
             "replies": [{"reply_id": f"reply-{i}", "state": "unknown"}]}
            for i in (1, 2)]}})

    def test_real_static_source_clone_inputs_reach_consumer(self):
        from ops.recovery.drill_inputs import isolated_inputs
        scope, source = self._real_static_source()
        self._real_semantic_receipts_fixture(scope, source)
        clone_plan = subprocess.run([sys.executable, "-B", "-m",
            "ops.recovery.a1_clone_prepare", "--config", str(self.prep_path)],
            cwd=self.code, capture_output=True, text=True, timeout=20)
        self.assertEqual(clone_plan.returncode, 0, clone_plan.stderr)
        self.assertEqual(json.loads(clone_plan.stdout)["status"], "planned")
        result = a1_clone_prepare.prepare_clone(self.prep,
            route_reader=lambda source, turns: {"model:" + "a" * 32,
                                                "model:" + "b" * 32},
            usage_reader=self._usage, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        self.assertEqual(result["assertions"], 6)
        config = a1_once.load_config(scope / "inputs/a1-once.json")
        def diagnose(source, request):
            return {"valid": True}
        def publish(source, action, request):
            if action == "publish":
                return {"action": action, "receipt": {"config_version": 5}}
            return {"action": "issue", "receipt": {
                "assertion_ref": "origin:fixture-" + request.stem,
                "expires_at": "2099-01-01T00:00:00Z"}}
        a1_once.seal(config, publisher=(diagnose, publish),
                     attempt_id=str(uuid.uuid4()))
        inputs = scope / "drill-inputs/clone-inputs"
        index = read_json(inputs / "inputs.json")
        runtime = read_json(source / "reports/runtime-identity.json")
        value = {"inputs_directory": str(inputs), "inputs_sha256": a1_once.file_hash(
            inputs / "inputs.json"), "drill_directory": str(scope / "deployments/clone"),
            "config_sha256": {name: checksum for name, checksum in index["files"].items()
                if name.startswith(("config/", "private/", "observability/config/",
                                    "observability/private/", "observability-input/"))},
            "compose_sha256": {"core": index["files"]["compose.json"],
                "observability": index["files"]["observability/compose.yaml"]},
            "projects": config["projects"],
            "image_ids": {name: row["image_id"] for name, row in
                          runtime["services"].items()}}
        manifest = read_json(source / "release-manifest.json")
        isolated_inputs(value, manifest, resource_profile=read_json(self.profile))
        self.assertEqual(value["inputs_sha256"], a1_once.file_hash(inputs / "inputs.json"))

    def test_wrong_original_manifest_and_nonempty_target_stop_before_initializer(self):
        bad = self.parent / "preparations/bad-manifest.json"
        value = json.loads(self.original.read_text())
        value["features"][0]["enabled"] = False
        put(bad, value)
        self.prep["original_manifest"] = str(bad)
        self.prep["original_manifest_sha256"] = a1_once.file_hash(bad)
        put(self.prep_path, self.prep)
        with self.assertRaises(Exception):
            a1_prepare.load(self.prep_path)
        self.assertFalse((self.parent / self.prep["scope_name"]).exists())
        self.prep["original_manifest"] = str(self.original)
        self.prep["original_manifest_sha256"] = a1_once.file_hash(self.original)
        put(self.prep_path, self.prep)
        (self.parent / self.prep["scope_name"]).mkdir()
        with self.assertRaises(Exception):
            a1_prepare.load(self.prep_path)

    def test_complete_fixture_preparation_to_driver_entry(self):
        scope, source = self._source()
        self._semantic_fixture(scope, source)
        self.assertEqual(a1_prepare.load(self.prep_path, phase="clone"), self.prep)
        result = a1_clone_prepare.prepare_clone(self.prep,
            route_reader=lambda source, turns: {"model:" + "a" * 32,
                                                "model:" + "b" * 32},
            usage_reader=self._usage, now=datetime(2026, 9, 25, tzinfo=timezone.utc))
        self.assertEqual(result["status"], "clone_placeholders_prepared")
        self.assertEqual(result["assertions"], 6)
        driver_path = scope / "inputs/a1-once.json"
        driver = a1_once.load_config(driver_path)
        self.assertEqual(driver["scope_id"], self.prep["scope_id"])
        self.assertNotIn("r2h", driver_path.read_text())
        self.assertNotEqual((source / "private/gateway.env").read_bytes(),
                            (scope / "drill-inputs/clone-inputs/private/gateway.env").read_bytes())
        self.assertFalse((scope / "deployments/clone").exists())
        clone_env = (scope / "drill-inputs/clone-inputs/private/gateway.env").read_text()
        companion_env = (scope / "drill-inputs/clone-inputs/private/companion.env").read_text()
        sink = next(line.split("=", 1)[1] for line in clone_env.splitlines()
                    if line.startswith("TS_A1_SINK_TOKEN="))
        self.assertIn("TS_A1_SINK_TOKEN=" + sink, companion_env)
        stages = []
        def runner(argv, timeout):
            stage = next(name for name in a1_once.STAGES if
                (name in argv or (name == "drill-plan" and
                 "drill-clone" in argv and "--execute" not in argv) or
                 (name == "drill-execute" and "drill-clone" in argv and
                  "--execute" in argv)))
            stages.append(stage)
            if stage == "seal":
                index_path = scope / "drill-inputs/clone-inputs/inputs.json"
                index = json.loads(index_path.read_text())
                index["a1_origin_admission"] = {"minimum_remaining_seconds": 180}
                publish = scope / "inputs/clone-publish-receipt.json"
                config = scope / "drill-inputs/clone-inputs/private/a1-config-origin-issue.json"
                actor = scope / "drill-inputs/clone-inputs/private/a1-actor-origin-issue.json"
                put(publish, {"action": "publish", "receipt": {"config_version": 5}})
                for name, path in (("config", config), ("actor", actor)):
                    put(path, {"action": "issue", "receipt": {
                        "assertion_ref": "origin:fixture-" + name,
                        "expires_at": "2099-01-01T00:00:00Z"}})
                for path in (config, actor):
                    index["files"][path.relative_to(index_path.parent).as_posix()] = (
                        a1_once.file_hash(path))
                put(scope / "inputs/a1-seal-actions-complete.json", {
                    "schema_version": "a1-seal-actions-complete/1",
                    "scope_id": self.prep["scope_id"],
                    "attempt_id": argv[argv.index("--attempt-id") + 1],
                    "receipts": {"publish": a1_once.file_hash(publish),
                        "config": a1_once.file_hash(config),
                        "actor": a1_once.file_hash(actor)}})
                put(index_path, index)
                checksum = a1_once.file_hash(index_path)
                put(scope / "inputs/clone-inputs-finalized.json", {"inputs_sha256": checksum})
                return 0, {"status": "sealed", "inputs_sha256": checksum}
            if stage == "linux-rehearse":
                return 0, {"status": "disabled_restore_complete",
                           "runtime_owners_stopped": 9,
                           "verification_sha256": "1" * 64}
            if stage == "host-preflight":
                return 0, {"status": "host_preflight_passed", "clone_ports_checked": 6}
            if stage == "permit":
                (scope / "inputs/permit").write_bytes(b"fixture permit")
                return 0, {"status": "permit_issued",
                           "permit_sha256": a1_once.file_hash(scope / "inputs/permit"),
                           "admission": {"remaining_at_check_seconds": 181}}
            if stage == "drill-plan":
                return 0, {"status": "planned", "mode": "plan",
                           "actual_owners_and_facts": "not_checked"}
            return 0, {"status": "drill_passed"}
        execution = a1_once.drive(driver, runner=runner)
        self.assertEqual(execution["execute_calls"], 1)
        self.assertEqual(stages, list(a1_once.STAGES))
        self.assertFalse((scope / "deployments/clone").exists())

    def _clone_ready(self):
        scope, source = self._source()
        self._semantic_fixture(scope, source)
        self.assertEqual(a1_prepare.load(self.prep_path, phase="clone"), self.prep)
        return scope, source

    def test_missing_semantic_receipt_fails_before_clone_write(self):
        scope, source = self._clone_ready()
        (source / "reports" / self.prep["run_label"] / "source-facts.json").unlink()
        with self.assertRaises(Exception):
            a1_clone_prepare.prepare_clone(self.prep,
                route_reader=lambda source, turns: set(), usage_reader=self._usage)
        self.assertFalse((scope / "drill-inputs/clone-inputs").exists())

    def test_missing_model_template_fails_before_clone_write(self):
        scope, source = self._clone_ready()
        (scope / "inputs/model-publication-template.json").unlink()
        with self.assertRaises(Exception):
            a1_clone_prepare.prepare_clone(self.prep,
                route_reader=lambda source, turns: set(), usage_reader=self._usage)
        self.assertFalse((scope / "drill-inputs/clone-inputs").exists())

    def test_bad_gateway_usage_fails_before_clone_write(self):
        scope, source = self._clone_ready()
        def invalid_usage(source, c, since, until):
            report, raw = self._usage(source, c, since, until)
            report["counts"]["unknown"] = 3
            return report, raw
        with self.assertRaises(Exception):
            a1_clone_prepare.prepare_clone(self.prep,
                route_reader=lambda source, turns: {"model:" + "a" * 32,
                                                    "model:" + "b" * 32},
                usage_reader=invalid_usage)
        self.assertFalse((scope / "drill-inputs/clone-inputs").exists())

    def test_wrong_credential_mapping_cannot_publish_driver_config(self):
        scope, source = self._clone_ready()
        settings = json.loads((source / "config/memory/settings.json").read_text())
        settings["callers"]["companion"]["token"] = "wrong-source-token"
        put(source / "config/memory/settings.json", settings)
        inventory = json.loads((source / "bundle-integrity.json").read_text())
        inventory["files"]["config/memory/settings.json"] = a1_once.file_hash(
            source / "config/memory/settings.json")
        put(source / "bundle-integrity.json", inventory)
        with self.assertRaises(Exception):
            a1_clone_prepare.prepare_clone(self.prep,
                route_reader=lambda source, turns: {"model:" + "a" * 32,
                                                    "model:" + "b" * 32},
                usage_reader=self._usage)
        self.assertFalse((scope / "inputs/a1-once.json").exists())
        self.assertFalse((scope / "inputs/permit").exists())

    def test_wrong_source_address_cannot_publish_driver_config(self):
        scope, source = self._clone_ready()
        settings = json.loads((source / "config/gateway/settings.json").read_text())
        settings["targets"][0]["addresses"] = ["10.205.48.11"]
        put(source / "config/gateway/settings.json", settings)
        inventory = json.loads((source / "bundle-integrity.json").read_text())
        inventory["files"]["config/gateway/settings.json"] = a1_once.file_hash(
            source / "config/gateway/settings.json")
        put(source / "bundle-integrity.json", inventory)
        with self.assertRaises(Exception):
            a1_clone_prepare.prepare_clone(self.prep,
                route_reader=lambda source, turns: {"model:" + "a" * 32,
                                                    "model:" + "b" * 32},
                usage_reader=self._usage)
        self.assertFalse((scope / "inputs/a1-once.json").exists())

    def test_preexisting_clone_inputs_stop_before_rotation(self):
        scope, source = self._clone_ready()
        (scope / "drill-inputs/clone-inputs").mkdir(parents=True)
        with self.assertRaises(Exception):
            a1_clone_prepare.prepare_clone(self.prep,
                route_reader=lambda source, turns: set(), usage_reader=self._usage)
        self.assertFalse((scope / "inputs/tls-clone-inputs").exists())

    def test_public_clone_plan_rejects_wrong_loaded_tree_without_write(self):
        scope, source = self._clone_ready()
        result = subprocess.run([sys.executable, "-B", "-m",
            "ops.recovery.a1_clone_prepare", "--config", str(self.prep_path)],
            cwd=Path(__file__).resolve().parents[3], capture_output=True,
            text=True, timeout=20)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["reason"],
                         "a1_entry_code_root_mismatch")
        self.assertFalse((scope / "drill-inputs/clone-inputs").exists())


if __name__ == "__main__":
    unittest.main()
