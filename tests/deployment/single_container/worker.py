"""A real, isolated service process for supervisor tests; no product access."""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--name", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--events", required=True)
    parser.add_argument("--ready-after", type=float, default=0)
    parser.add_argument("--readiness-file")
    parser.add_argument("--exit-after", type=float)
    parser.add_argument("--ignore-stop", action="store_true")
    parser.add_argument("--token")
    parser.add_argument("--spawn-child", action="store_true")
    args = parser.parse_args()
    stopping = threading.Event()
    started = time.monotonic()
    descendant = None

    def event(kind):
        raw = json.dumps({"name": args.name, "event": kind, "pid": os.getpid(),
                          "fixture": os.environ.get("FIXTURE_VALUE"),
                          "uid": os.getuid() if os.name == "posix" else None,
                          "gid": os.getgid() if os.name == "posix" else None,
                          "groups": os.getgroups() if os.name == "posix" else None,
                          "descendant": descendant.pid if descendant else None}) + "\n"
        with Path(args.events).open("a", encoding="utf-8") as stream:
            stream.write(raw)

    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), lambda *_: None if args.ignore_stop else stopping.set())

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            status = 200 if time.monotonic() - started >= args.ready_after else 503
            if args.readiness_file and Path(args.readiness_file).read_text() != "ready":
                status = 503
            if args.token and self.headers.get("Authorization") != "Bearer " + args.token:
                status = 401
            if self.path == "/redirect":
                self.send_response(302)
                self.send_header("Location", "/ready")
            else:
                self.send_response(status)
            self.send_header("Content-Length", "0")
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    if args.spawn_child:
        descendant = subprocess.Popen([sys.executable, "-c", "import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)"])
        time.sleep(0.05)
    event("started")
    try:
        while not stopping.wait(0.02):
            if args.exit_after is not None and time.monotonic() - started >= args.exit_after:
                return 7
    finally:
        server.shutdown()
        server.server_close()
        event("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
