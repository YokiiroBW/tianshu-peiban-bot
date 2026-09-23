"""Fixed-ID TERM/poll and stopped-only Vector replacement. Never escalates signals."""

import json
import time

from acceptance import require


class OwnedContainers:
    def __init__(self, docker, binding, evidence, timeout=90):
        self.docker, self.binding, self.evidence = docker, binding, evidence
        self.timeout = timeout
        self.pins = {}
        self.signalled = set()
        self.failed = False
        self.serial = 0

    def call(self, args, deadline):
        remaining = deadline - time.monotonic()
        require(remaining > 0, "stop_unconfirmed")
        return self.docker.call(args, timeout=remaining)

    def rows(self, deadline):
        ids = (
            self.call(
                [
                    "ps",
                    "-aq",
                    "--filter",
                    "label=com.docker.compose.project=" + self.binding.obs_project,
                ],
                deadline,
            )
            .decode()
            .split()
        )
        return json.loads(self.call(["inspect", *ids], deadline)) if ids else []

    def validate(self, rows, adopt=()):
        seen = {}
        for row in rows:
            owner = (row["Config"].get("Labels") or {}).get(
                "com.docker.compose.service"
            )
            require(
                owner in self.binding.stack["services"] and owner not in seen,
                "stop_owner_mismatch",
            )
            self.binding.check_container(
                row,
                self.binding.obs_project,
                owner,
                self.binding.stack["services"][owner],
            )
            require(
                row["Image"] == self.binding.document["services"][owner]["image_id"],
                "stop_image_mismatch",
            )
            require(
                owner in adopt or self.pins.get(owner) == row["Id"],
                "stop_container_id_changed",
            )
            seen[owner] = row
        require(
            all(owner in seen for owner in self.pins if owner not in adopt),
            "stop_container_missing",
        )
        return seen

    def facts(self, rows):
        return [
            {
                "owner": (row["Config"].get("Labels") or {}).get(
                    "com.docker.compose.service"
                )
                if (row["Config"].get("Labels") or {}).get("com.docker.compose.service")
                in self.binding.stack["services"]
                else "unrecognized",
                "container_id": row["Id"],
                "image_id": row["Image"],
                "status": row["State"].get("Status"),
                "running": row["State"].get("Running"),
                "exit_code": row["State"].get("ExitCode"),
                "oom_killed": row["State"].get("OOMKilled"),
                "error_present": bool(row["State"].get("Error")),
                "restart_policy": row["HostConfig"]
                .get("RestartPolicy", {})
                .get("Name"),
            }
            for row in rows
        ]

    def record(self, operation, value):
        self.serial += 1
        return self.evidence.artifact(
            f"lifecycle-{self.serial:02d}-{operation}.json", value
        )

    def capture_initial(self):
        """Called exactly once after fresh-project up, including partial up failure."""
        require(not self.pins, "initial_ids_already_captured")
        rows = self.rows(time.monotonic() + self.timeout)
        try:
            seen = self.validate(rows, adopt=self.binding.stack["services"])
            self.pins = {owner: row["Id"] for owner, row in seen.items()}
        finally:
            self.record(
                "initial",
                {
                    "containers": self.facts(rows),
                    "expected_owners": sorted(self.binding.stack["services"]),
                    "complete": len(rows) == len(self.binding.stack["services"]),
                },
            )

    @staticmethod
    def normal(row):
        state = row["State"]
        return (
            state.get("Status") == "exited"
            and state.get("Running") is False
            and state.get("ExitCode") == 0
            and not state.get("OOMKilled")
            and not state.get("Error")
            and row["HostConfig"].get("RestartPolicy", {}).get("Name") == "no"
        )

    def stop(self, owners, cleanup=False):
        require(cleanup or not self.failed, "previous_stop_unconfirmed")
        deadline = time.monotonic() + self.timeout
        proof = {
            "status": "stop_unconfirmed",
            "signal": "SIGTERM",
            "sigkill_fallback": False,
            "owners": sorted(owners),
            "observations": [],
            "signals_sent": [],
            "signal_attempts": [],
        }
        try:
            require(
                all(owner in self.pins for owner in owners), "stop_owner_not_pinned"
            )
            rows = self.rows(deadline)
            proof["observations"].append(self.facts(rows))
            self.validate(rows)
            # Fresh inspection of the fixed ID immediately before each TERM.
            for owner in owners:
                ident = self.pins[owner]
                row = json.loads(self.call(["inspect", ident], deadline))[0]
                self.check_one(owner, row)
                proof["observations"].append(self.facts([row]))
                # TERM alone must not race an always/unless-stopped restart loop.
                if row["HostConfig"].get("RestartPolicy", {}).get("Name") != "no":
                    self.call(["update", "--restart=no", ident], deadline)
                    row = json.loads(self.call(["inspect", ident], deadline))[0]
                    self.check_one(owner, row)
                    proof["observations"].append(self.facts([row]))
                    require(
                        row["HostConfig"].get("RestartPolicy", {}).get("Name") == "no",
                        "restart_policy_not_disabled",
                    )
                if row["State"].get("Running") and ident not in self.signalled:
                    self.signalled.add(ident)
                    proof["signal_attempts"].append(ident)
                    self.call(["kill", "--signal", "TERM", ident], deadline)
                    proof["signals_sent"].append(ident)
            while True:
                rows = self.rows(deadline)
                proof["observations"].append(self.facts(rows))
                seen = self.validate(rows)
                selected = [seen[owner] for owner in owners]
                require(
                    not any(
                        not r["State"].get("Running") and not self.normal(r)
                        for r in selected
                    ),
                    "abnormal_container_exit",
                )
                if all(self.normal(row) for row in selected):
                    proof["status"] = "normal_exit_confirmed"
                    return
                time.sleep(min(0.1, max(0, deadline - time.monotonic())))
        except Exception:
            self.failed = True
            raise
        finally:
            self.record("stop", proof)

    def check_one(self, owner, row):
        require(row["Id"] == self.pins[owner], "stop_container_id_changed")
        self.binding.check_container(
            row, self.binding.obs_project, owner, self.binding.stack["services"][owner]
        )
        require(
            row["Image"] == self.binding.document["services"][owner]["image_id"],
            "stop_image_mismatch",
        )

    def start(self, owner):
        require(not self.failed, "previous_stop_unconfirmed")
        deadline = time.monotonic() + self.timeout
        seen = self.validate(self.rows(deadline))
        require(self.normal(seen[owner]), "normal_exit_required_before_start")
        self.call(["start", self.pins[owner]], deadline)
        self.signalled.discard(self.pins[owner])
        rows = self.rows(deadline)
        self.validate(rows)
        self.record("start", {"owner": owner, "containers": self.facts(rows)})

    def recreate_vector(self):
        require(not self.failed, "previous_stop_unconfirmed")
        owner = "obs-vector"
        self.stop([owner])
        deadline = time.monotonic() + self.timeout
        rows = self.rows(deadline)
        seen = self.validate(rows)
        require(self.normal(seen[owner]), "normal_exit_required_before_recreate")
        reference = self.binding.stack["services"][owner]["image"]
        image = json.loads(self.call(["image", "inspect", reference], deadline))[0]
        require(
            image["Id"] == self.binding.document["services"][owner]["image_id"],
            "recreate_image_changed",
        )
        old_id = self.pins[owner]
        proof = {
            "old_container_id": old_id,
            "pre_remove": self.facts([seen[owner]]),
            "force_remove": False,
            "delete_volumes": False,
            "status": "recreate_unconfirmed",
        }
        try:
            # Non-force rm refuses a concurrently restarted container. No -v or source deletion.
            self.call(["rm", old_id], deadline)
            del self.pins[owner]
            try:
                self.docker.compose(
                    self.binding.obs_root,
                    self.binding.obs_project,
                    "up",
                    "-d",
                    "--pull",
                    "never",
                    "--no-build",
                    "--no-deps",
                    "--no-recreate",
                    owner,
                )
            finally:
                rows = self.rows(time.monotonic() + self.timeout)
                proof["post_create"] = self.facts(rows)
                seen = self.validate(rows, adopt=[owner])
                if owner in seen:
                    self.pins[owner] = seen[owner]["Id"]
            require(
                owner in self.pins and seen[owner]["State"].get("Running") is True,
                "recreate_unconfirmed",
            )
            proof["status"] = "recreated"
        except Exception:
            self.failed = True
            raise
        finally:
            self.record("recreate", proof)
