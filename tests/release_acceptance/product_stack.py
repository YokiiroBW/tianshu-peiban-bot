"""Four unchanged product CLIs, fresh local state, real TLS, and one declared model fixture."""

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from acceptance.evidence import Report, digest
from acceptance.catalog import build_catalog
from acceptance.health import validate_ready
from acceptance.inputs import verify_snapshot
from acceptance.suite import Suite
from acceptance.source_lease import completed_after_expiry
from acceptance.transport import Failed, Missing, check
from product_inputs import ROLES, inputs
from product_lifecycle import ProductLifecycle
from product_model import ModelFixture

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "deploy/tianshu"))
from bootstrap import first_install, remaining  # noqa: E402
from manifest import load_manifest  # noqa: E402


def implementation_files():
    paths = list(Path(__file__).parent.glob("product_*.py"))
    paths += list((Path(__file__).parent / "acceptance").glob("*.py"))
    paths += list((ROOT / "deploy/tianshu/templates").glob("*.json"))
    paths += [
        ROOT / "deploy/tianshu/bootstrap.py",
        ROOT / "deploy/tianshu/configuration.py",
    ]
    return {
        p.relative_to(ROOT).as_posix(): digest(p.read_bytes()) for p in sorted(paths)
    }


class Stack(ProductLifecycle):
    def __init__(self, root, snapshots, manifest, binding):
        self.root, self.snapshots, self.manifest, self.binding = (
            root,
            snapshots,
            manifest,
            binding,
        )
        self.configs, self.envs, self.urls, self.ports, publication = inputs(
            root, snapshots, manifest, ROOT / "deploy/tianshu/templates"
        )
        self.children, self.launch = {}, {}
        self.model = None
        self.receipt = first_install(
            Path(sys.executable),
            snapshots / "platform",
            root / "config/platform.json",
            self.envs["platform"],
            publication,
            root / "bootstrap",
        )
        self.envs["gateway"]["TS_GATEWAY_ORIGIN"] = self.receipt["assertion_ref"]
        self.model = ModelFixture(
            root, self.ports["model"], self.envs["gateway"]["TS_SYNTHETIC_MODEL"]
        )

    def fingerprint(self):
        return digest(Path(__file__).read_bytes())

    def check_initial_budget(self):
        # Once opted in, the real gateway enforces the current lease on every fetch.
        # The bootstrap receipt is an initial expiry, not the product's renewed expiry.
        if not (
            self.configs["platform"].get("model_origin_renewal_http") is True
            and self.configs["gateway"].get("platform_origin_renewal") is True
        ):
            remaining(self.receipt, minimum_seconds=5)

    def call(self, operation, **_):
        if operation == "logs":
            lines = []
            for role in ROLES:
                for file in (self.root / "logs" / role).glob("*.jsonl*"):
                    for line in file.read_bytes().splitlines(keepends=True):
                        if (
                            line.endswith(b"\n")
                            and json.loads(line).get("correlation_id")
                            == _["correlation_id"]
                        ):
                            lines.append(line.decode("utf-8"))
            # Keep original safe product diagnostics, never construct causal events.
            (
                self.root.parent / ("causal-" + _["correlation_id"] + ".jsonl")
            ).write_text("".join(lines), encoding="utf-8", newline="\n")
            return {"lines": lines}
        if operation == "identity":
            check(
                all(p.poll() is None for p in self.children.values())
                and len(self.children) == 4,
                "product_process_not_running",
            )
            verify_snapshot(self.snapshots)
            return {
                "scope": "synthetic_isolated",
                "runtime_kind": "product",
                "release_sha256": self.binding["manifest_sha256"],
                "products": {
                    r: {k: self.binding["products"][r][k] for k in ("commit", "digest")}
                    for r in ROLES
                },
            }
        if operation == "configuration":
            hashes = {}
            for role in ROLES:
                observed = json.loads(
                    (self.root / (role + "-loaded.json")).read_bytes()
                )
                check(
                    self.children[role].pid in {observed["pid"], observed["parent_pid"]}
                    and self.children[role].poll() is None,
                    "configuration_process_mismatch",
                )
                self.launch[role]["observed_process"] = {
                    k: observed[k] for k in ("pid", "parent_pid", "basis")
                }
                status, body = self.client(role).request(
                    "GET",
                    "/health/ready",
                    headers={
                        "Authorization": "Bearer "
                        + self.envs[role]["TIANSHU_DIAGNOSTICS_TOKEN"]
                    },
                )
                validate_ready(role, status, body)
                check(
                    observed["config_sha256"]
                    == digest((self.root / "config" / (role + ".json")).read_bytes()),
                    "parsed_config_changed",
                )
                hashes[role] = observed["config_sha256"]
            return {"basis": "runtime_loaded", "loaded_config_sha256": hashes}
        if operation == "state":
            from acceptance.capabilities import disabled_facts

            self.check_initial_budget()
            facts = disabled_facts(
                self.client("companion"),
                self.envs["companion"]["TIANSHU_DIAGNOSTICS_TOKEN"],
            )
            if any(facts["retained_outbox"].values()):
                raise Missing("retained_outbox_exact_counts_unavailable")
            # Fresh state + disabled generation + no state-bearing outbox rows. No cross-product SQL.
            sent = len(self.delivery_ids())
            return {
                "scope": "synthetic_isolated",
                "model_calls": self.model.calls,
                "send_calls": sent,
                "candidate_count": 0,
                "pending_candidates": 0,
            }
        raise Missing("product_control_operation_unavailable_" + operation)

    def delivery_ids(self):
        return {
            row["event_id"]
            for row in self.logs("companion")
            if row["event"] == "turn.delivery.finished"
            and row["outcome"] == "succeeded"
        }

    def logs(self, role):
        rows = []
        for file in (self.root / "logs" / role).glob("*.jsonl*"):
            for line in file.read_bytes().splitlines(keepends=True):
                if line.endswith(b"\n"):
                    rows.append(json.loads(line))
        return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshots", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repositories", type=Path, required=True)
    parser.add_argument(
        "--turns",
        type=int,
        default=0,
        help="Additional complete synthetic turns (0 or 257..300)",
    )
    parser.add_argument(
        "--minimum-duration-seconds",
        type=int,
        default=0,
        help="Pace the additional turns to span an initial 300s source lease (310..600)",
    )
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--faults", action="store_true", help="Run DEP-H real-service faults"
    )
    args = parser.parse_args()
    if not args.execute:
        print(
            json.dumps(
                {
                    "state": "plan_only",
                    "execution_requires": "--execute",
                    "scope": "new_synthetic_only",
                    "faults": args.faults,
                }
            )
        )
        return 2
    check(args.turns == 0 or 257 <= args.turns <= 300, "long_run_turn_count_invalid")
    check(not (args.faults and args.turns), "fault_suite_cannot_precede_long_run")
    check(
        args.minimum_duration_seconds == 0
        or (args.turns and 310 <= args.minimum_duration_seconds <= 600),
        "long_run_duration_invalid",
    )
    manifest = load_manifest(args.manifest)
    marker = verify_snapshot(args.snapshots)
    check(
        marker["binding"]["manifest_sha256"] == digest(args.manifest.read_bytes()),
        "snapshot_manifest_mismatch",
    )
    check(not args.output.exists(), "new_output_required")
    args.output.mkdir(parents=True)
    report = Report("local", marker["binding"], "product")
    frozen_implementation = implementation_files()
    report.data["implementation_files"] = frozen_implementation
    report.data["coverage"] = "actual_product_cli_tls_synthetic_model"
    report.data["limitations"] = [
        "windows_host",
        "shared_test_dependency_environment",
        "synthetic_static_assets",
        "no_linux_images",
        "no_browser_rendering",
    ]
    stack = None
    with tempfile.TemporaryDirectory(prefix="dep-e-", dir=args.output) as folder:
        try:
            stack = Stack(
                Path(folder).absolute(),
                args.snapshots.absolute(),
                manifest,
                marker["binding"],
            )
            if args.faults:
                from product_faults import FaultControl

                stack.faults = FaultControl(stack)
            stack.start()
            config = {
                "input_version": "dep-d/1",
                "mode": "local",
                "runtime_kind": "product",
                "scope": "synthetic_isolated",
                "model_kind": "recorded",
                "features": {"automatic_memory": False, "chat_archive": False},
                "case_timeout_seconds": 30,
                "endpoints": {},
                "expected_config_sha256": {},
                "web": {
                    "username_env": "DEP_E_USERNAME",
                    "password_env": "DEP_E_PASSWORD",
                    "conversation": "web-input",
                    "actor": "actor:household",
                },
            }
            catalog = build_catalog(
                marker["binding"], json.loads(args.repositories.read_bytes())
            )
            catalog_path = stack.root / "catalog.json"
            from product_inputs import write

            write(catalog_path, catalog)
            config["logs"] = {
                "catalog_file": str(catalog_path),
                "catalog_sha256": digest(catalog_path.read_bytes()),
                "success": {
                    "required": [
                        ["platform", "http.request.finished", "succeeded"],
                        ["companion", "turn.delivery.finished", "succeeded"],
                        ["memory", "request.completed", "succeeded"],
                        ["gateway", "upstream.call_finished", "succeeded"],
                    ]
                },
                "failure": {
                    "required": [["companion", "turn.generation.finished", "unknown"]],
                    "forbidden": [["companion", "turn.delivery.finished", "succeeded"]],
                },
            }
            os.environ["DEP_E_USERNAME"] = "synthetic-admin"
            os.environ["DEP_E_PASSWORD"] = stack.envs["platform"]["TS_ADMIN_PASSWORD"]
            for role in ROLES:
                name = "DEP_E_DIAGNOSTICS_" + role.upper()
                os.environ[name] = stack.envs[role]["TIANSHU_DIAGNOSTICS_TOKEN"]
                config["endpoints"][role] = {
                    "url": stack.urls[role],
                    "ca_file": str(stack.root / "tls/ca.pem"),
                    "diagnostics_token_env": name,
                }
                config["expected_config_sha256"][role] = stack.launch[role][
                    "config_sha256"
                ]
            suite_type = Suite
            if args.faults:
                from product_fault_suite import ProductFaultSuite

                suite_type = ProductFaultSuite
                config["case_timeout_seconds"] = 60
                config["logs"]["failure"]["required"] = [
                    ["companion", "turn.generation.finished", "failed"]
                ]
            suite = suite_type(config, marker["binding"], report)
            suite.adapter = stack
            suite.execute()
            if args.turns:
                check(
                    suite.success.get("dialogue_model_reply"),
                    "long_run_requires_dialogue",
                )
                before = stack.call("state")
                stress_started = time.monotonic()
                accepted_correlations, completed_deliveries = [], []
                for index in range(args.turns):
                    target_time = (
                        stress_started
                        + args.minimum_duration_seconds * index / max(1, args.turns - 1)
                    )
                    while time.monotonic() < target_time:
                        time.sleep(min(0.5, max(0, target_time - time.monotonic())))
                    stack.check_initial_budget()
                    request = suite.submit()
                    check(
                        request["status"] == 200
                        and request["result"].get("state") == "accepted",
                        "long_run_not_accepted",
                    )
                    accepted_correlations.append(request["correlation"])
                    # Only one outstanding request exists. Product delivery diagnostics
                    # establish completed sends without repeatedly reading all web history.
                    deadline = time.monotonic() + 30

                    def delivered():
                        return [
                            row
                            for row in stack.logs("companion")
                            if row["event"] == "turn.delivery.finished"
                            and row["outcome"] == "succeeded"
                            and row["correlation_id"] == request["correlation"]
                        ]

                    while not delivered() and time.monotonic() < deadline:
                        time.sleep(0.1)
                    rows = delivered()
                    check(len(rows) == 1, "long_run_reply_failed")
                    completed_deliveries.append(rows[0])
                    with (args.output / "stress-delivery.jsonl").open(
                        "a", encoding="utf-8", newline="\n"
                    ) as evidence:
                        evidence.write(
                            json.dumps(rows[0], separators=(",", ":")) + "\n"
                        )
                    report.data["disabled_long_run_progress"] = index + 1
                    if (index + 1) % 25 == 0:
                        print(
                            json.dumps({"completed_synthetic_turns": index + 1}),
                            flush=True,
                        )
                after = stack.call("state")
                check(
                    after["candidate_count"] == 0
                    and after["pending_candidates"] == 0
                    and after["model_calls"] - before["model_calls"] == args.turns,
                    "long_run_candidate_growth_or_duplicate",
                )
                report.data["disabled_long_run"] = {
                    "result": "passed",
                    "completed_turns": args.turns,
                    "model_calls_delta": args.turns,
                    "pending_candidates": 0,
                    "candidate_count": 0,
                    "basis": "public_web_inputs_product_delivery_events_public_capabilities_and_provider_http_calls",
                    "per_turn_web_history_readback": False,
                    "actual_memory_writes_claimed": False,
                    "delivery_evidence_sha256": digest(
                        (args.output / "stress-delivery.jsonl").read_bytes()
                    ),
                    "duration_seconds": round(time.monotonic() - stress_started, 3),
                }
                if args.minimum_duration_seconds:
                    report.data["source_renewal"] = {
                        **completed_after_expiry(
                            completed_deliveries,
                            stack.receipt["expires_at"],
                            accepted_correlations,
                        ),
                        "basis": "same_gateway_process_and_initial_ref_no_rebootstrap_successful_model_and_delivery",
                    }
            if args.faults:
                report.data["fault_observations"] = stack.faults.observations
            report.data["runtime_diagnostics"] = {
                role: [
                    {k: row[k] for k in ("event", "outcome", "error_code")}
                    for row in stack.logs(role)[-24:]
                ]
                for role in ROLES
            }
            report.data["model_fixture_calls"] = stack.model.calls
            verify_snapshot(stack.snapshots)
            report.data["source_snapshot_unchanged"] = True
            report.data["launch_observation"] = stack.launch
            report.data["config_observation_basis"] = (
                "child_successful_json_parse_plus_authenticated_product_readiness"
            )
            report.data["bootstrap"] = json.loads(
                (stack.root / "bootstrap/result.json").read_bytes()
            )
        except Exception as error:
            code = (
                str(error)
                if isinstance(error, (Failed, Missing, ValueError, AssertionError))
                and str(error).replace("_", "").isalnum()
                else "product_stack_failed"
            )
            report.add("product_stack", "fail", code)
        finally:
            if stack:
                if hasattr(stack, "faults"):
                    stack.faults.close()
                report.data["cleanup"] = stack.close()
            report.data["implementation_unchanged"] = (
                implementation_files() == frozen_implementation
            )
            if not report.data["implementation_unchanged"]:
                report.add(
                    "immutable_runner", "fail", "runner_changed_during_execution"
                )
            report.save(args.output)
    print(json.dumps({"verdict": report.data["verdict"], "output": str(args.output)}))
    return 1 if report.data["verdict"] == "failed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
