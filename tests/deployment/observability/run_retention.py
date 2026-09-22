"""Real Loki TTL probe: 24h minimum policy, 25h-old synthetic fixture, actual chunk deletion."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import time

from helpers import certificates, event
from configs import loki
from policy import canonical
from query import LokiClient
from run_native import port, substitute


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--loki", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--timeout-seconds", type=int, default=240)
    args = p.parse_args()
    if not 30 <= args.timeout_seconds <= 3600:
        raise SystemExit("bounded_timeout_required")
    root = Path(
        tempfile.mkdtemp(prefix="retention-", dir=Path(__file__).parent / ".runtime")
    ).resolve()
    certificates(root / "tls")
    (root / "loki").mkdir()
    cfg = substitute(
        loki(retention_hours=48),
        {"/var/lib/loki": root / "loki", "/run/tls": root / "tls"},
    )
    http_port = port()
    cfg["server"].update(
        http_listen_port=http_port,
        grpc_listen_port=port(),
        http_listen_address="127.0.0.1",
    )
    cfg["ingester"].update(chunk_idle_period="5s", max_chunk_age="1m")
    # Use the SAME store-only path and range before and after the policy change.
    # Initial visibility must come from the store, never an unflushed ingester.
    cfg["querier"] = {"query_store_only": True, "query_ingesters_within": "3h"}
    cfg["compactor"].update(
        compaction_interval="5s",
        retention_delete_delay="1s",
        apply_retention_interval="5s",
    )
    # Bound cache/index lag in this accelerated probe; production keeps its 2h
    # deletion delay. A cached log line must not be mistaken for an on-disk chunk.
    cfg["storage_config"]["tsdb_shipper"]["resync_interval"] = "5s"
    cfg["chunk_store_config"] = {
        "chunk_cache_config": {"embedded_cache": {"enabled": False}}
    }
    cfg["query_range"] = {"cache_results": False}
    config = root / "loki.json"
    config.write_text(json.dumps(cfg))
    report = {
        "kind": "dep-b-retention",
        "status": "failed",
        "retention_hours": 24,
        "fixture_age_hours": 25,
        "query_path_before_and_after": "store_only",
        "production_default_query_path_verified": False,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "timeline": [],
        "claims": {
            "elapsed_24_hours": False,
            "nas": False,
            "linux_compose": False,
            "actual_chunk_deletion": False,
        },
    }
    (root / "config-before.json").write_bytes(config.read_bytes())
    output = (root / "loki.stderr").open("wb")
    process = subprocess.Popen(
        [str(args.loki.resolve()), "-config.file=" + str(config)],
        cwd=root,
        stdout=output,
        stderr=output,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    started = time.monotonic()

    def observe(state, **facts):
        entry = {
            "state": state,
            "utc": datetime.now(timezone.utc).isoformat(),
            "elapsed_seconds": round(time.monotonic() - started, 2),
            **facts,
        }
        report["timeline"].append(entry)
        print(json.dumps(entry), flush=True)

    try:
        client = LokiClient(
            f"https://127.0.0.1:{http_port}",
            root / "tls/ca.pem",
            certificate=root / "tls/client.pem",
            key=root / "tls/client.key",
        )
        deadline = time.monotonic() + args.timeout_seconds
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("loki_exited")
            try:
                if client.request("/ready")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        timestamp = time.time_ns() - 25 * 3600 * 10**9
        payload = {
            "streams": [
                {
                    "stream": {"stack": "tianshu", "service": "retention_fixture"},
                    "values": [[str(timestamp), canonical(event()).decode()]],
                }
            ]
        }
        (root / "fixture-push.json").write_text(json.dumps(payload) + "\n")
        report["query"] = {
            "selector": '{stack="tianshu",service="retention_fixture"}',
            "start_ns": timestamp - 10**9,
            "end_ns": timestamp + 10**9,
        }
        status, _, _ = client.request(
            "/loki/api/v1/push",
            "POST",
            json.dumps(payload).encode(),
            "application/json",
        )
        if status != 204:
            raise RuntimeError("fixture_push_not_accepted")
        selector = '{stack="tianshu",service="retention_fixture"}'
        while time.monotonic() < deadline:
            if len(client.range(selector, timestamp - 10**9, timestamp + 10**9)) == 1:
                break
            observe("waiting_initial_visibility")
            time.sleep(5)
        else:
            raise RuntimeError("fixture_not_initially_retrievable")
        report["initially_retrievable"] = True
        observe("initially_retrievable", rows=1)
        client.request("/flush", "POST", b"")
        chunk_root = root / "loki/chunks/tianshu"
        initial = set()
        while time.monotonic() < deadline:
            if chunk_root.exists():
                initial |= {
                    str(path.relative_to(chunk_root))
                    for path in chunk_root.rglob("*")
                    if path.is_file()
                }
            if initial:
                break
            time.sleep(0.5)
        if not initial:
            raise RuntimeError("flushed_chunk_not_observed")
        report["initial_chunk_count"] = len(initial)
        report["initial_chunk_paths"] = sorted(initial)
        observe("flushed_chunk_observed", chunks=len(initial))
        # A real policy reduction makes an initially readable, flushed fixture expire.
        process.terminate()
        process.wait(timeout=10)
        cfg["limits_config"]["retention_period"] = "24h"
        config.write_text(json.dumps(cfg))
        (root / "config-after.json").write_bytes(config.read_bytes())
        process = subprocess.Popen(
            [str(args.loki.resolve()), "-config.file=" + str(config)],
            cwd=root,
            stdout=output,
            stderr=output,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        report["previous_retention_hours"] = 48
        observe("policy_reduced", retention_hours=24)
        while time.monotonic() < deadline:
            try:
                if client.request("/ready")[0] == 200:
                    break
            except Exception:
                pass
            time.sleep(1)
        while time.monotonic() < deadline:
            remaining = {
                str(path.relative_to(chunk_root))
                for path in chunk_root.rglob("*")
                if path.is_file()
            }
            rows = client.range(selector, timestamp - 10**9, timestamp + 10**9)
            report["remaining_initial_chunks"] = len(remaining & initial)
            report["query_visible"] = bool(rows)
            report["claims"]["actual_chunk_deletion"] = not bool(remaining & initial)
            report["deleted_chunk_count"] = len(initial - remaining)
            observe(
                "observing",
                query_visible=bool(rows),
                remaining_initial_chunks=len(remaining & initial),
            )
            if (
                not rows
                and not (remaining & initial)
                and client.request("/ready")[0] == 200
            ):
                report["status"] = "passed"
                break
            time.sleep(5)
        if report["status"] != "passed":
            raise RuntimeError("retention_not_observed_within_budget")
    except Exception as exc:
        report["error_code"] = (
            str(exc) if type(exc) is RuntimeError else "retention_unverified"
        )
    finally:
        process.terminate()
        process.wait(timeout=10)
        output.close()
        report["duration_seconds"] = round(time.monotonic() - started, 2)
        report["finished_at"] = datetime.now(timezone.utc).isoformat()
        report["artifacts"] = {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("config-before.json", "config-after.json", "fixture-push.json")
            if (root / name).is_file()
        }
        report["runtime_root"] = str(root)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps({"status": report["status"]}), flush=True)
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
