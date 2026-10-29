"""TLS role gateway plus bounded integrity monitor. No Docker API, product imports or raw logging."""

import argparse
import hmac
import json
import re
import shutil
import sqlite3
import ssl
import threading
import time
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from alerts import MONITOR_STALE_SECONDS, AlertStore
from monitor import Ledger, capacities
from policy import Policy
from query import LokiClient, QueryError
from retained_query import query_path, window_filter
from retention import days, max_events
from source_retention import prune_sources
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

MONITOR_FAILURE_STAGES = (
    "startup", "source_scan", "capacity", "reconcile", "source_retention",
    "expiration", "refill", "retained_window", "deletions", "metrics", "alerts",
)
MONITOR_QUERY_REASONS = (
    "query_response_over_budget", "redirect_refused", "query_deadline_exceeded",
    "backend_unavailable", "query_request_budget_exceeded", "query_failed",
    "invalid_query_response", "timestamp_bucket_saturated",
)
MONITOR_FAILURE_REASONS = (
    *MONITOR_QUERY_REASONS, "sqlite_busy", "sqlite_error", "io_error",
    "invalid_state", "unexpected",
)


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
    if method == "POST" and raw_path == "/retention/clear":
        return "manage"
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
            **self.monitor_failure_values(),
        }
        self.stop = threading.Event()
        self.monitor_running = threading.Event()
        self.retained_filter = window_filter([])
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

    @staticmethod
    def monitor_failure_values(stage=None, reason=None):
        # Fixed metric names only. Never expose exception text, SQL, source paths,
        # selectors, credentials or log bodies through the diagnostics endpoint.
        return {
            **{f"monitor_failure_stage_{name}_current": int(name == stage)
               for name in MONITOR_FAILURE_STAGES},
            **{f"monitor_failure_reason_{name}_current": int(name == reason)
               for name in MONITOR_FAILURE_REASONS},
        }

    def monitor_failed(self, stage, error):
        if isinstance(error, QueryError) and str(error) in MONITOR_QUERY_REASONS:
            reason = str(error)
        elif isinstance(error, sqlite3.Error):
            reason = (
                "sqlite_busy"
                if getattr(error, "sqlite_errorcode", 0) & 0xFF in {
                    sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED
                }
                else "sqlite_error"
            )
        elif isinstance(error, OSError):
            reason = "io_error"
        elif isinstance(error, (ValueError, KeyError, TypeError)):
            reason = "invalid_state"
        else:
            reason = "unexpected"
        self.update(monitor_success=0, **self.monitor_failure_values(stage, reason))

    def authenticated(self, role, header):
        if role == "manage":
            role = "writer"
        roles = self.tokens if role == "probe" else (role,)
        return any(
            hmac.compare_digest(header, "Bearer " + self.tokens[key]) for key in roles
        )

    def clear_logs(self):
        """Persist one operator action; caller-supplied deletion queries are absent."""
        before = time.time()
        with closing(sqlite3.connect(self.settings["ledger"], timeout=3)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            known, pending = db.execute(
                "SELECT count(*),coalesce(sum(verified=0),0) FROM events "
                "WHERE first_seen<=? OR occurred<=?",
                (before, before),
            ).fetchone()
            db.execute(
                "UPDATE retention_control SET clear_before=max(clear_before,?) WHERE singleton=1",
                (before,),
            )
        with self.lock:
            self.retained_filter = window_filter([])
            self.metrics["retained_events"] = 0
        return {
            "before": before,
            "known_events": known,
            "known_pending_events": pending,
        }

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

    def monitor_ready(self, now=None):
        if self.stop.is_set() or not self.monitor_running.is_set():
            return False
        with self.lock:
            success = self.metrics["monitor_success"]
            last_success = self.metrics["monitor_last_success_timestamp"]
        now = time.time() if now is None else now
        # Reuse IntegrityMonitorStale's existing window, four default 30s cycles.
        return (
            success == 1
            and last_success > 0
            and 0 <= now - last_success <= MONITOR_STALE_SECONDS
        )

    def run_monitor(self):
        ledger, alerts = None, None
        stage = "startup"
        self.monitor_running.set()
        try:
            policy = Policy(json.loads(Path(self.settings["snapshot"]).read_bytes()))
            policy.verify_contract(Path(self.settings["contract"]))
            retention_days = days(self.settings.get("retention_days", 30))
            maximum = max_events(
                self.settings.get(
                    "log_max_events", self.settings.get("ledger_max_events", 10000)
                )
            )
            ledger = Ledger(
                self.settings["ledger"],
                policy,
                maximum,
                retention_days,
            )
            alerts = AlertStore(
                str(Path(self.settings["ledger"]).with_name("alerts.sqlite")),
                retention_days,
            )
            while not self.stop.is_set():
                succeeded = True
                try:
                    stage = "source_scan"
                    result = ledger.scan(self.settings["log_roots"])
                    stage = "capacity"
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
                    self.update(**result, **extra)
                    stage = "reconcile"
                    ledger.reconcile(
                        self.backend,
                        retention_seconds=retention_days * 86400,
                        batch=min(1024, maximum),
                    )
                    stage = "source_retention"
                    cleanup, coverage = prune_sources(
                        ledger,
                        self.settings["log_roots"],
                        retention_days,
                        host_state_path=self.settings.get("host_state_path"),
                    )
                    stage = "expiration"
                    self.update(
                        **cleanup,
                        **ledger.expire(
                            retention_days,
                            coverage=coverage,
                            reserve=min(512, max(1, maximum // 10))
                            if ledger.capacity_rejected_current
                            else 0,
                        ),
                    )
                    free = (
                        maximum
                        - ledger.db.execute(
                            "SELECT landed FROM totals WHERE singleton=1"
                        ).fetchone()[0]
                    )
                    if ledger.capacity_rejected_current and free > 0:
                        stage = "refill"
                        # Fill only proven free slots now. The read budget stops
                        # before another full-capacity rejection, while keeping
                        # every new unconsumed source byte for the next cycle.
                        self.update(
                            **ledger.scan(self.settings["log_roots"], line_budget=free)
                        )
                    if free > 0:
                        ledger.capacity_rejected_current = 0
                    stage = "retained_window"
                    with self.lock:
                        # An operator clear may arrive during the backend query.
                        # Publish under the same lock used to hide its old window;
                        # a completed clear cannot be overwritten by stale IDs.
                        ledger.apply_clear()
                        window = ledger.retained_window()
                        self.retained_filter = window_filter(window)
                        self.metrics["retained_events"] = len(window)
                    self.update(retention_delete_failures_current=0)
                    try:
                        stage = "deletions"
                        ledger.submit_deletions(self.backend)
                    except Exception:
                        self.update(retention_delete_failures_current=1)
                        raise
                except Exception as error:
                    succeeded = False
                    self.add("query_failures_total")
                    self.monitor_failed(stage, error)
                # Read one final snapshot, including newly landed events even if
                # Loki was unavailable. Aggregate metadata needs no full scan.
                try:
                    self.update(**ledger.metrics())
                except Exception as error:
                    succeeded = False
                    self.add("query_failures_total")
                    self.monitor_failed("metrics", error)
                if succeeded:
                    self.update(
                        monitor_success=1,
                        monitor_last_success_timestamp=time.time(),
                    )
                with self.lock:
                    metrics = dict(self.metrics)
                stage = "alerts"
                self.update(**alerts.update(metrics))
                if succeeded:
                    self.update(**self.monitor_failure_values())
                self.stop.wait(self.settings.get("interval_seconds", 30))
        except Exception as error:
            self.monitor_failed(stage, error)
        finally:
            self.monitor_running.clear()
            self.update(monitor_success=0)
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
        self.workers = set()
        self.stopping = threading.Event()
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if self.stopping.is_set():
            request.close()
            return
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        with self.active_lock:
            if self.stopping.is_set():
                self.slots.release()
                request.close()
                return
            self.active[request] = Deadline()
            worker = threading.Thread(
                target=self.process_request_thread,
                args=(request, client_address),
                daemon=True,
            )
            self.workers = {thread for thread in self.workers if thread.is_alive()}
            self.workers.add(worker)
        try:
            worker.start()
        except BaseException:
            with self.active_lock:
                self.active.pop(request).cancelled.set()
                self.workers.discard(worker)
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

    def cancel_requests(self):
        self.stopping.set()
        with self.active_lock:
            for deadline in self.active.values():
                deadline.cancelled.set()

    def join_requests(self, timeout):
        deadline = time.monotonic() + timeout
        with self.active_lock:
            workers = tuple(self.workers)
        for thread in workers:
            if thread.ident is not None:
                thread.join(max(0, deadline - time.monotonic()))
        with self.active_lock:
            return not self.active and all(
                not thread.is_alive() for thread in self.workers
            )

    def server_close(self):
        self.cancel_requests()
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
        if role == "manage":
            if (
                self.headers.get("Transfer-Encoding")
                or self.headers.get("Content-Length", "0") != "0"
            ):
                return self.reply(400, b'{"error":"empty_clear_request_required"}')
            try:
                cleared = state.clear_logs()
                return self.reply(
                    202, json.dumps({"status": "accepted", **cleared}).encode()
                )
            except (sqlite3.Error, OSError, KeyError):
                return self.reply(503, b'{"error":"retention_unavailable"}')
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
                ready = status == 200 and state.monitor_ready()
                return self.reply(
                    200 if ready else 503,
                    b'{"status":"observed"}' if ready else b'{"status":"unavailable"}',
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
            path = self.path
            if role == "query":
                with state.lock:
                    expression = state.retained_filter
                path = query_path(path, expression)
            status, data, content_type = state.backend.request(
                path,
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


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("--settings", type=Path, required=True)
    p.add_argument("--host", default="0.0.0.0")
    p.add_argument("--port", type=int, default=8443)
    args = p.parse_args(argv)
    settings = json.loads(args.settings.read_bytes())
    backend = LokiClient(
        settings["loki_url"],
        settings["ca"],
        certificate=settings["client_cert"],
        key=settings["client_key"],
    )
    from guard_lifecycle import SignalStop, run

    try:
        state = State(settings, backend)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(settings["server_cert"], settings["server_key"])
        server = Server((args.host, args.port), state, context)
    except Exception:
        backend.close()
        print(json.dumps({"status": "startup_failed"}))
        return 2
    with SignalStop() as stop:
        result = run(server, state, backend, lambda: stop.requested)
    print(json.dumps({"status": "stopped" if result == 0 else "shutdown_unconfirmed"}))
    return result


if __name__ == "__main__":
    raise SystemExit(main())
