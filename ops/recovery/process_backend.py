"""Test-process lifecycle adapter. This is deliberately not a product stop protocol."""

from contextlib import ExitStack

from .process_identity import ProcessGone, ProcessIdentity
from .safety import (
    canonical,
    child,
    digest,
    files,
    lease,
    read_json,
    require,
    write_new,
)


class ProcessBackend:
    def __init__(self, directory, home, binding, budget):
        self.home, self.binding, self.budget = home, binding, budget
        self.processes, self.owners, self.locks = {}, {}, ExitStack()
        self.binding_hash = digest(canonical(binding))

    def inspect(self):
        names = files(child(self.home, "owners"))
        allowed = {
            service + suffix
            for service in self.binding["services"]
            for suffix in (".json", ".lock", ".stop", ".exited")
        }
        require(set(names) <= allowed, "unregistered_runtime_owner")
        for service in self.binding["services"]:
            owner = read_json(child(self.home, "owners/" + service + ".json"))
            require(
                set(owner) == {"service", "pid", "birth", "binding_sha256", "runtime"},
                "invalid_process_registration",
            )
            require(
                owner["service"] == service
                and owner["binding_sha256"] == self.binding_hash
                and owner["runtime"] == "synthetic-fixture/1",
                "process_binding_mismatch",
            )
            if service in self.owners:
                require(owner == self.owners[service], "runtime_owner_changed")
            else:
                try:
                    process = ProcessIdentity(owner["pid"])
                except ProcessGone:
                    process = None
                if process is not None:
                    if process.birth != owner["birth"]:
                        process.close()
                        require(False, "process_identity_mismatch")
                self.owners[service], self.processes[service] = owner, process
        require(
            len({(v["pid"], v["birth"]) for v in self.owners.values()})
            == len(self.owners),
            "duplicate_process_owner",
        )

    def disable_restart(self):
        # The persistent maintenance gate was created by the orchestrator BEFORE stopping.
        require(
            child(self.home, "MAINTENANCE.json").is_file(), "maintenance_gate_required"
        )

    def stop(self, service):
        self.budget.check()
        request = self.home / "owners" / (service + ".stop")
        if not request.exists():
            write_new(request, canonical(self.owners[service]))
        else:
            require(
                read_json(request) == self.owners[service], "stop_identity_mismatch"
            )

    def stopped(self, service):
        process = self.processes[service]
        if process is not None and not process.exited():
            return False
        receipt = read_json(child(self.home, "owners/" + service + ".exited"))
        require(
            receipt == {"owner": self.owners[service], "graceful": True},
            "owner_exit_unconfirmed",
        )
        return True

    def assert_stopped(self):
        self.inspect()
        require(
            all(self.stopped(s) for s in self.binding["services"]), "residual_writer"
        )

    def reserve(self):
        self.assert_stopped()
        for service in self.binding["services"]:
            self.locks.enter_context(
                lease(child(self.home, "owners/" + service + ".lock"))
            )

    def close(self):
        self.locks.close()
        for process in self.processes.values():
            if process is not None:
                process.close()
