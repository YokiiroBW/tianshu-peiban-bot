"""Distribution checks use native generators and isolated fictional config bytes."""

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deploy.observability import configs
from deploy.single_container.compose import compose
from deploy.single_container.installation import export, file_bytes, main
from deploy.single_container.plan import make_installation
from deploy.single_container.profile import MODULES, OBSERVABILITY, PYTHON, adapt

ROOT = Path(__file__).resolve().parents[3]


def native_files():
    snapshot = json.loads((ROOT / "deploy/observability/vocabulary.json").read_bytes())
    files = {}
    for name in MODULES:
        state = (
            "/data"
            if name == "companion"
            else "/srv/tianshu"
            if name in {"memory", "knowledge"}
            else "/var/lib/tianshu"
        )
        files[f"modules/{name}/settings.json"] = (
            '{\r\n  "database_path" : "' + state + '/unchanged.sqlite",\r\n'
            '  "log_directory" : "/var/log/tianshu",\r\n'
            '  "storage_backends" : {"driver":"postgresql","namespace":"keep-exact"},\r\n'
            '  "service" : "https://gateway.internal:8443/v1",\r\n'
            '  "provider" : "https://external.example:8443/v1",\r\n'
            '  "secret" : "fictional-$-value"\r\n}\r\n'
        ).encode()
        files[f"private/{name}.env"] = (
            b"# original\r\nTOKEN='fictional-$-value'\r\nSSL_CERT_FILE='/etc/tianshu/tls/ca.pem'\r\n"
        )
    files.update(
        {
            "contracts/memory/diagnostics/v1/manifest.json": b'{"frozen":true}\r\n',
            "contracts/knowledge/diagnostics/v1/manifest.json": b'{"frozen":true}\r\n',
            "contracts/diagnostics/v1/manifest.json": b'{"frozen":true}\r\n',
            "observability/vector.json": configs.vector(
                snapshot, include_knowledge=True
            ),
            "observability/loki.json": configs.loki(),
            "observability/prometheus.json": configs.prometheus(),
            "observability/prometheus-web.json": {
                "tls_server_config": {
                    "cert_file": "/run/tls/prometheus.pem",
                    "key_file": "/run/tls/prometheus.key",
                }
            },
            "observability/provisioning/datasources/native.yaml": json.dumps(
                configs.datasources("PUBLIC CERT\n")
            ),
            "observability/provisioning/alerting/native.yaml": configs.alert_rules(),
            "observability/provisioning/dashboards/native.yaml": {
                "options": {"path": "/etc/grafana/dashboards"}
            },
            "observability/dashboards/native.json": configs.dashboard(),
            "observability/vocabulary.json": snapshot,
            "observability/guard.json": {
                "loki_url": "https://obs-loki:3100",
                "ledger": "/var/lib/guard/integrity.sqlite",
                "snapshot": "/opt/observability/vocabulary.json",
                "contract": "/contracts/diagnostics/v1",
                "log_roots": {name: f"/sources/{name}" for name in MODULES},
                "log_budgets": {name: 1073741824 for name in MODULES},
                "storage_roots": {
                    name: f"/capacity/{name}" for name in ("vector", "loki")
                },
                "ledger_max_events": 1_000_000,
                "interval_seconds": 30,
            },
            "observability/tls/grafana.key": b"FICTIONAL PRIVATE BYTES\r\n",
            "observability/secrets/query_token": b"fictional-query-$-bytes\r\n",
        }
    )
    return files


def installation(files=None, **kwargs):
    return make_installation(
        "tianshu:local",
        native_files() if files is None else files,
        knowledge_client="existing-knowledge-client",
        observability_environment={
            "obs-grafana": {
                "GF_SERVER_DOMAIN": "logs.internal",
                "GF_SERVER_ROOT_URL": "https://logs.internal:18490/",
                "GF_SECURITY_COOKIE_SAMESITE": "strict",
            }
        },
        **kwargs,
    )


