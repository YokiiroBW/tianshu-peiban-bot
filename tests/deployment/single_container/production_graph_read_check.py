#!/usr/bin/env python3
"""Read the installed production graph query without constructing any application.

Run in the new container with its venv Python, also supported as ``python -B -``
over docker exec stdin. Only the actual host config and the reader's original
guard CA/query credential are read. Output contains closed statuses, aggregate
counts and validated event kinds; no event bodies, identifiers or credentials.
This checks the configured guard reader and installed safe projection directly,
not the browser login/session gate or business completion.
"""

import argparse
import asyncio
from collections import Counter
import importlib
import json
from pathlib import Path
import ssl
import sys
import sysconfig
import time

sys.dont_write_bytecode = True

INDEX_LIMIT = 1500
DETAIL_LIMIT = 1000


class CheckFailed(RuntimeError):
    pass


def require(condition):
    if not condition:
        raise CheckFailed()


def boundaries():
    return {
        "read_only_guard_query": True,
        "platform_assembly_created": False,
        "business_host_created": False,
        "production_chat_store_read": False,
        "events_emitted": False,
        "arbitrary_selector_accepted": False,
        "event_identifiers_output": False,
        "event_bodies_output": False,
        "credentials_or_addresses_output": False,
        "browser_auth_exercised": False,
        "business_completion_inferred": False,
        "installed_platform_projection": False,
        "image_host_reader": False,
        "authenticated_tls_query": False,
        "reader_closed": False,
    }


def installed_modules():
    reader_module = importlib.import_module("deploy.single_container.flow_reader")
    settings_module = importlib.import_module("deploy.single_container.host_settings")
    projection_module = importlib.import_module("services.platform.web_flow_graph")
    diagnostics_module = importlib.import_module("services.platform.diagnostics")
    image_deploy = Path("/opt/tianshu/application/deploy").resolve()
    require(Path(reader_module.__file__).resolve().is_relative_to(image_deploy))
    require(Path(settings_module.__file__).resolve().is_relative_to(image_deploy))
    package_roots = {
        Path(sysconfig.get_path(key)).resolve() for key in ("purelib", "platlib")
    }
    for module in (projection_module, diagnostics_module):
        require(
            any(
                Path(module.__file__).resolve().is_relative_to(root)
                for root in package_roots
            )
        )
    require(len(diagnostics_module.FIELDS) == 12)
    require(len(diagnostics_module.SERVICE_NAMES) == 5)
    return reader_module, settings_module, projection_module, diagnostics_module


def summarize(events, projection, services):
    kinds = Counter((row["service"], row["event"]) for row in events)
    by_service = Counter(row["service"] for row in events)
    by_outcome = Counter(row["outcome"] for row in events)
    correlations = {}
    stages = Counter()
    for row in events:
        if row["correlation_id"] is not None:
            correlations.setdefault(row["correlation_id"], False)
        stage = projection.event_stage(row)
        if stage is not None:
            stages[stage] += 1
            if row["correlation_id"] is not None:
                correlations[row["correlation_id"]] = True
    return {
        "counts": {
            "events": len(events),
            "correlations": len(correlations),
            "business_correlations": sum(correlations.values()),
            "by_service": {name: by_service[name] for name in services},
            "by_outcome": dict(sorted(by_outcome.items())),
            "by_observed_stage": dict(sorted(stages.items())),
        },
        "event_kinds": [
            {"service": service, "event": event, "count": count}
            for (service, event), count in sorted(kinds.items())
        ],
    }


def query_status(events, complete):
    if not complete:
        return "ready_partial"
    return "ready_with_events" if events else "ready_empty"


async def run(options):
    safety = boundaries()
    report = {
        "status": "not_started",
        "counts": {"guard_queries": 0, "window_minutes": options.minutes},
        "safety": safety,
    }
    reader = None
    phase = "installed_code_unavailable"
    exit_code = 1
    try:
        reader_module, settings_module, projection, diagnostics = installed_modules()
        safety.update(installed_platform_projection=True, image_host_reader=True)
        report["counts"]["fixed_sources"] = len(diagnostics.SERVICE_NAMES)
        phase = "host_configuration_unavailable"
        host = settings_module.load_settings(options.host_config)
        spec = host["modules"]["platform"].get("flow_query")
        if spec is None:
            report["status"] = "not_configured"
            return report, 2
        phase = "reader_configuration_unavailable"
        reader = reader_module.FlowEventReader.from_settings(spec)
        require(
            bool(reader.client.token)
            and reader.client.context.verify_mode == ssl.CERT_REQUIRED
            and reader.client.context.check_hostname
            and reader.client.context.minimum_version >= ssl.TLSVersion.TLSv1_2
        )
        end_ns = time.time_ns()
        start_ns = end_ns - options.minutes * 60 * 1_000_000_000
        phase = "index_query_failed"
        report["counts"]["guard_queries"] += 1
        raw = await reader(
            correlation_id=None, start_ns=start_ns, end_ns=end_ns, limit=INDEX_LIMIT
        )
        safety["authenticated_tls_query"] = True
        phase = "index_projection_rejected"
        events, complete = projection.safe_events(
            raw, None, start_ns, end_ns, INDEX_LIMIT
        )
        report["index"] = {
            "status": query_status(events, complete),
            "complete": complete,
            **summarize(events, projection, diagnostics.SERVICE_NAMES),
        }
        # Select only a validated, already observed correlation. Prefer an actual
        # business-stage event without excluding generic service observations.
        correlated = [row for row in events if row["correlation_id"] is not None]
        business = [
            row for row in correlated if projection.event_stage(row) is not None
        ]
        if correlated:
            correlation = (business or correlated)[-1]["correlation_id"]
            phase = "detail_query_failed"
            report["counts"]["guard_queries"] += 1
            raw_detail = await reader(
                correlation_id=correlation,
                start_ns=start_ns,
                end_ns=end_ns,
                limit=DETAIL_LIMIT,
            )
            phase = "detail_projection_rejected"
            detail, detail_complete = projection.safe_events(
                raw_detail, correlation, start_ns, end_ns, DETAIL_LIMIT
            )
            report["detail"] = {
                "status": query_status(detail, detail_complete),
                "complete": detail_complete,
                **summarize(detail, projection, diagnostics.SERVICE_NAMES),
            }
        else:
            report["detail"] = {
                "status": "no_observed_correlation",
                "counts": {"events": 0},
            }
        report["status"] = query_status(events, complete)
        exit_code = 0
    except Exception:
        # Exception strings can contain backend response text, paths or secrets.
        # The phase is chosen only from the static labels above.
        report["status"] = phase
    finally:
        if reader is not None:
            try:
                await reader.aclose()
                safety["reader_closed"] = True
            except Exception:
                report["status"] = "reader_close_failed"
                exit_code = 1
    return report, exit_code


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host-config", type=Path, default=Path("/config/host.json"))
    parser.add_argument("--minutes", type=int, choices=(5, 15, 60), default=5)
    options = parser.parse_args()
    try:
        report, code = asyncio.run(run(options))
    except BaseException:
        report, code = {"status": "read_check_interrupted", "safety": boundaries()}, 1
    print(json.dumps(report, ensure_ascii=True, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
