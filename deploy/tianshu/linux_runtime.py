"""Local Docker execution; private output, serial builds, project-scoped SIGTERM only."""

import json
import os
import re
import shutil
import subprocess
import sys
import time

from bundle import preflight
from configuration import PORTS
from linux_bootstrap import first_install_container, prepare
from manifest import PRODUCTS, Refused, digest, inside, read_json, require, write_json
from runtime_identity import lifecycle_lease, save


def local_daemon():
    require(sys.platform == "linux", "linux_host_required")
    require(shutil.which("docker") is not None, "docker_executable_missing")
    require(
        not any(
            os.environ.get(k)
            for k in (
                "DOCKER_HOST",
                "DOCKER_CONTEXT",
                "DOCKER_TLS_VERIFY",
                "DOCKER_CERT_PATH",
            )
        ),
        "custom_docker_endpoint_refused",
    )
    value = subprocess.run(
        ["docker", "context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
        capture_output=True,
        timeout=15,
        check=True,
    )
    require(
        value.stdout.strip().startswith(b"unix:///"), "local_docker_endpoint_required"
    )
    endpoint = value.stdout.decode().strip()
    value = subprocess.run(
        ["docker", "--host", endpoint, "info", "--format", "{{.OSType}}"],
        capture_output=True,
        timeout=15,
        check=True,
    )
    require(value.stdout.strip() == b"linux", "linux_daemon_required")
    return endpoint


def image_fact(value):
    require(
        value["Os"] == "linux" and value["Architecture"] == "amd64",
        "image_platform_mismatch",
    )
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", value["Id"]), "image_id_invalid")
    repos = value.get("RepoDigests") or []
    require(
        all(re.fullmatch(r"[^@]+@sha256:[0-9a-f]{64}", s) for s in repos),
        "image_digest_invalid",
    )
    return dict(image_id=value["Id"], repo_digests=repos, platform="linux/amd64")


def owned_container(value, project, root, allowed):
    labels = value["Config"]["Labels"]
    require(
        labels.get("com.docker.compose.project") == project
        and labels.get("com.docker.compose.project.working_dir") == str(root)
        and labels.get("com.docker.compose.service") in allowed,
        "container_ownership_mismatch",
    )
    require(re.fullmatch(r"[0-9a-f]{64}", value["Id"]), "container_id_invalid")
    return labels["com.docker.compose.service"]


class Runner:
    def __init__(self, report, path, endpoint="unix:///var/run/docker.sock"):
        self.report, self.path = report, path
        self.environment = dict(os.environ, DOCKER_HOST=endpoint, DOCKER_CONTEXT="")

    def __call__(self, name, argv, seconds, *, input=None, capture=False):
        start = time.monotonic()
        try:
            result = subprocess.run(
                argv,
                input=input,
                stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=seconds,
                env=self.environment,
            )
            code, status = (
                result.returncode,
                "passed" if result.returncode == 0 else "failed",
            )
        except subprocess.TimeoutExpired:
            code, status = None, "timeout"
        self.report["results"].append(
            dict(
                step=name,
                status=status,
                exit_code=code,
                duration_seconds=round(time.monotonic() - start, 3),
            )
        )
        write_json(self.path, self.report)
        require(status == "passed", "runtime_step_failed_" + name)
        return result.stdout if capture else None


def execute(root, contexts, steps, report, path, *, dialogue=False):
    endpoint = local_daemon()
    with lifecycle_lease(root):
        from linux_validate import plan

        require(not path.exists(), "new_report_under_reports_required")
        try:
            _, locked_steps = plan(root, contexts)
            require(locked_steps == steps, "source_plan_changed")
            leased_execute(
                root,
                contexts,
                steps,
                report,
                path,
                dialogue=dialogue,
                endpoint=endpoint,
            )
            report["result"] = "passed"
        except (
            Refused,
            OSError,
            ValueError,
            KeyError,
            subprocess.SubprocessError,
        ) as error:
            report["result"] = "failed"
            report["error"] = (
                str(error) if isinstance(error, Refused) else "linux_runtime_failed"
            )
            raise
        finally:
            report["unexecuted_dimensions"] = [
                k for k, v in report["dimensions"].items() if v == "not_run"
            ]
            write_json(path, report)


def leased_execute(
    root,
    contexts,
    steps,
    report,
    path,
    *,
    dialogue=False,
    endpoint="unix:///var/run/docker.sock",
):
    core_only = not (root / "observability-release.json").exists()
    preflight(root, runtime=True, core_only=core_only)
    dimensions = report.setdefault("dimensions", {})
    dimensions["linux_permissions"] = "passed"
    meta = read_json(root / "deployment.json")
    require(
        re.fullmatch(r"tianshu-qa-[a-z0-9-]+", meta["project_name"]),
        "isolated_qa_project_required",
    )
    require(
        all(v == "isolated_test" for v in meta["tls_provenance"].values()),
        "synthetic_tls_required",
    )
    for p in PRODUCTS:
        for folder in ("data", "logs"):
            require(
                not any(inside(root, folder + "/" + p).iterdir()),
                "fresh_qa_storage_required",
            )
    require(
        not (root / "reports/execution-attempt.json").exists(),
        "execution_already_attempted",
    )
    run = Runner(report, path, endpoint)
    project = meta["project_name"]
    compose = [
        "docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
    ]

    # Global label query is deliberate: catches even orphans absent from this Compose file.
    def containers():
        raw = run(
            "owned_container_inventory",
            [
                "docker",
                "ps",
                "-aq",
                "--filter",
                "label=com.docker.compose.project=" + project,
            ],
            15,
            capture=True,
        )
        ids = raw.decode().split()
        require(
            all(re.fullmatch("[0-9a-f]{12,64}", cid) for cid in ids),
            "container_id_invalid",
        )
        if not ids:
            return []
        values = json.loads(
            run(
                "owned_container_inspect", ["docker", "inspect", *ids], 15, capture=True
            )
        )
        for value in values:
            owned_container(value, project, root, PRODUCTS)
        return values

    require(not containers(), "existing_project_refused")
    write_json(
        root / "reports/execution-attempt.json",
        dict(state="started", automatic_retry=False),
    )
    observations = {}
    started_ids = set()
    stop_confirmed = False
    try:
        run("compose_config", [*compose, "config", "--quiet"], 30)
        for step in steps:
            tag = step["argv"][step["argv"].index("--tag") + 1]
            existing = run(
                "fresh_image_tag_" + step["id"],
                ["docker", "image", "ls", "--quiet", "--no-trunc", tag],
                15,
                capture=True,
            )
            require(
                not existing.strip(),
                "existing_image_tag_refused_use_new_candidate_tags",
            )
            run(step["id"], step["argv"], 1800)
        composition = read_json(root / "compose.json")
        if (root / "observability/compose.yaml").exists():
            composition["services"].update(
                read_json(root / "observability/compose.yaml")["services"]
            )
        for owner, service in composition["services"].items():
            value = json.loads(
                run(
                    "image_" + owner,
                    ["docker", "image", "inspect", service["image"]],
                    15,
                    capture=True,
                )
            )
            require(len(value) == 1, "image_identity_ambiguous")
            observations[owner] = image_fact(value[0])
            if owner in PRODUCTS:
                require(
                    value[0]["Config"]["User"] == "10001:10001", "image_user_mismatch"
                )
                installed = run(
                    "dependencies_" + owner,
                    [
                        *compose,
                        "run",
                        "--rm",
                        "--no-deps",
                        "-T",
                        "--entrypoint",
                        "python",
                        owner,
                        "-c",
                        (root / "tools/dependency_probe.py").read_text(),
                        owner,
                    ],
                    60,
                    capture=True,
                )
                report.setdefault("installed_dependencies", {})[owner] = json.loads(
                    installed
                )
        publication, password = prepare(root, dialogue)
        dimensions["linux_images"] = "passed"
        dimensions["installed_dependencies"] = "passed"
        first_install_container(root, compose, run, publication)
        for action, backup in (
            ("migrate-profiles", "first-install.pre-profiles.sqlite"),
            ("migrate-sources", "first-install.pre-sources.sqlite"),
        ):
            run(
                "memory_" + action,
                [
                    *compose,
                    "run",
                    "--rm",
                    "--no-deps",
                    "-T",
                    "memory",
                    "--config",
                    "/etc/tianshu/settings.json",
                    action,
                    "--backup",
                    "/srv/tianshu/" + backup,
                ],
                60,
            )
        preflight(root, runtime=True, core_only=core_only)
        run(
            "start_liveness",
            [
                *compose,
                "up",
                "--detach",
                "--no-build",
                "--pull",
                "never",
                "--wait",
                "--wait-timeout",
                "120",
            ],
            180,
        )
        found = containers()
        require(len(found) == 4, "four_runtime_containers_required")
        require(
            {v["Config"]["Labels"]["com.docker.compose.service"] for v in found}
            == set(PRODUCTS),
            "four_unique_runtime_services_required",
        )
        started_ids = {v["Id"] for v in found}
        for value in found:
            owner = owned_container(value, project, root, PRODUCTS)
            require(
                value["Image"] == observations[owner]["image_id"],
                "running_image_changed",
            )
            uid = json.loads(
                run(
                    "runtime_uid_" + owner,
                    [
                        "docker",
                        "exec",
                        value["Id"],
                        "python",
                        "-c",
                        "import os,json;print(json.dumps([os.getuid(),os.getgid()]))",
                    ],
                    15,
                    capture=True,
                )
            )
            require(uid == [10001, 10001], "runtime_uid_mismatch")
            observations[owner].update(
                uid=uid[0], gid=uid[1], container_id=value["Id"], status="observed"
            )
            script = (root / "tools/container_probe.py").read_text()
            run(
                "authenticated_ready_" + owner,
                [
                    "docker",
                    "exec",
                    value["Id"],
                    "python",
                    "-c",
                    script,
                    "ready",
                    owner,
                    str(PORTS[owner]),
                ],
                20,
            )
        dimensions["runtime_uid_gid"] = "passed"
        dimensions["authenticated_readiness"] = "passed"
        if dialogue:
            fixture = (root / "tools/synthetic_model.py").read_text()
            run(
                "start_synthetic_model",
                [*compose, "exec", "-T", "-d", "gateway", "python", "-c", fixture],
                15,
            )
            script = (root / "tools/container_probe.py").read_text()
            run(
                "synthetic_model_ready",
                [
                    *compose,
                    "exec",
                    "-T",
                    "gateway",
                    "python",
                    "-c",
                    script,
                    "model-ready",
                ],
                20,
            )
            run(
                "synthetic_dialogue",
                [
                    *compose,
                    "exec",
                    "-T",
                    "platform",
                    "python",
                    "-c",
                    script,
                    "dialogue",
                ],
                90,
                input=json.dumps(dict(password=password)).encode(),
            )
            dimensions["synthetic_dialogue"] = "passed"
        run(
            "platform_preflight",
            [
                *compose,
                "exec",
                "-T",
                "platform",
                "python",
                "-m",
                "services.platform",
                "--settings",
                "/etc/tianshu/settings.json",
                "preflight",
            ],
            30,
        )
    finally:
        # Never Compose down/prune, and never SIGKILL fallback. Retain state and inspectable containers.
        try:
            for value in containers():
                if value["State"]["Running"]:
                    run(
                        "sigterm_" + value["Id"][:12],
                        ["docker", "kill", "--signal=SIGTERM", value["Id"]],
                        15,
                    )
            deadline = time.monotonic() + 45
            while True:
                values = containers()
                require(
                    started_ids <= {v["Id"] for v in values},
                    "runtime_container_disappeared",
                )
                if not any(v["State"]["Running"] for v in values):
                    stop_confirmed = not any(
                        r["status"] == "timeout" for r in report["results"]
                    )
                    report["stop_exit_codes"] = {
                        v["Config"]["Labels"]["com.docker.compose.service"]: v["State"][
                            "ExitCode"
                        ]
                        for v in values
                    }
                    break
                require(
                    time.monotonic() < deadline, "stop_unconfirmed_no_force_fallback"
                )
                time.sleep(1)
        except (Refused, OSError, ValueError, subprocess.SubprocessError):
            report["stop_error"] = "stop_unconfirmed_no_force_fallback"
        report["stop_confirmed"] = stop_confirmed
        try:
            save(
                root,
                observed=observations,
                state="stopped" if stop_confirmed else "stop_unconfirmed",
                linux_stat=True,
            )
            report["runtime_identity_sha256"] = digest(
                (root / "reports/runtime-identity.json").read_bytes()
            )
        except (Refused, OSError, ValueError):
            report["identity_error"] = "incomplete_identity_retained_for_review"
        write_json(path, report)
    require(stop_confirmed, "stop_unconfirmed_no_force_fallback")
    require("identity_error" not in report, "runtime_identity_not_saved")
    require(
        all(code == 0 for code in report.get("stop_exit_codes", {}).values()),
        "abnormal_product_exit",
    )
    dimensions["core_stop_observed"] = "passed"
