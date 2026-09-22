"""Read-only fixed-source CLI checks plus Platform's real preflight on a synthetic empty site.

Requires the four products' runtime dependencies in this interpreter. This is NOT a Linux
image test or a model/identity acceptance. Only new synthetic DBs are migrated; no
application server or external call starts.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from contextlib import closing
from pathlib import Path

from test_packaging import PACKAGE, certificates

sys.path.insert(0, str(PACKAGE))
from configuration import references, resolve_config  # noqa: E402
from manifest import PRODUCTS, digest, read_json, write_json  # noqa: E402


def verify(contexts, contracts):
    inventory = read_json(contexts / "source-inventory.json")
    report = {
        "kind": "fixed_source_entrypoints",
        "runtime": "local_python",
        "container_build": "not_run",
        "results": [],
        "products": {p: inventory["products"][p]["source"] for p in PRODUCTS},
    }
    entries = {
        "platform": ["services.platform", "--help"],
        "companion": ["tianshu_companion.runtime_cli", "--help"],
        "memory": ["tianshu_memory.cli", "--config", "unused", "serve", "--help"],
        "gateway": ["tianshu_gateway", "--help"],
    }
    for p, command in entries.items():
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(
            contexts / p / ("src" if p != "platform" else "")
        )
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        result = subprocess.run(
            [sys.executable, "-B", "-m", *command],
            env=environment,
            capture_output=True,
            timeout=30,
        )
        report["results"].append(
            {
                "case": p + "_actual_cli",
                "status": "passed" if result.returncode == 0 else "failed",
                "exit_code": result.returncode,
            }
        )
    with tempfile.TemporaryDirectory(
        dir=PACKAGE / ".work", prefix="actual-preflight-"
    ) as directory:
        root = Path(directory)
        names = {p: [p + ".internal", "console.test"] for p in PRODUCTS}
        certificates(root / "tls", names)
        settings = read_json(PACKAGE / "templates/platform.json")
        values = {
            name: "SYNTHETIC_ENTRYPOINT_CANARY_" + name for name in references(settings)
        }
        settings = resolve_config(settings, values)
        settings["database_path"] = str(root / "platform.sqlite")
        settings["contract_directory"] = str(contracts / "text-dialogue/v1")
        settings["diagnostics"]["contract_directory"] = str(
            contracts / "diagnostics/v1"
        )
        (root / "logs").mkdir()
        settings["diagnostics"]["log_directory"] = str(root / "logs")
        settings["tls"] = {
            "certificate_file": str(root / "tls/platform/server.pem"),
            "private_key_file": str(root / "tls/platform/server.key"),
        }
        settings["web"]["origin"] = "https://console.test:18443"
        (root / "web").mkdir()
        (root / "web/index.html").write_text(
            "<!doctype html><title>Synthetic static fixture</title>"
        )
        settings["web"]["static_directory"] = str(root / "web")
        settings["core"]["ca_file"] = str(root / "tls/ca.pem")
        write_json(root / "settings.json", settings)
        environment = dict(os.environ, **values)
        environment["PYTHONPATH"] = str(contexts / "platform")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        before = {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }
        completed = subprocess.run(
            [
                sys.executable,
                "-B",
                "-m",
                "services.platform",
                "--settings",
                str(root / "settings.json"),
                "preflight",
            ],
            env=environment,
            capture_output=True,
            timeout=30,
        )
        try:
            outcome = json.loads(completed.stdout)
        except ValueError:
            outcome = {}
        after = {
            p.relative_to(root).as_posix(): p.read_bytes()
            for p in root.rglob("*")
            if p.is_file()
        }
        expected = (
            completed.returncode == 1
            and outcome.get("requires_initialization") is True
            and set(outcome.get("reasons", []))
            == {"requires_initialization", "web_static_incomplete"}
            and all(
                outcome.get("checks", {}).get(k) == "ok"
                for k in ("config", "contract", "credentials", "tls")
            )
            and before == after
            and not (root / "platform.sqlite").exists()
        )
        report["results"].append(
            {
                "case": "platform_real_preflight_empty_database",
                "status": "passed" if expected else "failed",
                "exit_code": completed.returncode,
                "preflight": {
                    key: outcome.get(key)
                    for key in (
                        "status",
                        "checks",
                        "reasons",
                        "requires_initialization",
                    )
                },
                "files_unchanged": before == after,
                "file_count": len(before),
                "before_bytes_index_sha256": digest(
                    json.dumps(
                        {k: digest(v) for k, v in before.items()},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ),
                "after_bytes_index_sha256": digest(
                    json.dumps(
                        {k: digest(v) for k, v in after.items()},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ),
            }
        )
        memory = read_json(PACKAGE / "templates/memory.json")
        memory["contract_directory"] = str(contracts / "text-dialogue/v1")
        marker = str(root / "environment-selected-not-created.sqlite")
        memory["database_path"] = marker
        write_json(root / "memory-selected.json", memory)
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(contexts / "memory/src")
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["TIANSHU_MEMORY_CONFIG"] = str(root / "memory-selected.json")
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(Path(__file__).parent / "probe_memory_factory.py"),
                str(root / "different-cli-argument.json"),
                marker,
            ],
            env=environment,
            capture_output=True,
            timeout=30,
        )
        report["results"].append(
            {
                "case": "memory_real_factory_config_load_store_intercepted",
                "status": "passed"
                if result.returncode == 0 and not Path(marker).exists()
                else "failed",
                "exit_code": result.returncode,
                "database_opened": False,
                "scope": "instrumented_real_factory_not_runtime_startup",
            }
        )
        # Product-owned first-install transitions on an explicitly new synthetic database.
        # No fixture issuer, application server or network is needed by these maintenance CLIs.
        memory["database_path"] = str(root / "fresh-memory.sqlite")
        memory["source_sync"]["recovery_path"] = str(
            root / "fresh-memory.sqlite.source-guard.json"
        )
        write_json(root / "memory-migration.json", memory)
        codes = []
        for action in ("migrate-profiles", "migrate-sources"):
            result = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "tianshu_memory.cli",
                    "--config",
                    str(root / "memory-migration.json"),
                    action,
                    "--backup",
                    str(root / (action + ".backup.sqlite")),
                ],
                env=environment,
                capture_output=True,
                timeout=30,
            )
            codes.append(result.returncode)
            if result.returncode:
                break
        schema = None
        if codes == [0, 0]:
            with closing(
                sqlite3.connect(
                    (root / "fresh-memory.sqlite").as_uri() + "?mode=ro", uri=True
                )
            ) as db:
                schema = db.execute(
                    "SELECT value FROM metadata WHERE key='schema'"
                ).fetchone()[0]
        report["results"].append(
            {
                "case": "memory_real_first_install_synthetic_schema_1_to_3",
                "status": "passed"
                if codes == [0, 0]
                and schema == "3"
                and (root / "fresh-memory.sqlite.source-guard.json").is_file()
                else "failed",
                "exit_codes": codes,
                "schema": schema,
                "scope": "synthetic_database_product_cli_not_container",
            }
        )
    report["result"] = (
        "passed"
        if all(r["status"] == "passed" for r in report["results"])
        else "failed"
    )
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contexts", type=Path, required=True)
    parser.add_argument("--contracts-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = verify(args.contexts.resolve(), args.contracts_root.resolve())
    write_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
