"""Registered target projections, preview plans and persistent operation workers."""

from __future__ import annotations

import copy
import base64
import difflib
import hashlib
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..paging import page
from ..protocol import Failure, utc_now
from ..sampling import stable_id
from ..transport import Deadline
from .engine import Engine, runtime
from .files import StackFiles, content, document, fingerprint, no_symlinks, normalize_binds, redact, unmask
from .protocol import PROJECT, empty_runtime, envelope, matches, receipt, semantic
from .state import Store
from .lifecycle import launcher_active, process_identity

TERMINAL = {"succeeded", "failed", "rejected"}


def project_name(raw, directory):
    value = document(raw)
    name = value.get("name", directory.name.lower())
    if not matches(PROJECT, name):
        raise Failure("compose_project_unresolved", "unsupported")
    return name


class Service:
    def __init__(self, config, deadline, *, transport=None):
        self.config, self.deadline = config, deadline
        self.store = Store(config.state_dir)
        self.files = StackFiles(config, self.store)
        self.engine = Engine(config, self.files, deadline, transport=transport)

    def stack(self, stack_id, *, editable=False, inventory=None):
        directory = self.files.directory(stack_id)
        snapshot = self.files.snapshot(directory)
        raw = content(snapshot["files"][snapshot["primary"]])
        project, revision = project_name(raw, directory), fingerprint(snapshot)
        items = self.engine.project_items(project, directory, inventory=inventory)
        protection = self.engine.protection(project=project, items=items)
        owner = self.files.owner(directory)
        actions = [] if protection["protected"] else ["stack.start", "stack.stop", "stack.restart", "stack.update"]
        if owner == "platform" and actions:
            actions += ["stack.deploy", "stack.restore"]
        return {"id": stack_id, "name": directory.name, "project_name": project, "directory": str(directory),
                "compose_file": str(directory / snapshot["primary"]), "revision": revision, "owner": owner,
                "protection": protection, "allowed_actions": actions,
                "compose_content": redact(raw, revision)[0] if editable and owner == "platform" else None,
                "runtime": runtime(items, expected_services=document(raw)["services"]), "reason_codes": []}

    def unavailable_stack(self, stack_id, name, failure):
        return {"id": stack_id, "name": name, "project_name": None, "directory": str(self.config.stacks_root / name),
                "compose_file": None, "revision": None, "owner": "external", "protection": self.engine.protection(project=name),
                "allowed_actions": [], "compose_content": None, "runtime": empty_runtime(), "reason_codes": [failure.code]}

    def inspect_container(self, container_id):
        item = self.engine.container(container_id)
        protection = self.engine.protection(items=[item])
        if not protection["protected"] and self.engine.protected_mount_blockers(item["mounts"]):
            protection = {"protected": True, "owner": "registered-bind-owner", "reason": "protected_bind_root", "maintenance_ref": None}
        return {"id": item["id"], "name": item["name"], "allowed_actions": [] if protection["protected"] else ["container.start", "container.stop", "container.restart"],
                "protection": protection, "runtime": runtime([item])}

    def plan(self, params):
        action = params["action"]
        base, images, restore = None, {}, None
        if action == "create":
            project = params["project_name"]
            directory = self.config.stacks_root / project
            no_symlinks(self.config.stacks_root)
            no_symlinks(directory, allow_missing=True)
            if directory.exists():
                raise Failure("stack_already_exists", "conflict")
            stack_id = stable_id("stack", self.config.node_id, project)
            raw = normalize_binds(params["compose_content"].encode(), directory)
            if project_name(raw, directory) != project:
                raise Failure("compose_project_identity_conflict", "conflict")
            if references_for_create(raw):
                raise Failure("create_reference_files_not_registered", "unsupported")
        else:
            stack_id = params["stack_id"]
            directory = self.files.directory(stack_id)
            base = self.files.assert_revision(directory, params["expected_revision"])
            current = content(base["files"][base["primary"]])
            project = project_name(current, directory)
            owner = self.files.owner(directory)
            if action in ("deploy", "restore") and owner != "platform":
                raise Failure("stack_configuration_not_owned", "unsupported")
            if action == "deploy":
                raw = normalize_binds(unmask(params["compose_content"], redact(current, fingerprint(base))[1]), directory)
                if project_name(raw, directory) != project:
                    raise Failure("compose_project_identity_conflict", "conflict")
                self.files.snapshot(directory, proposed=raw)
            elif action == "restore":
                restore = self.store.read("version", params["restore_version_id"])
                if not restore or restore["public"]["stack_id"] != stack_id:
                    raise Failure("version_not_found", "conflict")
                raw = content(restore["snapshot"]["files"][restore["snapshot"]["primary"]])
                images = restore["image_ids"]
            else:
                raw = current
        inventory = self.engine.inventory()
        items = self.engine.project_items(project, directory, inventory=inventory) if base else []
        if not base and any(item["compose"] and item["compose"]["project"] == project for item in inventory):
            raise Failure("compose_project_identity_conflict", "conflict")
        self.engine.assert_unprotected(project=project, items=items)
        blockers, warnings = [], []
        if action == "restore":
            # A restore may change .env. Validate the normalized visible topology
            # from the saved Compose without substituting current secret files.
            # Referenced input changes are listed explicitly and checked at run.
            if any(base["files"].get(name, {}).get("sha256") != (entry or {}).get("sha256") for name, entry in restore["snapshot"]["files"].items() if name != base["primary"] and base["files"].get(name) is not None):
                warnings.append("restore_referenced_configuration_version")
            if any(entry is None and base["files"].get(name) is not None for name, entry in restore["snapshot"]["files"].items()):
                blockers.append("restore_would_remove_new_reference_file")
        try:
            preview = self.engine.validate(directory, project, raw, snapshot=restore["snapshot"] if restore else None)
            blockers += self.engine.port_blockers(preview["ports"], project, directory, inventory=inventory)
            blockers += self.engine.protected_mount_blockers(preview["mounts"])
            if action == "update":
                for mount in preview["mounts"]:
                    if mount["kind"] != "bind":
                        continue
                    actual = [binding for item in items if item["compose"]["service"] == mount["service"] for binding in item["mounts"] if binding["target"] == mount["target"]]
                    if actual and not any(binding["kind"] == "bind" and binding["source"] == mount["source"] and binding["read_only"] == mount["read_only"] for binding in actual):
                        blockers.append("existing_bind_source_changed")
        except Failure as failure:
            preview = {"ports": [], "mounts": [], "images": [], "services": []}
            blockers.append(failure.code)
        if action == "update":
            warnings.append("image_update_not_data_rollback")
        if action == "restore":
            warnings.append("configuration_restore_not_data_rollback")
        pull_services = []
        applied = self.store.read("applied", stack_id) if base else None
        provenance = applied if applied and applied["canonical_sha256"] == base["files"][base["primary"]]["sha256"] else None
        for image in preview["images"]:
            current_items = [item for item in items if item["compose"]["service"] == image["service"]]
            prior = provenance["services"].get(image["service"]) if provenance else None
            source_image = prior["source"] if prior and current_items and all(item["image_id"] == prior["image_id"] for item in current_items) else current_items[0]["image_reference"] if current_items else None
            image["current_image_id"] = current_items[0]["image_id"] if current_items else None
            image["pull"] = action == "update"
            if action in ("create", "deploy") and (action == "create" or source_image != image["to"]):
                local_image = self.engine.available_image(image["to"])
                policy = image["pull_policy"]
                if policy == "never" and local_image is None:
                    blockers.append("local_image_required")
                image["pull"] = policy != "never" and (local_image is None or policy not in ("missing", "if_not_present"))
                if not image["pull"] and local_image:
                    images[image["service"]] = local_image
                    image["target_image_id"] = local_image
            if image["pull"]:
                pull_services.append(image["service"])
            elif action == "deploy" and current_items and source_image == image["to"]:
                images[image["service"]] = current_items[0]["image_id"]
            if action == "restore" and image["service"] in images:
                image["to"] = images[image["service"]]
        before = redact(content(base["files"][base["primary"]]), fingerprint(base))[0] if base else ""
        after = redact(raw, fingerprint(base) if base else stack_id)[0]
        difference = "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile="current", tofile="proposed"))
        created = datetime.now(timezone.utc)
        public = {"plan_id": "plan:" + uuid.uuid4().hex, "stack_id": stack_id, "project_name": project, "action": action,
                  "state": "blocked" if blockers else "ready", "base_revision": fingerprint(base) if base else None,
                  "proposed_revision": hashlib.sha256(raw).hexdigest(), "created_at": utc_now(),
                  "expires_at": (created + timedelta(hours=1)).isoformat().replace("+00:00", "Z"), "config_diff": difference,
                  "ports": preview["ports"], "mounts": preview["mounts"], "images": preview["images"],
                  "warnings": list(dict.fromkeys(warnings)), "blockers": list(dict.fromkeys(blockers)), "restore_version_id": params.get("restore_version_id")}
        write_expectations = {}
        if action == "restore":
            write_expectations = {name: self.files.reference_state(directory, name) for name in restore["snapshot"]["files"]}
        elif action == "deploy":
            write_expectations = {base["primary"]: base["files"][base["primary"]]}
        record = {"public": public, "directory": str(directory), "base": base, "write_expectations": write_expectations,
                  "raw": base64.b64encode(raw).decode(), "preview": preview, "pull_services": pull_services,
                  "restore_snapshot": restore["snapshot"] if restore else None, "image_ids": images,
                  "runtime_identity": runtime_identity(items),
                  "root_identity": self.files.identity(self.config.stacks_root)}
        self.store.write("plan", public["plan_id"], record)
        return public

    def resolve(self, params):
        target, action = params["target"], params["action"]
        if action.startswith("container."):
            item = self.engine.container(target["container_id"])
            self.engine.assert_unprotected(items=[item])
            project = item["compose"]["project"] if item["compose"] else None
            return {"project": project, "directory": None, "stack_id": None, "lock": "project:" + project if project else "container:" + item["id"],
                    "container_id": item["id"], "identity": {key:item[key] for key in ("id", "created_at", "compose")}}
        if action == "stack.apply":
            plan = self.store.read("plan", target["plan_id"])
            if not plan:
                raise Failure("plan_not_found", "conflict")
            public = plan["public"]
            if public["state"] != "ready":
                raise Failure("plan_blocked", "conflict")
            if datetime.fromisoformat(public["expires_at"].replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                raise Failure("plan_expired", "conflict")
            directory = self.config.stacks_root / public["project_name"] if public["action"] == "create" else self.files.directory(public["stack_id"])
            if str(directory) != plan["directory"] or self.files.identity(self.config.stacks_root) != plan["root_identity"]:
                raise Failure("stack_target_replaced", "conflict")
            if plan["base"]:
                self.files.assert_revision(directory, public["base_revision"])
                for name, expected in plan["write_expectations"].items():
                    if self.files.reference_state(directory, name) != expected:
                        raise Failure("compose_input_changed", "conflict")
                if public["action"] in ("deploy", "restore") and self.files.owner(directory) != "platform":
                    raise Failure("stack_configuration_not_owned", "unsupported")
            elif directory.exists():
                raise Failure("stack_already_exists", "conflict")
            project, stack_id = public["project_name"], public["stack_id"]
            inventory = self.engine.inventory()
            items = self.engine.project_items(project, directory, inventory=inventory) if plan["base"] else []
            if runtime_identity(items) != plan["runtime_identity"]:
                raise Failure("stack_runtime_identity_changed", "conflict")
            if not plan["base"] and any(item["compose"] and item["compose"]["project"] == project for item in inventory):
                raise Failure("compose_project_identity_conflict", "conflict")
            self.engine.assert_unprotected(project=project, items=items)
            blockers = self.engine.port_blockers(plan["preview"]["ports"], project, directory, inventory=inventory)
            blockers += self.engine.protected_mount_blockers(plan["preview"]["mounts"])
            if blockers:
                raise Failure(blockers[0], "conflict")
        else:
            directory = self.files.directory(target["stack_id"])
            current = self.files.assert_revision(directory, target["expected_revision"])
            project = project_name(content(current["files"][current["primary"]]), directory)
            stack_id, plan = target["stack_id"], None
            self.engine.assert_unprotected(project=project, items=self.engine.project_items(project, directory))
        return {"project": project, "directory": str(directory), "stack_id": stack_id, "lock": "project:" + project, "plan_id": params["target"].get("plan_id")}

    def submit(self, params, launch):
        operation_id = params["operation_id"]
        with self.store.lock("submission", operation_id, self.deadline, wait=True):
            previous = self.store.read("operation", operation_id)
            digest = semantic(self.config.node_id, params)
            if previous:
                if previous["semantic"] != digest:
                    raise Failure("operation_id_conflict", "conflict")
                return self.get(operation_id), True
            resolved = self.resolve(params)
            with self.store.lock("project", resolved["lock"], self.deadline, busy_code="project_operation_active"):
                reservation = self.store.read("reservation", resolved["lock"])
                if reservation:
                    older = self.get(reservation["operation_id"])
                    if older["execution_active"] is not False:
                        raise Failure("project_operation_active", "conflict")
                value = {"semantic": digest, "params": params, "resolved": resolved, "receipt": receipt(self.config.node_id, params)}
                self.store.write("operation", operation_id, value)
                self.store.write("reservation", resolved["lock"], {"operation_id": operation_id})
                self.store.write("launcher", operation_id, {"state": "preparing"})
                try:
                    launched = launch(operation_id)
                    if launched:
                        self.store.write("launcher", operation_id, launched)
                except OSError:
                    value["receipt"].update(state="rejected", phase="complete", execution_active=False, reason_codes=["worker_launch_failed"], updated_at=utc_now())
                    self.store.write("operation", operation_id, value)
                return value["receipt"], False

    def get(self, operation_id, *, reconcile=True):
        value = self.store.read("operation", operation_id)
        if not value:
            raise Failure("operation_not_found", "conflict")
        public = copy.deepcopy(value["receipt"])
        settled = public.get("execution_active") is False
        active = False
        try:
            with self.store.lock("worker", operation_id, self.deadline):
                pass
        except Failure as failure:
            if failure.code != "control_busy":
                raise
            active = True
        public["execution_active"] = active
        if not active and not settled:
            launched = launcher_active(self.store.read("launcher", operation_id))
            remote_active = self.engine.execution_active(value.get("leases", []))
            public["execution_active"] = True if launched is True or remote_active is True else None if launched is None or remote_active is None else False
            if public["execution_active"] is False and public["state"] not in TERMINAL:
                public.update(state="unknown", phase="unknown", reason_codes=list(dict.fromkeys(public["reason_codes"] + ["worker_interrupted"])))
                if public["command"]["state"] == "sent":
                    public["command"] = {"state": "unknown", "exit_code": None, "reason_codes": ["worker_interrupted"]}
        if reconcile and not active:
            try:
                public["runtime"] = self.observe(value["resolved"])
            except Failure as failure:
                public["runtime"] = empty_runtime()
                public["reason_codes"] = list(dict.fromkeys(public["reason_codes"] + [failure.code]))
        return public

    def observe(self, resolved, *, services=None):
        if resolved.get("container_id"):
            return runtime(self.engine.docker.inspect([resolved["container_id"]]))
        directory = self.config.stacks_root / Path(resolved["directory"]).name
        if services is None:
            snapshot = self.files.snapshot(directory)
            services = document(content(snapshot["files"][snapshot["primary"]]))["services"]
        return runtime(self.engine.project_items(resolved["project"], directory), expected_services=services)

    def save_version(self, operation_id, stack_id, directory, items, *, suffix):
        snapshot = self.files.snapshot(directory)
        images = self.engine.saved_images(items)
        public = {"version_id": "version:" + operation_id + ":" + suffix, "stack_id": stack_id, "created_at": utc_now(),
                  "operation_id": operation_id, "revision": fingerprint(snapshot),
                  "images": [{"service": name, "image_id": image} for name, image in sorted(images.items())], "restores_data": False}
        if len(public["version_id"]) > 128:
            public["version_id"] = "version:" + hashlib.sha256(public["version_id"].encode()).hexdigest()
        self.store.write("version", public["version_id"], {"public": public, "snapshot": snapshot, "image_ids": images})
        return public["version_id"]

    def work(self, operation_id):
        with self.store.lock("worker", operation_id, self.deadline):
            value = self.store.read("operation", operation_id)
            if not value or value["receipt"]["state"] != "accepted":
                return
            public = value["receipt"]
            reservation = self.store.read("reservation", value["resolved"]["lock"])
            if not reservation or reservation["operation_id"] != operation_id:
                return
            self.store.write("launcher", operation_id, {"state": "launched", "identity": process_identity(os.getpid())})

            def save(**updates):
                public.update(updates, updated_at=utc_now())
                self.store.write("operation", operation_id, value)

            def lease_started(lease):
                value.setdefault("leases", []).append(lease)
                self.store.write("operation", operation_id, value)

            self.engine.on_lease = lease_started

            try:
                with self.store.lock("project", value["resolved"]["lock"], self.deadline, wait=True):
                    reservation = self.store.read("reservation", value["resolved"]["lock"])
                    if not reservation or reservation["operation_id"] != operation_id:
                        raise Failure("operation_reservation_replaced", "conflict")
                    save(state="running", phase="validating", execution_active=True)
                    resolved = self.resolve(value["params"])
                    if resolved != value["resolved"]:
                        raise Failure("operation_target_replaced", "conflict")
                    action = value["params"]["action"]
                    services, plan = None, None
                    if action.startswith("container."):
                        item = self.engine.container(resolved["container_id"])
                        if {key:item[key] for key in ("id", "created_at", "compose")} != resolved["identity"]:
                            raise Failure("container_identity_changed", "conflict")
                        save(phase="applying", command={"state": "sent", "exit_code": None, "reason_codes": []})
                        result = self.engine.mutate_container(item["id"], action)
                    else:
                        directory, project = Path(resolved["directory"]), resolved["project"]
                        if action == "stack.apply":
                            plan = self.store.read("plan", resolved["plan_id"])
                            services = plan["preview"]["services"]
                            if plan["base"]:
                                self.save_version(operation_id, resolved["stack_id"], directory, self.engine.project_items(project, directory), suffix="before")
                            public_plan = plan["public"]
                            if plan["pull_services"]:
                                save(phase="pulling", command={"state": "sent", "exit_code": None, "reason_codes": []})
                                pulled = self.engine.pull(directory, project, base64.b64decode(plan["raw"]), plan["pull_services"])
                                if pulled.reason:
                                    self.finish(value, pulled, resolved, services, plan, save)
                                    return
                                # Pulling is long enough for an external editor
                                # or Docker replacement; verify the whole plan.
                                self.resolve(value["params"])
                                plan["image_ids"].update(self.engine.resolve_images(plan["preview"]["images"], plan["pull_services"]))
                            if public_plan["action"] == "create":
                                no_symlinks(directory, allow_missing=True)
                                directory.mkdir(mode=0o755)
                                metadata = {"uid": directory.stat().st_uid, "gid": directory.stat().st_gid, "mode": 0o600}
                                self.files.replace(directory, "compose.yaml", base64.b64decode(plan["raw"]), metadata, expected=None)
                                self.files.claim(directory)
                            elif public_plan["action"] in ("deploy", "restore"):
                                self.files.assert_revision(directory, public_plan["base_revision"])
                                if public_plan["action"] == "restore":
                                    snapshot = plan["restore_snapshot"]
                                    for name, record in snapshot["files"].items():
                                        if record is not None and name != snapshot["primary"]:
                                            self.files.replace(directory, name, content(record), record, expected=plan["write_expectations"][name])
                                else:
                                    snapshot = plan["base"]
                                self.files.replace(directory, snapshot["primary"], base64.b64decode(plan["raw"]), snapshot["files"][snapshot["primary"]], expected=plan["write_expectations"].get(snapshot["primary"]))
                            save(phase="applying", command={"state": "sent", "exit_code": None, "reason_codes": []})
                            result = self.engine.mutate_stack(directory, project, "stack.apply", image_ids=plan["image_ids"], mounts=plan["preview"]["mounts"])
                        else:
                            raw_snapshot = self.files.snapshot(directory)
                            services = document(content(raw_snapshot["files"][raw_snapshot["primary"]]))["services"]
                            save(phase="applying", command={"state": "sent", "exit_code": None, "reason_codes": []})
                            result = self.engine.mutate_stack(directory, project, action)
                    self.finish(value, result, resolved, services, plan, save)
            except Failure as failure:
                sent = public["command"]["state"] == "sent"
                save(state="unknown" if sent else "rejected", phase="unknown" if sent else "complete", execution_active=self.engine.execution_active() if sent else False, reason_codes=[failure.code],
                     command={"state": "unknown" if sent else "not_sent", "exit_code": None, "reason_codes": [failure.code] if sent else []})
            except Exception:
                sent = public["command"]["state"] == "sent"
                save(state="unknown" if sent else "rejected", phase="unknown" if sent else "complete", execution_active=self.engine.execution_active() if sent else False,
                     reason_codes=["worker_internal_error"], command={"state": "unknown" if sent else "not_sent", "exit_code": None, "reason_codes": ["worker_internal_error"] if sent else []})

    def finish(self, value, result, resolved, services, plan, save):
        public = value["receipt"]
        execution = self.engine.execution_active()
        uncertain = result.timed_out or result.truncated or result.exit_code is None and not result.unavailable and not result.setup_failed
        uncertain = uncertain or bool(result.reason) and execution is not False
        command = {"state": "unknown" if uncertain else "failed" if result.reason else "succeeded", "exit_code": result.exit_code,
                   "reason_codes": ["command_outcome_unknown"] if uncertain else ["control_command_failed"] if result.reason else []}
        save(phase="observing", command=command)
        # Readback has its own short budget after a command timeout. It neither
        # retries the command nor invents completion from a matching observation.
        self.engine.deadline = Deadline(20)
        self.engine.transport.deadline = self.engine.deadline
        try:
            observed = self.observe(resolved, services=services)
        except Failure as failure:
            observed = empty_runtime()
            public["reason_codes"].append(failure.code)
        expected = "stopped" if public["action"].endswith(".stop") else "running"
        failed_runtime = observed["state"] != expected or observed["health"] == "unhealthy"
        state = "unknown" if uncertain else "failed" if result.reason or failed_runtime else "succeeded"
        reasons = list(public["reason_codes"]) + command["reason_codes"]
        if failed_runtime:
            reasons.append("runtime_expectation_not_met")
        if state == "succeeded" and plan:
            try:
                directory = Path(resolved["directory"])
                public["version_id"] = self.save_version(public["operation_id"], resolved["stack_id"], directory, self.engine.project_items(resolved["project"], directory), suffix="after")
                actual_images = {item["compose"]["service"]: item["image_id"] for item in self.engine.project_items(resolved["project"], directory)}
                current = self.files.snapshot(directory)
                self.store.write("applied", resolved["stack_id"], {"operation_id": public["operation_id"],
                    "canonical_sha256": current["files"][current["primary"]]["sha256"],
                    "services": {image["service"]: {"source": image["from"], "image_id": actual_images.get(image["service"])} for image in plan["preview"]["images"]},
                    "mounts": plan["preview"]["mounts"]})
            except Failure as failure:
                reasons.append(failure.code)
                state = "failed"
        save(state=state, phase="unknown" if uncertain else "complete", execution_active=execution, runtime=observed, reason_codes=list(dict.fromkeys(reasons)))

    def dispatch(self, request, launch):
        operation, params = request.operation, request.params
        if request.node_id != self.config.node_id:
            raise Failure("node_registration_mismatch", "invalid")
        cursor, total, reasons = None, 1, []
        if operation == "stack.list":
            discovered = self.files.discover()
            selected, cursor = page(sorted(discovered), self.config.node_id, operation, params)
            inventory = self.engine.inventory()
            stacks = []
            for stack_id in selected:
                self.deadline.check()
                try:
                    stacks.append(self.stack(stack_id, inventory=inventory))
                except Failure as failure:
                    stacks.append(self.unavailable_stack(stack_id, discovered[stack_id], failure))
                    reasons.append(failure.code)
            data, total = {"stacks": stacks}, len(discovered)
        elif operation == "stack.inspect":
            data = {"stack": self.stack(params["stack_id"], editable=True)}
        elif operation == "container.inspect":
            data = {"container": self.inspect_container(params["container_id"])}
        elif operation == "stack.plan":
            data = {"plan": self.plan(params)}
        elif operation == "operation.submit":
            public, replayed = self.submit(params, launch)
            data = {"operation": public, "replayed": replayed}
        elif operation == "operation.get":
            data = {"operation": self.get(params["operation_id"], reconcile=params["reconcile"]), "replayed": True}
        elif operation in ("operation.list", "stack.versions"):
            if operation == "operation.list":
                records = {entry["receipt"]["operation_id"]: entry for entry in self.store.records("operation")}
                selected, cursor = page(sorted(records), self.config.node_id, operation, params)
                data = {"operations": [self.get(identity, reconcile=False) for identity in selected]}
            else:
                self.files.directory(params["stack_id"])
                records = {entry["public"]["version_id"]: entry for entry in self.store.records("version") if entry["public"]["stack_id"] == params["stack_id"]}
                selected, cursor = page(sorted(records), self.config.node_id, operation + ":" + params["stack_id"], params)
                data = {"versions": [records[identity]["public"] for identity in selected]}
            total = len(records)
        else:
            raise Failure("operation_not_configured", "unsupported")
        returned = len(next(iter(data.values()))) if operation in ("stack.list", "operation.list", "stack.versions") else 1
        return envelope(request, data=data, status="partial" if reasons else "ok", reasons=reasons, returned=returned, total=total, cursor=cursor)


def references_for_create(raw):
    from .files import references
    return references(document(raw)) - {".env"}


def runtime_identity(items):
    return sorted([[item["id"], item["created_at"], item["image_id"], item["compose"]] for item in items], key=lambda item: item[0])
