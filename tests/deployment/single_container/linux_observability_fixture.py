"""Synthetic-only fixture assembled from the native observability generators."""

import json
import sys
from pathlib import Path

OBS_TESTS = Path(__file__).resolve().parents[1] / "observability"
sys.path.insert(0, str(OBS_TESTS))

from helpers import SNAPSHOT, deployment_fixture

# isort: split

from configs import vector
from configure import prepare
from run_native import substitute

from deploy.single_container.plan import observer_processes
from deploy.single_container.profile import OBSERVABILITY, adapt

SOURCES = ("platform", "companion", "memory", "knowledge", "gateway")


def prepare_fixture(root, contract):
    """Require a new synthetic root; no existing runtime/configuration is loaded."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    manifest, settings = deployment_fixture(root, Path(contract).resolve(strict=True))
    settings["grafana_hostname"] = "localhost"
    bundle = prepare(root, settings, manifest, "standalone", SNAPSHOT, candidate=True)
    native = root / "native"
    native.mkdir()
    (root / "logs/knowledge").mkdir()
    (root / "engine-logs/grafana").mkdir(parents=True)
    replacements = {
        "/config/observability": native,
        "/config/contracts/diagnostics/v1": root / "contracts/diagnostics/v1",
        **{
            f"/data/observability/{name}": root / "data" / name
            for name in ("vector", "loki", "guard", "prometheus", "grafana")
        },
        **{f"/data/logs/{name}": root / "logs" / name for name in SOURCES},
        **{
            f"https://{name}:{port}": f"https://127.0.0.1:{port}"
            for name, port in (
                ("obs-guard", 8445),
                ("obs-loki", 3100),
                ("obs-prometheus", 9090),
                ("obs-vector", 9598),
            )
        },
        "https://localhost:3000": "https://127.0.0.1:3000",
        "obs-guard:8445": "127.0.0.1:8445",
        "obs-vector:9598": "127.0.0.1:9598",
        "/tmp/grafana": root / "engine-logs/grafana",
    }
    for path in sorted((bundle / "config").rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(bundle / "config")
        value = json.loads(path.read_bytes())
        if relative.as_posix() == "vector.json":
            value = vector(SNAPSHOT, include_knowledge=True)
        if relative.as_posix() == "guard.json":
            value["log_roots"]["knowledge"] = "/sources/knowledge"
            value["log_budgets"]["knowledge"] = 1024**3
        value = substitute(adapt(value, "obs-guard"), replacements)
        if relative.as_posix() == "loki.json":
            value["server"]["http_listen_address"] = "127.0.0.1"
        if relative.as_posix() == "vector.json":
            value["sinks"]["metrics"]["address"] = "127.0.0.1:9598"
        target = native / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    # Files retain their native bytes; only generated configuration paths move.
    import shutil

    for directory in ("tls", "secrets"):
        shutil.copytree(root / directory, native / directory)
    (native / "vocabulary.json").write_text(
        json.dumps(SNAPSHOT) + "\n", encoding="utf-8"
    )
    for name in ("vector", "loki", "guard", "prometheus", "grafana"):
        (root / "data" / name).mkdir(parents=True)
    processes = observer_processes(
        {}, {"obs-grafana": {"GF_SERVER_DOMAIN": "localhost"}}
    )
    processes = substitute(processes, replacements)
    for spec in processes:
        if spec["name"] == "obs-grafana":
            spec["env"]["GF_SERVER_HTTP_ADDR"] = "127.0.0.1"
        if spec["name"] == "obs-prometheus":
            spec["argv"].append("--web.listen-address=127.0.0.1:9090")
    assert {spec["name"] for spec in processes} == set(OBSERVABILITY)
    result = {
        "root": str(root),
        "processes": processes,
        "logs": {name: str(root / "logs" / name) for name in SOURCES},
    }
    (root / "fixture.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    return result
