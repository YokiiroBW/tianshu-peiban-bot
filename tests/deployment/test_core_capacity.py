"""Exact ownership and bounded accounting regressions for the new deployment guard."""

from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import pytest


def module(name):
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).parents[2] / "deploy/core" / (name + ".py")
    )
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def fixture(tmp_path):
    root = tmp_path / "installation"
    root.mkdir()
    for name in ("config", "data"):
        (root / name).mkdir()
    binds = [
        {"source": str(root / "config"), "target": "/config", "read_only": True},
        {"source": str(root / "data"), "target": "/data", "read_only": False},
    ]
    config = {
        "schema_version": "tianshu.capacity.v1",
        "deployment_root": str(root),
        "targets": [str(root)],
        "state_directory": str(root / "guard"),
        "free_paths": [str(root)],
        "min_free_bytes": 1,
        "max_deployment_bytes": 10**9,
        "poll_seconds": 5,
        "term_timeout_seconds": 1,
        "container": {
            "id": "own",
            "image_id": "own-image",
            "image_bytes": 10,
            "project": "tianshu-core",
            "service": "tianshu",
            "release_id": "release",
            "entrypoint": ["python", "-m", "tianshu"],
            "command": ["serve", "--config", "/config/settings.json"],
            "user": "0:10001",
            "binds": binds,
        },
    }
    expected = config["container"]
    row = {
        "Id": "own",
        "Image": "own-image",
        "Config": {
            "User": expected["user"],
            "Entrypoint": expected["entrypoint"],
            "Cmd": expected["command"],
            "Labels": {
                "com.docker.compose.project": "tianshu-core",
                "com.docker.compose.service": "tianshu",
                "com.docker.compose.project.working_dir": str(root),
                "com.docker.compose.project.config_files": str(root / "compose.yaml"),
                "io.tianshu.product": "core",
                "io.tianshu.release": "release",
            },
        },
        "HostConfig": {"ReadonlyRootfs": True, "Privileged": False, "PidMode": ""},
        "Mounts": [
            {
                "Type": "bind",
                "Source": item["source"],
                "Destination": item["target"],
                "RW": not item["read_only"],
            }
            for item in binds
        ],
    }
    return config, row


def test_foreign_overlap_holds_only_the_exact_owned_product(tmp_path, monkeypatch):
    capacity = module("capacity")
    config, own = fixture(tmp_path)
    foreign = deepcopy(own)
    foreign["Id"] = "foreign"
    calls = []

    def docker(_binary, *args, **kwargs):
        calls.append(args)
        if args[:2] == ("ps", "-aq"):
            return "own\nforeign\n"
        if args[0] == "inspect":
            return json.dumps([own, foreign] if len(args) == 3 else [own])
        return ""

    monkeypatch.setattr(capacity, "docker", docker)
    result = capacity.CapacityGuard(config).poll(enforce=True)
    assert result["reason"] == "foreign_deployment_mount" and result["latched"]
    assert ("stop", "--time", "1", "own") in calls
    assert not any(args[0] in {"update", "stop"} and args[-1] == "foreign" for args in calls)


def test_wrong_image_cannot_be_stopped_as_the_owned_product(tmp_path, monkeypatch):
    capacity = module("capacity")
    config, row = fixture(tmp_path)
    row["Image"] = "different-image"
    writes = []

    def docker(_binary, *args, **kwargs):
        if args[0] in {"update", "stop"}:
            writes.append(args)
        return "own" if args[0] == "ps" else json.dumps([row])

    monkeypatch.setattr(capacity, "docker", docker)
    result = capacity.CapacityGuard(config).poll(enforce=True)
    assert result["reason"] == "foreign_deployment_identity" and writes == []


def test_one_large_flat_directory_is_processed_incrementally(tmp_path):
    capacity = module("capacity")
    for index in range(100):
        (tmp_path / str(index)).write_bytes(b"x" * 100)
    meter = capacity.TreeMeter([tmp_path])
    first = meter.step(entries=1)
    assert not first["complete"] and meter.last is None
    for _ in range(102):
        result = meter.step(entries=1)
        if result["complete"]:
            break
    assert result["complete"] and result["last_complete_bytes"] >= 10000


def test_business_data_growth_does_not_stop_product_even_with_legacy_quota(tmp_path, monkeypatch):
    capacity = module("capacity")
    config, row = fixture(tmp_path)
    config["max_deployment_bytes"] = 1  # Existing installed guards may retain this field.
    (tmp_path / "installation" / "data" / "download.part").write_bytes(b"video bytes")
    calls = []

    def docker(_binary, *args, **kwargs):
        calls.append(args)
        return "own" if args[0] == "ps" else json.dumps([row])

    monkeypatch.setattr(capacity, "docker", docker)
    result = capacity.CapacityGuard(config).poll(enforce=True)
    assert result["state"] == "healthy"
    assert result["deployment"]["image_bytes"] == 10
    assert not any(args[0] in {"update", "stop"} for args in calls)


def test_low_free_space_remains_independent_of_business_data_size(tmp_path, monkeypatch):
    capacity = module("capacity")
    config, row = fixture(tmp_path)
    config["min_free_bytes"] = 10**30
    calls = []

    def docker(_binary, *args, **kwargs):
        calls.append(args)
        return "own" if args[0] == "ps" else json.dumps([row])

    monkeypatch.setattr(capacity, "docker", docker)
    result = capacity.CapacityGuard(config).poll(enforce=True)
    assert result["reason"] == "minimum_free_bytes_breached"
    assert ("stop", "--time", "1", "own") in calls
