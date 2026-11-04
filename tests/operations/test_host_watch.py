import importlib.util
import json
from pathlib import Path


def host_module():
    path = Path(__file__).resolve().parents[2] / "deploy/core/operations/host_watch.py"
    spec = importlib.util.spec_from_file_location("tianshu_test_host_watch", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_unknown_smtp_receipt_is_not_sent_again(tmp_path, monkeypatch):
    host = host_module()
    path = tmp_path / "event-fixture.json"
    config = {"recipient": "owner@example.test"}
    data = {"event": {"incident_id": "incident_fixture", "event_id": "event_fixture"},
            "notification": {"state": "pending", "attempts": 0, "target": config["recipient"]}}
    calls = []
    def send(*args):
        calls.append(args)
        return {"state": "unknown", "code": "timeout_after_data"}
    monkeypatch.setattr(host, "smtp_send", send)
    host.send_pending(path, data, config)
    host.send_pending(path, json.loads(path.read_text(encoding="utf-8")), config)
    assert len(calls) == 1
    assert json.loads(path.read_text(encoding="utf-8"))["notification"]["state"] == "unknown"


def test_restart_requires_matching_owner_generation_and_is_bounded(tmp_path, monkeypatch):
    host = host_module()
    target = {"role": "operations", "container": "tianshu-ops", "generation": "v1", "restart_on_stopped": True}
    actual = {"state": "exited", "id": "container-id", "labels": {"com.tianshu.product": "tianshu",
               "com.tianshu.role": "operations", "com.tianshu.generation": "other"}}
    monkeypatch.setattr(host, "inspect", lambda _name: actual)
    commands = []
    monkeypatch.setattr(host.subprocess, "run", lambda *args, **kwargs: commands.append(args))
    config = {"targets": [target], "spool_directory": str(tmp_path), "smtp": None}
    state = {}
    host.tick(config, state)
    assert commands == []
    actual["labels"]["com.tianshu.generation"] = "v1"
    for _ in range(5):
        host.tick(config, state)
    assert len(commands) == 3
