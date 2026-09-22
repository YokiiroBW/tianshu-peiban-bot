"""Create/register/start by immutable ID. Labels alone never confer stop ownership."""

import copy
import os
import secrets
import time

from manifest import Refused, digest, require, write_json
from resource_profile import container_check


class Lifecycle:
    def __init__(self, root, project, compose, run, inventory, report):
        self.root, self.project, self.compose = root, project, compose
        self.run, self.inventory, self.report = run, inventory, report
        self.nonce = secrets.token_hex(12)
        self.registered = {}
        self.uncertain_create = False
        report["owned_containers"] = self.registered
        self.serial = 0

    def fact(self, value):
        labels = value["Config"]["Labels"]
        return dict(
            id=value["Id"],
            owner=labels.get("com.docker.compose.service"),
            image=value["Image"],
            name=value["Name"],
            nonce=labels.get("org.tianshu.execution"),
            project=labels.get("com.docker.compose.project"),
            directory=labels.get("com.docker.compose.project.working_dir"),
            config_files=labels.get("com.docker.compose.project.config_files"),
        )

    def check(self):
        values = self.inventory()
        ids = [v["Id"] for v in values]
        require(
            len(ids) == len(set(ids)) and set(ids) == set(self.registered),
            "owned_container_set_changed",
        )
        for value in values:
            owner = value["Config"]["Labels"].get("com.docker.compose.service")
            if owner in self.compose["services"]:
                container_check(self.compose["services"][owner], value)
            require(
                self.fact(value) == self.registered[value["Id"]],
                "owned_container_identity_changed",
            )
        return values

    def create(self, document, owners, images, purpose):
        self.check()
        self.serial += 1
        document = copy.deepcopy(document)
        core = purpose == "core"
        path = (
            self.root / "compose.json"
            if core
            else self.root / "reports" / f"lifecycle-{self.serial}.json"
        )
        expected = {}
        for owner in owners:
            service = document["services"][owner]
            name = (
                f"{self.project}-{owner}-1"
                if core
                else f"{self.project}-{self.nonce}-{self.serial}-{owner}"
            )
            if not core:
                service["container_name"] = name
                service.setdefault("labels", {})["org.tianshu.execution"] = self.nonce
                # Resource identity is supplied by the just-inspected immutable image, never tag lookup.
                service["image"] = images[owner]
            expected[owner] = dict(
                owner=owner,
                image=images[owner],
                name="/" + name,
                nonce=None if core else self.nonce,
                project=self.project,
                directory=str(self.root),
                config_files=str(path),
            )
        if not core:
            write_json(path, document)
        self.report.setdefault("lifecycle_compose_sha256", {})[path.name] = digest(
            path.read_bytes()
        )
        command = [
            "docker",
            "compose",
            "-p",
            self.project,
            "--project-directory",
            str(self.root),
            "-f",
            str(path),
        ]
        failed = False
        try:
            self.run(
                "create_" + purpose,
                [*command, "create", "--no-build", "--pull", "never", *owners],
                60,
            )
        except (Refused, OSError):
            failed = True
            self.uncertain_create = True
            raise
        finally:
            # create never starts anything. Even a partial failed create can be registered
            # if every new ID matches the predeclared unique name/nonce/owner/image.
            values = self.inventory()
            additions = []
            seen = set()
            for value in values:
                fact = self.fact(value)
                if value["Id"] in self.registered:
                    require(
                        fact == self.registered[value["Id"]],
                        "owned_container_identity_changed",
                    )
                    continue
                owner = fact["owner"]
                require(
                    owner in expected and owner not in seen,
                    "unexpected_created_container",
                )
                container_check(document["services"][owner], value)
                require(
                    {k: v for k, v in fact.items() if k != "id"} == expected[owner],
                    "unexpected_created_container",
                )
                require(
                    value["State"]["Status"] == "created"
                    and not value["State"]["Running"],
                    "created_container_already_started",
                )
                seen.add(owner)
                additions.append(fact)
            require(
                set(self.registered) <= {v["Id"] for v in values},
                "owned_container_set_changed",
            )
            for fact in additions:
                self.registered[fact["id"]] = fact
            if not failed:
                require(seen == set(owners), "created_container_missing")
        return [fact["id"] for fact in additions]

    def oneoff(self, name, argv, seconds, **kwargs):
        index = argv.index("run")
        tail = argv[index + 1 :]
        entry = None
        if "--entrypoint" in tail:
            pos = tail.index("--entrypoint")
            entry = [tail[pos + 1]]
            del tail[pos : pos + 2]
        tail = [v for v in tail if v not in ("--rm", "--no-deps", "-T")]
        owner, args = tail[0], tail[1:]
        service = copy.deepcopy(self.compose["services"][owner])
        service.pop("depends_on", None)
        service.pop("networks", None)
        service.pop("ports", None)
        service["network_mode"] = "none"
        service["stdin_open"] = True
        service["restart"] = "no"
        service["healthcheck"] = {"disable": True}
        if entry is not None:
            service["entrypoint"] = entry
        service["command"] = args
        images = self.report["product_image_ids"]
        ids = self.create({"services": {owner: service}}, [owner], images, name)
        self.check()
        # No --rm: stdout failure/timeout cannot erase the identity or exit evidence.
        output = self.run(
            name,
            ["docker", "start", "-ai", ids[0]],
            seconds,
            input=kwargs.get("input", b""),
            capture=kwargs.get("capture", False),
        )
        value = next(v for v in self.check() if v["Id"] == ids[0])
        require(self.clean_exit(value), "oneoff_exit_unconfirmed")
        self.retire_oneoff(ids[0])
        return output

    def retire_oneoff(self, cid, *, stable_seconds=3):
        baseline, signature = None, None
        start = time.monotonic()
        while True:
            value = next(v for v in self.check() if v["Id"] == cid)
            require(
                self.clean_exit(value)
                and value["HostConfig"]["RestartPolicy"]["Name"] == "no",
                "oneoff_exit_unconfirmed",
            )
            current = (value["RestartCount"], value["State"].get("FinishedAt"))
            if baseline is None:
                baseline, signature = current, copy.deepcopy(value["State"])
            require(current == baseline and baseline[0] == 0, "oneoff_restarted")
            if time.monotonic() - start >= stable_seconds:
                break
            time.sleep(0.2)
        # Preserve the immutable identity and complete exit-state evidence before exact rm.
        self.report.setdefault("completed_oneoffs", {})[cid] = {
            **self.registered[cid],
            "state": signature,
            "restart_count": baseline[0],
            "stable_seconds": stable_seconds,
        }
        write_json(self.run.path, self.report)
        with self.run.path.open("r+b") as evidence:
            os.fsync(evidence.fileno())
        self.run("retire_oneoff_" + cid[:12], ["docker", "rm", cid], 15)
        del self.registered[cid]
        self.check()

    @staticmethod
    def clean_exit(value):
        state = value["State"]
        return (
            state["Status"] == "exited"
            and state["Running"] is False
            and state["Restarting"] is False
            and state["ExitCode"] == 0
            and state["OOMKilled"] is False
            and state.get("Error", "") == ""
        )

    def stop(self, *, timeout=45, stable_seconds=3):
        # Whole-set validation precedes *any* mutation. An extra same-label owner is not ours.
        values = self.check()
        ids = list(self.registered)
        if ids:
            self.run(
                "disable_owned_restart", ["docker", "update", "--restart=no", *ids], 15
            )
        values = self.check()
        for value in values:
            require(
                value["HostConfig"]["RestartPolicy"]["Name"] == "no",
                "restart_policy_not_disabled",
            )
        baseline = {v["Id"]: v["RestartCount"] for v in values}
        for cid in ids:
            # Recheck the entire set immediately before each signal; never signal an enumeration newcomer.
            values = self.check()
            require(
                all(v["HostConfig"]["RestartPolicy"]["Name"] == "no" for v in values),
                "restart_policy_not_disabled",
            )
            value = next(v for v in values if v["Id"] == cid)
            if value["State"]["Running"]:
                self.run(
                    "sigterm_" + cid[:12],
                    ["docker", "kill", "--signal=SIGTERM", cid],
                    15,
                )
        deadline, stable_since, terminal = time.monotonic() + timeout, None, None
        while True:
            values = self.check()
            require(
                all(v["HostConfig"]["RestartPolicy"]["Name"] == "no" for v in values),
                "restart_policy_not_disabled",
            )
            require(
                all(v["RestartCount"] == baseline[v["Id"]] for v in values),
                "container_restarted_during_stop",
            )
            stopped = all(
                not v["State"]["Running"] and not v["State"]["Restarting"]
                for v in values
            )
            if stopped:
                require(
                    all(
                        v["State"]["OOMKilled"] is False
                        and v["State"]["ExitCode"] == 0
                        and v["RestartCount"] == 0
                        for v in values
                    ),
                    "abnormal_product_exit",
                )
                require(
                    all(
                        self.clean_exit(v) or v["State"]["Status"] == "created"
                        for v in values
                    ),
                    "abnormal_product_exit",
                )
                signature = sorted(
                    (
                        v["Id"],
                        v["State"]["Status"],
                        v["State"].get("FinishedAt"),
                        v["RestartCount"],
                    )
                    for v in values
                )
                if stable_since is None:
                    stable_since, terminal = time.monotonic(), signature
                require(signature == terminal, "container_terminal_state_changed")
                if time.monotonic() - stable_since >= stable_seconds:
                    require(
                        not self.uncertain_create, "uncertain_create_requires_review"
                    )
                    self.report["stop_exit_codes"] = {
                        v["Id"]: v["State"]["ExitCode"] for v in values
                    }
                    return
            else:
                require(stable_since is None, "container_restarted_during_stop")
            require(time.monotonic() < deadline, "stop_unconfirmed_no_force_fallback")
            time.sleep(0.2)
