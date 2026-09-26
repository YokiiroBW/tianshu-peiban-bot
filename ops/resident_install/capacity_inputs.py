"""Render the fixed A2 guard inputs from a real A3 candidate export.

The source export may be an offline export of the final private inputs. The
Linux runner compares this config with the actual first and final exports
before starting any remaining service.
"""

import argparse
import hashlib
import json
import os
import posixpath
import re
from pathlib import Path

ROOT = "/volume2/tianshu-v2-resident"
TOOLING = "/volume2/tianshu-v2-resident-tooling"
CORE = "tianshu-v2-resident"
OBS = "tianshu-v2-resident-obs"
SERVICES = frozenset((
    "platform", "companion", "memory", "gateway", "obs-vector", "obs-loki",
    "obs-grafana", "obs-prometheus", "obs-guard",
))


def _read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _put(path, raw, mode):
    with path.open("xb") as stream:
        os.chmod(path, mode)
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def render(export, template, python, output):
    if not export.is_absolute() or not template.is_absolute() or not output.is_absolute():
        raise ValueError("absolute_paths_required")
    if output.exists() or not output.parent.is_dir():
        raise ValueError("fresh_output_required")
    lock_path = export / "resident-export.lock.json"
    lock = _read(lock_path)
    if (lock.get("status") != "resident_candidate"
            or lock.get("deployment_root") != ROOT
            or lock.get("release_ready") is not False
            or set(lock.get("images", {})) != SERVICES):
        raise ValueError("real_candidate_export_required")
    core_path = export / CORE / "compose.yaml"
    obs_path = export / OBS / "compose.yaml"
    for project, path in ((CORE, core_path), (OBS, obs_path)):
        if _sha(path) != lock["compose_sha256"][project]:
            raise ValueError("export_compose_hash_mismatch")
    core = _read(core_path)["services"]
    obs = _read(obs_path)["services"]
    services = {**core, **obs}
    if set(services) != SERVICES:
        raise ValueError("nine_services_required")
    binds = {}
    for name, spec in services.items():
        if spec.get("image") != lock["images"][name]:
            raise ValueError("export_image_mismatch")
        entries = []
        for volume in spec.get("volumes", []):
            if volume.get("type") != "bind":
                raise ValueError("unexpected_non_bind_volume")
            source = volume["source"]
            target = volume["target"]
            if (not isinstance(source, str) or not source.startswith(ROOT + "/")
                    or posixpath.normpath(source) != source
                    or not isinstance(target, str) or not target.startswith("/")
                    or posixpath.normpath(target) != target):
                raise ValueError("export_bind_invalid")
            entries.append({
                "source": source, "target": target,
                "read_only": volume.get("read_only") is True,
            })
        if not entries or len({(x["source"], x["target"]) for x in entries}) != len(entries):
            raise ValueError("export_bind_set_invalid")
        binds[name] = sorted(entries, key=lambda item: (item["source"], item["target"]))
    first_core = TOOLING + "/exports/first/" + CORE + "/compose.yaml"
    final_core = TOOLING + "/exports/final/" + CORE + "/compose.yaml"
    final_obs = TOOLING + "/exports/final/" + OBS + "/compose.yaml"
    config = {
        "deployment_root": ROOT,
        "state_dir": TOOLING + "/capacity-state",
        "docker_binary": "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose": {
            CORE: {"workdir": ROOT, "file": final_core},
            OBS: {"workdir": ROOT, "file": final_obs},
        },
        "platform_first_compose": {"workdir": ROOT, "file": first_core},
        "images": lock["images"],
        "binds": binds,
        "free_paths": ["/volume2/@docker"],
        "min_free_bytes": 20 * 1024**3,
        "max_deployment_bytes": 20 * 1024**3,
        "poll_seconds": 5,
        "term_timeout_seconds": 120,
    }
    unit = template.read_text(encoding="utf-8")
    if (python != "/volume2/Dockers/tianshu-v2-validation/wave1-20260923a/tooling/venv/bin/python"
            or unit.count("@PYTHON@") != 2
            or any(unit.count(marker) != 1 for marker in (
                "@CODE_ROOT@", "@CONFIG@", "@STATE_DIR@"
            ))):
        raise ValueError("unit_template_invalid")
    for marker, replacement in {
        "@PYTHON@": python,
        "@CODE_ROOT@": TOOLING + "/repo",
        "@CONFIG@": TOOLING + "/resident-capacity.json",
        "@STATE_DIR@": TOOLING + "/capacity-state",
    }.items():
        unit = unit.replace(marker, replacement)
    if re.search(r"@[A-Z_]+@", unit) or "TimeoutStopSec=600s" not in unit:
        raise ValueError("unit_template_not_final")
    output.mkdir(mode=0o700)
    os.chmod(output, 0o700)
    config_path = output / "resident-capacity.json"
    unit_path = output / "tianshu-resident-capacity.service"
    _put(config_path, (json.dumps(config, indent=2) + "\n").encode(), 0o600)
    _put(unit_path, unit.encode(), 0o644)
    return {
        "status": "capacity_inputs_ready",
        "config_sha256": _sha(config_path), "unit_sha256": _sha(unit_path),
        "source_export_lock_sha256": _sha(lock_path),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--export", type=Path, required=True)
    parser.add_argument("--unit-template", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = render(args.export, args.unit_template, args.python, args.output)
    except (OSError, ValueError, KeyError, TypeError):
        print(json.dumps({"status": "refused", "code": "invalid_or_unavailable_input"}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
