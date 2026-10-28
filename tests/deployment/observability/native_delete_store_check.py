#!/usr/bin/env python3
"""Exercise native Loki Bolt -> SQLite+Bolt backup -> Bolt on synthetic state.

Run inside the installed R14 Linux image with the existing verification root
mounted read-only. This starts only Loki, uses loopback mutual TLS, and never
loads a Platform/Memory application or production configuration. Request-store
migration/dual writes are checked through the real HTTP API and normal restarts.
Physical chunk reclamation and processed-filter retirement are not claimed.
"""

import argparse
import copy
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
from urllib.parse import urlencode

sys.dont_write_bytecode = True


def require(condition, code):
    if not condition:
        raise RuntimeError(code)


def write_json(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")


class Budget:
    def __init__(self, seconds):
        self.started = time.monotonic()
        self.end = self.started + seconds

    def remaining(self):
        left = self.end - time.monotonic()
        require(left > 0, "native_loki_check_timeout")
        return left


class NativeLoki:
    def __init__(
        self, binary, configuration, root, name, client_type, deadline_type, budget
    ):
        self.root, self.name, self.budget = root, name, budget
        self.client, self.process, self.log = None, None, None
        self.deadline_type = deadline_type
        self.stop_evidence = None
        self.path = root / (name + ".json")
        write_json(self.path, configuration)
        self.log = (root / (name + ".private.log")).open("xb")
        try:
            self.process = subprocess.Popen(
                [str(binary), "-config.file=" + str(self.path)],
                cwd=root,
                stdin=subprocess.DEVNULL,
                stdout=self.log,
                stderr=subprocess.STDOUT,
            )
            self.client = client_type(
                "https://127.0.0.1:" + str(configuration["server"]["http_listen_port"]),
                root / "tls/ca.pem",
                certificate=root / "tls/client.pem",
                key=root / "tls/client.key",
            )
            ready_end = min(budget.end, time.monotonic() + 32)
            while time.monotonic() < ready_end:
                require(self.process.poll() is None, name + "_exited_before_ready")
                try:
                    if self.request("/ready")[0] == 200:
                        return
                except Exception:
                    pass
                time.sleep(0.2)
            raise RuntimeError(name + "_not_ready")
        except Exception:
            self.stop()
            raise

    def deadline(self):
        return self.deadline_type(seconds=min(4, self.budget.remaining()))

    def request(self, path, method="GET", body=None, content_type=None):
        require(self.process.poll() is None, self.name + "_unexpected_exit")
        return self.client.request(
            path, method, body, content_type, deadline=self.deadline()
        )

    def query(self, selector, start_ns, end_ns):
        return self.client.range(
            selector,
            start_ns,
            end_ns,
            limit=100,
            max_requests=4,
            deadline=self.deadline(),
        )

    def stop(self):
        if self.stop_evidence is not None:
            return self.stop_evidence
        graceful, result = True, None
        try:
            if self.client is not None:
                self.client.close()
            if self.process is not None:
                if self.process.poll() is None:
                    self.process.terminate()
                try:
                    result = self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    graceful = False
                    self.process.kill()
                    result = self.process.wait(timeout=5)
        finally:
            if self.log is not None:
                self.log.close()
            self.stop_evidence = {
                "phase": self.name,
                "returncode": result,
                "normal_termination": graceful and result == 0,
                "client_closed": self.client is None or self.client.closed.is_set(),
                "log_closed": self.log is None or self.log.closed,
            }
        require(graceful and result == 0, self.name + "_normal_shutdown_failed")
        return self.stop_evidence


def inventory(native):
    status, raw, _ = native.request("/loki/api/v1/delete")
    require(status == 200, "delete_inventory_http_failed")
    rows = json.loads(raw)
    require(isinstance(rows, list), "delete_inventory_list_required")
    result = {}
    for row in rows:
        require(
            isinstance(row, dict)
            and isinstance(row.get("request_id"), str)
            and row["request_id"]
            and row["request_id"] not in result
            and isinstance(row.get("query"), str),
            "invalid_delete_request_identity",
        )
        for field in ("start_time", "end_time"):
            require(
                isinstance(row.get(field), (int, float))
                and not isinstance(row[field], bool),
                "invalid_delete_request_time",
            )
        result[row["request_id"]] = {
            key: row[key] for key in ("request_id", "query", "start_time", "end_time")
        }
    return result


def submit(native, spec, before):
    status, _, _ = native.request(
        "/loki/api/v1/delete?"
        + urlencode(
            {"query": spec["query"], "start": spec["start"], "end": spec["end"]}
        ),
        "POST",
    )
    require(status == 204, "delete_request_post_failed")
    after = inventory(native)
    require(set(after) >= set(before), "post_lost_existing_delete_request")
    require(
        all(after[key] == value for key, value in before.items()),
        "post_changed_old_request",
    )
    added = set(after) - set(before)
    require(len(added) == 1, "one_new_delete_request_required")
    row = after[added.pop()]
    require(
        row["query"] == spec["query"]
        and row["start_time"] == spec["start"]
        and row["end_time"] == spec["end"],
        "posted_delete_fields_not_preserved",
    )
    return after, row["request_id"]


def sqlite_evidence(state):
    databases = []
    for path in state.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            with path.open("rb") as stream:
                header = stream.read(16)
        except FileNotFoundError:
            continue
        if header != b"SQLite format 3\x00":
            continue
        with closing(
            sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=3)
        ) as db:
            db.execute("PRAGMA query_only=ON")
            require(
                db.execute("PRAGMA quick_check").fetchall() == [("ok",)],
                "sqlite_store_corrupt",
            )
            tables = [
                row[0]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            ]
            require(bool(tables), "sqlite_delete_store_has_no_tables")
        databases.append(
            {
                "path": path.relative_to(state).as_posix(),
                "quick_check": "ok",
                "tables": tables,
            }
        )
    require(bool(databases), "actual_sqlite_delete_database_missing")
    return databases


