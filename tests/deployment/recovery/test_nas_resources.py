import copy
import tempfile
import unittest
from pathlib import Path

from ops.recovery.nas_resources import (
    check_container,
    check_definition,
    check_host,
    limits,
    profile_for,
)
from ops.recovery.safety import RecoveryError, canonical


class NASRecoveryResources(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.profile = {
            "kind": "nas-cpuset-qa-v1",
            "cpus": [6, 7],
            "pid_limit": "unsupported",
        }
        self.metadata = {
            "project_name": "tianshu-qa-test",
            "compose_inputs": {
                "bind_address": "127.0.0.1",
                "resource_profile": self.profile,
            },
            "tls_provenance": dict.fromkeys(
                ("platform", "companion", "memory", "gateway"), "isolated_test"
            ),
        }
        self.definition = {"cpuset": "6,7", "mem_limit": "512m"}
        self.runtime = {
            "projects": {
                "core": {"compose_json": {"services": {"platform": self.definition}}}
            }
        }
        self.persist()

    def persist(self):
        (self.root / "deployment.json").write_bytes(canonical(self.metadata))

    def test_requires_same_explicit_qa_profile(self):
        self.assertEqual(profile_for(self.root, self.runtime), self.profile)
        self.metadata["compose_inputs"]["bind_address"] = "0.0.0.0"
        self.persist()
        with self.assertRaises(ValueError):
            profile_for(self.root, self.runtime)
        self.metadata["compose_inputs"]["bind_address"] = "127.0.0.1"
        self.metadata["compose_inputs"]["resource_profile"] = None
        self.persist()
        with self.assertRaises((ValueError, RecoveryError)):
            profile_for(self.root, self.runtime)

    def test_clone_cannot_change_limits_or_use_implicit_profile(self):
        self.assertTrue(check_definition(self.definition, self.profile))
        for bad in (
            {**self.definition, "cpuset": "0,1"},
            {**self.definition, "pids_limit": 128},
            {"cpuset": "6,7"},
        ):
            with self.assertRaises(ValueError):
                check_definition(bad, self.profile)
        with self.assertRaises(RecoveryError):
            check_definition(self.definition, None)

    def test_actual_container_resource_drift_refused(self):
        expected = {"resource_limits": limits(self.definition)}
        host = {
            "CpusetCpus": "6,7",
            "Memory": 512 * 1024**2,
            "PidsLimit": None,
            "NanoCpus": 0,
            "CpuQuota": 0,
        }
        check_container(expected, {"HostConfig": host})
        for key, value in [
            ("Memory", 0),
            ("CpusetCpus", "0,1"),
            ("NanoCpus", 100),
            ("PidsLimit", 128),
        ]:
            bad = copy.deepcopy(host)
            bad[key] = value
            with self.assertRaises(ValueError):
                check_container(expected, {"HostConfig": bad})

    def test_actual_host_must_have_memory_and_cpuset(self):
        class Docker:
            def run(inner, *args):
                return canonical(
                    {
                        "MemoryLimit": True,
                        "CPUSet": False,
                        "PidsLimit": False,
                        "NCPU": 8,
                    }
                ).decode()

        with self.assertRaises(ValueError):
            check_host(Docker(), self.profile)


if __name__ == "__main__":
    unittest.main()
