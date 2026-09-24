"""One-use clone boundaries with synthetic databases and explicit Docker/HTTP doubles."""

import json
import time
import unittest
import uuid
from contextlib import closing, nullcontext
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import test_linux_recovery as base_tests
from fixtures import put
from runtime_fixtures import RuntimeDocker

from ops.recovery import drill, linux_recovery
from ops.recovery.drill_inputs import isolated_inputs, permit
from ops.recovery.safety import RecoveryError, file_hash, read_json


class DrillTests(unittest.TestCase):
    def setUp(self):
        self.base = base_tests.LinuxRecoveryTests()
        self.base.setUp()
        self.root, self.source, self.recovery = (
            self.base.root,
            self.base.source,
            self.base.recovery,
        )
        (self.source / "observability/data/loki/segment-wal").write_bytes(
            b"opaque WAL must be retained"
        )
        (self.source / "observability/data/loki/new-empty").mkdir()
        prepared = self.base.prepare()
        restored = linux_recovery.rehearse(
            self.recovery,
            self.source,
            prepared["registration_sha256"],
            "backup",
            "restored",
            execute=True,
            docker_executable=__file__,
        )
        self.target = self.root / "deployments/restored"
        self.clone = self.root / "deployments/drill"
        self.inputs = self.root / "drill-inputs/drill"
        self.inputs.mkdir(parents=True)
        self.manifest = read_json(self.source / "release-manifest.json")
        self.value = {
            "schema_version": "dep-j-drill/1",
            "permit_id": str(uuid.uuid4()),
            "scope_id": self.base.scope,
            "authority_id": self.base.authority,
            "source_directory": self.source.as_posix(),
            "restored_directory": self.target.as_posix(),
            "drill_directory": self.clone.as_posix(),
            "inputs_directory": self.inputs.as_posix(),
            "snapshot_sha256": restored["backup"]["result"]["snapshot_sha256"],
            "verification_sha256": restored["verification_sha256"],
            "runtime_identity_sha256": self.base.pin,
            "registration_sha256": prepared["registration_sha256"],
            "inputs_sha256": "0" * 64,
            "projects": {
                "core": "tianshu-qa-drill",
                "observability": "tianshu-qa-drill-obs",
            },
            "compose_sha256": {},
            "config_sha256": {},
            "versions": {
                p: v["source"]["commit"] for p, v in self.manifest["products"].items()
            },
            "image_ids": {
                p: v["image_id"] for p, v in self.base.runtime["services"].items()
            },
            "issued_at": int(time.time()) - 1,
            "expires_at": int(time.time()) + 300,
            "max_runtime_seconds": 300,
        }
        self.documents = {}
        endpoints = {}
        for group, filename in (
            ("core", "compose.json"),
            ("observability", "observability/compose.yaml"),
        ):
            doc = deepcopy(self.base.runtime["projects"][group]["compose_json"])
            doc["name"] = self.value["projects"][group]
            doc["networks"] = {"isolated": {"internal": True}}
            for service, definition in doc["services"].items():
                definition.update(
                    image=self.value["image_ids"][service],
                    user="10001:10001",
                    read_only=True,
                    restart="no",
                    cap_drop=["ALL"],
                    pull_policy="never",
                    pids_limit=128,
                    mem_limit="1g",
                    cpus=1.0,
                    networks=["isolated"],
                )
                for mount in definition["volumes"]:
                    mount["source"] = str(
                        self.clone / Path(mount["source"]).relative_to(self.source)
                    )
                if service in self.manifest["products"]:
                    port = 18443 + len(endpoints)
                    definition["ports"] = [
                        {"host_ip": "127.0.0.1", "published": str(port), "target": 8443}
                    ]
                    endpoints[service] = port
            self.documents[group] = doc
            put(self.inputs / filename, doc)
        put(self.inputs / "config/test.json", {"fixture_only": True})
        put(self.inputs / "private/token", {"fixture_only": True})
        put(self.inputs / "config/ca.pem", {"fixture_only": True})
        put(
            self.inputs / "observability-input/secrets/writer_token",
            {"fixture_only": "clone-only"},
        )
        cases = [
            ("data_readback", "platform"),
            ("source_revoked", "memory"),
            ("model_revoked", "gateway"),
            ("forgotten", "memory"),
            ("unknown_no_resend", "companion"),
        ]
        self.index = {
            "schema_version": "dep-j-drill-inputs/1",
            "files": {},
            "assertions": [
                {
                    "id": kind,
                    "service": service,
                    "url": f"https://127.0.0.1:{endpoints[service]}/fixture/{kind}",
                    "ca_file": "config/ca.pem",
                    "token_file": "private/token",
                    "expected_status": 200,
                    "expected_json": {"fixture_only": True},
                }
                for kind, service in cases
            ],
        }
        self.permit_path = self.root / "permit.json"
        self.save()
        self.clone_docker = RuntimeDocker(
            self.clone,
            drill.clone_binding(self.clone, self.documents, self.manifest, self.value),
        )
        self.created = False
        self.partial_start_failure = False
        self.bad_exit = False
        self.calls = []
        outer = self

        class Combined:
            def run(self, *args):
                outer.calls.append(args)
                if args[:2] == ("network", "ls"):
                    return ""
                if args[0] == "compose":
                    outer.created = True
                    if outer.partial_start_failure:
                        outer.clone_docker.containers = outer.clone_docker.containers[
                            :2
                        ]
                        raise RecoveryError("injected_compose_failure")
                    return ""
                containers = outer.base.docker.containers + (
                    outer.clone_docker.containers if outer.created else []
                )
                if args[:2] == ("container", "ls"):
                    return "\n".join(c["Id"] for c in containers)
                if args[:2] == ("container", "inspect"):
                    return json.dumps([c for c in containers if c["Id"] in args[2:]])
                if args[:2] == ("image", "inspect"):
                    return json.dumps(
                        [
                            {
                                "Id": args[2]
                                if args[2].startswith("sha256:")
                                else outer.base.docker.images[args[2]],
                                "Os": "linux",
                                "Architecture": "amd64",
                                "RepoDigests": [],
                            }
                        ]
                    )
                if args[:2] in {("container", "update"), ("container", "kill")}:
                    backend = (
                        outer.clone_docker
                        if any(
                            c["Id"] == args[-1] for c in outer.clone_docker.containers
                        )
                        else outer.base.docker
                    )
                    return backend.run(*args)
                raise AssertionError(args)

        for index, c in enumerate(self.clone_docker.containers, 1):
            c["Id"] = f"{index + 100:064x}"
            c["State"]["Health"] = {"Status": "healthy"}
            c["HostConfig"]["RestartPolicy"]["Name"] = "no"
        self.base.stack.enter_context(
            patch.object(drill, "runtime_lease", lambda *_: nullcontext())
        )
        self.base.stack.enter_context(
            patch.object(
                drill, "load_identity", lambda *args, **kwargs: self.base.runtime
            )
        )
        self.base.stack.enter_context(
            patch.object(drill, "DockerCLI", lambda *_: Combined())
        )
        self.base.stack.enter_context(
            patch.object(drill.os, "chown", lambda *_: None, create=True)
        )
        self.base.stack.enter_context(
            patch.object(
                drill,
                "http_check",
                lambda root, a, b: {
                    "id": a["id"],
                    "service": a["service"],
                    "status": "passed",
                },
            )
        )

    def tearDown(self):
        self.base.tearDown()

    def save(self):
        self.index["files"] = {
            p.relative_to(self.inputs).as_posix(): file_hash(p)
            for p in self.inputs.rglob("*")
            if p.is_file() and p.name != "inputs.json"
        }
        self.value["config_sha256"] = {
            k: v
            for k, v in self.index["files"].items()
            if k.startswith(
                (
                    "config/",
                    "private/",
                    "observability/config/",
                    "observability/private/",
                    "observability-input/",
                )
            )
        }
        self.value["compose_sha256"] = {
            g: file_hash(self.inputs / p)
            for g, p in (
                ("core", "compose.json"),
                ("observability", "observability/compose.yaml"),
            )
        }
        put(self.inputs / "inputs.json", self.index)
        self.value["inputs_sha256"] = file_hash(self.inputs / "inputs.json")
        put(self.permit_path, self.value)

    def run_drill(self, execute=True):
        return drill.run(
            self.recovery,
            self.permit_path,
            file_hash(self.permit_path),
            execute=execute,
            docker_executable=__file__,
        )

    def test_plan_does_not_claim_or_create_clone(self):
        result = self.run_drill(False)
        self.assertEqual(result["status"], "planned")
        self.assertFalse(self.clone.exists())
        self.assertFalse((self.root / "drill-claims").exists())
        self.assertEqual(self.calls, [])

    def test_post_input_is_restricted_to_documented_read_endpoints(self):
        assertion = self.index["assertions"][0]
        port = self.documents["core"]["services"]["platform"]["ports"][0]["published"]
        assertion.update(
            method="POST",
            request_json={"schema_version": 1, "operation": "current"},
            url=f"https://127.0.0.1:{port}/internal/v1/source-access/read",
        )
        self.save()
        isolated_inputs(self.value, self.manifest)

        assertion["url"] = f"https://127.0.0.1:{port}/internal/v1/conversation/send"
        self.save()
        with self.assertRaisesRegex(
            RecoveryError, "drill_assertion_endpoint_forbidden"
        ):
            isolated_inputs(self.value, self.manifest)

    def test_access_bridge_requires_loopback_published_members(self):
        document = self.documents["core"]
        document["networks"]["access"] = {"internal": False, "driver": "bridge"}
        document["services"]["gateway"]["networks"].append("access")
        put(self.inputs / "compose.json", document)
        self.save()
        isolated_inputs(self.value, self.manifest)
        document["services"]["gateway"]["ports"][0]["host_ip"] = "0.0.0.0"
        put(self.inputs / "compose.json", document)
        self.save()
        with self.assertRaisesRegex(RecoveryError, "drill_loopback_port_required"):
            isolated_inputs(self.value, self.manifest)
        document["services"]["gateway"]["ports"] = []
        put(self.inputs / "compose.json", document)
        self.save()
        with self.assertRaisesRegex(
            RecoveryError, "drill_access_requires_loopback_publication"
        ):
            isolated_inputs(self.value, self.manifest)

    def test_other_external_bridge_remains_forbidden(self):
        document = self.documents["core"]
        document["networks"]["isolated"]["internal"] = False
        put(self.inputs / "compose.json", document)
        self.save()
        with self.assertRaisesRegex(RecoveryError, "drill_internal_network_required"):
            isolated_inputs(self.value, self.manifest)

    def test_capacity_reader_requires_registered_clone_data_directory(self):
        document = self.documents["observability"]
        volume = next(
            v
            for v in self.manifest["volumes"]
            if v["mount"]
            and v["kind"] == "directory"
            and v["owner_service"] == "obs-vector"
        )
        mount = dict(
            type="bind",
            source=str(self.clone / volume["host_path"]),
            target="/capacity/vector",
            read_only=True,
        )
        document["services"]["obs-guard"]["volumes"].append(mount)
        put(self.inputs / "observability/compose.yaml", document)
        self.save()
        isolated_inputs(self.value, self.manifest)
        mount["source"] = str(self.clone / "unregistered-data")
        put(self.inputs / "observability/compose.yaml", document)
        self.save()
        with self.assertRaisesRegex(RecoveryError, "drill_read_mount_unregistered"):
            isolated_inputs(self.value, self.manifest)

    def test_one_use_clone_preserves_originals_and_stops_nine_owners(self):
        for container in self.clone_docker.containers:
            if container["Config"]["Labels"]["com.docker.compose.service"].startswith(
                "obs-"
            ):
                container["State"].pop("Health")
        before = {p: file_hash(p) for p in self.target.rglob("*") if p.is_file()}
        result = self.run_drill()
        self.assertEqual(result["status"], "drill_passed")
        self.assertFalse(result["original_restore_activation"])
        self.assertTrue(
            all(not c["State"]["Running"] for c in self.clone_docker.containers)
        )
        self.assertEqual(
            before, {p: file_hash(p) for p in self.target.rglob("*") if p.is_file()}
        )
        self.assertEqual(
            read_json(self.source / ".deployment.json")["role"], "authority"
        )
        with self.assertRaisesRegex(RecoveryError, "drill_must_be_new"):
            self.run_drill()

    def test_partial_startup_is_stopped_without_force_kill(self):
        self.partial_start_failure = True
        result = self.run_drill()
        self.assertEqual(result["status"], "drill_failed_or_cancelled")
        self.assertTrue(
            all(not c["State"]["Running"] for c in self.clone_docker.containers)
        )
        self.assertTrue(
            all(
                c[2] == "--signal=SIGTERM"
                for c in self.calls
                if c[:2] == ("container", "kill")
            )
        )

    def test_expired_and_future_permit_and_path_overlap_rejected(self):
        now = time.time()
        original = deepcopy(self.value)
        for change in (
            {"expires_at": int(now) - 1},
            {"issued_at": int(now) + 10},
            {"drill_directory": self.value["source_directory"]},
        ):
            self.value = original | change
            self.save()
            with self.assertRaises(RecoveryError):
                permit(self.recovery, self.permit_path, file_hash(self.permit_path))

    def test_external_network_original_mount_and_health_assertion_rejected(self):
        original = deepcopy(self.documents["core"])
        mutations = [
            lambda d: d["networks"]["isolated"].update(internal=False),
            lambda d: d["services"]["platform"]["volumes"][0].update(
                source=str(self.source / "data/platform")
            ),
            lambda d: d["services"]["platform"].update(network_mode="host"),
        ]
        for change in mutations:
            document = deepcopy(original)
            change(document)
            put(self.inputs / "compose.json", document)
            self.save()
            with self.assertRaises(RecoveryError):
                isolated_inputs(self.value, self.manifest)
        put(self.inputs / "compose.json", original)
        self.index["assertions"][0]["url"] = (
            self.index["assertions"][0]["url"].split("/fixture")[0] + "/health/live"
        )
        self.save()
        with self.assertRaisesRegex(
            RecoveryError, "health_is_not_functional_assertion"
        ):
            isolated_inputs(self.value, self.manifest)

    def test_abnormal_clone_exit_reports_unconfirmed_and_keeps_claim(self):
        self.clone_docker.containers[0]["State"]["ExitCode"] = 143
        result = self.run_drill()
        self.assertEqual(result["status"], "stop_unconfirmed")
        self.assertEqual(result["activation"], "stop_unconfirmed")
        self.assertTrue(
            (self.root / "drill-claims" / (self.value["permit_id"] + ".json")).exists()
        )

    def test_authority_changes_refuse_before_claim_or_clone(self):
        import sqlite3

        with closing(sqlite3.connect(self.source / "data/platform/main.db")) as db:
            db.execute("UPDATE facts SET value='new-revocation' WHERE id='model'")
            db.commit()
        with self.assertRaisesRegex(RecoveryError, "current_authority_diverged"):
            self.run_drill()
        self.assertFalse(self.clone.exists())
        self.assertFalse((self.root / "drill-claims").exists())

    def test_opaque_log_wal_and_empty_directories_survive_clone(self):
        self.assertEqual(self.run_drill()["status"], "drill_passed")
        self.assertEqual(
            (self.clone / "observability/data/loki/segment-wal").read_bytes(),
            b"opaque WAL must be retained",
        )
        self.assertTrue((self.clone / "observability/data/loki/new-empty").is_dir())

    def test_tampered_restored_logs_refuse_before_clone(self):
        (self.target / "observability/data/loki/segment-wal").write_bytes(b"tampered")
        with self.assertRaisesRegex(RecoveryError, "drill_restored_payload_changed"):
            self.run_drill()
        self.assertFalse(self.clone.exists())

    def test_changed_copied_configuration_never_starts(self):
        original = drill.copy_new

        def changed(source, destination, **kwargs):
            original(source, destination, **kwargs)
            if destination == self.clone / "config/test.json":
                destination.write_bytes(b'{"drift":true}')

        with patch.object(drill, "copy_new", side_effect=changed):
            result = self.run_drill()
        self.assertEqual(result["status"], "drill_failed_or_cancelled")
        self.assertFalse(any(c[0] == "compose" for c in self.calls))

    def test_failed_assertion_still_stops_and_consumes_permit(self):
        with patch.object(
            drill, "http_check", side_effect=RecoveryError("assertion_failed")
        ):
            result = self.run_drill()
        self.assertEqual(result["status"], "drill_failed_or_cancelled")
        self.assertTrue(
            all(not c["State"]["Running"] for c in self.clone_docker.containers)
        )
        self.assertTrue(
            (self.root / "drill-claims" / (self.value["permit_id"] + ".json")).exists()
        )
