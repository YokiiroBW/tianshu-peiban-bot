"""Resource-policy checks; synthetic unit tests, not NAS startup evidence."""

import copy
import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import test_packaging as packaging
from bundle import preflight
from manifest import Refused, read_json, write_json
from resource_profile import constrain, container_check, host_check, validate
from observability_release import apply_network_plan, apply_resource_profile
from fake_linux_docker import Docker, clock_patches
from linux_runtime import leased_execute

PROFILE = {"kind": "nas-cpuset-qa-v1", "cpus": [6, 7], "pid_limit": "unsupported"}


class ProfileTests(unittest.TestCase):
    def test_profile_rejects_ambiguous_or_unbounded_cpu_input(self):
        for value in ([], [1], [True, 2], [7, 6], [6, 6], [-1, 1], [1, 4096], "6,7"):
            with self.subTest(value=value), self.assertRaises(Refused):
                validate({**PROFILE, "cpus": value})
        for value in (
            {**PROFILE, "extra": 1},
            {**PROFILE, "pid_limit": "passed"},
            {**PROFILE, "kind": "automatic"},
        ):
            with self.subTest(value=value), self.assertRaises(Refused):
                validate(value)

    def test_portable_service_is_unchanged(self):
        service = {
            "cpus": "2.0",
            "pids_limit": 128,
            "mem_limit": "1g",
            "read_only": True,
        }
        expected = copy.deepcopy(service)
        self.assertEqual(constrain(service, None), expected)

    def test_nas_service_retains_nonresource_boundaries(self):
        service = {
            "cpus": "2.0",
            "pids_limit": 128,
            "mem_limit": "1g",
            "read_only": True,
            "cap_drop": ["ALL"],
            "user": "10001:10001",
        }
        result = constrain(service, PROFILE)
        self.assertEqual(
            result,
            {
                "cpuset": "6,7",
                "mem_limit": "1g",
                "read_only": True,
                "cap_drop": ["ALL"],
                "user": "10001:10001",
            },
        )

    def test_actual_docker_json_capitalization_and_missing_fields(self):
        info = {"MemoryLimit": True, "CPUSet": True, "PidsLimit": False, "NCPU": 8}
        value = host_check(PROFILE, info)
        self.assertEqual(value["pid_controller"], "unsupported")
        self.assertFalse(value["release_ready"])
        for key in info:
            missing = dict(info)
            del missing[key]
            with self.subTest(key=key), self.assertRaises(Refused):
                host_check(PROFILE, missing)

    def test_unavailable_cpu_or_controller_refused(self):
        info = {"MemoryLimit": True, "CPUSet": True, "PidsLimit": False, "NCPU": 8}
        for key, value in (
            ("NCPU", 7),
            ("NCPU", True),
            ("MemoryLimit", False),
            ("CPUSet", False),
            ("PidsLimit", True),
        ):
            with self.subTest(key=key), self.assertRaises(Refused):
                host_check(PROFILE, {**info, key: value})

    def test_real_hostconfig_must_match_before_start(self):
        service = {"cpuset": "6,7", "mem_limit": "1g"}
        config = {
            "CpusetCpus": "6,7",
            "Memory": 1024**3,
            "PidsLimit": None,
            "NanoCpus": 0,
            "CpuQuota": 0,
        }
        container_check(service, {"HostConfig": config})
        for key, value in (
            ("CpusetCpus", ""),
            ("Memory", 0),
            ("PidsLimit", 128),
            ("NanoCpus", 2000000000),
            ("CpuQuota", 200000),
        ):
            with self.subTest(key=key), self.assertRaises(Refused):
                container_check(service, {"HostConfig": {**config, key: value}})


