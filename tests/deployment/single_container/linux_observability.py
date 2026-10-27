"""Opt-in real five-engine exercise inside an isolated candidate Linux image.

Run as 10001:10001 with --network none, a read-only diagnostics contract and a
new writable output child. No Docker socket, SSH, business config or NAS path
is used. Default mode prepares the synthetic fixture without starting engines.
"""

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import ssl
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from types import SimpleNamespace

from linux_observability_fixture import SOURCES, prepare_fixture

# isort: split

from acceptance import Evidence, require
from configs import ALERTS
from helpers import SNAPSHOT, emit, event
from linux_scenarios import NoRedirect, Scenarios
from policy import Policy, canonical, digest_bytes
from query import LokiClient
from reconcile import compare, read_logs

from deploy.single_container.process import (
    ManagedProcess,
    ProcessGroup,
    validate_process,
)


def read_five(roots, policy):
    records, invalid, partial = read_logs(
        {key: path for key, path in roots.items() if key != "knowledge"}, policy
    )
    # Original read_logs predates the dedicated Knowledge directory. Its actual
    # service remains memory-knowledge, as in Vector and the guard's native policy.
    knowledge, bad, tail = read_logs({"memory": roots["knowledge"]}, policy)
    require(
        all(row["service"] == "memory-knowledge" for row in knowledge),
        "knowledge_service_changed",
    )
    return records + knowledge, invalid + bad, partial + tail


def exact_lines(expected, rows):
    wanted = {row["event_id"]: canonical(row) for row in expected}
    seen = set()
    for _, line in rows:
        encoded = line.encode("utf-8")
        ident = json.loads(encoded)["event_id"]
        require(
            ident in wanted and encoded == wanted[ident],
            "retrieved_original_bytes_changed",
        )
        seen.add(ident)
    require(seen == set(wanted), "retrieved_event_loss")
    return hashlib.sha256(b"\n".join(wanted[key] for key in sorted(wanted))).hexdigest()


class EngineLifecycle:
    """Use the native supervisor's process ownership, with an explicit crash case."""

    def __init__(self, group, evidence):
        self.group, self.evidence = group, evidence
        self.crashes = []

    def stop(self, names):
        for name in names:
            item = self.group.processes[name]
            require(
                item.stop() is not False and not item.forced, "engine_stop_not_graceful"
            )

    def start(self, name):
        previous = self.group.processes[name]
        require(not previous.group_running(), "old_engine_group_still_running")
        current = ManagedProcess(previous.spec)
        self.group.processes[name] = current
        current.start()

    def recreate_vector(self):
        item = self.group.processes["obs-vector"]
        # Rotation has already stopped the collector gracefully. It still uses
        # the same persistent directory, without inventing another crash.
        if not item.group_running():
            require(
                item.state == "stopped" and not item.forced, "collector_not_stopped"
            )
            self.start("obs-vector")
            return
        pid = item.child.pid
        item.signal(signal.SIGKILL)
        code = item.child.wait(timeout=10)
        require(
            not item.group_running() and code == -signal.SIGKILL,
            "collector_crash_not_observed",
        )
        self.crashes.append({"pid": pid, "exit_code": code, "intentional": True})
        self.start("obs-vector")


