"""One bounded JSON request in, one JSON response out; no implicit target."""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading

from .config import load_config
from .executor import execute
from .protocol import Failure, MAX_REQUEST_BYTES, encode_response, envelope, parse_request


def read_input(stream, timeout=5.0):
    completed = queue.Queue(maxsize=1)

    def read():
        try:
            chunks = bytearray()
            while len(chunks) <= MAX_REQUEST_BYTES:
                chunk = os.read(stream.fileno(), min(8192, MAX_REQUEST_BYTES + 1 - len(chunks)))
                if not chunk:
                    break
                chunks.extend(chunk)
            completed.put(bytes(chunks))
        except OSError:
            completed.put(None)

    threading.Thread(target=read, daemon=True).start()
    try:
        raw = completed.get(timeout=timeout)
    except queue.Empty:
        raise Failure("request_input_timeout", "invalid") from None
    if raw is None:
        raise Failure("request_input_unavailable", "invalid")
    return raw


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only registered-node observation JSON process")
    parser.add_argument("--config", required=True, help="Executor-owned JSON registration file")
    args = parser.parse_args(argv)
    request = None
    try:
        request = parse_request(read_input(sys.stdin.buffer))
        config = load_config(args.config)
        result = execute(request, config)
    except Failure as failure:
        result = envelope(request, status=failure.status, reasons=[failure.code])
    except Exception:
        # A protocol peer receives a bounded failure, never raw SSH/config data
        # from exception text. Direct collector tests still expose exceptions.
        result = envelope(request, status="unavailable", reasons=["internal_error"])
    try:
        sys.stdout.buffer.write(encode_response(result, request))
        sys.stdout.buffer.flush()
    except OSError:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
