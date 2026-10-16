"""Run in the candidate Linux image: actual POSIX processes and local OS files.

No SSH, external node, Docker daemon or business logs are accessed here. This
proves Linux runtime behavior only; registered NAS acceptance is a separate run.
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ops.infrastructure.acceptance import snapshot_summary
from ops.infrastructure.compose import collect_compose
from ops.infrastructure.config import Config, Node
from ops.infrastructure.linux import collect_node
from ops.infrastructure.protocol import Failure, envelope, utc_now
from ops.infrastructure.sampling import SampleStore
from ops.infrastructure.transport import Deadline, run_bounded


class LocalPOSIX:
    """Execute the real fixed collector shell commands in this container."""

    def __init__(self):
        self.deadline = Deadline(10)

    def script(self, script, **kwargs):
        return run_bounded([shutil.which("sh"), "-s"], ("export LC_ALL=C\n" + script).encode(), self.deadline, **kwargs)

    def command(self, command, payload=b"", **kwargs):
        return run_bounded(command, payload, self.deadline, **kwargs)


def retired(pid):
    path = Path(f"/proc/{pid}/stat")
    if not path.exists():
        return True
    return path.read_text().rsplit(")", 1)[1].split()[0] == "Z"


def process_checks(directory):
    result = {}
    flood = "import os;\nwhile True:\n os.write(1,b'x'*8192);os.write(2,b'y'*8192)"
    captured = run_bounded([sys.executable, "-c", flood], b"", Deadline(2), max_output=32768, stderr_limit=32768)
    assert captured.truncated and len(captured.stdout) + len(captured.stderr) <= 32768
    result["stdout_stderr_bounded"] = True
    for behavior in ("alive", "exit"):
        pid_file = directory / (behavior + ".pid")
        script = "import os,subprocess,sys,time; from pathlib import Path; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']); Path(sys.argv[1]).write_text(str(p.pid)); os._exit(0) if sys.argv[2]=='exit' else time.sleep(30)"
        started = time.monotonic()
        captured = run_bounded([sys.executable, "-c", script, str(pid_file), behavior], b"", Deadline(.6))
        assert time.monotonic() - started < 2
        assert pid_file.exists() and retired(int(pid_file.read_text()))
        if behavior == "alive":
            assert captured.timed_out
        result["process_group_parent_" + behavior] = True
    return result


def host_checks(directory):
    state = directory / "sampling"
    state.mkdir()
    node = Node("linux-runtime", "Candidate container", "linux", "127.0.0.1", 1, "unused",
                str(directory / "not-used-key"), str(directory / "not-used-hosts"))
    config = Config(state, {node.id: node})
    store = SampleStore(config, node)
    first, first_reasons = collect_node(LocalPOSIX(), node, store, 120)
    store.save()
    time.sleep(.25)
    second, second_reasons = collect_node(LocalPOSIX(), node, SampleStore(config, node), 120)
    assert first["cpu"]["state"] == "first_sample"
    assert second["cpu"]["state"] == "ok" and 0 <= second["cpu"]["usage_ratio"] <= 1
    assert second["memory"]["total_bytes"] > 0
    assert second["interfaces"] and all(item["state"] == "ok" for item in second["interfaces"])
    summary = snapshot_summary(envelope(None, data=second, status="partial" if second_reasons else "ok", reasons=second_reasons))
    return {"actual_proc_df_interface_sources": True, "first_reason_codes": first_reasons, "snapshot": summary}, node


def compose_checks(directory, node):
    import dataclasses

    stacks = directory / "registered-stacks"
    stack = stacks / "sample"
    stack.mkdir(parents=True)
    (stack / "compose.yaml").write_text("name: sample\nservices:\n  web:\n    image: nginx:alpine\n")
    node = dataclasses.replace(node, stacks_root=str(stacks))
    data, reasons, total, cursor = collect_compose(LocalPOSIX(), node, {"limit": 200, "cursor": None})
    assert total == 1 and cursor is None and len(data["stacks"]) == 1
    projected = data["stacks"][0]
    assert projected["project_name"] == "sample" and projected["services"][0]["image"]["reference"] == "nginx:alpine"
    assert projected["drift_status"] == "unavailable" and "runtime_inventory_partial" in projected["reason_codes"]
    # A registered primary filename that is a symlink must not be read.
    linked = stacks / "linked"
    linked.mkdir()
    (linked / "compose.yaml").symlink_to(stack / "compose.yaml")
    data, _, total, _ = collect_compose(LocalPOSIX(), node, {"limit": 200, "cursor": None})
    assert total == 2
    symlink = next(item for item in data["stacks"] if item["name"] == "linked")
    assert symlink["state"] == "unavailable" and symlink["reason_codes"] == ["compose_file_unavailable"]
    return {"actual_find_head_projection": True, "symlink_not_read": True, "docker_evidence": "not_requested", "reason_codes": reasons}


def main():
    report = {"format": "infrastructure-linux-runtime.v1", "verified_at": utc_now(),
              "scope": "candidate_linux_container_only", "python": platform.python_version(),
              "system": platform.system(), "machine": platform.machine(), "remote_connections": 0}
    if sys.platform != "linux":
        report.update(verification="not_run", reason_code="linux_runtime_required")
        print(json.dumps(report, indent=2))
        return 2
    try:
        with tempfile.TemporaryDirectory(prefix="ts181-linux-runtime-") as temporary:
            directory = Path(temporary)
            report["processes"] = process_checks(directory)
            report["host"], node = host_checks(directory)
            report["compose"] = compose_checks(directory, node)
        report["verification"] = "passed"
    except Exception as error:
        report.update(verification="failed", failure_type=type(error).__name__)
        if isinstance(error, Failure):
            report["failure_code"] = error.code
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["verification"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
