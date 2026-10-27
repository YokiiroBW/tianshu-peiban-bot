"""TS363: actual native five-engine retention, using only the owned fixture.

The 30-day configuration is retained. One stopped synthetic ledger's first-seen
clock is moved back to exercise age expiry without claiming a 30-day soak.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlencode

from acceptance import require
from configs import ALERTS
from helpers import emit, event
from policy import digest_bytes
from query import LokiClient
from retained_query import window_filter

MAXIMUM = 10000


def prepare(fixture):
    path = Path(fixture["root"]) / "native/guard.json"
    config = json.loads(path.read_bytes())
    require(
        config["log_max_events"] == config["ledger_max_events"] == MAXIMUM,
        "native_ten_thousand_policy_required",
    )
    require(config["retention_days"] == 30, "native_thirty_days_required")
    # Only pacing is accelerated; batch/ACK/TLS/retention/15 rules stay native.
    config["interval_seconds"] = 0.5
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def native_name(source, instance, index):
    if source == "platform":
        return f"platform-{instance}-{index:04d}.jsonl"
    if source == "gateway":
        return f"gateway-{instance}-{index:06d}.jsonl"
    if source in {"memory", "knowledge"}:
        return f"{instance}.{index:06d}.jsonl"
    return "companion.jsonl" + (f".{index}" if index else "")


def database(suite):
    path = suite.root / "data/guard/integrity.sqlite"
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)


def snapshot(suite):
    with closing(database(suite)) as db:
        db.execute("BEGIN")
        require(
            db.execute("PRAGMA user_version").fetchone()[0] == 3, "ledger_v3_required"
        )
        return {
            "counts": db.execute("SELECT landed,pending FROM totals").fetchone(),
            "events": db.execute(
                "SELECT id,hex(hash),verified,service,instance,sequence,first_seen "
                "FROM events ORDER BY first_seen,service,instance,sequence"
            ).fetchall(),
            "retired": db.execute(
                "SELECT * FROM retired_streams ORDER BY 1,2"
            ).fetchall(),
            "deletes": db.execute(
                "SELECT * FROM retention_deletes ORDER BY 1,2"
            ).fetchall(),
            "counters": dict(db.execute("SELECT name,value FROM counters")),
            "clear_before": db.execute(
                "SELECT clear_before FROM retention_control WHERE singleton=1"
            ).fetchone()[0],
            "files": db.execute(
                "SELECT name,offset FROM files ORDER BY name"
            ).fetchall(),
        }


def metrics(suite):
    status, raw, _ = suite.metric_client.request("/metrics")
    require(status == 200, "guard_metrics_unavailable")
    return {
        name: float(value)
        for name, value in (line.split() for line in raw.decode().splitlines())
    }


def stable_window(suite, *, count=None, includes=()):
    state = snapshot(suite)
    values = metrics(suite)
    suite.last_observation["ledger"] = {
        "counts": state["counts"],
        "current_rejected": values.get("tianshu_obs_ledger_capacity_rejected_current"),
        "monitor_success": values.get("tianshu_obs_monitor_success"),
        "retired_streams": len(state["retired"]),
    }
    ids = {row[0] for row in state["events"]}
    if (
        state["counts"][1]
        or not set(includes) <= ids
        or count is not None
        and state["counts"][0] != count
        or values.get("tianshu_obs_monitor_success") != 1
        or values.get("tianshu_obs_retained_events") != state["counts"][0]
    ):
        return None
    require(state["counts"][0] <= MAXIMUM, "logical_capacity_exceeded")
    require(values["tianshu_obs_sequence_gaps"] == 0, "retirement_invented_gap")
    require(
        values["tianshu_obs_ledger_capacity_rejected_current"] == 0,
        "retirement_still_rejects_current_traffic",
    )
    expected = {
        row["event_id"]: digest_bytes(row).hex().upper() for row in suite.generated
    }
    require(
        all(
            expected[ident] == digest and verified > 0
            for ident, digest, verified, *_ in state["events"]
        ),
        "retained_hash_or_ack_changed",
    )
    return state


def retained_query(suite, state, client=None):
    from linux_observability import exact_lines

    client = suite.client if client is None else client
    wanted = {row[0] for row in state["events"]}
    rows = client.range('{stack="tianshu"}', suite.start, time.time_ns(), limit=5000)
    digest = exact_lines(
        [row for row in suite.generated if row["event_id"] in wanted], rows
    )
    status, raw, _ = client.request(
        "/loki/api/v1/query?"
        + urlencode(
            {
                "query": 'sum(count_over_time({stack="tianshu"}[1h]))',
                "time": time.time_ns(),
            }
        )
    )
    require(status == 200, "retained_aggregate_query_failed")
    result = json.loads(raw)["data"]["result"]
    count = sum(float(row["value"][1]) for row in result)
    require(count == len(wanted), "aggregate_does_not_use_retained_window")
    return {
        "unique_events": len(wanted),
        "range_rows": len(rows),
        "aggregate": count,
        "canonical_source_query_sha256": digest,
    }


def resources(suite):
    owners = {}
    for name, item in suite.group.processes.items():
        stat = (
            Path(f"/proc/{item.child.pid}/stat").read_text().rsplit(")", 1)[1].split()
        )
        values = Path(f"/proc/{item.child.pid}/status").read_text().splitlines()
        owners[name] = {
            "cpu_ticks": int(stat[11]) + int(stat[12]),
            **{
                line.split(":", 1)[0]: line.split(":", 1)[1].strip()
                for line in values
                if line.startswith(("VmRSS:", "VmHWM:", "Threads:"))
            },
        }
    return {"clock_ticks_per_second": os.sysconf("SC_CLK_TCK"), "owners": owners}


def generate(suite, source, count, index):
    records = []
    for _ in range(count):
        suite.sequences[source] += 1
        record = event(
            suite.sequences[source],
            "memory-knowledge" if source == "knowledge" else source,
            suite.instances[source],
        )
        suite.policy.validate(record)
        records.append(record)
    path = suite.binding.logs[source] / native_name(
        source, suite.instances[source], index
    )
    emit(path, records)
    suite.generated.extend(records)
    return records


def execute(suite):
    suite.start_engines()
    suite.phase("retention_original_ui_metrics_rules")
    suite.ui_and_metrics()
    status, rules = suite.api("/api/v1/provisioning/alert-rules")
    require(
        status == 200
        and len(rules) == 15
        and {row["title"] for row in rules} == set(ALERTS),
        "fifteen_rules_not_loaded",
    )
    require(
        all(
            next(row["model"]["expr"] for row in rule["data"] if row["refId"] == "A")
            == ALERTS[rule["title"]][0]
            for rule in rules
        ),
        "retention_rule_expression_not_loaded",
    )
    suite.evidence.artifact("retention-original-fifteen-rules.json", rules)
    for source in suite.binding.logs:
        generate(suite, source, 2000, 0)
    suite.phase("retention_ten_thousand_native_ack")
    first = suite.wait(lambda: stable_window(suite, count=MAXIMUM))
    suite.evidence.artifact("retention-initial-ledger.json", first)
    expression = window_filter(
        [
            (ident, service, instance, sequence, False)
            for ident, _, _, service, instance, sequence, _ in first["events"]
        ]
    )
    require(len(expression.encode()) < 2048, "ten_thousand_filter_not_compact")
    suite.evidence.artifact(
        "retention-window-expression.json",
        {
            "expression": expression,
            "utf8_bytes": len(expression.encode()),
            "logical_events": MAXIMUM,
            "streams": len(suite.binding.logs),
        },
    )
    suite.phase("retention_ten_thousand_query_cost")
    before = resources(suite)
    costs = []
    backend = LokiClient(
        "https://127.0.0.1:3100",
        suite.binding.ca,
        certificate=suite.root / "tls/client.pem",
        key=suite.root / "tls/client.key",
    )
    try:
        for iteration in range(3):
            pair = [("guarded", suite.client), ("raw", backend)]
            if iteration % 2:
                pair.reverse()
            result = {"order": [name for name, _ in pair]}
            for name, client in pair:
                started = time.monotonic()
                result[name] = {
                    "query": retained_query(suite, first, client),
                    "range_and_aggregate_seconds": time.monotonic() - started,
                }
            costs.append(result)
        suite.evidence.artifact(
            "retention-ten-thousand-query-cost.json",
            {
                "rounds": costs,
                "before": before,
                "after": resources(suite),
                "same_round_scope": "range plus count aggregate; same initial 10000 events",
                "owner_process_counters_include_background_work": True,
            },
        )
        # Native higher segment proves rotation. No mtime/EOF-only deletion.
        suite.phase("retention_confirmed_closed_raw")
        originals = []
        for source, directory in suite.binding.logs.items():
            path = directory / native_name(source, suite.instances[source], 0)
            originals.append(path)
            os.utime(path, (time.time() - 31 * 86400,) * 2)
            (directory / native_name(source, suite.instances[source], 1)).touch(
                exist_ok=False
            )
        suite.wait(lambda: all(not path.exists() for path in originals))
        retained_query(suite, first)
        suite.evidence.artifact(
            "retention-confirmed-raw-deletion.json",
            {
                "closed_files_deleted": [str(path) for path in originals],
                "same_ten_thousand_still_queryable": True,
            },
        )

        suite.phase("retention_global_count_oldest_prefix")
        added = [
            row
            for source in suite.binding.logs
            for row in generate(suite, source, 200, 1)
        ]
        final = suite.wait(
            lambda: stable_window(suite, includes=[row["event_id"] for row in added])
        )
        old_ids = [row[0] for row in first["events"]]
        final_ids = {row[0] for row in final["events"]}
        removed = [ident for ident in old_ids if ident not in final_ids]
        require(1000 <= len(removed) < 1512, "unexpected_retirement_headroom")
        require(removed == old_ids[: len(removed)], "global_oldest_order_changed")
        suite.evidence.artifact(
            "retention-after-automatic-trim.json",
            {"ledger": final, "query": retained_query(suite, final)},
        )
        suite.phase("retention_normal_full_not_capacity_alarm")

        def normal_capacity_rule():
            rows = suite.prom(ALERTS["LedgerCapacity"][0])
            return (
                len(rows) == 1
                and float(rows[0]["value"][1]) == 0
                and (suite.metrics_value("tianshu_obs_alert_ledger_capacity") == 0)
            )

        suite.wait(normal_capacity_rule)

        # Exercise real age expiration with native acknowledged evidence retained.
        suite.phase("retention_accelerated_first_seen_age")
        suite.lifecycle.stop(["obs-guard"])
        aged = final["events"][0][0]
        with (
            closing(sqlite3.connect(suite.root / "data/guard/integrity.sqlite")) as db,
            db,
        ):
            db.execute(
                "UPDATE events SET first_seen=? WHERE id=?",
                (time.time() - 31 * 86400, aged),
            )
        suite.lifecycle.start("obs-guard")
        age_result = suite.wait(lambda: stable_window(suite, count=len(final_ids) - 1))
        require(
            aged not in {row[0] for row in age_result["events"]},
            "age_event_still_retained",
        )
        suite.evidence.artifact(
            "retention-accelerated-age.json",
            {
                "synthetic_stopped_clock_adjustment": True,
                "ledger": age_result,
                "query": retained_query(suite, age_result),
            },
        )

        # Keep an actual unacknowledged Vector disk queue and pending ledger at clear.
        suite.phase("retention_pending_buffer_before_operator_clear")
        suite.lifecycle.stop(["obs-loki"])
        old_pending = generate(suite, "platform", 600, 1)
        suite.wait(lambda: snapshot(suite)["counts"][1] > 0)
        suite.wait(
            lambda: (
                suite.metrics_value(
                    'sum(vector_buffer_size_bytes{component_id="loki"})'
                )
                > 0
            )
        )
        # A collector with an unavailable sink need not finish an online flush.
        # Reuse the established explicit crash/recreate case and its disk queue,
        # rather than treating a forced graceful-stop deadline as a clean exit.
        suite.lifecycle.recreate_vector()
        suite.evidence.artifact(
            "retention-intentional-collector-crash.json", suite.lifecycle.crashes
        )
        readonly = suite.client.request("/retention/clear", "POST", body=b"")[0]
        metric_role = suite.metric_client.request("/retention/clear", "POST", body=b"")[
            0
        ]
        require(readonly == metric_role == 401, "readonly_role_can_clear")
        cli = subprocess.run(
            [
                sys.executable,
                "-B",
                "/opt/tianshu/application/deploy/observability/manage_logs.py",
                "clear",
                "--settings",
                str(suite.root / "native/guard.json"),
                "--guard-url",
                suite.binding.query_url,
            ],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        require(cli.returncode == 0, "management_cli_failed")
        cleared = json.loads(cli.stdout)
        require(cleared["known_pending_events"] > 0, "clear_did_not_include_pending")
        cutoff = snapshot(suite)["clear_before"]
        require(cutoff > 0, "clear_cutoff_not_durable")
        suite.evidence.artifact("retention-writer-clear-includes-pending.json", cleared)
        suite.lifecycle.stop(["obs-guard"])
        suite.lifecycle.start("obs-guard")
        suite.lifecycle.start("obs-loki")
        suite.phase("retention_restart_old_buffer_and_raw_no_reentry")
        suite.wait(lambda: stable_window(suite, count=0))
        suite.wait(
            lambda: (
                suite.metrics_value(
                    'sum(vector_buffer_size_bytes{component_id="loki"})'
                )
                == 0
            )
        )
        empty = snapshot(suite)
        retained_query(suite, empty)
        # First fresh line avoids a deliberately identical Vector fingerprint;
        # subsequent old entries really traverse its collector after the clear.
        new = generate(suite, "platform", 1, 2)
        replay = next(row for row in suite.generated if row["service"] == "platform")
        emit(
            suite.binding.logs["platform"]
            / native_name("platform", suite.instances["platform"], 2),
            [replay, old_pending[-1]],
        )
        suite.phase("retention_new_event_and_late_old_replay")
        after = suite.wait(
            lambda: stable_window(suite, count=1, includes=[new[0]["event_id"]])
        )
        require(after["clear_before"] == cutoff, "restart_lost_clear_cutoff")
        require(
            after["counters"].get("manual_cleared_replays", 0) > 0,
            "old_source_not_consumed",
        )
        suite.evidence.artifact(
            "retention-clear-restart-new-only.json",
            {"ledger": after, "query": retained_query(suite, after)},
        )
        suite.phase("retention_real_loki_delete_requests")
        suite.wait(lambda: all(row[2] <= row[3] for row in snapshot(suite)["deletes"]))
        status, raw, _ = backend.request("/loki/api/v1/delete")
        require(status == 200, "real_loki_delete_inventory_unavailable")
        requests = json.loads(raw)
        require(
            isinstance(requests, list) and requests, "real_loki_delete_not_registered"
        )
        suite.evidence.artifact(
            "retention-real-loki-delete-requests.json",
            {
                "requests": requests,
                "physical_compaction_waited": False,
                "cancel_window_and_delete_delay_preserved": True,
            },
        )
        suite.retention_verdict = {
            "global_max_events": MAXIMUM,
            "configured_days": 30,
            "real_ack_and_query": True,
            "manual_old_pending_destroyed": True,
            "restarted_raw_and_buffer_no_reentry": True,
            "physical_disk_instant_max_not_claimed": True,
        }
        suite.evidence.artifact("retention-summary.json", suite.retention_verdict)
        suite.phase("scenarios_completed")
    finally:
        backend.close()