class ProfilePackagingTests(unittest.TestCase):
    def test_observability_network_plan_pins_declared_subnets(self):
        document = {
            "networks": {
                "observe": {"internal": True},
                "storage": {"internal": True},
            }
        }
        result = apply_network_plan(
            document,
            {"observe": "10.204.46.0/24", "storage": "10.204.47.0/24"},
            ["10.204.43.0/24", "10.204.44.0/24", "10.204.45.0/24"],
        )
        self.assertEqual(
            result["networks"]["observe"]["ipam"]["config"][0]["subnet"],
            "10.204.46.0/24",
        )
        self.assertEqual(
            result["networks"]["storage"]["ipam"]["config"][0]["subnet"],
            "10.204.47.0/24",
        )
        with self.assertRaises(Refused):
            apply_network_plan(
                {
                    "networks": {
                        "observe": {"internal": True},
                        "storage": {"internal": True},
                    }
                },
                {"observe": "10.204.45.0/24", "storage": "10.204.47.0/24"},
                ["10.204.43.0/24", "10.204.44.0/24", "10.204.45.0/24"],
            )

    def test_observability_profile_rewrites_pinned_compose_resource_fields(self):
        f = self.fixture
        path = f.root / "observability" / "compose.yaml"
        path.parent.mkdir()
        write_json(
            path,
            {
                "services": {
                    "obs-grafana": {
                        "cpus": 0.75,
                        "pids_limit": 128,
                        "mem_limit": "512m",
                    },
                    "obs-loki": {
                        "cpus": 1.5,
                        "pids_limit": 128,
                        "mem_limit": "1536m",
                    },
                }
            },
        )

        apply_resource_profile(path, PROFILE)

        services = read_json(path)["services"]
        for name, limit in (("obs-grafana", "512m"), ("obs-loki", "1536m")):
            self.assertEqual(services[name]["cpuset"], "6,7")
            self.assertEqual(services[name]["mem_limit"], limit)
            self.assertNotIn("cpus", services[name])
            self.assertNotIn("pids_limit", services[name])

    def test_a1_loopback_api_ports_are_declared_and_reproducible(self):
        f = self.fixture
        f.site.update(
            project_name="tianshu-qa-a1-ports",
            bind_address="127.0.0.1",
            resource_profile=PROFILE,
            a1_loopback_api_ports={
                "companion": 19512,
                "memory": 19513,
                "gateway": 19514,
            },
        )
        write_json(f.sitepath, f.site)
        f.init()

        self.assertEqual(preflight(f.output)["status"], "package_valid")
        services = read_json(f.output / "compose.json")["services"]
        self.assertEqual(services["platform"]["ports"], ["127.0.0.1:18443:8443"])
        self.assertEqual(services["companion"]["ports"], ["127.0.0.1:19512:8765"])
        self.assertEqual(services["memory"]["ports"], ["127.0.0.1:19513:8130"])
        self.assertEqual(services["gateway"]["ports"], ["127.0.0.1:19514:8443"])
        self.assertEqual(
            read_json(f.output / "deployment.json")["compose_inputs"][
                "a1_loopback_api_ports"
            ],
            f.site["a1_loopback_api_ports"],
        )

    def test_a1_loopback_api_ports_reject_other_scopes_and_values(self):
        f = self.fixture
        base = {
            **f.site,
            "project_name": "tianshu-qa-a1-ports",
            "bind_address": "127.0.0.1",
            "resource_profile": PROFILE,
            "a1_loopback_api_ports": {
                "companion": 19512,
                "memory": 19513,
                "gateway": 19514,
            },
        }
        for change in (
            {"project_name": "tianshu-qa-other"},
            {"bind_address": "192.168.31.210"},
            {"resource_profile": None},
            {"a1_loopback_api_ports": {"companion": 19520, "memory": 19513, "gateway": 19514}},
        ):
            with self.subTest(change=change):
                write_json(f.sitepath, {**base, **change})
                with self.assertRaises(Refused):
                    f.init()
                self.assertFalse(f.output.exists())

    def test_public_http_binds_web_only_and_is_recorded(self):
        f = self.fixture
        f.site["public_web"] = True
        f.site["web_origin"] = f.site["web_origin"].replace("https:", "http:")
        p = f.configs["platform"]
        p["web"]["origin"] = f.site["web_origin"]
        p["web_access"] = {
            "host": "0.0.0.0",
            "port": 8080,
            "initial": {
                "mode": "http",
                "origin": f.site["web_origin"],
                "certificate": None,
            },
            "certificates": {"nas-web": p["tls"]},
        }
        f.save_configs()
        write_json(f.sitepath, f.site)
        f.init()
        self.assertEqual(preflight(f.output)["status"], "package_valid")
        services = read_json(f.output / "compose.json")["services"]
        self.assertEqual(
            services["platform"]["ports"],
            [f"{f.site['bind_address']}:{f.site['web_port']}:8080"],
        )
        self.assertTrue(
            read_json(f.output / "deployment.json")["compose_inputs"]["public_web"]
        )
        self.assertTrue(
            all("ports" not in services[p] for p in ("companion", "memory", "gateway"))
        )
        self.assertEqual(p["mode"], "service_https")

    def setUp(self):
        self.fixture = packaging.PackagingTests()
        self.fixture.setUpClass()
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def test_profile_bound_to_bundle_and_release_remains_denied(self):
        f = self.fixture
        f.site["resource_profile"] = PROFILE
        f.site["bind_address"] = "127.0.0.1"
        write_json(f.sitepath, f.site)
        f.init()
        self.assertEqual(preflight(f.output)["status"], "package_valid")
        self.assertEqual(
            read_json(f.output / "deployment.json")["compose_inputs"][
                "resource_profile"
            ],
            PROFILE,
        )
        for service in read_json(f.output / "compose.json")["services"].values():
            self.assertEqual(service["cpuset"], "6,7")
            self.assertNotIn("cpus", service)
            self.assertNotIn("pids_limit", service)
        with self.assertRaisesRegex(Refused, "nas_qa_profile_not_release_approved"):
            preflight(f.output, release=True)

    def test_nas_profile_refuses_real_exposure_or_project(self):
        f = self.fixture
        for key, value in (
            ("bind_address", "192.168.31.210"),
            ("project_name", "tianshu-production"),
        ):
            site = {
                **f.site,
                "resource_profile": PROFILE,
                "bind_address": "127.0.0.1",
                key: value,
            }
            write_json(f.sitepath, site)
            with self.subTest(key=key), self.assertRaises(Refused):
                f.init()
            self.assertFalse(f.output.exists())

    def test_lan_qa_requires_separate_profile_and_matching_private_ip_origin(self):
        from configuration import load_inputs

        f = self.fixture
        site = {
            **f.site,
            "resource_profile": {**PROFILE, "kind": "nas-cpuset-lan-qa-v1"},
            "bind_address": "192.168.31.210",
            "web_origin": "https://192.168.31.210:18443",
        }
        write_json(f.sitepath, site)
        self.assertEqual(load_inputs(f.sitepath)["bind_address"], "192.168.31.210")
        for change in (
            {"bind_address": "0.0.0.0"},
            {"bind_address": "8.8.8.8"},
            {"bind_address": "127.0.0.1"},
            {"web_origin": "https://other.test:18443"},
        ):
            write_json(f.sitepath, {**site, **change})
            with self.subTest(change=change), self.assertRaises(Refused):
                load_inputs(f.sitepath)

    def exercise_runtime(self, wrong_memory=False, missing_controller=False):
        f = self.fixture
        f.site.update(resource_profile=PROFILE, bind_address="127.0.0.1")
        write_json(f.sitepath, f.site)
        f.init()
        root = f.output
        (root / "reports").mkdir()
        receipt = {
            "assertion_ref": "origin:" + "a" * 32,
            "expires_at": (
                datetime.now(timezone.utc) + timedelta(seconds=299)
            ).isoformat(),
        }
        fake = Docker(root, receipt)

        def run(argv, **kwargs):
            result = fake(argv, **kwargs)
            if "create" in argv:
                for cid, row in fake.active.items():
                    row["HostConfig"].update(
                        CpusetCpus=fake.specs[cid]["cpuset"],
                        Memory=0 if wrong_memory else 1024**3,
                        PidsLimit=None,
                        NanoCpus=0,
                        CpuQuota=0,
                    )
            return result

        info = {
            "MemoryLimit": not missing_controller,
            "CPUSet": True,
            "PidsLimit": False,
            "NCPU": 8,
        }
        report = {"results": []}
        with (
            patch("linux_runtime.preflight"),
            patch("linux_runtime.subprocess.run", side_effect=run),
            patch(
                "linux_runtime.subprocess.check_output",
                return_value=json.dumps(info).encode(),
            ),
            clock_patches(),
        ):
            if wrong_memory or missing_controller:
                with self.assertRaises(Refused):
                    leased_execute(
                        root, None, [], report, root / "reports/nas-test.json"
                    )
            else:
                leased_execute(root, None, [], report, root / "reports/nas-test.json")
        return report, fake

    def test_full_core_and_oneoff_lifecycle_inherits_profile(self):
        report, fake = self.exercise_runtime()
        self.assertTrue(report["stop_confirmed"])
        self.assertEqual(
            report["resource_capabilities"]["pid_controller"], "unsupported"
        )
        self.assertGreater(len(fake.specs), 4)
        for spec in fake.specs.values():
            self.assertEqual(spec["cpuset"], "6,7")
            self.assertNotIn("cpus", spec)
            self.assertNotIn("pids_limit", spec)

    def test_ineffective_memory_limit_never_starts_container(self):
        _, fake = self.exercise_runtime(wrong_memory=True)
        self.assertFalse(any(call[:2] == ["docker", "start"] for call in fake.calls))

    def test_missing_host_controller_refuses_before_any_docker_mutation(self):
        _, fake = self.exercise_runtime(missing_controller=True)
        self.assertEqual(fake.calls, [])


if __name__ == "__main__":
    unittest.main()
