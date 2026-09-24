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
        self.readback_running_states = []
        self.gateway_running_states = []
        self.gateway_counts = {}
        for index, outcome in enumerate(
            ("unknown", "unknown", "succeeded", "succeeded"), 1
        ):
            correlation = f"{index:032x}"
            self.gateway_counts[correlation] = {
                "request.accepted": {"succeeded": 1},
                "upstream.call_started": {"started": 1},
                "upstream.call_finished": {outcome: 1},
            }
        self.gateway_counts[f"{4:032x}"]["request.accepted"]["succeeded"] = 2
        for index in range(5, 10):
            self.gateway_counts[f"{index:032x}"] = {
                "request.accepted": {"succeeded": 1}
            }
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
                    if "up" in args:
                        project = args[args.index("--project-name") + 1]
                        for container in outer.clone_docker.containers:
                            labels = container["Config"]["Labels"]
                            if labels["com.docker.compose.project"] == project:
                                container["State"].update(
                                    Status="running", Running=True, ExitCode=0
                                )
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
                    result = backend.run(*args)
                    if (
                        outer.bad_exit
                        and args[:2] == ("container", "kill")
                        and args[-1] == outer.clone_docker.containers[0]["Id"]
                    ):
                        outer.clone_docker.containers[0]["State"]["ExitCode"] = 143
                    return result
                raise AssertionError(args)

        for index, c in enumerate(self.clone_docker.containers, 1):
            c["Id"] = f"{index + 100:064x}"
            c["State"]["Health"] = {"Status": "healthy"}
            c["State"].update(Status="exited", Running=False)
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
                self._record_readback,
            )
        )
        self.base.stack.enter_context(
            patch.object(
                drill,
                "check_readiness",
                lambda *_args, **_kwargs: {
                    "status": "ready",
                    "runtime": "ok",
                    "response_sha256": "0" * 64,
                },
            )
        )
        self.base.stack.enter_context(
            patch.object(
                drill,
                "gateway_counters",
                self._record_gateway_snapshot,
            )
        )

    def _record_readback(
        self, root, assertion, deadline, *, capture_unknown_turns=False
    ):
        self.readback_running_states.append(
            all(c["State"]["Running"] for c in self.clone_docker.containers)
        )
        result = {
            "id": assertion["id"],
            "service": assertion["service"],
            "status": "passed",
        }
        if capture_unknown_turns and "runtime_observation" in self.index:
            result["unknown_turns"] = deepcopy(
                self.index["runtime_observation"]["unknown_turns"]
            )
        return result

    def _record_gateway_snapshot(self, *_args, **_kwargs):
        self.gateway_running_states.append(
            all(c["State"]["Running"] for c in self.clone_docker.containers)
        )
        return deepcopy(self.gateway_counts)

    def enable_a1_observation(self, *, window_seconds=1):
        self.value["projects"] = {
            "core": "tianshu-accept-a1-readback",
            "observability": "tianshu-accept-a1-readback-obs",
        }
        for group, filename in (
            ("core", "compose.json"),
            ("observability", "observability/compose.yaml"),
        ):
            self.documents[group]["name"] = self.value["projects"][group]
            put(self.inputs / filename, self.documents[group])
        for container in self.clone_docker.containers:
            service = container["Config"]["Labels"]["com.docker.compose.service"]
            group = "core" if service in self.manifest["products"] else "observability"
            container["Config"]["Labels"]["com.docker.compose.project"] = self.value[
                "projects"
            ][group]
        put(self.inputs / "private/diagnostics.token", "diagnostics-test-only")
        companion_port = self.documents["core"]["services"]["companion"]["ports"][0][
            "published"
        ]
        self.index["runtime_observation"] = {
            "window_seconds": window_seconds,
            "worker_readiness": {
                "url": f"https://127.0.0.1:{companion_port}/health/ready",
                "ca_file": "config/ca.pem",
                "token_file": "private/diagnostics.token",
            },
            "unknown_turns": [
                {
                    "turn_id": "turn:4090732d24934b05a90f47640afac279",
                    "turn_sequence": 8,
                    "phase": "closed_unknown",
                    "delivery_state": "unknown",
                    "replies": [
                        {
                            "reply_id": "reply:4a499ec193754d219bf2de646d3bfc58",
                            "state": "unknown",
                        }
                    ],
                },
                {
                    "turn_id": "turn:1bcde083e8204998aaa8f7bd420536bf",
                    "turn_sequence": 9,
                    "phase": "closed_unknown",
                    "delivery_state": "unknown",
                    "replies": [
                        {
                            "reply_id": "reply:82542ea0041e4a73acbf45f992d23024",
                            "state": "unknown",
                        }
                    ],
                },
            ],
        }
        unknown_assertion = next(
            item
            for item in self.index["assertions"]
            if item["id"] == "unknown_no_resend"
        )
        unknown_port = self.documents["core"]["services"]["companion"]["ports"][0][
            "published"
        ]
        unknown_assertion.update(
            method="POST",
            request_json={"schema_version": 1, "fixture_only": True},
            url=(
                f"https://127.0.0.1:{unknown_port}"
                "/internal/v1/conversation/web-snapshot"
            ),
        )
        self.save()

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

    def test_a1_projects_are_allowed_and_all_nine_services_stay_running(self):
        self.enable_a1_observation()
        permit(self.recovery, self.permit_path, file_hash(self.permit_path))
        result = self.run_drill()
        self.assertEqual(result["status"], "drill_passed")
        self.assertEqual(result["service_coverage"]["mode"], "simultaneous")
        self.assertTrue(result["service_coverage"]["all_nine_simultaneous"])
        self.assertTrue(
            result["service_coverage"]["observability"][
                "running_during_functional_readback"
            ]
        )
        self.assertTrue(
            result["service_coverage"]["core"][
                "healthchecks_healthy_during_functional_readback"
            ]
        )
        self.assertEqual(
            result["service_coverage"]["core"]["configured_healthchecks"],
            sum(
                bool(spec.get("healthcheck"))
                for spec in self.documents["core"]["services"].values()
            ),
        )
        starts = [
            call[call.index("--project-name") + 1]
            for call in self.calls
            if call[:1] == ("compose",) and "up" in call
        ]
        self.assertEqual(
            starts,
            [
                self.value["projects"]["observability"],
                self.value["projects"]["core"],
            ],
        )
        self.assertEqual(len(self.readback_running_states), 6)
        self.assertTrue(all(self.readback_running_states))
        self.assertEqual(self.gateway_running_states, [False, True])
        observation = result["unknown_no_resend_observation"]
        self.assertEqual(observation["status"], "passed")
        self.assertEqual(observation["api_unknown_turn_count"], 2)
        self.assertEqual(observation["api_unknown_reply_record_count"], 2)
        self.assertTrue(observation["gateway_counters_unchanged_by_correlation"])
        self.assertEqual(
            observation["gateway_baseline"]["successful_control_groups"], 2
        )

    def test_a1_observation_fails_if_any_gateway_correlation_count_grows(self):
        self.enable_a1_observation()
        changed = deepcopy(self.gateway_counts)
        changed["0" * 31 + "1"]["upstream.call_started"]["started"] = 2
        with patch.object(
            drill,
            "gateway_counters",
            side_effect=[deepcopy(self.gateway_counts), changed],
        ):
            result = self.run_drill()
        self.assertEqual(result["status"], "drill_failed_or_cancelled")
        self.assertTrue(
            all(not c["State"]["Running"] for c in self.clone_docker.containers)
        )

    def test_a1_observation_requires_the_companion_web_snapshot_endpoint(self):
        self.enable_a1_observation()
        unknown_assertion = next(
            item
            for item in self.index["assertions"]
            if item["id"] == "unknown_no_resend"
        )
        unknown_assertion["url"] = unknown_assertion["url"].replace(
            "/internal/v1/conversation/web-snapshot", "/internal/v1/source-facts/read"
        )
        self.save()
        with self.assertRaisesRegex(
            RecoveryError, "drill_unknown_assertion_endpoint_forbidden"
        ):
            isolated_inputs(self.value, self.manifest)

    def test_a1_readiness_requires_a_dedicated_diagnostics_token(self):
        self.enable_a1_observation()
        self.index["runtime_observation"]["worker_readiness"][
            "token_file"
        ] = "private/token"
        self.save()
        with self.assertRaisesRegex(RecoveryError, "drill_readiness_input_invalid"):
            isolated_inputs(self.value, self.manifest)

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
        self.bad_exit = True
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
