"""DEP-D entry point. Plan is read-only; run/observe need explicit --execute."""

import argparse
import json
import sys
from pathlib import Path

from acceptance.evidence import Report, digest, read_json, verify_report
from acceptance.catalog import build_catalog
from acceptance.inputs import export_sources, load_binding
from acceptance.observation import observe
from acceptance.suite import CASES, Suite, validate_config


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("plan", "run", "observe", "snapshot", "catalog", "verify-report"),
    )
    parser.add_argument("--input")
    parser.add_argument("--manifest")
    parser.add_argument(
        "--mapping", default=str(Path(__file__).parent / "depa-mapping.json")
    )
    parser.add_argument("--contracts-root")
    parser.add_argument("--output", required=True)
    parser.add_argument(
        "--repositories",
        help="JSON role -> local Git repository; only used by snapshot",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--duration-seconds", type=float, default=86400)
    parser.add_argument("--interval-seconds", type=float, default=30)
    args = parser.parse_args(argv)
    if args.command == "verify-report":
        try:
            good = verify_report(Path(args.output) / "report.json")
        except Exception:
            good = False
        print(json.dumps({"integrity": "valid" if good else "invalid"}))
        return 0 if good else 1
    report = Report("unknown", {}, "unverified")
    try:
        config = validate_config(read_json(args.input)) if args.input else None
        binding = load_binding(args.manifest, args.mapping, args.contracts_root)
        if args.command == "catalog":
            catalog = build_catalog(binding, read_json(args.repositories))
            destination = Path(args.output)
            destination.mkdir(parents=True, exist_ok=True)
            raw = json.dumps(catalog, ensure_ascii=False, indent=2).encode() + b"\n"
            (destination / "catalog.json").write_bytes(raw)
            print(json.dumps({"catalog_sha256": digest(raw)}))
            return 0
        if args.command == "snapshot":
            marker = export_sources(
                binding, read_json(args.repositories), args.output, args.contracts_root
            )
            print(
                json.dumps(
                    {"snapshot": "exported", "archive_sha256": marker["archive_sha256"]}
                )
            )
            return 0
        if config is None:
            raise ValueError("input_required")
        report = Report(config["mode"], binding, config["runtime_kind"])
        report.data["input_sha256"] = digest(Path(args.input).read_bytes())
        if args.command == "observe" and args.execute:
            report.data["kind"] = "readiness_observation"
            from acceptance.transport import Missing

            try:
                facts = Suite(config, binding, report).runtime_binding()
                report.add("runtime_binding", "pass", "assertions_satisfied", facts)
            except Missing as exc:
                report.add("runtime_binding", "dependency_missing", str(exc))
                report.add("observation_24h", "not_run", "runtime_binding_missing")
                result = report.save(args.output)
                print(json.dumps({"verdict": result["verdict"]}))
                return 2
            observe(
                config,
                report,
                args.output,
                args.duration_seconds,
                args.interval_seconds,
            )
        else:
            Suite(config, binding, report).execute(
                plan=args.command == "plan" or not args.execute
            )
    except KeyboardInterrupt:
        report.add("preflight", "not_run", "interrupted")
    except FileNotFoundError:
        report.add("preflight", "dependency_missing", "input_file_missing")
    except Exception as exc:
        # Only our fixed ValueError codes are allowed to escape; file names and
        # network exception text may contain private configuration.
        code = (
            str(exc)
            if type(exc) is ValueError and str(exc).replace("_", "").isalnum()
            else "invalid_input"
        )
        report.add("preflight", "fail", code)
    if not report.data["results"] or any(
        x["case"] == "preflight" for x in report.data["results"]
    ):
        for name in CASES:
            report.add(name, "not_run", "preflight_not_completed")
    result = report.save(args.output)
    print(
        json.dumps(
            {"verdict": result["verdict"], "content_sha256": result["content_sha256"]}
        )
    )
    return (
        1
        if result["verdict"] == "failed"
        else 2
        if result["verdict"] == "incomplete"
        else 0
    )


if __name__ == "__main__":
    sys.exit(main())
