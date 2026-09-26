import copy
import json
import os
from pathlib import Path
import tempfile
import unittest

from helpers import SNAPSHOT, deployment_fixture
from configure import prepare
from nas_resources import bind, check_compose, container_check, host_check, validate

PROFILE = {"kind": "nas-cpuset-qa-v1", "cpus": [6, 7], "pid_limit": "unsupported"}


class NasResourcesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.metadata = {
            "project_name": "tianshu-qa-nas-observe",
            "compose_inputs": {
                "resource_profile": PROFILE,
                "bind_address": "127.0.0.1",
            },
            "tls_provenance": {
                k: "isolated_test"
                for k in ("platform", "companion", "memory", "gateway")
            },
        }

    def write_metadata(self):
        (self.root / "deployment.json").write_text(json.dumps(self.metadata))

    def test_profile_binding_rejects_mismatch_lan_production_and_missing_tls(self):
        cases = [
            ("project_name", "production"),
            (
                "compose_inputs",
                {"resource_profile": PROFILE, "bind_address": "0.0.0.0"},
            ),
            ("tls_provenance", {}),
            ("tls_provenance", {"platform": "production"}),
        ]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                original = copy.deepcopy(self.metadata)
                self.metadata[key] = value
                self.write_metadata()
                with self.assertRaises(ValueError):
                    bind(self.root, PROFILE)
                self.metadata = original
        self.write_metadata()
        self.assertEqual(bind(self.root, PROFILE), PROFILE)
        with self.assertRaises(ValueError):
            bind(self.root, None)
        changed = dict(PROFILE, cpus=[4, 5])
        with self.assertRaises(ValueError):
            bind(self.root, changed)

    def test_bad_profiles_and_host_capabilities(self):
        for profile in (
            {},
            dict(PROFILE, cpus=[True, 7]),
            dict(PROFILE, cpus=[7, 6]),
            dict(PROFILE, pid_limit="supported"),
        ):
            with self.subTest(profile=profile), self.assertRaises(ValueError):
                validate(profile)
        info = {"MemoryLimit": True, "CPUSet": True, "PidsLimit": False, "NCPU": 8}
        self.assertFalse(host_check(PROFILE, info)["release_ready"])
        for key, value in (
            ("MemoryLimit", False),
            ("CPUSet", False),
            ("PidsLimit", True),
            ("NCPU", 7),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                host_check(PROFILE, dict(info, **{key: value}))

    def test_explicit_lan_qa_profile_requires_matching_private_origin(self):
        profile = {**PROFILE, "kind": "nas-cpuset-lan-qa-v1"}
        self.metadata["compose_inputs"].update(
            resource_profile=profile,
            bind_address="192.168.31.210",
            web_origin="https://192.168.31.210:19443",
        )
        self.write_metadata()
        self.assertEqual(bind(self.root, profile), profile)
        self.metadata["compose_inputs"]["web_origin"] = "https://other.test:19443"
        self.write_metadata()
        with self.assertRaisesRegex(ValueError, "nas_lan_origin_address_mismatch"):
            bind(self.root, profile)

    def test_resident_profile_requires_exact_project_lan_http_and_operator_tls(self):
        profile = {**PROFILE, "kind": "nas-cpuset-resident-v1"}
        self.metadata.update(project_name="tianshu-v2-resident")
        self.metadata["compose_inputs"].update(
            resource_profile=profile,
            bind_address="192.168.31.210",
            web_origin="http://192.168.31.210:18443",
            public_web=True,
        )
        self.metadata["tls_provenance"] = dict.fromkeys(
            ("platform", "companion", "memory", "gateway"), "operator_supplied"
        )
        self.write_metadata()
        self.assertEqual(bind(self.root, profile), profile)
        for section, key, value in (
            ("metadata", "project_name", "tianshu-qa-nas-observe"),
            ("compose_inputs", "bind_address", "127.0.0.1"),
            ("compose_inputs", "web_origin", "https://192.168.31.210:18443"),
            ("compose_inputs", "public_web", False),
            ("tls_provenance", "platform", "isolated_test"),
        ):
            original = copy.deepcopy(self.metadata)
            target = self.metadata if section == "metadata" else self.metadata[section]
            target[key] = value
            self.write_metadata()
            with self.subTest(section=section, key=key), self.assertRaises(ValueError):
                bind(self.root, profile)
            self.metadata = original
        with self.assertRaisesRegex(ValueError, "resident_cpu_set_mismatch"):
            validate({**profile, "cpus": [4, 5]})

    def test_actual_resource_mismatches_rejected(self):
        spec = {"cpuset": "6,7", "mem_limit": "768m"}
        config = {
            "CpusetCpus": "6,7",
            "Memory": 768 * 1024**2,
            "NanoCpus": 0,
            "CpuQuota": 0,
            "PidsLimit": None,
        }
        container_check(spec, {"HostConfig": config})
        for key, value in (
            ("CpusetCpus", "0,1"),
            ("Memory", 0),
            ("NanoCpus", 1),
            ("CpuQuota", 10000),
            ("PidsLimit", 128),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                container_check(spec, {"HostConfig": dict(config, **{key: value})})
        with self.assertRaises(ValueError):
            check_compose({"services": {"x": dict(spec, cpus=1)}}, PROFILE)
        with self.assertRaises(ValueError):
            check_compose({"services": {"x": spec}}, None)

    def test_public_configuration_nas_limits_and_release_refusal(self):
        contract = Path(os.environ["DEP_B_CONTRACT"])
        manifest, settings = deployment_fixture(self.root, contract)
        self.write_metadata()
        settings["resource_profile"] = PROFILE
        with self.assertRaisesRegex(ValueError, "not_release_approved"):
            prepare(self.root, settings, manifest, "release", SNAPSHOT, candidate=False)
        self.assertFalse((self.root / "release").exists())
        output = prepare(
            self.root, settings, manifest, "bundle", SNAPSHOT, candidate=True
        )
        stack = json.loads((output / "compose.yaml").read_bytes())
        check_compose(stack, PROFILE)
        self.assertEqual(len(stack["services"]), 5)
        for spec in stack["services"].values():
            self.assertEqual(spec["cpuset"], "6,7")
            self.assertEqual(spec["user"], "10001:10001")
            self.assertTrue(spec["read_only"])
            self.assertNotIn("pids_limit", spec)
            self.assertNotIn("cpus", spec)
