"""Subprocess-only synthetic Docker CLI, backed by a temporary JSON fixture."""

import json
import sys
import time
from pathlib import Path


def main():
    path = Path(sys.argv[1])
    args = sys.argv[2:]
    if args[:2] != ["--host", "unix:///var/run/docker.sock"]:
        return 90
    args = args[2:]
    state = json.loads(path.read_text(encoding="utf-8"))
    containers = {item[0]: item for item in state["containers"]}
    command = args[0]
    ids = args[3:] if command == "inspect" else args[2:] if command in {"update", "kill"} else []
    stage = {
        "ps": "ps", "inspect": "inspect_one" if len(ids) == 1 else "batch_inspect",
        "update": "update", "kill": "term",
    }[command]
    state.setdefault("events", []).append({"stage": stage, "ids": ids})
    faults = state.get("faults", {}).get(stage, [])
    fault = faults.pop(0) if faults else {}
    output, stderr, returncode = "", "", 0
    if command == "ps":
        output = "\n".join(containers)
    elif any(item not in containers for item in ids):
        returncode = 1
        stderr = "fixture missing container; RAW_STDERR_MUST_NOT_BE_SAVED"
        if command == "inspect":
            output = "\n".join(json.dumps(containers[item]) for item in ids if item in containers)
    elif command == "inspect":
        output = "\n".join(json.dumps(containers[item]) for item in ids)
    elif "returncode" not in fault:
        for item in ids:
            if command == "update":
                containers[item][7] = "no"
            elif command == "kill":
                containers[item][6] = "exited"
    for item in fault.get("remove_after", []):
        containers.pop(item, None)
    for item in fault.get("add_after", []):
        containers[item[0]] = item
    state["containers"] = list(containers.values())
    path.write_text(json.dumps(state), encoding="utf-8")
    time.sleep(fault.get("sleep_seconds", 0))
    print(fault.get("stdout", output))
    print(fault.get("stderr", stderr), file=sys.stderr)
    return fault.get("returncode", returncode)


if __name__ == "__main__":
    sys.exit(main())
