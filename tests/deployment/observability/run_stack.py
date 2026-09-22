"""Opt-in synthetic Linux Docker acceptance. Only a fresh local project is ever operated."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import uuid

from helpers import SNAPSHOT, deployment_fixture, emit, event
from configure import prepare
from policy import Policy
from query import LokiClient
from reconcile import compare, read_logs


def local_docker():
    if os.environ.get("DOCKER_HOST") or os.environ.get("DOCKER_CONTEXT"):
        raise RuntimeError("docker_environment_override_refused")
    if not shutil.which("docker"):
        raise RuntimeError("docker_cli_missing")
    context = json.loads(
        subprocess.check_output(["docker", "context", "inspect"], timeout=10)
    )[0]
    endpoint = context["Endpoints"]["docker"]["Host"]
    if not (endpoint.startswith("unix:///") or endpoint.startswith("npipe://")):
        raise RuntimeError("remote_docker_refused")
    system = (
        subprocess.check_output(
            ["docker", "info", "--format", "{{.OSType}}"], timeout=15
        )
        .decode()
        .strip()
    )
    if system != "linux":
        raise RuntimeError("linux_daemon_required")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--run-local-containers", action="store_true")
    p.add_argument("--contract", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = {
        "kind": "dep-b-log-recovery",
        "mode": "linux_containers",
        "status": "not_run",
        "results": [],
        "claims": {
            "nas": False,
            "real_products": False,
            "production": False,
            "retention_deletion": False,
        },
    }
    project, bundle = None, None

    def record(case, status, facts=None):
        report["results"].append({"case": case, "status": status, "facts": facts or {}})
        print(json.dumps({"case": case, "status": status}), flush=True)

    def dc(*cmd):
        return subprocess.run(
            [
                "docker",
                "compose",
                "--project-name",
                project,
                "--file",
                str(bundle / "compose.yaml"),
                *cmd,
            ],
            capture_output=True,
            timeout=600,
            check=True,
        ).stdout

    try:
        if not args.run_local_containers:
            record("linux_stack", "not_run", {"reason": "explicit_opt_in_required"})
            return 2
        try:
            local_docker()
        except Exception as exc:
            record(
                "linux_stack",
                "dependency_missing",
                {
                    "reason": str(exc)
                    if type(exc) is RuntimeError
                    else "docker_unavailable"
                },
            )
            return 2
        runtime = Path(__file__).parent / ".runtime"
        runtime.mkdir(exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="depb-synthetic-", dir=runtime)).resolve()
        (root / "SYNTHETIC_ONLY").write_text("DEP-B generated isolation boundary\n")
        manifest, settings = deployment_fixture(root, args.contract.resolve())
        # Reserve two ephemeral loopback ports before creating the private compose.
        import socket

        reserved = [socket.socket(), socket.socket()]
        for sock in reserved:
            sock.bind(("127.0.0.1", 0))
        settings["ports"] = {
            "grafana": reserved[0].getsockname()[1],
            "query": reserved[1].getsockname()[1],
        }
        bundle = prepare(root, settings, manifest, "bundle", SNAPSHOT, candidate=True)
        project = "depb-" + uuid.uuid4().hex[:12]
        # Only synthetic directories created above. No chown, root helper or host permission escalation.
        for path in [root, *root.rglob("*")]:
            if not path.resolve().is_relative_to(root) or path.is_symlink():
                raise RuntimeError("fixture_path_escape")
            os.chmod(path, 0o777 if path.is_dir() else 0o644)
        for sock in reserved:
            sock.close()
        dc("config", "--quiet")
        dc(
            "run",
            "--rm",
            "obs-vector",
            "validate",
            "--skip-healthchecks",
            "/etc/tianshu/vector.json",
        )
        dc(
            "run",
            "--rm",
            "obs-loki",
            "-config.file=/etc/tianshu/loki.json",
            "-verify-config=true",
        )
        dc(
            "run",
            "--rm",
            "--entrypoint",
            "/bin/promtool",
            "obs-prometheus",
            "check",
            "config",
            "/etc/tianshu/prometheus.json",
        )
        record("native_component_config", "pass")
        dc("up", "-d")
        client = LokiClient(
            f"https://127.0.0.1:{settings['ports']['query']}",
            root / "tls/ca.pem",
            (root / "secrets/query_token").read_text(),
        )
        policy = Policy(SNAPSHOT)
        generated, start = [], time.time_ns() - 60 * 10**9
        roots = {p: root / "logs" / p for p in SNAPSHOT["products"]}

        def emit_batch(index):
            for product, directory in roots.items():
                instance = str(uuid.uuid4())
                batch = [event(n, product, instance) for n in range(1, 101)]
                generated.extend(batch)
                emit(directory / f"synthetic.jsonl.{index}", batch)

        def verify(case):
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                try:
                    rows = client.range('{stack="tianshu"}', start, time.time_ns())
                    retrieved = [policy.line(line.encode() + b"\n") for _, line in rows]
                    landed, invalid, partial = read_logs(roots, policy)
                    result = compare(generated, landed, retrieved, invalid, partial)
                    if result["status"] == "passed":
                        record(case, "pass", result)
                        return
                except Exception:
                    pass
                time.sleep(2)
            raise RuntimeError("reconciliation_timeout_" + case)

        emit_batch(0)
        verify("normal_numbered_events")
        dc("stop", "obs-loki")
        emit_batch(1)
        time.sleep(5)
        dc("restart", "obs-vector")
        dc("start", "obs-loki")
        verify("storage_outage_buffer_restart_recovery")
        dc("stop", "obs-vector")
        emit_batch(2)
        dc("up", "-d", "--force-recreate", "obs-vector")
        verify("collector_down_rotation_recreate_recovery")
        # Poison source, then demonstrate that valid events still pass and canary is absent from query results.
        (roots["platform"] / "canary.jsonl").write_text(
            '{"message":"DEP_B_SECRET_CANARY"}\n'
        )
        time.sleep(4)
        rows = client.range(
            '{stack="tianshu"} |= "DEP_B_SECRET_CANARY"', start, time.time_ns()
        )
        if rows:
            raise RuntimeError("secret_canary_leaked")
        record("secret_canary_central_absent", "pass")
        writer = LokiClient(
            client.url, root / "tls/ca.pem", (root / "secrets/writer_token").read_text()
        )
        if (
            writer.request("/loki/api/v1/labels")[0] != 401
            or client.request("/loki/api/v1/push", "POST", b"{}")[0] != 401
        ):
            raise RuntimeError("query_role_isolation_failed")
        record("query_permissions", "pass")
        # Retention has a 24h minimum. No short run masquerades as TTL deletion validation.
        record(
            "retention_actual_deletion",
            "not_run",
            {"entry": "run_retention.py", "minimum_policy_hours": 24},
        )
        record(
            "filesystem_full_and_grafana_delivery",
            "not_run",
            {
                "entry": "fault-probes.md",
                "requires": "isolated bounded filesystem plus alert receiver",
            },
        )
        report["status"] = "partial"
        report["runtime_root"] = str(root)
        report["binding"] = json.loads((bundle / "binding.json").read_bytes())
        report["claims"]["synthetic_numbered_recovery"] = True
    except Exception as exc:
        record(
            "execution",
            "fail",
            {
                "reason": str(exc)
                if type(exc) is RuntimeError
                else "stack_execution_failed"
            },
        )
        report["status"] = "failed"
    finally:
        if project and bundle:
            try:
                dc("down", "--timeout", "30")
            except Exception:
                record(
                    "cleanup",
                    "fail",
                    {"reason": "isolated_project_cleanup_required", "project": project},
                )
                report["status"] = "failed"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
