"""Real Vector+Loki binaries, TLS guard and synthetic files. Never substitutes for Linux Compose."""

import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import threading
import time
import uuid

from helpers import SNAPSHOT, deployment_fixture, emit, event
from configs import vector, loki
from guard import Server, State
from policy import Policy
from query import LokiClient
from reconcile import compare, read_logs


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def substitute(value, replacements):
    if isinstance(value, dict):
        return {k: substitute(v, replacements) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, replacements) for v in value]
    if isinstance(value, str):
        for old, new in replacements.items():
            value = value.replace(old, str(new).replace("\\", "/"))
    return value


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--vector", type=Path, required=True)
    p.add_argument("--loki", type=Path, required=True)
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = {
        "kind": "dep-b-log-recovery",
        "mode": "native_" + os.name,
        "status": "failed",
        "results": [],
        "claims": {
            "real_vector": True,
            "real_loki": True,
            "linux_compose": False,
            "grafana": False,
            "nas": False,
            "application_reclamation": False,
        },
    }
    root = Path(
        tempfile.mkdtemp(prefix="native-", dir=Path(__file__).parent / ".runtime")
    ).resolve()
    processes, output_files = {}, []
    server = None

    def record(name, facts=None):
        report["results"].append({"case": name, "status": "pass", "facts": facts or {}})
        print(json.dumps({"case": name, "status": "pass"}), flush=True)

    def launch(name, binary, arguments):
        out = (root / (name + f"-{len(output_files)}.stderr")).open("wb")
        output_files.append(out)
        env = dict(os.environ)
        if name == "vector":
            env["VECTOR_LOG"] = "error"
        processes[name] = subprocess.Popen(
            [str(binary.resolve()), *arguments],
            cwd=root,
            stdout=out,
            stderr=out,
            env=env,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def stop(name):
        process = processes.pop(name, None)
        if process:
            process.terminate()
            process.wait(timeout=10)

    try:
        _, settings = deployment_fixture(root, args.contract.resolve())
        report["versions"] = {
            name: subprocess.check_output(
                [str(binary.resolve()), "--version"], timeout=10
            )
            .decode()
            .strip()
            for name, binary in (("vector", args.vector), ("loki", args.loki))
        }
        loki_port, grpc_port, guard_port, metrics_port = port(), port(), port(), port()
        (root / "loki-state").mkdir()
        (root / "vector-state").mkdir()
        lc = substitute(
            loki(), {"/var/lib/loki": root / "loki-state", "/run/tls": root / "tls"}
        )
        lc["server"].update(
            http_listen_port=loki_port,
            grpc_listen_port=grpc_port,
            http_listen_address="127.0.0.1",
        )
        # Faster chunk flushing only in this isolated synthetic process exercise.
        lc["ingester"]["chunk_idle_period"] = "10s"
        (root / "loki.json").write_text(json.dumps(lc))
        result = subprocess.run(
            [
                str(args.loki.resolve()),
                "-config.file=" + str(root / "loki.json"),
                "-verify-config=true",
            ],
            capture_output=True,
            timeout=20,
        )
        if result.returncode:
            (root / "validation-error.txt").write_bytes(result.stdout + result.stderr)
            raise RuntimeError("loki_config_invalid")
        backend = LokiClient(
            f"https://127.0.0.1:{loki_port}",
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
                "storage_roots": {
                    "loki": str(root / "loki-state"),
                    "vector": str(root / "vector-state"),
                },
                "reserve_bytes": 0,
            },
            backend,
        )
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(root / "tls/guard.pem", root / "tls/guard.key")
        server = Server(("127.0.0.1", guard_port), state, tls)
        threading.Thread(
            target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True
        ).start()
        vc = substitute(
            vector(SNAPSHOT),
            {
                "/var/lib/vector": root / "vector-state",
                "/sources": root / "logs",
                "/run/tls": root / "tls",
                "/run/secrets": root / "secrets",
            },
        )
        vc["sinks"]["loki"]["endpoint"] = f"https://127.0.0.1:{guard_port}"
        vc["sinks"]["metrics"]["address"] = f"127.0.0.1:{metrics_port}"
        (root / "vector.json").write_text(json.dumps(vc))
        result = subprocess.run(
            [
                str(args.vector.resolve()),
                "validate",
                "--skip-healthchecks",
                str(root / "vector.json"),
            ],
            capture_output=True,
            timeout=20,
        )
        if result.returncode:
            (root / "validation-error.txt").write_bytes(result.stdout + result.stderr)
            raise RuntimeError("vector_config_invalid")
        record("native_config_validation")
        launch("loki", args.loki, ["-config.file=" + str(root / "loki.json")])
        launch("vector", args.vector, ["--config", str(root / "vector.json")])
        client = LokiClient(
            f"https://127.0.0.1:{guard_port}",
            root / "tls/ca.pem",
            (root / "secrets/query_token").read_text(),
        )
        policy = Policy(SNAPSHOT)
        roots = {name: root / "logs" / name for name in SNAPSHOT["products"]}
        produced, start = [], time.time_ns() - 60 * 10**9

        def batch(index):
            for name, path in roots.items():
                group = [
                    event(
                        n,
                        name,
                        str(uuid.UUID(int=index * 100 + list(roots).index(name) + 1)),
                    )
                    for n in range(1, 41)
                ]
                produced.extend(group)
                emit(path / f"events.jsonl.{index}", group)

        def verify(case):
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                for name, process in processes.items():
                    if process.poll() is not None:
                        raise RuntimeError(name + "_exited")
                try:
                    rows = client.range('{stack="tianshu"}', start, time.time_ns())
                    got = [policy.line(line.encode() + b"\n") for _, line in rows]
                    landed, invalid, partial = read_logs(roots, policy)
                    result = compare(produced, landed, got, invalid, partial)
                    if result["status"] == "passed":
                        record(case, result)
                        return
                except Exception:
                    pass
                time.sleep(1)
            raise RuntimeError("reconciliation_timeout_" + case)

        batch(0)
        verify("normal_160_numbered_events")
        stop("loki")
        batch(1)
        time.sleep(3)
        stop("vector")
        launch("vector", args.vector, ["--config", str(root / "vector.json")])
        launch("loki", args.loki, ["-config.file=" + str(root / "loki.json")])
        verify("loki_outage_vector_crash_restart_disk_replay")
        stop("vector")
        batch(2)
        launch("vector", args.vector, ["--config", str(root / "vector.json")])
        verify("collector_stopped_rotation_replay")
        # Exercise the real retriable path without filling the developer's filesystem.
        state.settings["reserve_bytes"] = 2**63
        batch(3)
        deadline = time.monotonic() + 30
        while state.metrics["push_rejected_total"] == 0 and time.monotonic() < deadline:
            time.sleep(0.5)
        if state.metrics["push_rejected_total"] == 0:
            raise RuntimeError("capacity_guard_did_not_reject")
        state.settings["reserve_bytes"] = 0
        verify("capacity_reserve_rejection_then_recovery")
        # Distinct event fields stay byte-equivalent even with historical producer timestamps.
        canary = "DEP_B_SYNTHETIC_SECRET_CANARY"
        (roots["platform"] / "bad.jsonl").write_text(
            json.dumps(dict(event(), message=canary)) + "\n"
        )
        time.sleep(2)
        rows = client.range(
            '{stack="tianshu"} |= "' + canary + '"', start, time.time_ns()
        )
        if rows:
            raise RuntimeError("canary_leaked_to_loki")
        metric_client = LokiClient(
            f"https://127.0.0.1:{metrics_port}", root / "tls/ca.pem"
        )
        deadline = time.monotonic() + 35
        metrics = b""
        while (
            b"component_discarded_events_total" not in metrics
            and time.monotonic() < deadline
        ):
            _, metrics, _ = metric_client.request("/metrics")
            time.sleep(0.5)
        (root / "vector-metrics.txt").write_bytes(metrics)
        if b"component_discarded_events_total" not in metrics:
            raise RuntimeError("discard_metrics_missing")
        record("secret_canary_rejected_with_discard_metric")
        report["status"] = "passed"
    except Exception as exc:
        report["results"].append(
            {
                "case": "execution",
                "status": "fail",
                "facts": {
                    "reason": str(exc)
                    if type(exc) is RuntimeError
                    else "native_execution_failed"
                },
            }
        )
    finally:
        for name in list(processes):
            stop(name)
        if server:
            server.shutdown()
            server.server_close()
        for output in output_files:
            output.close()
        report["runtime_root"] = str(root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps({"status": report["status"], "evidence": str(args.output)}),
            flush=True,
        )
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
