"""Docker inspect/command contract tests only. They do NOT execute containers."""

import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from fixtures import fixture, put
from lifecycle_fixtures import compose_fixture

from ops.recovery.compose_backend import ComposeBackend, DockerCLI
from ops.recovery.compose_backend import MASKED_PROMETHEUS_VOLUME
from ops.recovery.engine import Recovery
from ops.recovery.lifecycle import Deadline
from ops.recovery.lifecycle_binding import describe
from ops.recovery.safety import RecoveryError, digest

HERE = Path(__file__).resolve().parent
PROJECT = "tianshu-synthetic-compose"


class DockerContract:
    def __init__(self, directory, binding):
        self.calls, self.containers, self.images = [], [], {}
        for number, (service, definition) in enumerate(binding["services"].items(), 1):
            image_id = "sha256:" + f"{number:064x}"
            self.images[definition["image"]] = image_id
            self.containers.append(
                {
                    "Id": f"{number:064x}",
                    "Created": "synthetic-created-" + str(number),
                    "Image": image_id,
                    "Config": {
                        "Image": definition["image"],
                        "Labels": {
                            "com.docker.compose.project": definition["project"],
                            "com.docker.compose.service": service,
                            "com.docker.compose.project.working_dir": definition[
                                "compose_directory"
                            ],
                            "com.docker.compose.project.config_files": ",".join(
                                str(directory / name)
                                for name in definition["compose_files"]
                            ),
                            "com.docker.compose.oneoff": "False",
                        },
                    },
                    "Mounts": [
                        {
                            "Type": "bind",
                            "Source": str(directory / mount["source"]),
                            "Destination": mount["target"],
                            "RW": not mount["read_only"],
                        }
                        for mount in definition["mounts"]
                    ],
                    "HostConfig": {
                        "Privileged": False,
                        "PidMode": "",
                        "Devices": [],
                        "VolumesFrom": [],
                        "RestartPolicy": {"Name": "unless-stopped"},
                    },
                    "State": {
                        "Status": "running",
                        "Running": True,
                        "Paused": False,
                        "Restarting": False,
                        "OOMKilled": False,
                        "Dead": False,
                        "ExitCode": 0,
                        "Error": "",
                    },
                }
            )

    def run(self, *args):
        self.calls.append(args)
        if args[:2] == ("container", "ls"):
            return "\n".join(c["Id"] for c in self.containers)
        if args[:2] == ("container", "inspect"):
            return json.dumps([c for c in self.containers if c["Id"] in args[2:]])
        if args[:2] == ("image", "inspect"):
            return json.dumps([{"Id": self.images[args[2]]}])
        if args[:2] == ("container", "update"):
            assert args[2] == "--restart=no"
            next(c for c in self.containers if c["Id"] == args[3])["HostConfig"][
                "RestartPolicy"
            ]["Name"] = "no"
            return args[3]
        if args[:2] == ("container", "kill"):
            assert args[2] == "--signal=SIGTERM"
            next(c for c in self.containers if c["Id"] == args[3])["State"].update(
                Running=False, Status="exited"
            )
            return args[3]
        raise AssertionError(args)


