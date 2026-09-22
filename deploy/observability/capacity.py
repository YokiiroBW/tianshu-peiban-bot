"""Capacity arithmetic using explicit measured inputs, not promises about unknown traffic."""

import argparse
import json
import math


def plan(
    events_per_day,
    mean_line_bytes,
    expansion,
    safety,
    outage_hours,
    loki_bytes,
    source_bytes,
):
    inputs = (
        events_per_day,
        mean_line_bytes,
        expansion,
        safety,
        outage_hours,
        loki_bytes,
        source_bytes,
    )
    if (
        any(not math.isfinite(n) or n <= 0 for n in inputs)
        or mean_line_bytes > 4096
        or safety < 1
    ):
        raise ValueError("positive_measured_inputs_required")
    daily = events_per_day * mean_line_bytes
    return {
        "daily_source_bytes": math.ceil(daily),
        "loki_30_day_target_bytes": math.ceil(daily * expansion * safety * 30),
        "outage_buffer_target_bytes": math.ceil(daily * outage_hours / 24 * safety),
        "planned_loki_days": round(loki_bytes / (daily * expansion * safety), 2),
        "source_days_until_rejection_without_reclamation": round(
            source_bytes / daily, 2
        ),
        "unlimited_throughput_promised": False,
        "application_reclamation_available": False,
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    for name in (
        "events_per_day",
        "mean_line_bytes",
        "expansion",
        "safety",
        "outage_hours",
        "loki_bytes",
        "source_bytes",
    ):
        p.add_argument("--" + name.replace("_", "-"), type=float, required=True)
    print(json.dumps(plan(**vars(p.parse_args())), indent=2))
