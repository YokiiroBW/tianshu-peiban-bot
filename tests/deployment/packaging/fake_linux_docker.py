"""Explicit Docker simulator for lifecycle fault injection; never container evidence."""

import contextlib
import copy
import itertools
import json
import subprocess
from unittest.mock import patch
from pathlib import Path

from manifest import read_json


@contextlib.contextmanager
def clock_patches():
    ticks = itertools.count()
    with (
        patch("linux_lifecycle.time.monotonic", side_effect=lambda: next(ticks) * 0.5),
        patch("linux_lifecycle.time.sleep"),
    ):
        yield


class Docker:
    def __init__(self, root, receipt, **faults):
        self.root, self.receipt, self.faults = root, receipt, faults
        self.calls, self.active, self.specs = [], {}, {}
        self.outputs = {}
        self.serial, self.stopping, self.signaled = 0, False, False
        self.injected = False
        doc = read_json(root / "compose.json")
        self.tags = {s["image"]: p for p, s in doc["services"].items()}
        self.images = {
            p: "sha256:" + str(i + 1) * 64 for i, p in enumerate(self.tags.values())
        }

    def __call__(self, argv, **kwargs):
        self.calls.append(argv)
        raw, code = b"", 0
        if argv[:3] == ["docker", "ps", "-aq"]:
            if self.stopping and not self.injected:
                self.injected = True
                if self.faults.get("unknown"):
                    value = copy.deepcopy(next(iter(self.active.values())))
                    value["Id"] = "9" * 64
                    self.active[value["Id"]] = value
                elif self.faults.get("replace"):
                    old, value = next(iter(self.active.items()))
                    del self.active[old]
                    value["Id"] = "9" * 64
                    self.active[value["Id"]] = value
                elif self.faults.get("image"):
                    next(iter(self.active.values()))["Image"] = "sha256:" + "9" * 64
                elif self.faults.get("owner"):
                    next(iter(self.active.values()))["Config"]["Labels"][
                        "com.docker.compose.service"
                    ] = "platform"
                    # Ensure different from prior identity even if platform was first.
                    next(iter(self.active.values()))["Config"]["Labels"][
                        "com.docker.compose.service"
                    ] = "gateway"
            if self.signaled and self.faults.get("restart"):
                value = next(iter(self.active.values()))
                value["RestartCount"] += 1
            raw = "\n".join(self.active).encode()
        elif argv[:2] == ["docker", "inspect"]:
            raw = json.dumps([self.active[cid] for cid in argv[2:]]).encode()
        elif argv[:3] == ["docker", "image", "inspect"]:
            raw = json.dumps(
                [
                    dict(
                        Id=self.images[self.tags[argv[-1]]],
                        Os="linux",
                        Architecture="amd64",
                        RepoDigests=[],
                        Config=dict(User="10001:10001"),
                    )
                ]
            ).encode()
        elif "create" in argv:
            path = Path(argv[argv.index("-f") + 1])
            doc = read_json(path)
            core = path.name == "compose.json"
            for owner, spec in doc["services"].items():
                self.serial += 1
                cid = f"{self.serial:064x}"
                image = (
                    spec["image"]
                    if spec["image"].startswith("sha256:")
                    else self.images[owner]
                )
                labels = {
                    **spec.get("labels", {}),
                    "com.docker.compose.project": doc.get(
                        "name", read_json(self.root / "deployment.json")["project_name"]
                    ),
                    "com.docker.compose.project.working_dir": str(self.root),
                    "com.docker.compose.project.config_files": str(path),
                    "com.docker.compose.service": owner,
                }
                name = spec.get(
                    "container_name",
                    labels["com.docker.compose.project"] + "-" + owner + "-1",
                )
                self.active[cid] = dict(
                    Id=cid,
                    Image=image,
                    Name="/" + name,
                    Config=dict(Labels=labels),
                    HostConfig=dict(
                        RestartPolicy=dict(Name="unless-stopped" if core else "no")
                    ),
                    RestartCount=0,
                    State=dict(
                        Status="created",
                        Running=False,
                        Restarting=False,
                        ExitCode=0,
                        OOMKilled=False,
                        Error="",
                        FinishedAt="zero",
                    ),
                )
                self.specs[cid] = spec
                if core and self.faults.get("partial_create"):
                    return subprocess.CompletedProcess(argv, 1, b"", b"")
        elif argv[:3] == ["docker", "start", "-ai"]:
            cid = argv[-1]
            command = self.specs[cid]["command"]
            value = self.active[cid]
            value["State"].update(Status="exited", FinishedAt="oneoff-finished")
            if command[-1] == "issue":
                if self.faults.get("oneoff_timeout"):
                    value["State"].update(Status="running", Running=True)
                    raise subprocess.TimeoutExpired(argv, 35)
                if self.faults.get("fail_issue"):
                    code = 1
                    value["State"]["ExitCode"] = 1
                raw = json.dumps(self.receipt).encode()
            elif command[-1] == "publish":
                raw = b'{"status":"published"}'
            elif "distributions" in " ".join(command):
                raw = b'{"interpreter":"/synthetic/python","distributions":[["synthetic","1"]]}'
            self.outputs[cid] = raw
            if self.faults.get("empty_attach"):
                raw = b""
        elif argv[:2] == ["docker", "logs"]:
            raw = self.outputs[argv[-1]]
            if self.faults.get("invalid_logs"):
                raw = b"invalid synthetic output"
            elif self.faults.get("empty_logs"):
                raw = b""
        elif argv[:2] == ["docker", "start"]:
            for cid in argv[2:]:
                self.active[cid]["State"].update(
                    Status="running", Running=True, Health=dict(Status="healthy")
                )
                if self.faults.get("partial_start"):
                    return subprocess.CompletedProcess(argv, 1, b"", b"")
        elif argv[:2] == ["docker", "rm"]:
            del self.active[argv[-1]]
        elif argv[:2] == ["docker", "update"]:
            if not self.faults.get("ignore_update"):
                for cid in argv[3:]:
                    self.active[cid]["HostConfig"]["RestartPolicy"]["Name"] = "no"
        elif argv[:2] == ["docker", "kill"]:
            self.signaled = True
            self.active[argv[-1]]["State"].update(
                Status="exited",
                Running=False,
                FinishedAt="core-finished",
                ExitCode=self.faults.get("exit_code", 0),
                OOMKilled=self.faults.get("oom", False),
            )
        elif argv[:2] == ["docker", "exec"]:
            raw = b"[10001,10001]"
        elif argv[-1] == "preflight":
            self.stopping = True
        return subprocess.CompletedProcess(argv, code, raw, b"")