def environment(options):
    # Import the installed application first. helpers/run_native also import
    # legacy bare modules from their fixture root; those must not select our
    # actual R14 configuration producer or TLS client.
    configs = importlib.import_module("deploy.observability.configs")
    client_type = importlib.import_module("deploy.observability.query").LokiClient
    deadline_type = importlib.import_module("deploy.observability.transport").Deadline
    installed = Path(configs.__file__).resolve()
    require(
        installed.is_relative_to(Path("/opt/tianshu/application")),
        "installed_configs_required",
    )
    helper_path = options.fixture_root / "tests/deployment/observability"
    require(
        (helper_path / "helpers.py").is_file(), "existing_observability_helpers_missing"
    )
    require(
        (helper_path / "run_native.py").is_file(), "existing_run_native_helpers_missing"
    )
    sys.path.insert(0, str(helper_path))
    from helpers import certificates, event
    from run_native import port, substitute

    return (
        configs,
        client_type,
        deadline_type,
        installed,
        certificates,
        event,
        port,
        substitute,
    )


def exercise(options):
    require(os.name == "posix", "linux_native_image_required")
    budget = Budget(options.timeout_seconds)
    options.output.mkdir(parents=True, exist_ok=False)
    root = Path(
        tempfile.mkdtemp(prefix="loki-delete-store-", dir=options.output)
    ).resolve()
    report = {
        "kind": "r14-native-loki-delete-store-roundtrip",
        "status": "failed",
        "done": False,
        "fixture": str(root),
        "boundary": {
            "synthetic_only": True,
            "loopback_mutual_tls": True,
            "production_accessed": False,
            "application_assembly_started": False,
            "loki_native_processes_only": True,
            "physical_chunk_reclamation_verified": False,
            "processed_filter_retirement_verified": False,
        },
        "stops": [],
        "phases": [],
    }
    active = None
    try:
        (
            configs,
            client_type,
            deadline_type,
            installed,
            certificates,
            event,
            port,
            substitute,
        ) = environment(options)
        report.update(
            installed_configs=str(installed),
            installed_configs_sha256=hashlib.sha256(installed.read_bytes()).hexdigest(),
        )
        native_version = subprocess.run(
            [str(options.loki), "--version"],
            capture_output=True,
            text=True,
            check=True,
            timeout=min(5, budget.remaining()),
        )
        report["loki_version"] = (native_version.stdout + native_version.stderr).strip()
        certificates(root / "tls")
        state = root / "loki-state"
        state.mkdir()
        generated = configs.loki()
        require(
            generated["compactor"]["delete_request_store_db_type"] == "sqlite"
            and generated["compactor"]["backup_delete_request_store_db_type"]
            == "boltdb"
            and generated["compactor"]["delete_request_cancel_period"] == "1m"
            and generated["limits_config"]["split_queries_by_interval"] == "24h",
            "r14_generated_delete_configuration_required",
        )
        generated = substitute(
            generated, {"/var/lib/loki": state, "/run/tls": root / "tls"}
        )

        def configuration(old):
            cfg = copy.deepcopy(generated)
            http, grpc = port(), port()
            while grpc == http:
                grpc = port()
            cfg["server"].update(
                http_listen_port=http,
                grpc_listen_port=grpc,
                http_listen_address="127.0.0.1",
            )
            if old:
                cfg["compactor"].update(
                    delete_request_store_db_type="boltdb",
                    delete_request_cancel_period="24h",
                )
                cfg["compactor"].pop("backup_delete_request_store_db_type", None)
                cfg["limits_config"]["split_queries_by_interval"] = "1h"
            return cfg

        base_second = int(time.time()) - 30
        services = ["platform", "platform", "memory", "companion"]
        watermarks = [3, 7, 2, 4]
        instances = [
            "00000000-0000-4000-8000-" + f"{number:012d}" for number in (1, 2, 1, 3)
        ]
        specs, streams, expected_ids, retained_ids = [], {}, set(), set()
        for index, (service, instance, through) in enumerate(
            zip(services, instances, watermarks)
        ):
            query = (
                '{stack="tianshu",service="tianshu_' + service + '"} | json '
                '| instance_id="' + instance + '" | sequence<=' + str(through)
            )
            specs.append(
                {
                    "query": query,
                    "start": base_second - 5 - index,
                    "end": base_second + 5 + index,
                }
            )
            for offset, sequence in enumerate((1, through + 1)):
                timestamp = base_second * 10**9 + index * 10**6 + offset * 1000
                row = event(sequence, service, instance)
                row["timestamp"] = datetime.fromtimestamp(
                    timestamp / 10**9, timezone.utc
                ).isoformat()
                streams.setdefault(service, []).append(
                    [str(timestamp), json.dumps(row, separators=(",", ":"))]
                )
                expected_ids.add(row["event_id"])
                if offset:
                    retained_ids.add(row["event_id"])
        payload = {
            "streams": [
                {
                    "stream": {"stack": "tianshu", "service": "tianshu_" + service},
                    "values": sorted(values),
                }
                for service, values in streams.items()
            ]
        }
        start_ns, end_ns = (base_second - 10) * 10**9, (base_second + 10) * 10**9

        def retained(native):
            ids = set()
            for service, instance, through in zip(services, instances, watermarks):
                selector = (
                    '{stack="tianshu",service="tianshu_' + service + '"} | json '
                    '| instance_id="' + instance + '" | sequence>' + str(through)
                )
                ids.update(
                    json.loads(line)["event_id"]
                    for _, line in native.query(selector, start_ns, end_ns)
                )
            require(ids == retained_ids, "unmatched_synthetic_logs_not_retrievable")
            return len(ids)

        active = NativeLoki(
            options.loki,
            configuration(True),
            root,
            "01-bolt",
            client_type,
            deadline_type,
            budget,
        )
        require(inventory(active) == {}, "fixture_delete_store_not_empty")
        status, _, _ = active.request(
            "/loki/api/v1/push",
            "POST",
            json.dumps(payload).encode(),
            "application/json",
        )
        require(status == 204, "synthetic_log_push_failed")
        ids = {
            json.loads(line)["event_id"]
            for _, line in active.query('{stack="tianshu"}', start_ns, end_ns)
        }
        require(ids == expected_ids, "initial_synthetic_log_query_incomplete")
        expected = {}
        first_id = None
        for spec in specs:
            expected, request_id = submit(active, spec, expected)
            if first_id is None:
                first_id = request_id
        write_json(root / "01-bolt-requests.json", expected)
        report["phases"].append(
            {
                "name": "old_bolt_primary",
                "requests": len(expected),
                "logs": len(ids),
                "cancel_period": "24h",
                "split_interval": "1h",
            }
        )
        report["stops"].append(active.stop())
        active = None

        active = NativeLoki(
            options.loki,
            configuration(False),
            root,
            "02-sqlite-bolt-backup",
            client_type,
            deadline_type,
            budget,
        )
        require(
            inventory(active) == expected,
            "bolt_to_sqlite_request_migration_changed_core_fields",
        )
        report["sqlite_databases"] = sqlite_evidence(state)
        migrated = copy.deepcopy(expected)
        extra = {
            "query": '{stack="tianshu",service="tianshu_gateway"} | json | instance_id="00000000-0000-4000-8000-000000000004" | sequence<=9',
            "start": base_second - 9,
            "end": base_second + 9,
        }
        expected, added_id = submit(active, extra, expected)
        status, _, _ = active.request(
            "/loki/api/v1/delete?" + urlencode({"request_id": first_id}), "DELETE"
        )
        require(status == 204, "sqlite_primary_delete_cancel_failed")
        expected.pop(first_id)
        require(inventory(active) == expected, "sqlite_primary_cancel_not_visible")
        write_json(root / "02-sqlite-requests.json", expected)
        report["phases"].append(
            {
                "name": "sqlite_primary_bolt_backup",
                "migrated_requests": len(migrated),
                "posted_id": added_id,
                "cancelled_id": first_id,
                "requests": len(expected),
                "retained_logs": retained(active),
            }
        )
        report["stops"].append(active.stop())
        active = None

        active = NativeLoki(
            options.loki,
            configuration(True),
            root,
            "03-bolt-rollback",
            client_type,
            deadline_type,
            budget,
        )
        require(
            inventory(active) == expected, "bolt_backup_post_or_cancel_not_synchronized"
        )
        write_json(root / "03-bolt-requests.json", inventory(active))
        report["phases"].append(
            {
                "name": "rollback_bolt_primary",
                "requests": len(expected),
                "retained_logs": retained(active),
                "request_fields_preserved": True,
                "post_and_cancel_synchronized": True,
            }
        )
        report["stops"].append(active.stop())
        active = None
        budget.remaining()
        report.update(status="passed", done=True)
    except Exception as error:
        report.update(
            error_code=str(error)
            if isinstance(error, RuntimeError)
            else "native_loki_check_failed",
            error_type=type(error).__name__,
        )
    finally:
        if active is not None:
            try:
                report["stops"].append(active.stop())
            except Exception:
                report.update(
                    status="failed", done=False, cleanup_error="normal_shutdown_failed"
                )
        report["elapsed_seconds"] = round(time.monotonic() - budget.started, 3)
        report["processed_filter_retirement"] = {
            "status": "not_exercised",
            "reason": "The roundtrip preserves native compactor timing; no processed state or physical deletion is inferred from pending requests.",
        }
        write_json(options.output / "result.json", report)
        print(json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "passed" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--loki", type=Path, default=Path("/usr/local/bin/loki"))
    parser.add_argument("--fixture-root", type=Path, default=Path("/verification/root"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=120)
    options = parser.parse_args()
    require(
        60 <= options.timeout_seconds <= 120, "bounded_native_check_timeout_required"
    )
    raise SystemExit(exercise(options))


if __name__ == "__main__":
    main()