class Suite(Scenarios):
    """Reuse the established real Grafana/query/recovery scenarios, without Docker."""

    def __init__(self, fixture, evidence, events_per_source):
        import uuid

        self.root = Path(fixture["root"])
        self.evidence = evidence
        self.current_case = "native_engine_startup"
        self.real_engines_executed = False
        self.progress = {}
        self.last_observation = {}
        self.binding = SimpleNamespace(
            logs={name: Path(path) for name, path in fixture["logs"].items()},
            query_url="https://127.0.0.1:8445",
            grafana_url="https://127.0.0.1:3000",
            ca=self.root / "tls/ca.pem",
            writer_token=self.root / "secrets/writer_token",
            grafana_password=self.root / "secrets/grafana_admin_password",
        )
        self.policy = Policy(SNAPSHOT)
        self.generated = []
        self.instances = {name: str(uuid.uuid4()) for name in SOURCES}
        self.sequences = dict.fromkeys(SOURCES, 0)
        self.events_per_source, self.batch_index = events_per_source, 0
        self.start = time.time_ns() - 60 * 10**9
        self.client = LokiClient(
            self.binding.query_url,
            self.binding.ca,
            (self.root / "secrets/query_token").read_text().strip(),
        )
        self.metric_client = LokiClient(
            self.binding.query_url,
            self.binding.ca,
            (self.root / "secrets/metrics_token").read_text().strip(),
        )
        self.opener = urllib.request.build_opener(
            NoRedirect(),
            urllib.request.ProxyHandler({}),
            urllib.request.HTTPSHandler(
                context=ssl.create_default_context(cafile=str(self.binding.ca))
            ),
        )
        self.group = ProcessGroup(
            [validate_process(spec) for spec in fixture["processes"]]
        )
        self.lifecycle = EngineLifecycle(self.group, evidence)
        self.receiver = self.receiver_output = None

    def checkpoint(self):
        temporary = self.root / "progress.json.tmp"
        temporary.write_text(
            json.dumps(self.progress, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(self.root / "progress.json")

    def phase(self, name):
        self.current_case = name
        self.progress = {"phase": name, "state": "running"}
        self.checkpoint()
        print(json.dumps({"observability_phase": name}), flush=True)

    def wait(self, function, seconds=180):
        """Keep the original deadline/retries, retaining the failed observation."""
        started = time.monotonic()
        self.last_observation = {}
        self.progress = {
            "phase": self.current_case,
            "probe": function.__name__,
            "state": "waiting",
            "timeout_seconds": seconds,
            "attempts": 0,
        }
        self.checkpoint()
        print(
            json.dumps(
                {"observability_wait": self.current_case, "probe": function.__name__}
            ),
            flush=True,
        )
        while time.monotonic() < started + seconds:
            self.progress["attempts"] += 1
            try:
                result = function()
                if result:
                    self.progress.update(
                        state="passed", elapsed_seconds=time.monotonic() - started
                    )
                    self.progress["last_observation"] = self.last_observation
                    self.checkpoint()
                    return result
            except (OSError, ValueError, RuntimeError) as error:
                self.progress["last_error_type"] = type(error).__name__
                self.progress.pop("last_error_code", None)
                # Arbitrary response/exception text may contain credentials.
                code = str(error)
                if isinstance(error, ValueError) and code.replace("_", "").isalnum():
                    self.progress["last_error_code"] = code
            self.progress["last_observation"] = self.last_observation
            self.progress["elapsed_seconds"] = time.monotonic() - started
            self.checkpoint()
            time.sleep(2)
        self.progress.update(
            state="deadline_exceeded", elapsed_seconds=time.monotonic() - started
        )
        self.checkpoint()
        raise ValueError("scenario_deadline_exceeded")

    def prom(self, expression):
        rows = super().prom(expression)
        self.last_observation["prometheus"] = {"expression": expression, "result": rows}
        return rows

    def api(self, path, method="GET", data=None, **kwargs):
        if path == "/api/v1/provisioning/contact-points" and data is not None:
            data = json.loads(json.dumps(data))
            require(
                data["settings"]["url"] == "http://obs-guard:18081/record",
                "unexpected_receiver",
            )
            data["settings"]["url"] = "http://127.0.0.1:18081/record"
        result = super().api(path, method, data, **kwargs)
        self.last_observation["grafana"] = {
            "path": path,
            "method": method,
            "status": result[0],
        }
        return result

    def dc(self, *args):
        # Only the original scenario's local recorder is launched, as this UID.
        require(
            args[:6] == ("exec", "-d", "obs-guard", "python", "-B", "-c")
            and len(args) == 7,
            "docker_or_unknown_command_refused",
        )
        source = (
            Path(__file__).resolve().parents[1] / "observability/linux_receiver.py"
        ).read_text()
        require(
            args[6] == source and self.receiver is None, "unexpected_receiver_source"
        )
        source = source.replace(
            'ROOT = Path("/var/lib/guard")',
            "ROOT = Path(" + repr(str(self.root / "data/guard")) + ")",
        )
        source = source.replace('("0.0.0.0", 18081)', '("127.0.0.1", 18081)')
        self.receiver_output = (self.root / "engine-logs/receiver.log").open("xb")
        self.receiver = subprocess.Popen(
            [sys.executable, "-B", "-c", source],
            stdout=self.receiver_output,
            stderr=self.receiver_output,
        )

    def batch(self):
        self.batch_index += 1
        for name, directory in self.binding.logs.items():
            records = []
            for _ in range(self.events_per_source):
                self.sequences[name] += 1
                record = event(
                    self.sequences[name],
                    "memory-knowledge" if name == "knowledge" else name,
                    self.instances[name],
                )
                self.policy.validate(record)
                records.append(record)
            emit(directory / "depi.jsonl", records)
            self.generated.extend(records)
        self.evidence.artifact(f"generated-{self.batch_index}.json", self.generated)

    def reconcile(self, case, *, artifact_name=None):
        self.phase(artifact_name or case)

        def check():
            rows = self.client.range('{stack="tianshu"}', self.start, time.time_ns())
            retrieved = [self.policy.line(line.encode() + b"\n") for _, line in rows]
            landed, invalid, partial = read_five(self.binding.logs, self.policy)
            result = compare(self.generated, landed, retrieved, invalid, partial)
            self.last_observation["reconciliation"] = result
            if result["status"] != "passed":
                return None
            digest = exact_lines(self.generated, rows)
            return {
                "reconciliation": result,
                "canonical_source_query_sha256": digest,
                "retrieved": rows,
                "five_sources": list(SOURCES),
            }

        result = self.wait(check)
        artifact = self.evidence.artifact((artifact_name or case) + ".json", result)
        previous = self.evidence.report["dimensions"][case].get("artifacts", [])
        self.evidence.record(case, "passed", artifacts=[*previous, artifact])

    def durable(self, name):
        count = len({row["event_id"] for row in self.generated})
        # A prior Prometheus scrape cannot prove that a restarted guard's real
        # monitor has completed a new cycle. Query its authenticated native ready.
        self.phase(name + "_ready")

        def ready():
            status = self.metric_client.request("/ready")[0]
            self.last_observation["guard_ready_status"] = status
            return status == 200

        self.wait(ready)
        self.phase(name + "_metrics")
        self.wait(
            lambda: (
                self.metrics_value("tianshu_obs_landed_events") == count
                and self.metrics_value("tianshu_obs_pending_events") == 0
                and self.metrics_value("tianshu_obs_sequence_gaps") == 0
            ),
            seconds=max(180, ((count + 63) // 64) * 30 + 90),
        )
        path = self.root / "data/guard/integrity.sqlite"
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            connection.execute("BEGIN")
            require(
                connection.execute("PRAGMA user_version").fetchone()[0] == 2,
                "ledger_v2_not_running",
            )
            rows = connection.execute("SELECT id,hash,verified FROM events").fetchall()
            expected = {row["event_id"]: digest_bytes(row) for row in self.generated}
            require(
                len(rows) == count
                and all(
                    expected[ident] == digest and verified > 0
                    for ident, digest, verified in rows
                ),
                "ledger_evidence_incomplete",
            )
            require(
                connection.execute("SELECT landed,pending FROM totals").fetchone()
                == (count, 0),
                "ledger_summary_mismatch",
            )
            artifact = self.evidence.artifact(
                name + ".json",
                {
                    "schema_version": 2,
                    "unique_events": count,
                    "pending": 0,
                    "every_sha256_matches": True,
                    "every_event_verified": True,
                    "files": connection.execute(
                        "SELECT name,offset FROM files ORDER BY name"
                    ).fetchall(),
                },
            )
        finally:
            connection.close()
        return artifact

    def execute(self):
        self.phase("native_engine_startup")
        self.group.start()

        def ready():
            self.group.tick()
            status = self.group.status()
            self.last_observation["native_group_status"] = status
            if status == "failed":
                raise AssertionError("native_engine_start_failed")
            return status == "ready"

        self.wait(ready)
        self.real_engines_executed = True
        owners = {}
        for name, item in self.group.processes.items():
            fields = Path(f"/proc/{item.child.pid}/status").read_text().splitlines()
            uid = next(line.split()[1:] for line in fields if line.startswith("Uid:"))
            gid = next(line.split()[1:] for line in fields if line.startswith("Gid:"))
            require(set(uid + gid) == {"10001"}, "engine_identity_mismatch")
            owners[name] = {"pid": item.child.pid, "uid": uid, "gid": gid}
            owners[name]["argv"] = item.spec["argv"]
            owners[name]["process_counters"] = {
                line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                for line in fields
                if line.startswith(("VmRSS:", "VmHWM:", "Threads:"))
            }
        artifact = self.evidence.artifact("actual-engine-identities.json", owners)
        self.evidence.record("identity_permissions", "passed", artifacts=[artifact])
        package = Path("/opt/tianshu/application/deploy/observability")
        self.evidence.artifact(
            "actual-image-observer-code.json",
            {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(package.glob("*.py"))
            },
        )
        self.phase("grafana_datasources_dashboard")
        self.ui_and_metrics()
        self.phase("fifteen_live_alert_rules")
        status, rules = self.api("/api/v1/provisioning/alert-rules")
        require(
            status == 200
            and len(rules) == len(ALERTS) == 15
            and {row["title"] for row in rules} == set(ALERTS),
            "fifteen_alerts_not_loaded",
        )
        for rule in rules:
            expr = next(
                row["model"]["expr"] for row in rule["data"] if row["refId"] == "A"
            )
            require(
                expr == ALERTS[rule["title"]][0], "original_alert_expression_changed"
            )
        self.evidence.artifact("fifteen-live-alert-rules.json", rules)
        self.phase("alert_receiver_setup")
        self.alert_receiver()
        self.phase("query_permissions")
        self.query_permissions()
        self.recovery()
        self.durable("ledger-before-restart")
        self.lifecycle.stop(["obs-guard"])
        self.lifecycle.start("obs-guard")
        self.durable("ledger-after-restart")
        for name, directory in self.binding.logs.items():
            record = next(
                row
                for row in self.generated
                if row["instance_id"] == self.instances[name]
            )
            emit(directory / "replayed.jsonl", [record])
        self.reconcile("collector_recreate_rotation", artifact_name="source-replay")
        self.durable("ledger-after-source-replay")
        self.phase("source_replay_duplicate_metrics")
        self.wait(
            lambda: (
                self.metrics_value("tianshu_obs_source_duplicates_total")
                >= len(SOURCES)
            )
        )
        self.phase("sensitive_canary")
        self.canary()
        self.phase("local_alert_delivery")
        self.verify_alert()
        self.evidence.artifact(
            "intentional-collector-crash.json", self.lifecycle.crashes
        )
        self.phase("scenarios_completed")

    def close(self):
        clean = self.group.stop()
        if self.receiver is not None:
            self.receiver.terminate()
            try:
                self.receiver.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.receiver.kill()
                self.receiver.wait(timeout=5)
                clean = False
        if self.receiver_output is not None:
            self.receiver_output.close()
        self.client.close()
        self.metric_client.close()
        return clean and not any(item.forced for item in self.group.processes.values())


def execution_boundary():
    require(
        sys.platform == "linux" and os.geteuid() == 10001 and os.getegid() == 10001,
        "linux_uid_10001_required",
    )
    interfaces = {
        line.split(":", 1)[0].strip()
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]
    }
    require(interfaces == {"lo"}, "network_none_required")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    parser.add_argument("--events-per-source", type=int, default=8)
    parser.add_argument("--execute-synthetic", action="store_true")
    args = parser.parse_args(argv)
    require(1 <= args.events_per_source <= 100, "bounded_synthetic_count_required")
    if args.execute_synthetic:
        execution_boundary()
    fixture = prepare_fixture(args.output, args.contract)
    report = {
        "scope": "isolated_candidate_five_native_observers",
        "image_id_supplied_by_launcher": args.image_id,
        "status": "prepared_not_run",
        "real_engines_executed": False,
        "completed_all_scenarios": False,
        "production_30_day_retention_executed": False,
        "nas_business_verified": False,
        "physical_enospc": False,
        "source_reclamation_authorized": False,
    }
    suite = evidence = None
    code = 0
    previous_signal = None
    if args.execute_synthetic:

        def interrupted(*_):
            raise InterruptedError("synthetic_run_interrupted")

        previous_signal = signal.signal(signal.SIGTERM, interrupted)
    try:
        if args.execute_synthetic:
            evidence = Evidence(args.output / "evidence")
            suite = Suite(fixture, evidence, args.events_per_source)
            suite.execute()
            report.update(status="passed", completed_all_scenarios=True)
    except BaseException as error:  # noqa: BLE001 - interrupts must still finalize synthetic evidence
        report.update(status="failed", error_type=type(error).__name__)
        if isinstance(error, ValueError) and str(error).replace("_", "").isalnum():
            report["error_code"] = str(error)
        code = 1
    finally:
        try:
            if suite is not None:
                report["real_engines_executed"] = (
                    getattr(suite, "real_engines_executed", False) is True
                )
                if isinstance(getattr(suite, "progress", None), dict):
                    report["phase_diagnostics"] = suite.progress
                report["native_processes_stopped"] = suite.close()
                if not report["native_processes_stopped"]:
                    report["status"], code = "failed", 1
        except BaseException as error:  # noqa: BLE001 - record cleanup failure before writing the verdict
            report["cleanup_error_type"] = type(error).__name__
            report["status"], code = "failed", 1
        finally:
            if previous_signal is not None:
                signal.signal(signal.SIGTERM, previous_signal)
            if evidence is not None:
                evidence.finish()
            (args.output / "linux-observability.json").write_text(
                json.dumps(report, indent=2) + "\n", encoding="utf-8"
            )
    print(json.dumps(report), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
