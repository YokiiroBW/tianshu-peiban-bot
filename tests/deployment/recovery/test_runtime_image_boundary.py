"""Linux identity boundary using an explicit Docker contract double, never a container claim."""

import json
import unittest

import test_compose_lifecycle as compose_tests

from ops.recovery.safety import RecoveryError


class RuntimeImageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = compose_tests.ComposeContractTests()
        self.fixture.setUp()
        self.backend = self.fixture.backend
        self.docker = self.fixture.docker
        self.image_metadata = {}
        for container in self.docker.containers:
            service = container["Config"]["Labels"]["com.docker.compose.service"]
            expected = self.fixture.binding["services"][service]
            expected.update(
                image_id=container["Image"],
                container_id=container["Id"],
                repo_digests=[],
            )
            self.image_metadata[expected["image"]] = {
                "Id": container["Image"],
                "Os": "linux",
                "Architecture": "amd64",
                "RepoDigests": [],
            }
        original = self.docker.run

        def run(*args):
            if args[:2] == ("image", "inspect"):
                return json.dumps([self.image_metadata[args[2]]])
            return original(*args)

        self.docker.run = run

    def tearDown(self):
        self.fixture.tearDown()

    def test_local_image_id_with_no_registry_evidence_is_accepted(self):
        self.backend.inspect()
        self.assertEqual(len(self.backend.current), 9)

    def test_same_tag_rebuilt_before_first_inspect_is_rejected(self):
        container = self.docker.containers[0]
        container["Image"] = "sha256:" + "e" * 64
        self.image_metadata[container["Config"]["Image"]]["Id"] = container["Image"]
        with self.assertRaisesRegex(RecoveryError, "container_image_mismatch"):
            self.backend.inspect()

    def test_wrong_architecture_or_forged_registry_digest_rejected(self):
        key = self.docker.containers[0]["Config"]["Image"]
        self.image_metadata[key]["Architecture"] = "arm64"
        with self.assertRaisesRegex(RecoveryError, "runtime_image_identity_mismatch"):
            self.backend.inspect()
        self.image_metadata[key]["Architecture"] = "amd64"
        service = self.docker.containers[0]["Config"]["Labels"][
            "com.docker.compose.service"
        ]
        self.fixture.binding["services"][service]["repo_digests"] = [key]
        with self.assertRaisesRegex(
            RecoveryError, "runtime_registry_identity_mismatch"
        ):
            self.backend.inspect()

    def test_same_project_old_container_rejected_on_first_inspect(self):
        self.docker.containers[0]["Id"] = "e" * 64
        with self.assertRaisesRegex(RecoveryError, "runtime_owner_changed"):
            self.backend.inspect()


if __name__ == "__main__":
    unittest.main()