class DistributionTests(unittest.TestCase):
    def test_native_modules_share_one_process_without_internal_tcp_or_private_trees(
        self,
    ):
        value = installation()
        self.assertEqual(
            set(value), {"version", "image", "files", "processes", "mounts", "comics"}
        )
        host = value["files"]["host.json"]
        self.assertEqual(set(host["modules"]), set(MODULES))
        self.assertEqual(
            [p["name"] for p in value["processes"]], [*OBSERVABILITY, "business-host"]
        )
        business = value["processes"][-1]
        self.assertEqual((business["uid"], business["gid"]), (10001, 10001))
        self.assertEqual(business["health"]["type"], "module_state")
        self.assertEqual(business["health"]["path"], host["state_file"])
        self.assertEqual(
            business["argv"][-6:],
            [
                "--",
                PYTHON,
                "-m",
                "deploy.single_container.host",
                "--config",
                "/config/host.json",
            ],
        )
        argv = business["argv"]
        locked = [argv[i + 1] for i, arg in enumerate(argv) if arg == "--state-dir"]
        self.assertEqual(set(locked), {f"/data/modules/{name}" for name in MODULES})
        for name, spec in host["modules"].items():
            self.assertEqual(spec["env_file"], [f"/config/private/{name}.env"])
            self.assertEqual(
                spec["origin"],
                f"https://{name}.internal:{8443 if name in {'gateway', 'platform'} else {'memory': 8130, 'knowledge': 18135, 'companion': 8765}[name]}",
            )
            self.assertEqual("contracts" in spec, name in {"memory", "knowledge"})
        self.assertNotIn("/opt/tianshu/modules/", json.dumps(value["processes"]))
        self.assertNotIn("--port", business["argv"])

    def test_export_preserves_raw_contracts_secrets_and_json_except_relocated_paths(
        self,
    ):
        files = native_files()
        value = installation(files)
        before = copy.deepcopy(value)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            result = export(value, root)
            self.assertEqual(value, before)
            self.assertEqual(result["business_processes"], 1)
            self.assertEqual(result["native_observer_processes"], 5)
            self.assertNotIn("fictional", json.dumps(result))
            for name in (
                "contracts/memory/diagnostics/v1/manifest.json",
                "observability/tls/grafana.key",
                "observability/secrets/query_token",
            ):
                self.assertEqual((root / "config" / name).read_bytes(), files[name])
            expected = (
                files["modules/memory/settings.json"]
                .replace(b"/srv/tianshu/", b"/data/modules/memory/")
                .replace(b"/var/log/tianshu", b"/data/logs/memory")
            )
            self.assertEqual(
                (root / "config/modules/memory/settings.json").read_bytes(), expected
            )
            self.assertEqual(
                (root / "config/private/memory.env").read_bytes(),
                files["private/memory.env"].replace(
                    b"/etc/tianshu/", b"/config/modules/memory/"
                ),
            )
            service = json.loads((root / "compose.yaml").read_bytes())["services"][
                "tianshu"
            ]
            self.assertIn("18446:8443", service["ports"])
            self.assertNotIn("18765:18765", service["ports"])
            self.assertFalse(
                any(
                    name + ".internal:127.0.0.1" in service["extra_hosts"]
                    for name in MODULES
                )
            )
            self.assertIn("logs.internal:127.0.0.1", service["extra_hosts"])
            self.assertIn("./data/legacy-state:/var/lib/tianshu", service["volumes"])
            self.assertIn("./data/legacy-logs:/var/log/tianshu", service["volumes"])
            self.assertFalse(
                any(
                    item.startswith(("/var/lib/tianshu:", "/var/log/tianshu:"))
                    for item in service["tmpfs"]
                )
            )
            self.assertTrue((root / "data/legacy-state").is_dir())

    def test_observability_retains_sources_retention_tls_and_true_readiness(
        self,
    ):
        value = installation()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(value, root)
            config = root / "config/observability"
            vector = json.loads((config / "vector.json").read_bytes())
            self.assertEqual(set(vector["sources"]), {*MODULES, "internal_metrics"})
            self.assertEqual(
                vector["sources"]["knowledge"]["include"][0],
                "/data/logs/knowledge/*.jsonl",
            )
            self.assertEqual(
                vector["sinks"]["loki"]["endpoint"], "https://obs-guard:8445"
            )
            self.assertTrue(vector["sinks"]["loki"]["acknowledgements"]["enabled"])
            self.assertEqual(
                vector["secret"]["files"]["path"], "/config/observability/secrets"
            )
            self.assertEqual(
                json.loads((config / "loki.json").read_bytes())["limits_config"][
                    "retention_period"
                ],
                "720h",
            )
            guard = json.loads((config / "guard.json").read_bytes())
            self.assertNotIn("ledger", guard)
            self.assertNotIn("ledger_max_events", guard)
            self.assertEqual(guard["storage_roots"]["guard"], "/data/observability/guard")
            self.assertEqual(guard["vector_config"], "/config/observability/vector.json")
            self.assertEqual(guard["snapshot"], "/config/observability/vocabulary.json")
            self.assertEqual(set(guard["log_roots"]), set(MODULES))
        processes = {p["name"]: p for p in value["processes"]}
        self.assertIn("client_keyfile", processes["obs-loki"]["health"])
        self.assertIn("bearer_token_file", processes["obs-guard"]["health"])
        self.assertEqual(
            processes["obs-guard"]["health"]["url"], "https://obs-guard:8445/ready"
        )
        self.assertEqual(processes["obs-guard"]["argv"][0], PYTHON)
        grafana = processes["obs-grafana"]
        self.assertTrue(
            grafana["argv"][1].startswith(
                "/opt/tianshu/application/deploy/observability/"
            )
        )
        self.assertEqual(
            grafana["env"]["QUERY_TOKEN_FILE"],
            "/config/observability/secrets/query_token",
        )
        self.assertEqual(
            grafana["env"]["GF_SERVER_ROOT_URL"], "https://logs.internal:18490/"
        )
        self.assertEqual(
            grafana["health"]["url"], "https://logs.internal:3000/api/health"
        )

    def test_native_public_web_port_and_origin_are_preserved(self):
        files = native_files()
        platform = json.loads(files["modules/platform/settings.json"])
        platform["web_access"] = {
            "host": "0.0.0.0",
            "port": 8080,
            "initial": {"mode": "proxy", "origin": "https://chat.example/"},
            "certificates": {},
        }
        files["modules/platform/settings.json"] = platform
        value = installation(files)
        self.assertNotIn("frontend", value["files"]["host.json"])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(
                value,
                root,
                public_port=19446,
                observability_ports={"grafana": 19490, "query": 19491},
            )
            service = json.loads((root / "compose.yaml").read_bytes())["services"][
                "tianshu"
            ]
            self.assertEqual(service["ports"][0], "19446:8080")
            exported = json.loads(
                (root / "config/modules/platform/settings.json").read_bytes()
            )
            self.assertEqual(exported["web_access"], platform["web_access"])

    def test_one_retention_value_projects_without_changing_native_sources_or_rules(
        self,
    ):
        files = native_files()
        files["observability/guard.json"]["retention_days"] = 7
        files["observability/guard.json"]["log_max_events"] = 4321
        before = copy.deepcopy(files)
        value = installation(files)
        self.assertEqual(files, before)
        self.assertEqual(
            value["files"]["observability/guard.json"]["retention_days"], 7
        )
        self.assertNotIn("log_max_events", value["files"]["observability/guard.json"])
        self.assertNotIn("ledger_max_events", value["files"]["observability/guard.json"])
        self.assertEqual(
            value["files"]["observability/guard.json"]["host_state_path"],
            value["files"]["host.json"]["state_file"],
        )
        limits = value["files"]["observability/loki.json"]["limits_config"]
        self.assertEqual(limits["retention_period"], "168h")
        self.assertEqual(limits["max_query_length"], "192h")
        self.assertEqual(limits["reject_old_samples_max_age"], "720h")
        for name in (
            "observability/vector.json",
            "observability/vocabulary.json",
            *[f"modules/{module}/settings.json" for module in MODULES],
        ):
            self.assertEqual(value["files"][name], before[name])
        # Editing the canonical guard setting is sufficient; export derives Loki.
        value["files"]["observability/guard.json"]["retention_days"] = 2
        value["files"]["observability/guard.json"]["host_state_path"] = "/obsolete.json"
        original = copy.deepcopy(value)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(value, root)
            guard = json.loads((root / "config/observability/guard.json").read_bytes())
            loki = json.loads((root / "config/observability/loki.json").read_bytes())
            self.assertEqual(guard["retention_days"], 2)
            self.assertEqual(
                guard["host_state_path"], value["files"]["host.json"]["state_file"]
            )
            self.assertEqual(loki["limits_config"]["retention_period"], "48h")
            self.assertEqual(
                json.loads((root / "compose.yaml").read_bytes())["services"]["tianshu"][
                    "logging"
                ],
                {"driver": "local", "options": {"max-size": "10m", "max-file": "3"}},
            )
        self.assertEqual(value, original)

    def test_explicit_retention_overrides_default_and_rejects_invalid_before_export(
        self,
    ):
        self.assertEqual(
            installation()["files"]["observability/guard.json"]["retention_days"], 30
        )
        self.assertNotIn("log_max_events", installation()["files"]["observability/guard.json"])
        value = installation(retention_days=45)
        self.assertEqual(
            value["files"]["observability/guard.json"]["retention_days"], 45
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(value, root, retention_days=1)
            guard = json.loads((root / "config/observability/guard.json").read_bytes())
            self.assertEqual(guard["retention_days"], 1)
            self.assertNotIn("log_max_events", guard)
            source = Path(temporary) / "installation.json"
            source.write_text(
                json.dumps(value, default=lambda raw: raw.decode("utf-8"))
            )
            cli_root = Path(temporary) / "cli"
            with patch("sys.stdout"):
                main(
                    [
                        "--installation",
                        str(source),
                        "--output",
                        str(cli_root),
                        "--retention-days",
                        "3",
                    ]
                )
            cli_guard = json.loads(
                (cli_root / "config/observability/guard.json").read_bytes()
            )
            self.assertEqual(cli_guard["retention_days"], 3)
            self.assertNotIn("log_max_events", cli_guard)
        for invalid in (0, -1, True, "7", 1.5):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "retention_days"):
                    installation(retention_days=invalid)
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary) / "bad"
                    with self.assertRaisesRegex(ValueError, "retention_days"):
                        export(value, root, retention_days=invalid)
                    self.assertFalse(root.exists())

    def test_observer_log_binds_become_producer_writable_without_widening_other_binds(
        self,
    ):
        logs = [
            {
                "type": "bind",
                "source": f"/existing/logs/{name}",
                "target": f"/data/logs/{name}",
                "read_only": True,
            }
            for name in MODULES
        ]
        unrelated = [
            {
                "type": "bind",
                "source": f"/existing/{name}",
                "target": target,
                "read_only": True,
            }
            for name, target in (
                ("media", "/media/ComicInputsOriginal"),
                ("archive", "/data/logs/platform/archive"),
                ("database", "/data/modules/platform"),
            )
        ]
        mounts = logs + unrelated
        before = copy.deepcopy(mounts)
        value = installation(mounts=mounts)
        self.assertEqual(mounts, before)
        self.assertEqual(
            value["mounts"],
            [{**mount, "read_only": False} for mount in logs] + unrelated,
        )
        # Export also handles an existing captured installation with reader-only binds.
        value["mounts"] = copy.deepcopy(before)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(value, root)
            service = json.loads((root / "compose.yaml").read_bytes())["services"][
                "tianshu"
            ]
            self.assertEqual(
                service["volumes"][-len(mounts) :],
                [
                    {**mount, "read_only": False, "bind": {"create_host_path": False}}
                    for mount in logs
                ]
                + [
                    {**mount, "bind": {"create_host_path": False}}
                    for mount in unrelated
                ],
            )
            self.assertEqual(value["mounts"], before)

    def test_onebot_is_module_with_native_storage_and_token_not_another_process(self):
        files = native_files()
        files["modules/onebot/storage.json"] = (
            b'{"storage_backends":{"driver":"sqlite","namespace":"keep"}}\r\n'
        )
        files["modules/onebot/secrets/onebot-token"] = b"fictional-onebot-raw\r\n"
        value = installation(files)
        onebot = value["files"]["host.json"]["modules"]["onebot"]
        self.assertEqual(onebot["origin"], "http://onebot:18765")
        self.assertEqual(
            onebot["token_file"], "/config/modules/onebot/secrets/onebot-token"
        )
        self.assertEqual(len(value["processes"]), 6)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(value, root)
            self.assertIn(
                "18765:18765",
                json.loads((root / "compose.yaml").read_bytes())["services"]["tianshu"][
                    "ports"
                ],
            )
            self.assertEqual(
                (root / "config/modules/onebot/storage.json").read_bytes(),
                files["modules/onebot/storage.json"],
            )
        del files["modules/onebot/secrets/onebot-token"]
        with self.assertRaisesRegex(ValueError, "onebot_native"):
            installation(files)

    def test_http_console_and_explicit_tls_api_keep_native_origins_and_config(self):
        files = native_files()
        platform = json.loads(files["modules/platform/settings.json"])
        platform["web_access"] = {
            "host": "0.0.0.0",
            "port": 8080,
            "initial": {
                "mode": "http",
                "origin": "http://192.0.2.10:18446",
                "certificate": None,
            },
            "certificates": {},
        }
        files["modules/platform/settings.json"] = platform
        origins = {name: f"https://{name}.internal:8443" for name in MODULES}
        origins.update(
            memory="https://memory.internal:8130",
            knowledge="https://knowledge.internal:8131",
            companion="https://companion.internal:8765",
        )
        frontend = {
            "host": "0.0.0.0",
            "port": 8443,
            "tls_certfile": "/config/modules/platform/tls/server.pem",
            "tls_keyfile": "/config/modules/platform/tls/server.key",
        }
        before = copy.deepcopy(files)
        value = installation(files, native_origins=origins, frontend=frontend)
        self.assertEqual(files, before)
        self.assertEqual(
            {
                name: spec["origin"]
                for name, spec in value["files"]["host.json"]["modules"].items()
            },
            origins,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "candidate"
            export(value, root, public_port=28446, api_port=28443)
            ports = json.loads((root / "compose.yaml").read_bytes())["services"][
                "tianshu"
            ]["ports"]
            self.assertIn("28446:8080", ports)
            self.assertIn("28443:8443", ports)
            config = json.loads(
                (root / "config/modules/platform/settings.json").read_bytes()
            )
            self.assertEqual(config["web_access"], platform["web_access"])
            self.assertEqual(config["service"], platform["service"])
        for change in (
            {"knowledge": "https://knowledge.internal:8131"},
            {**origins, "memory": "https://memory.internal:8130/path"},
            {**origins, "memory": "https://memory.internal:8130?query=1"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                installation(files, native_origins=change)
        overlap = copy.deepcopy(value)
        overlap["files"]["host.json"]["frontend"]["port"] = 8080
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "bad"
            with self.assertRaisesRegex(ValueError, "frontend_ports_overlap"):
                export(overlap, root, api_port=28443)
            self.assertFalse(root.exists())

    def test_candidate_published_ports_are_explicit_and_distinct(self):
        ports = compose(
            "tianshu:local",
            frontend_port=8080,
            api_port=28443,
            onebot=True,
            onebot_port=28765,
        )["services"]["tianshu"]["ports"]
        self.assertIn("28765:18765", ports)
        self.assertIn("28443:8443", ports)
        with self.assertRaisesRegex(ValueError, "ports_overlap"):
            compose("tianshu:local", frontend_port=8080, api_port=18446)
        with self.assertRaisesRegex(ValueError, "listener_ports_overlap"):
            compose("tianshu:local", api_port=18443)

    def test_comics_needs_existing_plugin_and_only_fixed_control_reference_is_added(
        self,
    ):
        with self.assertRaisesRegex(ValueError, "native_comic_files"):
            installation(
                comics={"socket": "/run/tianshu/comics/control.sock", "processes": []}
            )
        files = native_files()
        platform = json.loads(files["modules/platform/settings.json"])
        platform["plugins"] = {
            "comic_files": {
                "defaults": {
                    "import_directory": "/existing/media",
                    "import_socket": "/run/tianshu/comics/inputs/inputs.sock",
                    "publisher_socket": "/run/tianshu/comics/publisher/publisher.sock",
                }
            }
        }
        files["modules/platform/settings.json"] = platform
        comics = {
            "socket": "/run/tianshu/comics/control.sock",
            "client_uid": 10001,
            "processes": [],
        }
        result = installation(files, comics=comics)
        self.assertEqual(
            result["files"]["modules/platform/settings.json"]["plugins"]["comic_files"][
                "defaults"
            ],
            platform["plugins"]["comic_files"]["defaults"],
        )
        self.assertEqual(
            result["files"]["modules/platform/settings.json"]["plugins"]["comic_files"][
                "runtime_socket"
            ],
            comics["socket"],
        )
        self.assertNotIn("runtime_socket", platform["plugins"]["comic_files"])

    def test_invalid_exports_never_create_output_or_overwrite_existing_installation(
        self,
    ):
        for mutate in (
            lambda item: item["processes"].append({"name": "memory"}),
            lambda item: item["files"].update(
                {"contracts/manifest.json": {"cannot": "serialize"}}
            ),
            lambda item: item["files"].update({"../escape": b"bad"}),
            lambda item: item["mounts"].append(
                {"type": "bind", "source": "/old", "target": "/data"}
            ),
            lambda item: item["files"]["observability/prometheus.json"][
                "scrape_configs"
            ].append({"static_configs": [{"targets": ["memory.internal:8130"]}]}),
        ):
            value = installation()
            mutate(value)
            with (
                self.subTest(mutate=mutate),
                tempfile.TemporaryDirectory() as temporary,
            ):
                root = Path(temporary) / "new"
                with self.assertRaises(ValueError):
                    export(value, root)
                self.assertFalse(root.exists())
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            marker = root / "keep"
            marker.write_bytes(b"unchanged")
            with self.assertRaisesRegex(ValueError, "already_exists"):
                export(installation(), root)
            self.assertEqual(marker.read_bytes(), b"unchanged")

    def test_legacy_four_source_input_reports_missing_knowledge_without_guessing_budget(
        self,
    ):
        value = installation()
        del value["files"]["observability/vector.json"]["sources"]["knowledge"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            with self.assertRaisesRegex(ValueError, "knowledge_vector_source_required"):
                export(value, root)
            self.assertFalse(root.exists())
        for field in ("log_roots", "log_budgets"):
            value = installation()
            del value["files"]["observability/guard.json"][field]["knowledge"]
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "new"
                with self.assertRaisesRegex(
                    ValueError, "knowledge_log_root_and_budget_required"
                ):
                    export(value, root)
                self.assertFalse(root.exists())

    def test_only_path_relocation_changes_existing_values(self):
        value = {
            "service": "https://gateway.internal:8443/v1",
            "external": "https://provider:8443/v1",
            "namespace": "keep",
            "text": "hash=/etc/tianshu",
            "path": "/data/modules/companion/file",
        }
        self.assertEqual(adapt(adapt(value, "companion"), "companion"), value)
        self.assertEqual(
            file_bytes("private/memory.env", b"TOKEN='/etc/tianshu/$secret'\r\n"),
            b"TOKEN='/etc/tianshu/$secret'\r\n",
        )
        service = compose("tianshu:local")["services"]["tianshu"]
        self.assertEqual(service["user"], "0:10001")
        self.assertEqual(service["cap_add"], ["SETUID", "SETGID", "KILL"])
        self.assertTrue(service["read_only"])
        self.assertEqual(
            file_bytes(
                "private/companion.env",
                b"TIANSHU_COMPANION_DATABASE='/data/original.db'\r\nTIANSHU_CONTRACTS='/contracts/text-dialogue/v1'\r\n",
            ),
            b"TIANSHU_COMPANION_DATABASE='/data/modules/companion/original.db'\r\nTIANSHU_CONTRACTS='/config/contracts/companion/text-dialogue/v1'\r\n",
        )

    def test_media_bind_is_preserved_without_reading_or_creating_its_source(self):
        mount = {
            "type": "bind",
            "source": "/existing/fictional-media",
            "target": "/media/ComicInputsOriginal",
            "read_only": True,
        }
        value = installation(mounts=[mount])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(value, root)
            service = json.loads((root / "compose.yaml").read_bytes())["services"][
                "tianshu"
            ]
            self.assertEqual(
                service["volumes"][-1], {**mount, "bind": {"create_host_path": False}}
            )
            self.assertEqual(value["mounts"], [mount])

    def test_path_like_credentials_namespaces_and_model_ids_are_preserved(self):
        value = {
            "database_path": "/data/companion.db",
            "password": "/data/not-a-file",
            "token": "/etc/tianshu/not-a-file",
            "headers": {"Authorization": "/data/raw-header"},
            "storage_backends": {"namespace": "/data/unchanged-identity"},
            "model": "/data/unchanged-model",
        }
        expected = {**value, "database_path": "/data/modules/companion/companion.db"}
        self.assertEqual(adapt(value, "companion"), expected)
        raw = json.dumps(value, separators=(",", ":")).encode() + b"\r\n"
        self.assertEqual(
            file_bytes("modules/companion/settings.json", raw),
            raw.replace(
                b'"database_path":"/data/', b'"database_path":"/data/modules/companion/'
            ),
        )

    def test_prometheus_authorization_file_relocates_but_authentication_bytes_do_not(
        self,
    ):
        value = configs.prometheus()
        for job in value["scrape_configs"]:
            if "authorization" in job:
                job["authorization"].update(
                    credentials="/run/secrets/literal-credential",
                    type="/run/tls/literal-type",
                )
        expected = adapt(value, "obs-guard")
        for job in expected["scrape_configs"]:
            if "authorization" in job:
                self.assertEqual(
                    job["authorization"],
                    {
                        "credentials_file": "/config/observability/secrets/metrics_token",
                        "credentials": "/run/secrets/literal-credential",
                        "type": "/run/tls/literal-type",
                    },
                )
        raw = json.dumps(value, indent=2).replace("\n", "\r\n").encode() + b"\r\n"
        relocated = file_bytes("observability/prometheus.json", raw)
        self.assertEqual(json.loads(relocated), expected)
        self.assertEqual(relocated.count(b"\r\n"), raw.count(b"\r\n"))
        self.assertIn(b'"credentials": "/run/secrets/literal-credential"', relocated)
        self.assertIn(b'"type": "/run/tls/literal-type"', relocated)
        headers = {
            "headers": {"Authorization": {"credentials_file": "/run/secrets/identity"}}
        }
        self.assertEqual(adapt(headers, "obs-guard"), headers)
        self.assertEqual(
            file_bytes("observability/headers.json", json.dumps(headers).encode()),
            json.dumps(headers).encode(),
        )

    def test_reviewed_module_data_binds_remain_external_and_shared_root_is_locked_once(
        self,
    ):
        mounts = [
            {
                "type": "bind",
                "source": "/existing/shared-memory-root",
                "target": f"/data/modules/{name}",
                "read_only": False,
            }
            for name in ("memory", "knowledge")
        ]
        value = installation(mounts=mounts)
        args = value["processes"][-1]["argv"]
        locked = [args[i + 1] for i, arg in enumerate(args) if arg == "--state-dir"]
        self.assertIn("/data/modules/memory", locked)
        self.assertNotIn("/data/modules/knowledge", locked)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "new"
            export(value, root)
            service = json.loads((root / "compose.yaml").read_bytes())["services"][
                "tianshu"
            ]
            self.assertEqual(
                service["volumes"][-2:],
                [{**mount, "bind": {"create_host_path": False}} for mount in mounts],
            )

    def test_existing_grafana_entrypoint_accepts_unified_path_and_preserves_standalone_default(
        self,
    ):
        shell = shutil.which("sh")
        if not shell and shutil.which("git"):
            candidate = (
                Path(shutil.which("git")).resolve().parents[1] / "usr/bin/sh.exe"
            )
            shell = str(candidate) if candidate.is_file() else None
        if not shell:
            self.skipTest("POSIX shell unavailable; Linux entrypoint not executed")
        entrypoint = ROOT / "deploy/observability/grafana-entrypoint.sh"
        with tempfile.TemporaryDirectory() as temporary:
            token = Path(temporary) / "query-token"
            token.write_text("fictional-query-token\n", encoding="utf-8")
            environment = {**os.environ, "QUERY_TOKEN_FILE": str(token)}
            environment["PATH"] = (
                str(Path(shell).parent) + os.pathsep + environment.get("PATH", "")
            )
            # Intercept only the final exec so no real Grafana or /run.sh is run.
            result = subprocess.run(
                [
                    shell,
                    "-c",
                    '''alias exec='printf "%s" "$OBS_QUERY_TOKEN"; : '; . "$1"''',
                    "fixture",
                    str(entrypoint),
                ],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout, "fictional-query-token")
            environment.pop("QUERY_TOKEN_FILE")
            script = '''cat() { [ "$1" = /run/secrets/query_token ] || exit 3; printf "standalone-fixture"; }; alias exec='printf "%s" "$OBS_QUERY_TOKEN"; : '; . "$1"'''
            result = subprocess.run(
                [shell, "-c", script, "fixture", str(entrypoint)],
                env=environment,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(result.stdout, "standalone-fixture")

    def test_grafana_public_certificate_fallback_never_reads_a_private_key(self):
        files = native_files()
        files["observability/tls/grafana.pem"] = b"PUBLIC CERTIFICATE ONLY"
        with patch(
            "deploy.single_container.plan.ssl._ssl._test_decode_cert",
            return_value={"subjectAltName": (("DNS", "actual.logs.internal"),)},
        ) as decode:
            value = make_installation(
                "tianshu:local", files, knowledge_client="existing"
            )
        self.assertTrue(decode.called)
        grafana = next(p for p in value["processes"] if p["name"] == "obs-grafana")
        self.assertEqual(
            grafana["health"]["url"], "https://actual.logs.internal:3000/api/health"
        )
        self.assertNotIn("GF_SERVER_ROOT_URL", grafana["env"])

    def test_real_public_certificate_san_is_used_only_without_native_hostname_metadata(
        self,
    ):
        try:
            from cryptography import x509
            from cryptography.hazmat.primitives import hashes, serialization
            from cryptography.hazmat.primitives.asymmetric import rsa
            from cryptography.x509.oid import NameOID
        except ImportError:
            self.skipTest(
                "Synthetic certificate generation needs existing cryptography"
            )
        from datetime import datetime, timedelta, timezone

        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        subject = x509.Name(
            [x509.NameAttribute(NameOID.COMMON_NAME, "TS346 SYNTHETIC ONLY")]
        )
        now = datetime.now(timezone.utc)
        certificate = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(subject)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName("first.logs.internal"),
                        x509.DNSName("actual.logs.internal"),
                    ]
                ),
                critical=False,
            )
            .sign(key, hashes.SHA256())
        )
        files = native_files()
        files["observability/tls/grafana.pem"] = certificate.public_bytes(
            serialization.Encoding.PEM
        )
        fallback = make_installation(
            "tianshu:local", files, knowledge_client="existing"
        )
        process = next(p for p in fallback["processes"] if p["name"] == "obs-grafana")
        self.assertEqual(
            process["health"]["url"], "https://first.logs.internal:3000/api/health"
        )
        with_metadata = make_installation(
            "tianshu:local",
            files,
            knowledge_client="existing",
            observability_environment={
                "obs-grafana": {
                    "GF_SERVER_ROOT_URL": "https://actual.logs.internal:18490/"
                }
            },
        )
        process = next(
            p for p in with_metadata["processes"] if p["name"] == "obs-grafana"
        )
        self.assertEqual(
            process["health"]["url"], "https://actual.logs.internal:3000/api/health"
        )
        self.assertEqual(
            process["env"]["GF_SERVER_ROOT_URL"], "https://actual.logs.internal:18490/"
        )


if __name__ == "__main__":
    unittest.main()
