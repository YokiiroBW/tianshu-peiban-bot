"""Fixed installed node runner; detached workers survive the SSH request process."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from ..__main__ import read_input
from ..protocol import Failure
from ..transport import Deadline
from .config import load_remote
from .protocol import MAX_REQUEST_BYTES, encode, envelope, parse_request
from .service import Service
from .lifecycle import process_identity


def launch(config, operation_id):
    environment = dict(os.environ)
    root = str(Path(__file__).resolve().parents[3])
    environment["PYTHONPATH"] = root + (os.pathsep + environment["PYTHONPATH"] if environment.get("PYTHONPATH") else "")
    argv = [sys.executable, "-B", "-m", "ops.infrastructure.control.remote", "--config", str(config.path), "--worker", operation_id]
    options = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL, "close_fds": True, "env": environment}
    if os.name == "nt":
        options["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        options["start_new_session"] = True
    # The worker is deliberately independent of the request process's stdio and
    # lifetime. Its own Docker children still use the shared bounded process tree.
    process = subprocess.Popen(argv, **options)
    return {"state": "launched", "identity": process_identity(process.pid)}


def main(argv=None):
    parser = argparse.ArgumentParser(description="Registered Docker/Dockge node runner")
    parser.add_argument("--config", required=True)
    parser.add_argument("--worker", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    request = None
    try:
        config = load_remote(args.config)
        if args.worker:
            service = Service(config, Deadline(config.operation_timeout_seconds))
            service.work(args.worker)
            return 0
        request = parse_request(read_input(sys.stdin.buffer, max_bytes=MAX_REQUEST_BYTES))
        service = Service(config, Deadline(request.timeout_ms / 1000))
        result = service.dispatch(request, lambda operation_id: launch(config, operation_id))
    except Failure as failure:
        result = envelope(request, status=failure.status, reasons=[failure.code])
    except Exception:
        result = envelope(request, status="unavailable", reasons=["internal_error"])
    try:
        output = encode(result)
    except Failure:
        output = encode(envelope(request, status="unavailable", reasons=["response_too_large"]))
    try:
        sys.stdout.buffer.write(output)
        sys.stdout.buffer.flush()
    except (OSError, Failure):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
