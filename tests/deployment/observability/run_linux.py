"""DEP-I plan by default; opt-in execution on a fixed DEP-G synthetic identity."""

import argparse
from contextlib import ExitStack, nullcontext
from pathlib import Path
import re

import helpers  # noqa: F401
from acceptance import DIMENSIONS, Evidence, LocalDocker, sha
from container_lifecycle import OwnedContainers
from runtime_binding import Binding, INTERFACE_COMMIT, lifecycle_lease, load_identity


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--identity-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--execute-synthetic", action="store_true")
    parser.add_argument(
        "--bounded-tmpfs-probe",
        action="store_true",
        help="Fill only guard's existing <=64MiB /tmp tmpfs, never a persistent volume",
    )
    args = parser.parse_args(argv)
    evidence = Evidence(args.output)
    lease_stack = ExitStack()
    try:
        document = load_identity(args.identity, args.identity_sha256)
        evidence.report["binding"] = {
            "interface_commit": INTERFACE_COMMIT,
            "runtime_identity_sha256": args.identity_sha256,
            "project": document["project_name"],
        }
        evidence.artifact(
            "plan.json",
            {
                "mode": "execute" if args.execute_synthetic else "plan",
                "dimensions": list(DIMENSIONS),
                "ownership": "observability five owners only; core must already be stopped",
                "lease": document["lease"],
                "cleanup": "stop owned observability containers; retain containers, source segments, all volumes and evidence",
                "unexecuted_long_tests": [
                    "production_30_day_retention",
                    "accelerated_ttl",
                    "physical_enospc",
                    "vector_buffer_full",
                ],
                "source_reclamation": "blocked; no receipt authorizes deletion",
            },
        )
        evidence.report["code_sha256"] = {
            p.name: sha(p.read_bytes())
            for p in sorted(Path(__file__).parent.glob("linux_*.py"))
        }
        evidence.report["code_sha256"]["run_linux.py"] = sha(
            Path(__file__).read_bytes()
        )
        evidence.report["package_sha256"] = {
            p.name: sha(p.read_bytes()) for p in sorted(helpers.PACKAGE.glob("*.py"))
        }
        if not args.execute_synthetic:
            return 0
        docker = LocalDocker()
        root = Path(document["deployment_root"])
        lease_stack.enter_context(lifecycle_lease(root))
        with nullcontext():
            binding = Binding(document)
            facts = binding.preflight(docker)
            artifact = evidence.artifact("preflight.json", facts)
            lifecycle = OwnedContainers(docker, binding, evidence)
            started = False
            try:
                # Set before issuing command so partial starts are stopped on error.
                started = True
                try:
                    docker.compose(
                        binding.obs_root,
                        binding.obs_project,
                        "up",
                        "-d",
                        "--pull",
                        "never",
                        "--no-build",
                        "--no-recreate",
                    )
                finally:
                    lifecycle.capture_initial()
                running = evidence.artifact(
                    "running-owners.json", binding.running(docker)
                )
                evidence.record(
                    "identity_permissions", "passed", artifacts=[artifact, running]
                )
                from linux_scenarios import Scenarios

                scenarios = Scenarios(docker, binding, evidence, lifecycle)
                try:
                    scenarios.run()
                    if args.bounded_tmpfs_probe:
                        scenarios.current_case = "bounded_tmpfs_capacity_probe"
                        scenarios.capacity_probe()
                except Exception:
                    evidence.record(scenarios.current_case, "failed", "scenario_failed")
                    raise
                finally:
                    scenarios.client.close()
                evidence.record(
                    "application_capacity_gate",
                    "blocked",
                    "no_application_reclamation_contract_preserve_sources_and_stop_admission_at_budget",
                )
            finally:
                if started and lifecycle.pins:
                    lifecycle.stop(tuple(lifecycle.pins), cleanup=True)
    except Exception as exc:
        reason = (
            str(exc)
            if type(exc) is ValueError and re.fullmatch(r"[a-z0-9_]+", str(exc))
            else "execution_failed_details_withheld"
        )
        evidence.report["error_code"] = reason
        return 1
    finally:
        try:
            evidence.finish()
        finally:
            lease_stack.close()
    # A short chain can be partial but never an overall release pass.
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
