"""Final guard/query real-Loki smoke and explicit TLS resource-boundary probes."""

import argparse
import hashlib
from http.client import HTTPSConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import uuid

from helpers import PACKAGE, ROOT, SNAPSHOT, deployment_fixture, emit, event
from configs import loki
from guard import Server, State
from policy import Policy, canonical
from query import LokiClient, QueryError
from reconcile import compare, read_logs
from run_native import port, substitute


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loki", type=Path, required=True)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    runtime = Path(__file__).parent / ".runtime"
    runtime.mkdir(exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="final-smoke-", dir=runtime)).resolve()
    report = {
        "kind": "dep-b-final-guard-query-smoke",
        "status": "failed",
        "real_guard": True,
        "real_loki": True,
        "real_query_client": True,
        "vector": "not_in_this_smoke",
        "ledger_background_monitor": "separate_regression_evidence",
        "linux_compose": False,
        "nas": False,
        "application_reclamation_authorized": False,
        "cases": [],
        "implementation_sha256": {},
    }
    for path in [
        *PACKAGE.glob("*.py"),
        Path(__file__),
        Path(__file__).with_name("helpers.py"),
    ]:
        report["implementation_sha256"][path.relative_to(ROOT).as_posix()] = (
            hashlib.sha256(path.read_bytes()).hexdigest()
        )
    server, process, budget_server = None, None, None
    sockets = []
    output = (root / "loki.stderr").open("wb")

    def record(name, **facts):
        report["cases"].append({"case": name, "status": "passed", **facts})
        print(json.dumps({"case": name, "status": "passed"}), flush=True)

    try:
        deployment_fixture(root, args.contract.resolve())
        (root / "loki-state").mkdir()
        config = substitute(
            loki(), {"/var/lib/loki": root / "loki-state", "/run/tls": root / "tls"}
        )
        http_port, grpc_port, guard_port = port(), port(), port()
        config["server"].update(
            http_listen_port=http_port,
            grpc_listen_port=grpc_port,
            http_listen_address="127.0.0.1",
        )
        (root / "loki.json").write_text(json.dumps(config))
        report["loki_config"] = config
        report["loki_config_sha256"] = hashlib.sha256(
            (root / "loki.json").read_bytes()
        ).hexdigest()
        report["loki_binary_sha256"] = hashlib.sha256(
            args.loki.read_bytes()
        ).hexdigest()
        process = subprocess.Popen(
            [str(args.loki.resolve()), "-config.file=" + str(root / "loki.json")],
            cwd=root,
            stdout=output,
            stderr=output,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        backend = LokiClient(
            f"https://127.0.0.1:{http_port}",
            root / "tls/ca.pem",
            certificate=root / "tls/client.pem",
            key=root / "tls/client.key",
        )
        state = State(
            {
                "tokens": {
                    role: str(root / "secrets" / (role + "_token"))
                    for role in ("writer", "query", "metrics")
                },
                "storage_roots": {"loki": str(root / "loki-state")},
                "reserve_bytes": 0,
            },
            backend,
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(root / "tls/guard.pem", root / "tls/guard.key")
        server = Server(("127.0.0.1", guard_port), state, context)
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        ).start()
        clients = {
            role: LokiClient(
                f"https://127.0.0.1:{guard_port}",
                root / "tls/ca.pem",
                (root / "secrets" / (role + "_token")).read_text(),
            )
            for role in ("writer", "query")
        }
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("loki_exited")
            if clients["query"].request("/ready")[0] == 200:
                break
            time.sleep(1)
        else:
            raise RuntimeError("loki_not_ready")
        records = []
        roots = {name: root / "logs" / name for name in SNAPSHOT["products"]}
        for name, path in roots.items():
            instance = str(uuid.uuid4())
            values = [event(n, name, instance) for n in (1, 2)]
            records.extend(values)
            emit(path / "smoke.jsonl", values)
        emit(root / "generated.jsonl", records)
        report["generated"] = records
        stamp = time.time_ns()
        payload = {
            "streams": [
                {
                    "stream": {"stack": "tianshu", "service": "final_smoke"},
                    "values": [
                        [str(stamp + i), canonical(value).decode()]
                        for i, value in enumerate(records)
                    ],
                }
            ]
        }
        status, _, _ = clients["writer"].request(
            "/loki/api/v1/push",
            "POST",
            json.dumps(payload).encode(),
            "application/json",
        )
        if status != 204:
            raise RuntimeError("push_not_accepted")
        policy = Policy(SNAPSHOT)
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            rows = clients["query"].range(
                '{stack="tianshu"}', stamp - 1, stamp + len(records)
            )
            landed, invalid, partial = read_logs(roots, policy)
            facts = compare(
                records,
                landed,
                [policy.line(line.encode() + b"\n") for _, line in rows],
                invalid,
                partial,
            )
            if facts["status"] == "passed":
                report["retrieved"] = rows
                report["query_bounds_ns"] = [stamp - 1, stamp + len(records)]
                record(
                    "real_guard_loki_query_roundtrip",
                    **{k: v for k, v in facts.items() if k != "status"},
                )
                break
            time.sleep(1)
        else:
            raise RuntimeError("query_reconciliation_failed")
        if (
            clients["query"].request("/loki/api/v1/push", "POST", b"{}")[0] != 401
            or clients["writer"].request("/loki/api/v1/labels")[0] != 401
        ):
            raise RuntimeError("role_boundary_failed")
        client_tls = ssl.create_default_context(cafile=str(root / "tls/ca.pem"))
        connection = HTTPSConnection(
            "127.0.0.1", guard_port, context=client_tls, timeout=3
        )
        connection.putrequest("POST", "/loki/api/v1/push")
        connection.putheader("Authorization", "Bearer " + clients["writer"].token)
        connection.putheader("Content-Length", str(4 * 1024**2 + 1))
        connection.endheaders()
        rejected = connection.getresponse().status
        connection.close()
        if rejected != 413:
            raise RuntimeError("push_size_boundary_failed")
        record(
            "roles_and_push_over_budget",
            query_push=401,
            writer_query=401,
            advertised_bytes=4 * 1024**2 + 1,
            rejection=413,
        )
        for _ in range(8):
            raw = socket.create_connection(("127.0.0.1", guard_port), timeout=3)
            sockets.append(client_tls.wrap_socket(raw, server_hostname="127.0.0.1"))
        if server.slots._value != 0:
            raise RuntimeError("worker_budget_not_occupied")
        rejected = False
        try:
            raw = socket.create_connection(("127.0.0.1", guard_port), timeout=3)
            with client_tls.wrap_socket(raw, server_hostname="127.0.0.1"):
                pass
        except (OSError, ssl.SSLError):
            rejected = True
        finally:
            for connection in sockets:
                connection.close()
            sockets.clear()
        if not rejected:
            raise RuntimeError("ninth_connection_not_rejected")
        deadline = time.monotonic() + 3
        while server.slots._value != 8 and time.monotonic() < deadline:
            time.sleep(0.05)
        again = clients["query"].range(
            '{stack="tianshu"}', stamp - 1, stamp + len(records)
        )
        if again != rows:
            raise RuntimeError("post_saturation_recovery_failed")
        record(
            "eight_tls_workers_bound_and_recovery",
            held_workers=8,
            ninth_rejected=True,
            recovered_rows=len(again),
        )

        class BudgetHandler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                count = 8 * 1024**2 + int(self.path == "/over")
                self.send_response(200)
                self.send_header("Content-Length", str(count))
                self.end_headers()
                self.wfile.write(b"x" * count)

        # Separate explicit substitute tests response bytes over real HTTPS;
        # this is NOT an oversized response generated by Loki.
        budget_server = ThreadingHTTPServer(("127.0.0.1", 0), BudgetHandler)
        budget_server.socket = context.wrap_socket(
            budget_server.socket, server_side=True
        )
        threading.Thread(
            target=budget_server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        ).start()
        budget_client = LokiClient(
            f"https://127.0.0.1:{budget_server.server_port}", root / "tls/ca.pem"
        )
        if len(budget_client.request("/exact")[1]) != 8 * 1024**2:
            raise RuntimeError("exact_response_budget_failed")
        try:
            budget_client.request("/over")
        except QueryError as exc:
            if str(exc) != "query_response_over_budget":
                raise
        else:
            raise RuntimeError("oversize_response_not_rejected")
        record(
            "query_response_budget_real_tls_synthetic_backend",
            exact_bytes=8 * 1024**2,
            over_bytes=8 * 1024**2 + 1,
            over_error="query_response_over_budget",
            real_loki_response=False,
        )
        report["status"] = "passed"
    except Exception as exc:
        report["error_code"] = (
            str(exc) if type(exc) is RuntimeError else "smoke_unverified"
        )
    finally:
        for connection in sockets:
            connection.close()
        for active in (server, budget_server):
            if active:
                active.shutdown()
                active.server_close()
        if process:
            process.terminate()
            process.wait(timeout=10)
        output.close()
        report["runtime_root"] = str(root)
        report["source_unchanged_during_run"] = all(
            hashlib.sha256((ROOT / name).read_bytes()).hexdigest() == digest
            for name, digest in report["implementation_sha256"].items()
        )
        if not report["source_unchanged_during_run"]:
            report["status"] = "failed"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"]}), flush=True)
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
