"""TLS role gateway plus bounded integrity monitor. No Docker API, product imports or raw logging."""

import argparse
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import shutil
import ssl
import threading
import time
from urllib.parse import urlsplit

from monitor import Ledger, capacities
from alerts import AlertStore
from policy import Policy
from query import LokiClient
from transport import Deadline, accept_tls

QUERY_PATHS = {
    "/loki/api/v1/query",
    "/loki/api/v1/query_range",
    "/loki/api/v1/labels",
    "/loki/api/v1/series",
    "/loki/api/v1/index/stats",
    "/loki/api/v1/detected_fields",
    "/loki/api/v1/detected_labels",
}


def route(method, raw_path):
    if len(raw_path) > 16384 or any(c in raw_path for c in ("\r", "\n", "\\", "#")):
        return None
    parts = urlsplit(raw_path)
    if (
        parts.scheme
        or parts.netloc
        or "%" in parts.path
        or "//" in parts.path
        or ".." in parts.path
    ):
        return None
    if method == "POST" and parts.path == "/loki/api/v1/push" and not parts.query:
        return "writer"
    if method == "GET" and (
        parts.path in QUERY_PATHS
        or re.fullmatch(r"/loki/api/v1/label/[a-zA-Z_][a-zA-Z0-9_]*/values", parts.path)
    ):
        return "query"
    if method == "GET" and raw_path in {"/metrics", "/backend-metrics"}:
        return "metrics"
    if method == "GET" and raw_path == "/ready":
        return "probe"
    return None


class State:
    def __init__(self, settings, backend):
        self.settings, self.backend = settings, backend
        self.lock = threading.Lock()
        self.metrics = {
            "monitor_success": 0,
            "monitor_last_success_timestamp": 0,
            "query_failures_total": 0,
            "push_rejected_total": 0,
            "proxy_auth_rejected_total": 0,
            "last_push_success_timestamp": 0,
        }
        self.stop = threading.Event()
        self.tokens = {
            role: Path(path).read_text().strip()
            for role, path in settings["tokens"].items()
        }
        if (
            set(self.tokens) != {"writer", "query", "metrics"}
            or len(set(self.tokens.values())) != 3
        ):
            raise ValueError("distinct_roles_required")
        if any(
            not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token)
            for token in self.tokens.values()
        ):
            raise ValueError("token_format_invalid")

    def add(self, name, amount=1):
        with self.lock:
            self.metrics[name] = self.metrics.get(name, 0) + amount

    def update(self, **values):
        with self.lock:
            self.metrics.update(values)

    def authenticated(self, role, header):
        roles = self.tokens if role == "probe" else (role,)
        return any(
            hmac.compare_digest(header, "Bearer " + self.tokens[key]) for key in roles
        )

    def space_available(self):
        return all(
            shutil.disk_usage(path).free > self.settings["reserve_bytes"]
            for path in self.settings["storage_roots"].values()
        )

    def render_metrics(self):
        with self.lock:
            return "".join(
                f"tianshu_obs_{name} {value}\n"
                for name, value in sorted(self.metrics.items())
            ).encode()

    def run_monitor(self):
        ledger, alerts = None, None
        try:
            policy = Policy(json.loads(Path(self.settings["snapshot"]).read_bytes()))
            policy.verify_contract(Path(self.settings["contract"]))
            ledger = Ledger(
                self.settings["ledger"],
                policy,
                self.settings.get("ledger_max_events", 1_000_000),
            )
            alerts = AlertStore(
                str(Path(self.settings["ledger"]).with_name("alerts.sqlite"))
            )
            while not self.stop.is_set():
                try:
                    result = ledger.scan(self.settings["log_roots"])
                    cap = capacities(
                        self.settings["log_roots"], self.settings["log_budgets"]
                    )
                    extra = {}
                    for service, capacity in cap.items():
                        for metric, value in capacity.items():
                            extra[f"source_{service}_{metric}"] = value
                    for role, path in self.settings["storage_roots"].items():
                        disk = shutil.disk_usage(path)
                        extra[f"storage_{role}_free_bytes"] = disk.free
                        extra[f"storage_{role}_ratio"] = 1 - disk.free / disk.total
                    self.update(**result, **extra, **ledger.metrics())
                    ledger.reconcile(self.backend)
                    self.update(
                        monitor_success=1,
                        monitor_last_success_timestamp=time.time(),
                        **ledger.metrics(),
                    )
                except Exception:
                    self.add("query_failures_total")
                    self.update(monitor_success=0)
                with self.lock:
                    metrics = dict(self.metrics)
                self.update(**alerts.update(metrics))
                self.stop.wait(self.settings.get("interval_seconds", 30))
        except Exception:
            self.update(monitor_success=0)
        finally:
            if ledger:
                ledger.close()
            if alerts:
                alerts.close()


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, state, context):
        self.state, self.context = state, context
        self.slots = threading.BoundedSemaphore(8)
        self.active_lock = threading.Lock()
        self.active = {}
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        with self.active_lock:
            self.active[request] = Deadline()
        try:
            super().process_request(request, client_address)
        except BaseException:
            with self.active_lock:
                self.active.pop(request).cancelled.set()
            self.slots.release()
            request.close()
            raise

    def process_request_thread(self, request, client_address):
        original = request
        with self.active_lock:
            deadline = self.active[original]
        try:
            request = accept_tls(request, self.context, deadline)
            super().process_request_thread(request, client_address)
        except Exception:
            request.close()
        finally:
            deadline.cancelled.set()
            request.close()
            with self.active_lock:
                self.active.pop(original, None)
            self.slots.release()

    def server_close(self):
        with self.active_lock:
            for deadline in self.active.values():
                deadline.cancelled.set()
        # Nonblocking I/O observes cancellation within its <=50ms poll interval.
        super().server_close()

    def handle_error(self, request, client_address):
        # Base class would dump arbitrary exception text (possibly headers/query).
        self.state.add("proxy_errors_total")


