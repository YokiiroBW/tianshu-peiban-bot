"""Pinned local Docker identities; graceful SIGTERM only, with no forced-kill fallback."""

import json
import os
import re
import subprocess
import tempfile
import time
from pathlib import Path

from .safety import child, require, safe_path


class DockerCLI:
    def __init__(self, executable, endpoint, budget):
        self.executable = str(safe_path(executable))
        require(
            endpoint == "unix:///var/run/docker.sock", "local_linux_docker_required"
        )
        self.endpoint, self.budget = endpoint, budget

    def run(self, *args):
        self.budget.check()
        # Never use a caller's remote DOCKER_HOST/context or execute a shell.
        env = {
            k: v
            for k, v in os.environ.items()
            if not k.startswith(("DOCKER_", "COMPOSE_"))
        }
        with tempfile.TemporaryFile() as output:
            process = subprocess.Popen(
                [self.executable, "--host", self.endpoint, *args],
                stdin=subprocess.DEVNULL,
                stdout=output,
                stderr=subprocess.DEVNULL,
                env=env,
            )
            try:
                while process.poll() is None:
                    self.budget.check()
                    require(
                        os.fstat(output.fileno()).st_size <= 4 * 1024**2,
                        "docker_output_limit",
                    )
                    time.sleep(0.02)
                require(process.returncode == 0, "docker_command_failed")
                require(
                    os.fstat(output.fileno()).st_size <= 4 * 1024**2,
                    "docker_output_limit",
                )
                output.seek(0)
                return output.read().decode("utf-8")
            finally:
                if process.poll() is None:
                    # Only the CLI child belongs to us. Container writers are NEVER killed.
                    process.kill()
                    process.wait(timeout=5)


class ComposeBackend:
    def __init__(self, directory, home, binding, budget, docker):
        self.directory, self.home, self.binding = directory, home, binding
        self.budget, self.docker = budget, docker
        self.identities, self.current = {}, {}
        self.images = {}
        self.restart_disabled = False

    def inspect(self):
        ids = self.docker.run(
            "container", "ls", "--all", "--quiet", "--no-trunc"
        ).split()
        require(
            len(ids) <= 1000 and all(re.fullmatch(r"[0-9a-f]{64}", v) for v in ids),
            "invalid_container_inventory",
        )
        containers = []
        for index in range(0, len(ids), 25):
            containers.extend(
                json.loads(
                    self.docker.run("container", "inspect", *ids[index : index + 25])
                )
            )
        observed = {}
        prefix = "com.docker.compose."
        for container in containers:
            labels = container["Config"].get("Labels") or {}
            belongs = labels.get(prefix + "project") in {
                s["project"] for s in self.binding["services"].values()
            }
            for mount in container["Mounts"]:
                if mount["Type"] == "bind":
                    path = Path(mount["Source"])
                    overlap = path.is_relative_to(
                        self.directory
                    ) or self.directory.is_relative_to(path)
                    require(belongs or not overlap, "foreign_container_mount_overlap")
            if not belongs:
                continue
            service = labels.get(prefix + "service")
            require(
                service in self.binding["services"] and service not in observed,
                "unexpected_project_container",
            )
            require(
                labels.get(prefix + "oneoff", "False").lower() == "false",
                "unexpected_project_container",
            )
            expected = self.binding["services"][service]
            require(
                labels.get(prefix + "project") == expected["project"],
                "compose_project_mismatch",
            )
            require(
                labels.get(prefix + "project.working_dir")
                == expected["compose_directory"],
                "compose_directory_mismatch",
            )
            expected_files = [
                str(child(self.directory, name)) for name in expected["compose_files"]
            ]
            require(
                labels.get(prefix + "project.config_files", "").split(",")
                == expected_files,
                "compose_files_mismatch",
            )
            require(
                container["Config"]["Image"] == expected["image"],
                "container_image_mismatch",
            )
            # RepoDigest identifies a registry manifest, Image identifies actual local bytes.
            if expected["image"] not in self.images:
                image = json.loads(
                    self.docker.run("image", "inspect", expected["image"])
                )
                require(len(image) == 1, "container_image_mismatch")
                self.images[expected["image"]] = image[0]["Id"]
            require(
                self.images[expected["image"]] == container["Image"],
                "container_image_mismatch",
            )
            actual = []
            for mount in container["Mounts"]:
                if mount["Type"] == "tmpfs":
                    continue
                require(mount["Type"] == "bind", "unregistered_container_volume")
                path = safe_path(mount["Source"])
                require(
                    path.is_relative_to(self.directory) and path != self.directory,
                    "mount_outside_deployment",
                )
                actual.append(
                    {
                        "source": path.relative_to(self.directory).as_posix(),
                        "target": mount["Destination"],
                        "read_only": not mount["RW"],
                    }
                )
            require(
                sorted(actual, key=lambda m: m["target"]) == expected["mounts"],
                "container_mount_mismatch",
            )
            host = container["HostConfig"]
            require(
                not host.get("Privileged")
                and not host.get("PidMode")
                and not host.get("Devices")
                and not host.get("VolumesFrom"),
                "unbounded_container_ownership",
            )
            require(
                host["RestartPolicy"]["Name"]
                in ({"no"} if self.restart_disabled else {"no", "unless-stopped"}),
                "restart_policy_mismatch",
            )
            identity = (container["Id"], container["Created"], container["Image"])
            if service in self.identities:
                require(self.identities[service] == identity, "runtime_owner_changed")
            else:
                self.identities[service] = identity
            observed[service] = container
        require(
            set(observed) == set(self.binding["services"]), "missing_project_container"
        )
        self.current = observed

    def disable_restart(self):
        for identity in self.identities.values():
            self.docker.run("container", "update", "--restart=no", identity[0])
        self.restart_disabled = True
        self.inspect()

    def stop(self, service):
        self.inspect()
        state = self.current[service]["State"]
        require(
            not state["Paused"] and not state["Restarting"], "container_not_stoppable"
        )
        if state["Running"]:
            self.docker.run(
                "container", "kill", "--signal=SIGTERM", self.identities[service][0]
            )

    def stopped(self, service):
        self.inspect()
        state = self.current[service]["State"]
        if state["Running"] or state["Restarting"] or state["Paused"]:
            return False
        require(
            state["Status"] == "exited"
            and not state["OOMKilled"]
            and not state["Dead"]
            and state["ExitCode"] == 0
            and not state["Error"],
            "owner_exit_unconfirmed",
        )
        return True

    def assert_stopped(self):
        self.inspect()
        for container in self.current.values():
            state = container["State"]
            require(
                not state["Running"]
                and not state["Restarting"]
                and not state["Paused"],
                "residual_writer",
            )
            require(
                state["Status"] == "exited"
                and not state["OOMKilled"]
                and not state["Dead"]
                and state["ExitCode"] == 0
                and not state["Error"],
                "owner_exit_unconfirmed",
            )

    def reserve(self):
        self.assert_stopped()

    def close(self):
        pass
