"""Time existing Platform PG schema probes without constructing a Platform or runtime.

Explicit physical settings/state directory only. --owners-inspect supplies live
Platform Config.Env for a SOURCE measurement; omit it for clone physical settings.
reused models warm checkout SQL; fresh measures open_existing per handle. Neither
mode is the running application's pool or a full nine-check readiness result.
"""

import argparse
from dataclasses import replace
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
import sys
import time


def measure(args):
    import psycopg.conninfo
    from psycopg import sql
    from services.platform.postgres import check_schema, dialect
    from tianshu_storage import StorageOptions, StorageRuntime
    from tianshu_storage.runtime import read_state, state_filename

    for name in ("services.platform.postgres", "tianshu_storage.runtime"):
        if (
            not Path(importlib.import_module(name).__file__)
            .resolve()
            .is_relative_to(Path(sys.prefix).resolve())
        ):
            raise RuntimeError("installed_wheels_required")
    if importlib.metadata.version("tianshu-storage") != "0.1.3":
        raise RuntimeError("storage_013_required")
    config = json.loads(args.settings.read_bytes())
    env = {}
    if args.owners_inspect:
        env = dict(
            item.split("=", 1)
            for item in json.loads(args.owners_inspect.read_bytes())["platform"][
                "Config"
            ]["Env"]
        )
    options = replace(StorageOptions.from_settings(config, environ=env), redis_url="")
    connection = psycopg.conninfo.conninfo_to_dict(options.postgresql_url)
    if args.pg_host is not None:
        connection["host"] = args.pg_host
    if args.pg_port is not None:
        connection["port"] = str(args.pg_port)
    options = replace(
        options, postgresql_url=psycopg.conninfo.make_conninfo(**connection)
    )
    directory = args.state_directory.resolve(strict=True)
    state = read_state(
        directory / state_filename("platform"),
        namespace=options.namespace,
        service="platform",
    )
    if (
        not state
        or state["backend"] != "postgresql"
        or state["state"] != "active"
        or len(state["databases"]) != 17
    ):
        raise RuntimeError("existing_seventeen_platform_domains_required")
    report = {
        "mode": args.mode,
        "transport": "unix" if connection.get("host", "").startswith("/") else "tcp",
        "settings_sha256": hashlib.sha256(args.settings.read_bytes()).hexdigest(),
        "environment_from_inspect": bool(args.owners_inspect),
        "rounds": [],
        "readiness_claimed": False,
        "database_or_marker_modified": False,
    }
    current = None
    try:
        for repeat in range(args.rounds):
            started = time.perf_counter()
            rows = []
            for logical, schema in sorted(state["databases"].items()):
                item = {"logical": logical, "error_class": None}
                phase = "open_or_select"
                point = time.perf_counter()
                try:
                    if args.mode == "fresh" or current is None:
                        current = StorageRuntime.open_existing(
                            options,
                            service="platform",
                            state_directory=directory,
                            name=logical,
                            path=config["database_path"],
                            dialect=dialect,
                            timeout=0.25,
                        )
                    if args.mode == "reused":
                        # Exact pooled _checkout settings, without creating a pool/startup probe.
                        current.raw.execute(
                            "SELECT pg_catalog.set_config('search_path', %s, false),pg_catalog.set_config('lock_timeout', %s, false),pg_catalog.set_config('default_transaction_read_only', %s, false)",
                            (
                                sql.Identifier(schema).as_string() + ", pg_catalog",
                                "250ms",
                                "on",
                            ),
                        )
                        binding = current.raw.execute(
                            "SELECT installation_id,logical_name,state FROM __tianshu_storage_binding WHERE singleton=1"
                        ).fetchone()
                        if binding != (state["installation_id"], logical, "ready"):
                            raise RuntimeError("binding_mismatch")
                    item[phase + "_ms"] = (time.perf_counter() - point) * 1000
                    phase, point = "check_schema", time.perf_counter()
                    check_schema(current)
                    item[phase + "_ms"] = (time.perf_counter() - point) * 1000
                    phase, point = "metadata", time.perf_counter()
                    if (
                        current.execute(
                            "SELECT version FROM _ts_schema_metadata WHERE id=1"
                        ).fetchone()
                        is None
                    ):
                        raise RuntimeError("metadata_missing")
                    item[phase + "_ms"] = (time.perf_counter() - point) * 1000
                except Exception as error:
                    item[phase + "_ms"] = (time.perf_counter() - point) * 1000
                    item["error_class"] = type(error).__name__
                finally:
                    point = time.perf_counter()
                    if current is not None:
                        if args.mode == "fresh":
                            current.close()
                            current = None
                        else:
                            current.raw.rollback()
                    item["close_or_rollback_ms"] = (time.perf_counter() - point) * 1000
                rows.append(item)
            report["rounds"].append(
                {
                    "index": repeat + 1,
                    "total_ms": (time.perf_counter() - started) * 1000,
                    "handles": rows,
                }
            )
    finally:
        if current is not None:
            current.close()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--state-directory", type=Path, required=True)
    parser.add_argument("--owners-inspect", type=Path)
    parser.add_argument("--mode", choices=("reused", "fresh"), default="reused")
    parser.add_argument("--rounds", type=int, choices=range(1, 4), default=2)
    parser.add_argument("--pg-host")
    parser.add_argument("--pg-port", type=int)
    args = parser.parse_args()
    try:
        result = measure(args)
    except Exception as error:
        print(
            json.dumps(
                {"status": "measurement_failed", "error_class": type(error).__name__}
            )
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
