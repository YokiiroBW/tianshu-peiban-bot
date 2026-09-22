"""Real DEP-A init -> pinned DEP-B configure -> release preflight, synthetic material only."""

import argparse
import json
from pathlib import Path
import secrets
import shutil
import sys

from test_packaging import PackagingTests, PACKAGE, ROOT

sys.path.insert(0, str(ROOT / "tests/release_acceptance"))
from product_inputs import tls  # noqa: E402
from manifest import check_contracts, digest, load_manifest, write_json  # noqa: E402
from bundle import preflight, verify_integrity  # noqa: E402
from observability_release import configure, verify_layout  # noqa: E402


def verify(contracts, repository, projects=None):
    harness = PackagingTests()
    harness.setUpClass()
    harness.setUp()
    try:
        manifest = load_manifest(PACKAGE / "release-manifest.example.json")
        check_contracts(manifest, contracts)
        # Own isolated destination only; test setup's synthetic contract placeholders are replaced
        # through a new sibling directory, never by editing the authoritative inputs.
        harness.contracts = harness.root / "verified-contracts"
        harness.contracts.mkdir()
        for contract in manifest["contracts"]:
            shutil.copytree(
                contracts / contract["id"], harness.contracts / contract["id"]
            )
        check_contracts(manifest, harness.contracts)
        write_json(harness.manifestpath, manifest)
        harness.init()
        before = preflight(harness.output)
        assert "observability_not_configured" in before["blockers"]
        origin = harness.root / "obs-input"
        origin.mkdir()
        tls(origin / "tls")
        (origin / "secrets").mkdir()
        secret_names = (
            "writer_token",
            "query_token",
            "metrics_token",
            "grafana_admin_password",
        )
        for name in secret_names:
            (origin / "secrets" / name).write_text(secrets.token_urlsafe(32))
        settings = {
            "grafana_hostname": "logs.internal",
            "ports": {"query": 19491, "grafana": 19490},
            "log_budgets": dict.fromkeys(
                ("platform", "companion", "memory", "gateway"), 1073741824
            ),
            "image_digests": {},
            "tls": {
                name: "tls/" + name
                for name in (
                    "ca.pem",
                    "client-ca.pem",
                    "guard.pem",
                    "guard.key",
                    "loki.pem",
                    "loki.key",
                    "vector.pem",
                    "vector.key",
                    "prometheus.pem",
                    "prometheus.key",
                    "grafana.pem",
                    "grafana.key",
                    "client.pem",
                    "client.key",
                )
            },
            "secrets": {name: "secrets/" + name for name in secret_names},
        }
        write_json(origin / "settings.json", settings)
        configured = configure(
            harness.output, origin / "settings.json", repository, projects
        )
        verify_integrity(harness.output)
        checked = preflight(harness.output)
        assert "observability_composition" in checked["checks"]
        assert "observability_not_configured" not in checked["blockers"]
        stack = verify_layout(harness.output, manifest)
        from reconcile_fixture import exercise

        reconciliation = exercise(harness.output, origin, harness.root)
        wrong = json.loads(json.dumps(stack))
        wrong["services"]["obs-vector"]["volumes"][0]["read_only"] = True
        write_json(harness.output / "observability/compose.yaml", wrong)
        try:
            verify_layout(harness.output, manifest)
        except ValueError as error:
            assert str(error) == "observability_write_mount_mismatch"
        else:
            raise AssertionError("wrong_mount_accepted")
        return {
            "kind": "dep-e-combination",
            "result": "passed",
            "synthetic_material": True,
            "release_manifest_sha256": digest(
                (PACKAGE / "release-manifest.example.json").read_bytes()
            ),
            "products": {k: v["source"] for k, v in manifest["products"].items()},
            "observability_source": manifest["observability"]["source"],
            "checks": [
                "actual_initialize",
                "fixed_git_observability_export",
                "actual_configure_tls_handshakes",
                "five_write_mounts",
                "distinct_project",
                "bundle_integrity",
                "actual_preflight",
                "tampered_mount_refused",
            ],
            "configure": configured,
            "linux_containers": "not_run",
            "reconcile_wiring": reconciliation,
        }
    finally:
        harness.tearDown()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contracts-root", required=True, type=Path)
    parser.add_argument("--root-repository", required=True, type=Path)
    parser.add_argument("--projects", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    write_json(
        args.output, verify(args.contracts_root, args.root_repository, args.projects)
    )
