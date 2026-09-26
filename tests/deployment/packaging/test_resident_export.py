"""Offline resident prepare -> fixed OBS configure -> Dockge export, synthetic material."""

import json
import ipaddress
import os
import secrets
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "deploy/tianshu"))

import test_packaging as packaging  # noqa: E402
from bundle import preflight  # noqa: E402
from manifest import Refused, check_contracts, digest, load_manifest, read_json, write_json  # noqa: E402
from observability_release import configure  # noqa: E402
from resident_export import OBS_COMMIT, deployment_path, export, rewrite_stack, validate_observability_networks  # noqa: E402


def observability_tls(root):
    """Short lived synthetic CA with AKI/SKI for current OpenSSL verification."""
    root.mkdir()
    key = ec.generate_private_key(ec.SECP256R1())
    issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OBS SYNTHETIC TEST CA")])
    now = datetime.now(timezone.utc)
    ca = (
        x509.CertificateBuilder().subject_name(issuer).issuer_name(issuer)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, None, None), critical=True)
        .sign(key, hashes.SHA256())
    )
    (root / "ca.pem").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (root / "client-ca.pem").write_bytes((root / "ca.pem").read_bytes())
    for role in ("vector", "loki", "grafana", "prometheus", "guard", "client"):
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        leaf = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "SYNTHETIC " + role)]))
            .issuer_name(issuer).public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()), critical=False)
            .add_extension(x509.SubjectAlternativeName([
                x509.DNSName("obs-" + role), x509.DNSName("logs.internal"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([
                ExtendedKeyUsageOID.CLIENT_AUTH if role == "client" else ExtendedKeyUsageOID.SERVER_AUTH
            ]), critical=False)
            .sign(key, hashes.SHA256())
        )
        (root / (role + ".pem")).write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
        (root / (role + ".key")).write_bytes(leaf_key.private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        ))


class ResidentExportTests(unittest.TestCase):
    def setUp(self):
        self.f = packaging.PackagingTests()
        self.f.setUpClass()
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def prepare(self):
        f = self.f
        f.site.update(
            project_name="tianshu-v2-resident",
            bind_address="192.168.31.210",
            web_origin="http://192.168.31.210:18443",
            public_web=True,
            auxiliary_subnets={"egress": "172.30.87.0/24", "frontend": "172.30.88.0/24"},
            resource_profile={"kind": "nas-cpuset-resident-v1", "cpus": [6, 7], "pid_limit": "unsupported"},
        )
        for product in f.site["tls"]:
            f.site["tls"][product]["provenance"] = "operator_supplied"
        f.configs["platform"]["web"]["origin"] = f.site["web_origin"]
        f.configs["platform"]["web_access"] = {
            "host": "0.0.0.0", "port": 8080,
            "initial": {"mode": "http", "origin": f.site["web_origin"], "certificate": None},
            "certificates": {"nas-web": f.configs["platform"]["tls"]},
        }
        f.save_configs()
        write_json(f.sitepath, f.site)
        f.names["platform"] = ["platform.internal", "192.168.31.210"]
        packaging.certificates(f.inputs / "tls", f.names)
        manifest = load_manifest(ROOT / "deploy/tianshu/release-manifest.example.json")
        manifest["observability"]["source"]["commit"] = OBS_COMMIT
        for product in manifest["products"]:
            manifest["products"][product]["image"]["digest"] = "sha256:" + "a" * 64
        f.manifest = manifest
        contract_root = os.environ.get("TIANSHU_CONTRACTS_ROOT")
        if not contract_root:
            self.skipTest("fixed contracts root required for configure integration")
        contract_root = Path(contract_root)
        check_contracts(manifest, contract_root)
        f.contracts = f.root / "real-contracts"
        f.contracts.mkdir()
        for contract in manifest["contracts"]:
            shutil.copytree(contract_root / contract["id"], f.contracts / contract["id"])
        write_json(f.manifestpath, manifest)
        f.init()
        self.assertEqual(preflight(f.output)["status"], "resident_candidate")
        with self.assertRaisesRegex(Refused, "resident_not_release_approved"):
            preflight(f.output, release=True)
        return manifest

    def configure_observability(self):
        f = self.f
        origin = f.root / "obs-input"
        origin.mkdir()
        observability_tls(origin / "tls")
        (origin / "secrets").mkdir()
        names = ("writer_token", "query_token", "metrics_token", "grafana_admin_password")
        for name in names:
            (origin / "secrets" / name).write_text(secrets.token_urlsafe(32))
        tls_files = (
            "ca.pem", "client-ca.pem", "guard.pem", "guard.key", "loki.pem", "loki.key",
            "vector.pem", "vector.key", "grafana.pem", "grafana.key", "prometheus.pem",
            "prometheus.key", "client.pem", "client.key",
        )
        settings = {
            "grafana_hostname": "logs.internal",
            "ports": {"query": 19491, "grafana": 19490},
            "log_budgets": dict.fromkeys(("platform", "companion", "memory", "gateway"), 1073741824),
            "network_subnets": {"observe": "10.204.45.0/24", "storage": "10.204.46.0/24", "access": "10.204.47.0/24"},
            "image_digests": dict.fromkeys(("vector", "loki", "grafana", "prometheus", "guard"), "sha256:" + "b" * 64),
            "tls": {name: "tls/" + name for name in tls_files},
            "secrets": {name: "secrets/" + name for name in names},
        }
        write_json(origin / "settings.json", settings)
        projects_root = os.environ.get("TIANSHU_PROJECTS_ROOT")
        if not projects_root:
            self.skipTest("fixed product Git objects required for configure integration")
        # Windows cannot chown UID/GID 10001. Only that Linux filesystem operation is
        # replaced; fixed-source configure, TLS handshakes, binding, and export are real.
        def invoke_with_code(arguments):
            result = subprocess.run([sys.executable, "-B", *map(str, arguments)], capture_output=True, timeout=60)
            if result.returncode:
                script = (
                    "import json,sys; from pathlib import Path; "
                    "sys.path.insert(0,str(Path(sys.argv[1]).parent)); import configure; "
                    "a=sys.argv[2:]; get=lambda n:a[a.index(n)+1]; "
                    "configure.prepare(Path(get('--deployment-root')),json.loads(Path(get('--settings')).read_bytes()),"
                    "json.loads(Path(get('--release-manifest')).read_bytes()),get('--output-relative'),"
                    "json.loads(Path(get('--snapshot')).read_bytes()),True)"
                )
                detail = subprocess.run([sys.executable, "-B", "-c", script, *map(str, arguments)], capture_output=True, timeout=60)
                self.fail(result.stdout.decode(errors="replace") + detail.stderr.decode(errors="replace"))

        with patch("observability_release.apply_runtime_permissions"), patch("observability_release.invoke", side_effect=invoke_with_code):
            report = configure(f.output, origin / "settings.json", ROOT, Path(projects_root))
        self.assertEqual(report["status"], "observability_configured")
        return settings

    def test_actual_fixed_source_configure_and_export(self):
        f = self.f
        self.prepare()
        self.configure_observability()
        self.assertEqual(preflight(f.output)["status"], "resident_candidate")
        out = f.root / "dockge-export"
        result = export(f.output, ROOT, "/volume1/tianshu-v2-resident", out)
        self.assertEqual(result["status"], "resident_candidate")
        self.assertFalse(result["release_ready"])
        lock = read_json(out / "resident-export.lock.json")
        self.assertEqual(lock["observability_source"]["commit"], OBS_COMMIT)
        self.assertEqual(lock["manifest_sha256"], digest((f.output / "release-manifest.json").read_bytes()))
        self.assertFalse((out / "INCOMPLETE").exists())
        for project in lock["projects"]:
            compose_file = out / project / "compose.yaml"
            self.assertEqual(lock["compose_sha256"][project], digest(compose_file.read_bytes()))
            stack = read_json(compose_file)
            self.assertEqual(stack["name"], project)
            for service in stack["services"].values():
                self.assertEqual(service["restart"], "unless-stopped")
                self.assertEqual(service["cpuset"], "6,7")
                self.assertNotIn("cpus", service)
                self.assertNotIn("pids_limit", service)
                self.assertIn("@sha256:", service["image"])
                self.assertTrue(all(v["source"].startswith("/volume1/tianshu-v2-resident/") for v in service["volumes"]))
        self.assertEqual(
            read_json(out / "tianshu-v2-resident" / "compose.yaml")["services"]["platform"]["ports"],
            ["192.168.31.210:18443:8080"],
        )
        self.assertEqual(set(read_json(out / "tianshu-v2-resident-obs" / "compose.yaml")["networks"]), {"observe", "storage", "access"})
        with self.assertRaisesRegex(Refused, "new_target_required"):
            export(f.output, ROOT, "/volume1/tianshu-v2-resident", out)

    def test_resident_input_scope_refuses_qa_certificate_or_wrong_project(self):
        from configuration import load_inputs

        f = self.f
        f.site["resource_profile"] = {"kind": "nas-cpuset-resident-v1", "cpus": [6, 7], "pid_limit": "unsupported"}
        f.site["public_web"] = True
        f.site["auxiliary_subnets"] = {"egress": "172.30.87.0/24", "frontend": "172.30.88.0/24"}
        f.site["bind_address"] = "192.168.31.210"
        f.site["web_origin"] = "http://192.168.31.210:18443"
        for project in ("tianshu-qa-packaging", "tianshu-v2-resident-obs"):
            f.site["project_name"] = project
            write_json(f.sitepath, f.site)
            with self.assertRaisesRegex(Refused, "resident_project_required"):
                load_inputs(f.sitepath)
        f.site["project_name"] = "tianshu-v2-resident"
        write_json(f.sitepath, f.site)
        with self.assertRaisesRegex(Refused, "resident_operator_tls_required"):
            load_inputs(f.sitepath)

    def test_export_refuses_external_secret_and_two_network_template(self):
        with self.assertRaisesRegex(Refused, "unsafe_deployment_root"):
            deployment_path("/volume1/../resident")
        bundle = self.f.root / "bundle"
        bundle.mkdir()
        stack = {"services": {"platform": {
            "restart": "unless-stopped", "cpuset": "6,7", "mem_limit": "1g",
            "image": "example@sha256:" + "a" * 64,
            "volumes": [], "env_file": ["/outside/private.env"],
        }}}
        with self.assertRaisesRegex(Refused, "external_secret_file_refused"):
            rewrite_stack(bundle, deployment_path("/volume1/resident"), stack, "tianshu-v2-resident", {"platform"})
        stack["services"]["platform"].pop("env_file")
        stack["services"]["platform"]["image"] = "example:latest"
        with self.assertRaisesRegex(Refused, "image_digest_required"):
            rewrite_stack(bundle, deployment_path("/volume1/resident"), stack, "tianshu-v2-resident", {"platform"})
        with self.assertRaisesRegex(Refused, "observability_three_networks_required"):
            validate_observability_networks({"networks": {"observe": {"internal": True}, "storage": {"internal": True}}})


if __name__ == "__main__":
    unittest.main()
