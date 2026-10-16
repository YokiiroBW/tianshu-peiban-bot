"""Synthetic-only packaging checks. No NAS, account, model, or application DB is used."""

import contextlib
import copy
import io
import ipaddress
import json
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[3]
PACKAGE = ROOT / "deploy" / "tianshu"
sys.path.insert(0, str(PACKAGE))

from bundle import export_sources, initialize, preflight  # noqa: E402
from configuration import PRODUCTS, load_inputs, references  # noqa: E402
from manifest import Refused, check_evidence, digest, inside, load_manifest, write_json  # noqa: E402
from release import main  # noqa: E402


def certificates(target, names, *, bad_name=False, expired=False, other_ca=False):
    key = ec.generate_private_key(ec.SECP256R1())
    ca_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(timezone.utc)
    issuer = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "DEP-A SYNTHETIC TEST CA")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(issuer)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    target.mkdir(parents=True, exist_ok=True)
    (target / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    for product in PRODUCTS:
        directory = target / product
        directory.mkdir(exist_ok=True)
        subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SYNTHETIC LEAF")])
        leaf = (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(issuer)
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=2))
            .not_valid_after(now + timedelta(days=-1 if expired else 1))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.IPAddress(ipaddress.ip_address(n))
                        if n.replace(".", "").isdigit()
                        else x509.DNSName(n)
                        for n in (["wrong.test"] if bad_name else names[product])
                    ]
                ),
                critical=False,
            )
            .add_extension(
                x509.BasicConstraints(ca=False, path_length=None), critical=True
            )
            .add_extension(
                x509.AuthorityKeyIdentifier.from_issuer_public_key(
                    ca_key.public_key()
                ),
                critical=False,
            )
            .sign(ca_key, hashes.SHA256())
        )
        (directory / "server.pem").write_bytes(
            leaf.public_bytes(serialization.Encoding.PEM)
        )
        (directory / "server.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
    if other_ca:
        wrong = ec.generate_private_key(ec.SECP256R1())
        ca = (
            x509.CertificateBuilder()
            .subject_name(issuer)
            .issuer_name(issuer)
            .public_key(wrong.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=False,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=True,
                    crl_sign=True,
                    encipher_only=None,
                    decipher_only=None,
                ),
                critical=True,
            )
            .add_extension(
                x509.SubjectKeyIdentifier.from_public_key(wrong.public_key()),
                critical=False,
            )
            .sign(wrong, hashes.SHA256())
        )
        (target / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))


class PackagingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (PACKAGE / ".work").mkdir(exist_ok=True)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=PACKAGE / ".work", prefix="qa-")
        self.root = Path(self.temp.name)
        self.inputs = self.root / "input"
        shutil.copytree(PACKAGE / "templates", self.inputs)
        self.sitepath = self.inputs / "deployment-input.example.json"
        self.site = json.loads(self.sitepath.read_text())
        self.site["web_origin"] = "https://console.test:18443"
        self.site["project_name"] = "tianshu-qa-packaging"
        for p in PRODUCTS:
            self.site["tls"][p]["provenance"] = "isolated_test"
        write_json(self.sitepath, self.site)
        self.configs = {
            p: json.loads((self.inputs / (p + ".json")).read_text()) for p in PRODUCTS
        }
        self.configs["platform"]["web"]["origin"] = self.site["web_origin"]
        self.save_configs()
        self.names = {
            p: [p + ".internal"] + (["console.test"] if p == "platform" else [])
            for p in PRODUCTS
        }
        certificates(self.inputs / "tls", self.names)
        self.manifest = load_manifest(PACKAGE / "release-manifest.example.json")
        # Explicit synthetic contract bytes replace the real contract inventory for isolation.
        self.contracts = self.root / "contracts"
        for contract in self.manifest["contracts"]:
            directory = self.contracts / contract["id"]
            directory.mkdir(parents=True)
            raw = b'{"synthetic":true}\r\n'
            (directory / "manifest.json").write_bytes(raw)
            contract["manifest_sha256"] = digest(raw)
            contract["files"] = [{"path": "manifest.json", "sha256": digest(raw)}]
        self.manifestpath = self.root / "manifest.json"
        write_json(self.manifestpath, self.manifest)
        self.env = {}
        for p in PRODUCTS:
            for name in references(self.configs[p]) - {"TIANSHU_DIAGNOSTICS_TOKEN"}:
                self.env[name] = "SYNTHETIC_CANARY_" + name + "_0123456789abcdef"
            self.env[self.site["diagnostics_env"][p]] = (
                "SYNTHETIC_DIAGNOSTIC_" + p + "_0123456789abcdef"
            )
        self.output = self.root / "new-bundle"
        self.env["TS_ADMIN_PASSWORD"] = "合成口令-only-a-local-test-2026"

    def tearDown(self):
        self.temp.cleanup()

    def save_configs(self):
        for p, config in self.configs.items():
            write_json(self.inputs / (p + ".json"), config)

    def init(self):
        return initialize(
            self.manifestpath, self.sitepath, self.contracts, self.output, self.env
        )

    def test_valid_inputs_initialize_movable_candidate_and_keep_database_absent(self):
        result = self.init()
        self.assertEqual(result["status"], "initialized_candidate")
        self.assertFalse(result["release_ready"])
        self.assertEqual(preflight(self.output)["status"], "package_valid")
        moved = self.root / "moved"
        self.output.rename(moved)
        self.assertEqual(preflight(moved)["status"], "package_valid")
        self.assertEqual(list((moved / "data").rglob("*.sqlite")), [])
        compose = json.loads((moved / "compose.json").read_text())
        self.assertEqual(
            [p for p, s in compose["services"].items() if "ports" in s], ["platform"]
        )
        self.assertTrue(compose["networks"]["core"]["internal"])
        self.assertFalse(compose["networks"]["frontend"]["internal"])
        companion_networks = compose["services"]["companion"]["networks"]
        self.assertEqual(set(companion_networks), {"core", "frontend"})
        self.assertEqual(
            companion_networks["core"],
            {"ipv4_address": self.site["service_ips"]["companion"], "aliases": ["companion.internal"]},
        )
        self.assertEqual(set(compose["services"]["memory"]["networks"]), {"core"})
        self.assertEqual(set(compose["services"]["gateway"]["networks"]), {"core", "egress"})
        for p, service in compose["services"].items():
            self.assertEqual(service["user"], "10001:10001")
            self.assertTrue(service["read_only"])
            self.assertEqual(service["cap_drop"], ["ALL"])
            self.assertEqual(service["scale"], 1)
            self.assertTrue(
                all(not v["bind"]["create_host_path"] for v in service["volumes"])
            )
            self.assertNotIn("docker.sock", json.dumps(service))
        self.assertEqual(compose["services"]["memory"]["command"][2], "serve")

    def test_no_secrets_in_manifest_compose_or_report(self):
        result = self.init()
        public = (
            json.dumps(result)
            + (self.output / "compose.json").read_text()
            + (self.output / "release-manifest.json").read_text()
        )
        for secret in self.env.values():
            self.assertNotIn(secret, public)
        memory = json.loads((self.output / "config/memory/settings.json").read_text())
        self.assertEqual(
            memory["callers"]["companion"]["token"], self.env["TS_CORE_MEMORY"]
        )
        platform = (self.output / "config/platform/settings.json").read_text()
        self.assertNotIn(self.env["TS_ADMIN_PASSWORD"], platform)
        self.assertIn("scrypt-v1$", platform)
        self.assertNotIn(
            "TS_ADMIN_PASSWORD", (self.output / "private/platform.env").read_text()
        )

    def test_missing_credential_refuses_before_writes(self):
        del self.env["TS_CORE_GATEWAY"]
        with self.assertRaisesRegex(Refused, "required_environment_missing"):
            self.init()
        self.assertFalse(self.output.exists())

    def first_run_account(self):
        platform = self.configs["platform"]
        platform["web_account"] = {"mode": "create", "setup_token_env": "TS_WEB_SETUP_TOKEN"}
        del platform["web"]["username"]
        del platform["web"]["password_hash"]
        self.env["TS_WEB_SETUP_TOKEN"] = "SYNTHETIC_SETUP_ONLY_0123456789abcdef"
        self.save_configs()

    def test_first_run_bundle_has_setup_credential_but_no_precreated_login(self):
        self.first_run_account()
        result = self.init()
        settings = json.loads((self.output / "config/platform/settings.json").read_text())
        self.assertNotIn("username", settings["web"])
        self.assertNotIn("password_hash", settings["web"])
        self.assertEqual(settings["web_account"]["mode"], "create")
        private = (self.output / "private/platform.env").read_text()
        self.assertIn("TS_WEB_SETUP_TOKEN=", private)
        self.assertNotIn("TS_ADMIN_PASSWORD", private)
        public = json.dumps(result) + (self.output / "compose.json").read_text()
        self.assertNotIn(self.env["TS_WEB_SETUP_TOKEN"], public)
        self.assertNotIn(self.env["TS_WEB_SETUP_TOKEN"], json.dumps(settings))
        self.assertEqual(list((self.output / "data").rglob("*.sqlite")), [])
        self.assertEqual(preflight(self.output)["status"], "package_valid")

    def test_first_run_missing_or_short_setup_credential_is_rejected_before_writes(self):
        self.first_run_account()
        del self.env["TS_WEB_SETUP_TOKEN"]
        with self.assertRaisesRegex(Refused, "required_environment_missing"):
            self.init()
        self.assertFalse(self.output.exists())
        self.env["TS_WEB_SETUP_TOKEN"] = "x" * 23
        with self.assertRaisesRegex(Refused, "unsafe_environment_value"):
            self.init()
        self.assertFalse(self.output.exists())

    def test_first_run_refuses_existing_login_and_reused_credential(self):
        self.first_run_account()
        self.configs["platform"]["web"]["username"] = "admin"
        self.save_configs()
        with self.assertRaisesRegex(Refused, "web_account_create_invalid"):
            self.init()
        del self.configs["platform"]["web"]["username"]
        self.configs["platform"]["web_account"]["setup_token_env"] = "TS_ADMIN_TOKEN"
        self.save_configs()
        with self.assertRaisesRegex(Refused, "web_setup_credential_reused"):
            self.init()
        self.assertFalse(self.output.exists())

    def test_claim_bundle_keeps_existing_password_for_authenticated_handover(self):
        self.configs["platform"]["web_account"] = {"mode": "claim"}
        self.save_configs()
        self.init()
        settings = json.loads((self.output / "config/platform/settings.json").read_text())
        self.assertEqual(settings["web_account"], {"mode": "claim"})
        self.assertTrue(settings["web"]["password_hash"].startswith("scrypt-v1$"))
        self.assertNotIn("TS_WEB_SETUP_TOKEN", (self.output / "private/platform.env").read_text())

    def test_disabled_memory_and_internal_model_grant_are_explicit(self):
        from configuration import validate_configs

        for role, field, bad, code in (
            (
                "companion",
                "automatic_memory_candidates",
                True,
                "explicit_memory_candidate_disable_required",
            ),
            (
                "companion",
                "automatic_memory_candidates",
                0,
                "explicit_memory_candidate_disable_required",
            ),
        ):
            changed = copy.deepcopy(self.configs)
            changed[role][field] = bad
            with self.assertRaisesRegex(Refused, code):
                validate_configs(changed, self.site, self.manifest)
        changed = copy.deepcopy(self.configs)
        changed["gateway"]["clients"][0]["internal"] = False
        with self.assertRaisesRegex(Refused, "companion_internal_model_grant_required"):
            validate_configs(changed, self.site, self.manifest)

    def test_web_enable_requires_reviewed_companion_and_matching_flag(self):
        from configuration import validate_configs

        changed = copy.deepcopy(self.configs)
        changed["platform"]["web"]["dialogue_enabled"] = True
        with self.assertRaisesRegex(Refused, "feature_configuration_mismatch"):
            validate_configs(changed, self.site, self.manifest)
        manifest = copy.deepcopy(self.manifest)
        manifest["products"]["companion"]["source"]["commit"] = "a" * 40
        with self.assertRaisesRegex(
            Refused, "reviewed_memory_disable_version_required"
        ):
            validate_configs(changed, self.site, manifest)

    def test_renewal_opt_in_and_dedicated_scope_are_required(self):
        from configuration import validate_configs

        for role, name in (
            ("platform", "model_origin_renewal_http"),
            ("gateway", "platform_origin_renewal"),
        ):
            for value in (False, 1):
                changed = copy.deepcopy(self.configs)
                changed[role][name] = value
                with self.assertRaisesRegex(
                    Refused, "explicit_source_renewal_required"
                ):
                    validate_configs(changed, self.site, self.manifest)
        changed = copy.deepcopy(self.configs)
        changed["platform"]["entries"]["config-entry"]["routes"] += [
            {"caller": "platform", "receiver": "companion", "purpose": "dialogue"}
        ]
        with self.assertRaisesRegex(Refused, "dedicated_config_entry_required"):
            validate_configs(changed, self.site, self.manifest)

    def test_unknown_manifest_field_or_missing_commit_rejected(self):
        for change in ("secret", "missing"):
            m = copy.deepcopy(self.manifest)
            if change == "secret":
                m["api_key"] = "SYNTHETIC_CANARY"
            else:
                del m["products"]["memory"]["source"]["commit"]
            write_json(self.manifestpath, m)
            with self.assertRaisesRegex(Refused, "manifest_schema_invalid"):
                load_manifest(self.manifestpath)

    def test_unverified_cannot_be_promoted_by_completeness(self):
        self.manifest["status"] = "verified"
        write_json(self.manifestpath, self.manifest)
        with self.assertRaisesRegex(Refused, "manifest_schema_invalid"):
            load_manifest(self.manifestpath)

    def test_image_verified_requires_digest(self):
        self.manifest["products"]["platform"]["image"]["verification"] = "verified"
        write_json(self.manifestpath, self.manifest)
        with self.assertRaisesRegex(Refused, "manifest_schema_invalid"):
            load_manifest(self.manifestpath)

    def test_wrong_ca_wrong_san_expired_certificate_and_wrong_key(self):
        for options in ({"bad_name": True}, {"other_ca": True}, {"expired": True}):
            with self.subTest(options=options):
                certificates(self.inputs / "tls", self.names, **options)
                with self.assertRaises((ssl.SSLError, Refused)):
                    self.init()
                self.assertFalse(self.output.exists())
        certificates(self.inputs / "tls", self.names)
        key = ec.generate_private_key(ec.SECP256R1())
        (self.inputs / "tls/platform/server.key").write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        with self.assertRaises(ssl.SSLError):
            self.init()

    def test_missing_fields_wrong_origin_and_plaintext(self):
        for change in ("field", "origin", "tls"):
            c = copy.deepcopy(self.configs)
            if change == "field":
                del c["platform"]["core"]
            elif change == "origin":
                c["platform"]["web"]["origin"] = "https://elsewhere.test:18443"
            else:
                c["platform"]["mode"] = "local_rehearsal"
            for p in PRODUCTS:
                write_json(self.inputs / (p + ".json"), c[p])
            with self.assertRaises((Refused, KeyError)):
                self.init()
            self.assertFalse(self.output.exists())

    def test_private_port_subnet_and_placeholder_origin(self):
        for field, value in (
            ("web_port", 1),
            ("subnet", "8.8.8.0/24"),
            ("web_origin", "https://console.invalid:18443"),
            ("bind_address", "0.0.0.0"),
        ):
            candidate = copy.deepcopy(self.site)
            candidate[field] = value
            write_json(self.sitepath, candidate)
            with self.assertRaises((Refused, ValueError)):
                load_inputs(self.sitepath)

    def test_path_traversal_absolute_device_and_backslash_refused(self):
        for value in (
            "../escape",
            "/tmp/x",
            "C:/secret",
            "x/../../bad",
            "x\\y",
            "NUL",
            "x/..",
            "x./y",
        ):
            with self.subTest(path=value):
                with self.assertRaises(Refused):
                    inside(self.root, value)

    def test_config_path_escape_rejected(self):
        self.site["config_files"]["platform"] = "../manifest.json"
        write_json(self.sitepath, self.site)
        with self.assertRaises(Refused):
            self.init()
        self.assertFalse(self.output.exists())

    def test_linked_directory_refused(self):
        linked = self.root / "linked"
        if os.name == "nt":
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(linked), str(self.inputs)],
                capture_output=True,
            )
            self.assertEqual(completed.returncode, 0)
        else:
            linked.symlink_to(self.inputs, target_is_directory=True)
        try:
            with self.assertRaisesRegex(Refused, "linked_path_refused"):
                inside(self.root, "linked/platform.json")
        finally:
            if os.name == "nt":
                linked.rmdir()
            else:
                linked.unlink()

    def test_mount_overlap_and_guard_backup_group(self):
        for change in ("overlap", "guard"):
            m = copy.deepcopy(self.manifest)
            if change == "overlap":
                m["volumes"][2]["host_path"] = m["volumes"][0]["host_path"] + "/nested"
            else:
                next(v for v in m["volumes"] if v["category"] == "guard")[
                    "backup_group"
                ] = "wrong"
            write_json(self.manifestpath, m)
            with self.assertRaises(Refused):
                load_manifest(self.manifestpath)

    def test_contract_raw_byte_change_rejected(self):
        (
            self.contracts / self.manifest["contracts"][0]["id"] / "manifest.json"
        ).write_bytes(b'{"synthetic":true}\n')
        with self.assertRaisesRegex(Refused, "contract_bytes_changed"):
            self.init()

    def test_repeat_init_will_not_overwrite(self):
        self.init()
        before = (self.output / "config/memory/settings.json").read_bytes()
        with self.assertRaisesRegex(Refused, "new_target_required"):
            self.init()
        self.assertEqual(
            before, (self.output / "config/memory/settings.json").read_bytes()
        )

    def test_ready_credential_values_are_independent(self):
        self.env["TS_DIAG_PLATFORM"] = self.env["TS_ADMIN_TOKEN"]
        with self.assertRaisesRegex(Refused, "diagnostics_credentials_not_independent"):
            self.init()

    def test_corrupted_bundle_and_unlisted_config_are_detected(self):
        self.init()
        config = self.output / "config/platform/settings.json"
        original = config.read_bytes()
        config.write_bytes(original + b" ")
        with self.assertRaisesRegex(Refused, "bundle_bytes_changed"):
            preflight(self.output)
        config.write_bytes(original)
        (config.parent / "extra.json").write_text("{}")
        with self.assertRaisesRegex(Refused, "unlisted_bundle_file"):
            preflight(self.output)

    def test_memory_cli_and_environment_disagreement_refused_even_after_rehash(self):
        self.init()
        path = self.output / "compose.json"
        doc = json.loads(path.read_text())
        doc["services"]["memory"]["environment"]["TIANSHU_MEMORY_CONFIG"] = (
            "/etc/tianshu/wrong.json"
        )
        write_json(path, doc)
        inventory_path = self.output / "bundle-integrity.json"
        inventory = json.loads(inventory_path.read_text())
        inventory["files"]["compose.json"] = digest(path.read_bytes())
        write_json(inventory_path, inventory)
        with self.assertRaisesRegex(Refused, "composition_changed"):
            preflight(self.output)

    def test_disk_reserve_shortage_fails_preflight(self):
        self.init()
        with patch("bundle.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            with self.assertRaisesRegex(
                Refused, "insufficient_free_space_for_logs_and_migration_reserve"
            ):
                preflight(self.output)

    def test_linux_plan_reports_not_run_and_refuses_modified_snapshot(self):
        # The actual fixed-source export is exercised separately. This tests the public plan
        # boundary against a minimal explicit inventory, without touching a Docker daemon.
        from linux_validate import plan

        self.init()
        contexts = self.root / "plan-contexts"
        contexts.mkdir()
        inventory = {"release_id": self.manifest["release_id"], "products": {}}
        for p in PRODUCTS:
            path = contexts / p
            path.mkdir()
            name = "infra/container/Dockerfile" if p == "memory" else "Dockerfile"
            file = path / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("FROM synthetic:1\n")
            inventory["products"][p] = {
                "source": self.manifest["products"][p]["source"],
                "files": {name: digest(file.read_bytes())},
            }
        write_json(contexts / "source-inventory.json", inventory)
        _, steps = plan(self.output, contexts)
        self.assertEqual(len(steps), 4)
        (contexts / "platform/Dockerfile").write_text("changed\n")
        with self.assertRaisesRegex(Refused, "source_bytes_changed"):
            plan(self.output, contexts)

    def test_cli_errors_do_not_leak_configuration_or_exception(self):
        self.site["config_files"]["memory"] = "../SECRET_CANARY.json"
        write_json(self.sitepath, self.site)
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            result = main(
                [
                    "init",
                    "--manifest",
                    str(self.manifestpath),
                    "--inputs",
                    str(self.sitepath),
                    "--contracts-root",
                    str(self.contracts),
                    "--target",
                    str(self.output),
                ]
            )
        self.assertEqual(result, 2)
        self.assertNotIn("SECRET_CANARY", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "refused")

    def test_export_uses_committed_files_and_never_dirty_worktree(self):
        repo = self.root / "repo"
        repo.mkdir()

        def git(*args):
            return subprocess.check_output(
                ["git", "-C", str(repo), *args], stderr=subprocess.DEVNULL
            )

        git("init")
        (repo / "Dockerfile").write_text("FROM synthetic:1\n")
        (repo / "infra/container").mkdir(parents=True)
        (repo / "infra/container/Dockerfile").write_text("FROM synthetic:1\n")
        (repo / "source.txt").write_bytes(b"fixed source\n")
        git("add", ".")
        git(
            "-c",
            "user.name=Synthetic Test",
            "-c",
            "user.email=test@invalid",
            "commit",
            "-m",
            "fixture",
        )
        commit = git("rev-parse", "HEAD").decode().strip()
        for p in PRODUCTS:
            self.manifest["products"][p]["source"]["commit"] = commit
        write_json(self.manifestpath, self.manifest)
        (repo / "source.txt").write_text("ACTIVE_WORKTREE_SECRET_CANARY")
        (repo / "untracked-secret.txt").write_text("UNTRACKED_SECRET_CANARY")
        repos = self.root / "repos.json"
        write_json(repos, {p: str(repo) for p in PRODUCTS})
        target = self.root / "contexts"
        report = export_sources(self.manifestpath, repos, self.contracts, target)
        self.assertFalse(report["images_built"])
        for p in PRODUCTS:
            self.assertNotIn(b"CANARY", (target / p / "source.txt").read_bytes())
            self.assertFalse((target / p / "untracked-secret.txt").exists())
        self.assertTrue(
            (target / "platform/contracts/diagnostics/v1/manifest.json").is_file()
        )

    def test_evidence_bytes_and_release_bindings_must_match(self):
        self.manifest["status"] = "verified"
        self.manifest["blockers"] = []
        for p in PRODUCTS:
            self.manifest["products"][p]["image"].update(
                digest="sha256:" + "a" * 64, verification="verified"
            )
        evidence = self.root / "proof.json"
        report = {
            "kind": "linux_images",
            "result": "passed",
            "release_id": "wrong-release",
            "products": self.manifest["products"],
            "contracts": {
                c["id"]: c["manifest_sha256"] for c in self.manifest["contracts"]
            },
            "synthetic_only": False,
            "skipped": 0,
        }
        write_json(evidence, report)
        self.manifest["evidence"] = [
            {
                "kind": "linux_images",
                "path": "proof.json",
                "sha256": digest(evidence.read_bytes()),
                "result": "passed",
            }
        ]
        with self.assertRaisesRegex(Refused, "evidence_binding_mismatch"):
            check_evidence(self.manifest, self.root)
        evidence.write_text("{}")
        with self.assertRaisesRegex(Refused, "evidence_hash_mismatch"):
            check_evidence(self.manifest, self.root)

    def test_dep_d_content_subject_cases_and_static_identity(self):
        from acceptance import CASES, inspect_acceptance

        report = {
            "report_version": "dep-d/1",
            "kind": "release_acceptance",
            "mode": "container",
            "runtime_kind": "product",
            "verdict": "incomplete",
            "binding": {
                "manifest_sha256": digest(self.manifestpath.read_bytes()),
                "release_id": self.manifest["release_id"],
                "release_status": "candidate",
                "features": self.manifest["features"],
                "release_blockers": self.manifest["blockers"],
                "identity_basis": "static_input_only",
                "products": {
                    p: {
                        "repo": self.manifest["products"][p]["source"]["repo"],
                        "commit": self.manifest["products"][p]["source"]["commit"],
                        "image": self.manifest["products"][p]["image"]["reference"],
                        "digest": None,
                    }
                    for p in PRODUCTS
                },
                "contracts": {
                    c["id"] + "/" + f["path"]: f["sha256"]
                    for c in self.manifest["contracts"]
                    for f in c["files"]
                },
            },
            "results": [{"case": c, "status": "not_run"} for c in sorted(CASES)],
        }
        path = self.root / "dep-d-report.json"

        def save():
            payload = {k: v for k, v in report.items() if k != "content_sha256"}
            report["content_sha256"] = digest(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode()
            )
            write_json(path, report)

        save()
        result = inspect_acceptance(path, self.manifestpath)
        self.assertEqual(result["counts"]["not_run"], 20)
        self.assertFalse(result["release_ready"])
        self.assertIn(
            "independent_runtime_identity_review_required", result["blockers"]
        )
        report["results"].pop()
        save()
        with self.assertRaisesRegex(Refused, "acceptance_cases_missing_or_duplicate"):
            inspect_acceptance(path, self.manifestpath)
        report["binding"]["manifest_sha256"] = "0" * 64
        save()
        with self.assertRaisesRegex(Refused, "acceptance_subject_mismatch"):
            inspect_acceptance(path, self.manifestpath)


if __name__ == "__main__":
    unittest.main()
