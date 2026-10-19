"""One versioned control request through the registered SSH transport."""

from __future__ import annotations

import argparse
import sys

from ..__main__ import read_input
from ..protocol import Failure
from .client import execute
from .config import load_relay
from .protocol import MAX_REQUEST_BYTES, encode, envelope, parse_request


def main(argv=None):
    parser = argparse.ArgumentParser(description="Persistent registered Docker/Dockge control JSON process")
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    request = None
    try:
        request = parse_request(read_input(sys.stdin.buffer, max_bytes=MAX_REQUEST_BYTES))
        result = execute(request, load_relay(args.config))
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
