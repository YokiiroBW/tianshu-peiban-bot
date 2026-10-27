"""Affected TS362 capacity workflow, using the original five native engines."""

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from acceptance import require
from configs import ALERTS
from helpers import emit, event


def prepare(fixture):
    path = Path(fixture["root"]) / "native/guard.json"
    config = json.loads(path.read_bytes())
    config["ledger_max_events"] = 5
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def state(suite):
    path = suite.root / "data/guard/integrity.sqlite"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        return {
            "counts": db.execute("SELECT landed,pending FROM totals").fetchone(),
            "files": db.execute(
                "SELECT name,offset FROM files ORDER BY name"
            ).fetchall(),
            "counters": dict(db.execute("SELECT name,value FROM counters")),
        }


def rule_value(suite):
    rows = suite.prom(ALERTS["LedgerCapacity"][0])
    require(len(rows) == 1, "one_capacity_rule_result_required")
    return float(rows[0]["value"][1])


def execute(suite):
    require(
        suite.events_per_source == 1, "capacity_scenario_requires_one_event_per_source"
    )
    suite.start_engines()
    suite.phase("capacity_datasources_targets")
    suite.ui_and_metrics()
    status, rules = suite.api("/api/v1/provisioning/alert-rules")
    require(
        status == 200
        and len(rules) == len(ALERTS) == 15
        and {row["title"] for row in rules} == set(ALERTS),
        "fifteen_alerts_not_loaded",
    )
    rule = next(row for row in rules if row["title"] == "LedgerCapacity")
    require(
        next(row["model"]["expr"] for row in rule["data"] if row["refId"] == "A")
        == ALERTS["LedgerCapacity"][0],
        "capacity_rule_not_loaded",
    )
    suite.evidence.artifact("capacity-live-grafana-rule.json", rule)
    suite.batch()
    suite.reconcile("numbered_collection")
    suite.durable("capacity-full-ledger")
    suite.phase("capacity_full_rule")
    suite.wait(
        lambda: (
            suite.metrics_value("tianshu_obs_ledger_max_events") == 5
            and suite.metrics_value("tianshu_obs_alert_ledger_capacity") == 1
            and rule_value(suite) >= 1
        )
    )

    for name, directory in suite.binding.logs.items():
        original = next(
            row
            for row in suite.generated
            if row["instance_id"] == suite.instances[name]
        )
        emit(directory / "z-replay.jsonl", [original])
    suite.phase("capacity_known_replay")
    suite.wait(lambda: suite.metrics_value("tianshu_obs_source_duplicates_total") >= 5)
    replay = state(suite)
    require(replay["counts"] == (5, 0), "full_replay_changed_event_count")
    require(
        all(
            dict(replay["files"])[name + "/z-replay.jsonl"]
            == (directory / "z-replay.jsonl").stat().st_size
            for name, directory in suite.binding.logs.items()
        ),
        "full_replay_offset_not_consumed",
    )
    require(
        suite.metrics_value("tianshu_obs_ledger_capacity_rejected_current") == 0,
        "known_replay_rejected_as_new",
    )
    suite.evidence.artifact("capacity-known-replay.json", replay)

    path = suite.binding.logs["platform"] / "zz-new.jsonl"
    record = event(2, "platform", suite.instances["platform"])
    emit(path, [record])
    suite.generated.append(record)
    # A full integrity ledger must not prevent real Vector/Loki storage/query.
    suite.reconcile("numbered_collection", artifact_name="capacity-new-event-in-loki")
    suite.phase("capacity_real_new_event_rejected")
    suite.wait(
        lambda: suite.metrics_value("tianshu_obs_ledger_capacity_rejected_current") == 1
    )
    blocked = state(suite)
    require(
        blocked["counts"] == (5, 0)
        and dict(blocked["files"])["platform/zz-new.jsonl"] == 0,
        "new_event_not_preserved_at_source_offset",
    )
    suite.evidence.artifact("capacity-new-event-blocked.json", blocked)
    suite.lifecycle.stop(["obs-guard"])
    frozen_counter = state(suite)["counters"]["ledger_capacity_rejected"]
    config_path = suite.root / "native/guard.json"
    config = json.loads(config_path.read_bytes())
    config["ledger_max_events"] = 20
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    suite.lifecycle.start("obs-guard")
    suite.durable("capacity-raised-backlog-recovered")
    suite.phase("capacity_current_alarm_resolved_history_retained")
    suite.wait(
        lambda: (
            suite.metrics_value("tianshu_obs_ledger_max_events") == 20
            and suite.metrics_value("tianshu_obs_ledger_capacity_rejected_current") == 0
            and suite.metrics_value("tianshu_obs_alert_ledger_capacity") == 0
            and rule_value(suite) == 0
        )
    )
    final = state(suite)
    require(
        final["counts"] == (6, 0)
        and final["counters"]["ledger_capacity_rejected"] == frozen_counter
        and frozen_counter > 0,
        "capacity_history_or_backlog_changed",
    )
    require(
        dict(final["files"])["platform/zz-new.jsonl"] == path.stat().st_size,
        "new_event_offset_not_recovered",
    )
    suite.evidence.artifact("capacity-recovered-with-history.json", final)
    suite.phase("scenarios_completed")