class Handler(BaseHTTPRequestHandler):
    server_version = "TianShuObservability"
    sys_version = ""

    def log_message(self, *args):
        pass

    def send_error(self, code, message=None, explain=None):
        self.reply(code, b'{"error":"request_refused"}')

    def reply(self, status, body, content_type="application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def do_GET(self):
        self.dispatch()

    def do_POST(self):
        self.dispatch()

    def dispatch(self):
        state = self.server.state
        role = route(self.command, self.path)
        if role is None:
            return self.reply(404, b'{"error":"route_refused"}')
        headers = self.headers.get_all("Authorization", [])
        if len(headers) != 1 or not state.authenticated(role, headers[0]):
            state.add("proxy_auth_rejected_total")
            return self.reply(401, b'{"error":"unauthorized"}')
        if role == "metrics":
            if self.path == "/backend-metrics":
                try:
                    status, body, content_type = state.backend.request(
                        "/metrics", deadline=self.connection.deadline
                    )
                    return self.reply(
                        status, body if status == 200 else b"", content_type
                    )
                except Exception:
                    return self.reply(503, b'{"error":"backend_unavailable"}')
            return self.reply(200, state.render_metrics(), "text/plain; version=0.0.4")
        if role == "probe":
            try:
                status, _, _ = state.backend.request(
                    "/ready", deadline=self.connection.deadline
                )
                return self.reply(
                    200 if status == 200 else 503, b'{"status":"observed"}'
                )
            except Exception:
                return self.reply(503, b'{"status":"unavailable"}')
        body = None
        if role == "writer":
            if (
                self.headers.get("Transfer-Encoding")
                or len(self.headers.get_all("Content-Length", [])) != 1
            ):
                return self.reply(400, b'{"error":"length_required"}')
            try:
                size = int(self.headers["Content-Length"])
                if not 0 < size <= 4 * 1024 * 1024:
                    raise ValueError()
            except ValueError:
                return self.reply(413, b'{"error":"body_over_budget"}')
            if not state.space_available():
                state.add("push_rejected_total")
                # Retriable by Vector (unlike 4xx); no incoming events are acknowledged.
                return self.reply(503, b'{"error":"storage_capacity"}')
            body = self.rfile.read(size)
            if len(body) != size:
                return self.reply(400, b'{"error":"incomplete_body"}')
        try:
            status, data, content_type = state.backend.request(
                self.path,
                self.command,
                body,
                self.headers.get("Content-Type"),
                self.headers.get("Content-Encoding"),
                deadline=self.connection.deadline,
            )
            if role == "writer":
                if 200 <= status < 300:
                    state.update(last_push_success_timestamp=time.time())
                else:
                    state.add("push_rejected_total")
            # Backend error strings may contain submitted lines; only successful queries pass.
            if status >= 300:
                return self.reply(
                    status if status >= 400 else 502, b'{"error":"backend_refused"}'
                )
            return self.reply(status, data if role == "query" else b"", content_type)
        except Exception:
            state.add("proxy_errors_total")
            return self.reply(503, b'{"error":"backend_unavailable"}')


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--settings", type=Path, required=True)
    args = p.parse_args()
    settings = json.loads(args.settings.read_bytes())
    backend = LokiClient(
        settings["loki_url"],
        settings["ca"],
        certificate=settings["client_cert"],
        key=settings["client_key"],
    )
    state = State(settings, backend)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(settings["server_cert"], settings["server_key"])
    server = Server(("0.0.0.0", 8443), state, context)
    threading.Thread(target=state.run_monitor, daemon=True).start()
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        state.stop.set()
        server.server_close()
        backend.close()


if __name__ == "__main__":
    main()
