"""Bounded operator browser window; availability never counts as UI acceptance."""

import os
import time

from manifest import read_json, require, write_json


def wait(root, password, seconds):
    require(
        type(seconds) is int and 1 <= seconds <= 600, "browser_window_budget_invalid"
    )
    metadata = read_json(root / "deployment.json")
    require(
        metadata["compose_inputs"].get("resource_profile", {}).get("kind")
        == "nas-cpuset-lan-qa-v1",
        "browser_window_lan_qa_required",
    )
    private = root / "reports/browser-private"
    require(not private.exists(), "browser_window_already_attempted")
    private.mkdir(mode=0o700)
    settings = read_json(root / "config/platform/settings.json")
    path = private / "login.json"
    write_json(
        path,
        {
            "username": settings["web"]["username"],
            "password": password,
            "origin": settings["web"]["origin"],
        },
    )
    os.chmod(path, 0o600)
    print("[validation] bounded LAN browser window ready", flush=True)
    deadline = time.monotonic() + seconds
    try:
        while time.monotonic() < deadline:
            finished = root / "reports/browser-finish.json"
            if finished.exists():
                require(
                    read_json(finished) == {"finished": True}, "browser_finish_invalid"
                )
                return "closed_by_operator"
            time.sleep(0.5)
        return "window_expired"
    finally:
        path.unlink()
