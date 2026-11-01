"""Relocated helper entry points and uncertain-operation recovery; no real SSH/Docker."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from jsonschema import Draft202012Validator, FormatChecker


HELPER = Path(__file__).parents[2] / "deploy/core/infrastructure"


def registration(tmp_path):
    node = {
        "label": "Synthetic registered NAS",
        "kind": "synology",
        "host": "not-connected.example.invalid",
        "port": 22,
        "user": "fixture",
        "identity_file": str(tmp_path / "identity"),
        "known_hosts_file": str(tmp_path / "known-hosts"),
    }
    observation = {
        "version": "infrastructure-node.v1",
        "state_dir": str(tmp_path / "sampling"),
        "nodes": {"fixture-nas": node},
    }
    path = tmp_path / "executor.json"
    path.write_text(json.dumps(observation))
    relay = {
        "version": "infrastructure-control.config.v1",
        "observation_config": str(path),
        "state_dir": str(tmp_path / "relay-state"),
        "nodes": {"fixture-nas": {"command": ["never-executed-remote-command"]}},
    }
    relay_path = tmp_path / "relay.json"
    relay_path.write_text(json.dumps(relay))
    return path, relay_path


def exchange(launcher, config, kind):
    request = {
        "version": "infrastructure-" + kind + ".v1",
        "request_id": "catalog-fixture",
        "node_id": None,
        "operation": "node.catalog" if kind == "node" else "control.catalog",
        "params": {},
        "timeout_ms": 2000,
    }
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(HELPER / launcher), "--config", str(config)],
        input=json.dumps(request).encode(),
        capture_output=True,
        timeout=10,
        check=True,
    )
    response = json.loads(result.stdout)
    for name, value in (("request", request), ("response", response)):
        schema = json.loads((HELPER / "contracts" / kind / (name + ".schema.json")).read_text())
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    assert response["request_id"] == request["request_id"]
    assert response["status"] == "ok"
    assert response["data"]["nodes"][0]["id"] == "fixture-nas"


def test_isolated_catalog_launchers_match_native_schemas(tmp_path):
    observation, relay = registration(tmp_path)
    exchange("run_node.py", observation, "node")
    exchange("run_control.py", relay, "control")
    result = subprocess.run(
        [sys.executable, "-I", "-B", str(HELPER / "run_remote.py"),
         "--python", sys.executable, "--help"],
        capture_output=True,
        timeout=10,
        check=True,
    )
    assert b"Registered Docker/Dockge node runner" in result.stdout
    expected = {
        "node/request": "83208d146feaaeca586558391bf04c80f7266973a4fa17cde184da389fdffbb5",
        "node/response": "e2bee732aa22119000be753974e54124cc119e01ca2b1fd9d79157e267f6e092",
        "control/request": "30b0f4f785708797d9061388b1db77606c70137983eaefcea24345df00d1a830",
        "control/response": "908a2e93282b0dd283f31a2aa67fc4201de1af619b86115e9e9a8c3c42867b22",
    }
    for path, digest in expected.items():
        assert hashlib.sha256((HELPER / "contracts" / (path + ".schema.json")).read_bytes()).hexdigest() == digest


def test_relay_retains_unknown_original_id_and_reads_back_without_resubmit(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    from tianshu_infrastructure.control.client import execute
    from tianshu_infrastructure.control.config import load_relay
    from tianshu_infrastructure.control.protocol import Request

    _, relay = registration(tmp_path)
    config = load_relay(str(relay))
    calls = []

    class LostTransport:
        def __init__(self, *args):
            pass

        def command(self, argv, raw, **kwargs):
            calls.append(json.loads(raw))
            return SimpleNamespace(reason="ssh_unavailable")

    request = Request("fixture-call", "fixture-nas", "operation.submit", {
        "operation_id": "original-operation",
        "action": "container.restart",
        "target": {"container_id": "a" * 64},
    }, 2000)
    for _ in range(2):
        response = execute(request, config, transport_factory=LostTransport)
        assert response["data"]["operation"]["state"] == "unknown"
        assert response["data"]["operation"]["operation_id"] == "original-operation"
    assert [call["operation"] for call in calls] == ["operation.submit", "operation.get"]
    assert calls[1]["params"] == {"operation_id": "original-operation", "reconcile": True}


def test_remote_worker_uses_relocated_isolated_launcher(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    from tianshu_infrastructure.control import remote

    calls = []

    def spawn(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(pid=12345)

    monkeypatch.setattr(remote.subprocess, "Popen", spawn)
    monkeypatch.setattr(remote, "process_identity", lambda pid: {"pid": pid, "created": "fixture"})
    remote.launch(SimpleNamespace(path=tmp_path / "remote.json"), "original-operation")
    argv, options = calls[0]
    assert argv[:4] == [sys.executable, "-I", "-B", str(HELPER / "run_remote.py")]
    assert argv[-2:] == ["--worker", "original-operation"]
    assert options["stdin"] == options["stdout"] == options["stderr"] == subprocess.DEVNULL


def test_registered_ssh_and_compose_retain_fixed_trust_boundaries(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(HELPER))
    from tianshu_infrastructure.compose import _YAML
    from tianshu_infrastructure.config import load_config
    from tianshu_infrastructure.transport import Deadline, SSHTransport

    observation, _ = registration(tmp_path)
    (tmp_path / "identity").write_text("synthetic-not-a-real-key")
    (tmp_path / "known-hosts").write_text("synthetic-not-a-real-host-pin")
    config = load_config(str(observation))
    argv = SSHTransport(config, config.nodes["fixture-nas"], Deadline(1))._argv("true")
    assert "StrictHostKeyChecking=yes" in argv
    assert "IdentityAgent=none" in argv and "ProxyCommand=none" in argv
    assert "ClearAllForwardings=yes" in argv
    assert argv[-2:] == ["not-connected.example.invalid", "true"]
    assert _YAML("services:\n  app:\n    image: example.invalid/fixture:1\n").root is not None
    try:
        _YAML("services: !!python/object:fixture {}")
    except ValueError:
        pass
    else:
        raise AssertionError("unsafe YAML tag accepted")
