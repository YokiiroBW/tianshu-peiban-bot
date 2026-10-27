import os
import sys

import pytest

from deploy.single_container.process import validate_process
from deploy.single_container.runtime import Runtime
from deploy.tianshu.runtime_guard import acquire, arguments


def test_native_and_unified_owner_arguments():
    command = ["python", "-m", "native"]
    assert arguments(["/data/module", *command]) == (["/data/module"], command)
    assert arguments(
        ["--state-dir", "/data/a", "--state-dir", "/data/b", "--", *command]
    ) == (["/data/a", "/data/b"], command)
    with pytest.raises(ValueError):
        arguments(["--state-dir", "/data/a", "--"])


def test_plugin_controller_stays_open_until_business_shutdown(tmp_path):
    events = []

    class Controller:
        closed = False

        def open(self):
            pass

        def status(self):
            return {"state": "stopped"}

        def live_pids(self):
            return set()

        def close(self):
            self.closed = True
            events.append("controller_closed")
            return True

    class Group:
        def __init__(self):
            self.processes = {}

        def start(self):
            pass

        def snapshot(self):
            return {"fixture": {"state": "ready", "health": {"ok": True}}}

        def stop(self):
            assert not controller.closed  # Platform can still acknowledge plugin.stop.
            events.append("business_stopped")
            return True

    runtime = Runtime(
        {
            "state_file": str(tmp_path / "runtime.json"),
            "processes": [
                validate_process(
                    {
                        "name": "fixture",
                        "argv": [sys.executable],
                        "health": {"type": "tcp", "host": "127.0.0.1", "port": 1},
                    }
                )
            ],
        }
    )
    controller = Controller()
    runtime.comics, runtime.group = controller, Group()
    runtime.stop_event.set()
    assert runtime.run(install_signals=False) == 0
    assert events == ["business_stopped", "controller_closed"]


@pytest.mark.skipif(
    sys.platform != "linux", reason="native deployment ownership uses Linux flock"
)
def test_multiple_directory_ownership_and_failure_rollback(tmp_path):
    a, b = tmp_path / "a", tmp_path / "b"
    a.mkdir()
    b.mkdir()
    first = acquire([str(b)])
    try:
        with pytest.raises(OSError):
            acquire([str(a), str(b)])
        # Failed multi-lock acquisition did not strand the first directory.
        released = acquire([str(a)])
        for fd in released:
            os.close(fd)
    finally:
        for fd in first:
            os.close(fd)
    both = acquire([str(a), str(b), str(a)])
    try:
        assert len(both) == 2
        assert all(os.get_inheritable(fd) for fd in both)
    finally:
        for fd in both:
            os.close(fd)
    # Two bind destinations can expose the very same native lock inode.
    (b / ".deployment-owner.lock").unlink()
    os.link(a / ".deployment-owner.lock", b / ".deployment-owner.lock")
    aliased = acquire([str(a), str(b)])
    try:
        assert len(aliased) == 1
    finally:
        for fd in aliased:
            os.close(fd)