class ComposeContractTests(unittest.TestCase):
    def setUp(self):
        runtime = HERE / ".runtime"
        runtime.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.base = Path(self.temp.name).resolve()
        root = self.base / "sandbox"
        scope, _ = fixture(root)
        self.directory = root / "deployments/source"
        self.files = compose_fixture(self.directory)
        manifest_path = self.directory / "release-manifest.json"
        manifest = json.loads(manifest_path.read_bytes())
        for product in manifest["products"].values():
            product["image"]["digest"] = "sha256:" + "a" * 64
        put(manifest_path, manifest)
        inventory_path = self.directory / "recovery-inventory.json"
        inventory = json.loads(inventory_path.read_bytes())
        inventory["release_manifest_sha256"] = digest(manifest_path.read_bytes())
        put(inventory_path, inventory)
        for name in self.files:
            path = self.directory / name
            document = json.loads(path.read_bytes())
            for service, definition in document["services"].items():
                definition["image"] = (
                    (
                        manifest["products"][service]["image"]["reference"]
                        if service in manifest["products"]
                        else definition["image"]
                    )
                    + "@sha256:"
                    + "a" * 64
                )
            put(path, document)
        self.recovery = Recovery(root, scope)
        self.binding = describe(
            self.recovery, self.directory, PROJECT, self.files, "compose"
        )
        self.docker = DockerContract(self.directory, self.binding)
        self.backend = ComposeBackend(
            self.directory,
            self.directory / ".lifecycle",
            self.binding,
            Deadline(30),
            self.docker,
        )

    def tearDown(self):
        self.assertTrue(
            self.base.resolve().is_relative_to((HERE / ".runtime").resolve())
        )
        self.temp.cleanup()

    def test_two_project_identity_restart_and_sigterm_contract(self):
        self.backend.inspect()
        self.backend.disable_restart()
        for service in self.binding["services"]:
            self.backend.stop(service)
            self.assertTrue(self.backend.stopped(service))
        self.backend.assert_stopped()
        mutations = [
            c
            for c in self.docker.calls
            if c[:2] in {("container", "update"), ("container", "kill")}
        ]
        self.assertEqual(len(mutations), 18)
        self.assertTrue(
            all(c[2] in {"--restart=no", "--signal=SIGTERM"} for c in mutations)
        )
        self.assertEqual(
            {s["project"] for s in self.binding["services"].values()},
            {PROJECT, PROJECT + "-obs"},
        )

    def test_wrong_project_directory_image_mount_and_extra_owner_fail_closed(self):
        mutations = [
            lambda c: c["Config"]["Labels"].update(
                {"com.docker.compose.project": PROJECT + "-other"}
            ),
            lambda c: c["Config"]["Labels"].update(
                {"com.docker.compose.project.working_dir": str(self.base)}
            ),
            lambda c: c["Config"]["Labels"].update(
                {"com.docker.compose.project.config_files": "unrelated.json"}
            ),
            lambda c: c.update(Image="sha256:" + "b" * 64),
            lambda c: c["Mounts"][0].update(RW=False),
            lambda c: c["Config"]["Labels"].update(
                {"com.docker.compose.oneoff": "True"}
            ),
            lambda c: c["HostConfig"].update(Privileged=True),
        ]
        original = deepcopy(self.docker.containers)
        for mutate in mutations:
            with self.subTest(mutation=mutate):
                self.docker.containers = deepcopy(original)
                mutate(self.docker.containers[0])
                with self.assertRaises(RecoveryError):
                    self.backend.inspect()
        self.docker.containers = original + [deepcopy(original[0])]
        self.docker.containers[-1]["Id"] = "f" * 64
        with self.assertRaisesRegex(RecoveryError, "unexpected_project_container"):
            self.backend.inspect()
        self.assertFalse(any(c[:2] == ("container", "kill") for c in self.docker.calls))

    def test_foreign_project_readonly_mount_also_rejected(self):
        foreign = deepcopy(self.docker.containers[0])
        foreign["Id"] = "f" * 64
        foreign["Config"]["Labels"]["com.docker.compose.project"] = "other-project"
        for mount in foreign["Mounts"]:
            mount["RW"] = False
        self.docker.containers.append(foreign)
        with self.assertRaisesRegex(RecoveryError, "foreign_container_mount_overlap"):
            self.backend.inspect()

    def test_only_verified_prometheus_tmpfs_may_mask_its_image_volume(self):
        container = next(
            item
            for item in self.docker.containers
            if item["Config"]["Labels"]["com.docker.compose.service"]
            == "obs-prometheus"
        )
        _, target, options = MASKED_PROMETHEUS_VOLUME
        container["Config"]["Volumes"] = {target: {}}
        container["HostConfig"]["Tmpfs"] = {target: options}
        container["State"]["Pid"] = 123
        container["Mounts"].append(
            {
                "Type": "volume",
                "Name": "synthetic-image-volume",
                "Source": "/var/lib/docker/volumes/synthetic-image-volume/_data",
                "Destination": target,
                "RW": True,
            }
        )
        visible_tmpfs = (
            "42 30 0:40 / /prometheus ro,nosuid,nodev,noexec,relatime - "
            "tmpfs tmpfs rw,size=1024k"
        )
        with patch(
            "ops.recovery.compose_backend.Path.read_text", return_value=visible_tmpfs
        ):
            self.backend.inspect()

        container["HostConfig"]["Tmpfs"][target] = "rw,size=1m"
        with self.assertRaisesRegex(RecoveryError, "unregistered_container_volume"):
            self.backend.inspect()
        container["HostConfig"]["Tmpfs"][target] = options
        with patch(
            "ops.recovery.compose_backend.Path.read_text",
            return_value=visible_tmpfs.replace("tmpfs tmpfs", "ext4 /dev/sda"),
        ):
            with self.assertRaisesRegex(RecoveryError, "unregistered_container_volume"):
                self.backend.inspect()

    def test_replacement_and_automatic_restart_rejected_after_quiescence(self):
        self.backend.inspect()
        self.backend.disable_restart()
        self.docker.containers[0]["Created"] = "replacement"
        with self.assertRaisesRegex(RecoveryError, "runtime_owner_changed"):
            self.backend.inspect()
        self.docker.containers[0]["Created"] = "synthetic-created-1"
        self.docker.containers[0]["HostConfig"]["RestartPolicy"]["Name"] = (
            "unless-stopped"
        )
        with self.assertRaisesRegex(RecoveryError, "restart_policy_mismatch"):
            self.backend.inspect()

    def test_abnormal_exit_oom_and_running_owner_rejected(self):
        self.backend.inspect()
        self.backend.disable_restart()
        for service in self.binding["services"]:
            self.backend.stop(service)
        for values in (
            {"ExitCode": 137},
            {"OOMKilled": True},
            {"Dead": True},
            {"Error": "fault"},
            {"Running": True},
        ):
            original = deepcopy(self.docker.containers[0]["State"])
            self.docker.containers[0]["State"].update(values)
            with self.assertRaises(RecoveryError):
                self.backend.assert_stopped()
            self.docker.containers[0]["State"] = original

    def test_compose_binding_requires_pinned_release_and_no_extra_write_mount(self):
        path = self.directory / self.files[0]
        document = json.loads(path.read_bytes())
        document["services"]["platform"]["image"] = "synthetic/platform:mutable"
        put(path, document)
        with self.assertRaisesRegex(RecoveryError, "pinned_image_required"):
            describe(self.recovery, self.directory, PROJECT, self.files, "compose")
        document["services"]["platform"]["image"] += "@sha256:" + "a" * 64
        document["services"]["platform"]["volumes"].append(
            {
                "type": "bind",
                "source": str(self.directory / "logs/gateway"),
                "target": "/other",
                "read_only": False,
            }
        )
        put(path, document)
        with self.assertRaisesRegex(RecoveryError, "unregistered_writable_mount"):
            describe(self.recovery, self.directory, PROJECT, self.files, "compose")

    def test_remote_docker_endpoint_rejected_without_execution(self):
        with self.assertRaisesRegex(RecoveryError, "local_linux_docker_required"):
            DockerCLI(__file__, "tcp://remote.invalid:2375", Deadline(1))
