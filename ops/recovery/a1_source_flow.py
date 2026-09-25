"""One-attempt synthetic A1 source workflow, using only product CLIs and HTTPS APIs.

The preparation file names a fresh scope and the exact executables.  This entry
does not allocate NAS resources.  A failed attempt is retained for inspection;
it must never be replayed in the same scope.
"""

import argparse
import copy
import ipaddress
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .a1_code import deploy_module, require_entry_origin
from .a1_once import _network_plan, _ports, _write_once
from .a1_prepare import load as load_preparation
from .safety import RecoveryError, file_hash, read_json, require
from . import a1_acceptance as product

CORE = ("platform", "companion", "memory", "gateway")
OBS = ("obs-vector", "obs-loki", "obs-grafana", "obs-prometheus", "obs-guard")
MEMORY = {"green": "绿色", "red": "红色", "reading": "阅读"}


def _utc(seconds=0):
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat(
        timespec="seconds").replace("+00:00", "Z")


def _result(result, *, status=200):
    require(type(result) is dict and result.get("http_status") == status and
            type(result.get("body")) is dict, "a1_source_api_result_invalid")
    return result["body"]


def _unknown(result):
    body = _result(result)
    turns = []
    for item in body.get("history", []):
        turn = item.get("turn", {})
        if turn.get("turn_sequence") in (1, 2):
            turns.append({"turn_id": turn.get("turn_id"),
                "sequence": turn.get("turn_sequence"), "phase": turn.get("phase"),
                "delivery_state": turn.get("delivery_state"),
                "replies": [{"reply_id": row.get("reply_id"), "state": row.get("state")}
                            for row in item.get("replies", [])]})
    turns.sort(key=lambda row: row["sequence"])
    require(len(turns) == 2 and [row["sequence"] for row in turns] == [1, 2] and
            len({row["turn_id"] for row in turns}) == 2 and
            all(row["phase"] == "closed_unknown" and
                row["delivery_state"] == "unknown" and len(row["replies"]) == 1 and
                row["replies"][0]["state"] == "unknown" for row in turns),
            "a1_source_unknown_turns_invalid")
    return {"http_status": 200, "turns": turns}


class Source:
    def __init__(self, c):
        self.c = c
        self.scope = Path(c["scope_parent"]) / c["scope_name"]
        self.root = self.scope / "deployments/source"
        self.report = self.root / "reports" / c["run_label"]
        self.docker = c["docker"]
        require(self.scope.is_dir() and not self.scope.is_symlink() and
                self.root.is_dir() and not self.root.is_symlink() and
                self.report.is_dir() and not self.report.is_symlink() and
                self.root.resolve(strict=True) == self.root and
                self.report.resolve(strict=True) == self.report and
                read_json(self.root / "deployment.json")["project_name"] ==
                c["source_project"] and
                read_json(self.scope / ".recovery-scope.json")["scope_id"] == c["scope_id"],
                "a1_source_scope_mismatch")
        require(all((self.report / ("register-" + label + ".json")).is_file()
                    for label in ("forget-success-v3", "source-revoke-success-v3")),
                "a1_source_static_inputs_missing")
        require(not (self.root / ".recovery-registration.json").exists() and
                not (self.root / "reports/runtime-identity.json").exists(),
                "a1_source_already_registered")

    def save(self, name, value):
        path = self.report / name
        _write_once(path, value)
        return path

    def expected_cpuset(self):
        profile = read_json(self.c["resource_profile"])
        resource = deploy_module(self.c["code_root"], "resource_profile")
        resource.validate(profile)
        site = read_json(self.root / "deployment.json")["compose_inputs"]
        require(site.get("resource_profile") == profile,
                "a1_source_resource_profile_mismatch")
        expected = ",".join(map(str, profile["cpus"]))
        core = read_json(self.root / "compose.json")["services"]
        obs = read_json(self.root / "observability/compose.yaml")["services"]
        require(set(core) == set(CORE) and set(obs) == set(OBS) and
                all(core[owner].get("cpuset") == expected for owner in CORE) and
                all(obs[owner].get("cpuset") == expected for owner in OBS),
                "a1_source_compose_cpuset_mismatch")
        return expected

    def preflight(self):
        """Read current host allocations before any source product writer starts."""
        require(sys.platform == "linux", "a1_source_linux_required")
        nets = _network_plan(self.c["networks"], self.c)
        ports = _ports(self.c["ports"])
        cpuset = self.expected_cpuset()
        def docker_read(*args):
            result = subprocess.run([self.docker, *args], capture_output=True,
                                    timeout=25, cwd=self.c["code_root"])
            require(result.returncode == 0 and len(result.stdout) <= 1048576,
                    "a1_source_docker_preflight_failed")
            return result.stdout

        routes = Path("/proc/net/route").read_text()
        require("Iface" in routes, "a1_source_routes_unavailable")
        for row in routes.splitlines()[1:]:
            fields = row.split()
            if len(fields) < 8:
                continue
            destination = int.from_bytes(bytes.fromhex(fields[1]), "little")
            mask = int.from_bytes(bytes.fromhex(fields[7]), "little")
            route = ipaddress.IPv4Network((destination, mask.bit_count()), strict=False)
            require(not route.prefixlen or not any(route.overlaps(net) for net in nets),
                    "a1_source_route_overlap")
        network_ids = docker_read("network", "ls", "-q", "--no-trunc").decode().splitlines()
        if network_ids:
            observed = json.loads(docker_read("network", "inspect", *network_ids))
            for item in observed:
                for block in item.get("IPAM", {}).get("Config", []) or []:
                    if block.get("Subnet"):
                        other = ipaddress.ip_network(block["Subnet"], strict=False)
                        require(not any(other.overlaps(net) for net in nets),
                                "a1_source_docker_network_overlap")
        for project in (self.c["source_project"], self.c["source_project"] + "-obs",
                        self.c["clone_project"], self.c["clone_project"] + "-obs"):
            require(not docker_read("ps", "-a", "-q", "--no-trunc",
                "--filter", "label=com.docker.compose.project=" + project).strip(),
                "a1_source_project_already_exists")
        for port in ports:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
        memory = Path("/proc/meminfo").read_text()
        import re
        available = re.search(r"^MemAvailable:\s+(\d+) kB$", memory, re.MULTILINE)
        require(available and int(available.group(1)) >= 11.5 * 1024 * 1024,
                "a1_source_host_memory_insufficient")
        return {"status": "host_preflight_passed", "networks_checked": len(nets),
                "ports_checked": len(ports), "projects_checked": 4,
                "cpuset": cpuset}

    def command(self, argv, *, input_bytes=None, timeout=45):
        try:
            completed = subprocess.run(argv, input=input_bytes, capture_output=True,
                                       timeout=timeout, cwd=self.c["code_root"])
        except subprocess.TimeoutExpired as error:
            self.save("command-error-private.json", {"kind": "timeout",
                "program": Path(argv[0]).name, "timeout_seconds": timeout,
                "stderr_tail": (error.stderr or b"")[-2000:].decode("utf-8", "replace")})
            raise
        if completed.returncode:
            self.save("command-error-private.json", {"kind": "exit",
                "program": Path(argv[0]).name, "returncode": completed.returncode,
                "stderr_tail": completed.stderr[-2000:].decode("utf-8", "replace")})
        require(completed.returncode == 0 and len(completed.stdout) <= 1048576 and
                len(completed.stderr) <= 1048576, "a1_source_command_failed")
        return completed.stdout

    def compose(self, owner, *args, timeout=60):
        if owner == "core":
            project, directory, file = self.c["source_project"], self.root, self.root / "compose.json"
        else:
            project, directory, file = self.c["source_project"] + "-obs", self.root / "observability", self.root / "observability/compose.yaml"
        return self.command([self.docker, "compose", "-p", project,
                             "--project-directory", str(directory), "-f", str(file),
                             *args], timeout=timeout)

    def wait_owners(self, owners, seconds=120):
        names = [f"{self.c['source_project']}{'-obs' if owner in OBS else ''}-{owner}-1"
                 for owner in owners]
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            result = subprocess.run([self.docker, "inspect", *names],
                                    capture_output=True, timeout=20,
                                    cwd=self.c["code_root"])
            if result.returncode == 0 and len(result.stdout) <= 1048576:
                containers = json.loads(result.stdout)
                if len(containers) == len(names) and all(
                    row["State"]["Status"] == "running" and
                    (row["State"].get("Health", {}).get("Status") == "healthy"
                     if row["Name"].lstrip("/") in names[:4] else True)
                    for row in containers):
                    return
            time.sleep(2)
        raise RecoveryError("a1_source_owners_not_ready")

    def initial_permissions(self):
        """Prepare only empty A1 runtime mounts, then use the packaged preflight."""
        require(sys.platform == "linux", "a1_source_linux_required")
        for owner in CORE:
            for group in ("data", "logs"):
                path = self.root / group / owner
                require(path.is_dir() and not path.is_symlink() and
                        path.resolve(strict=True).parent == self.root / group and
                        not any(path.iterdir()), "a1_source_runtime_mount_not_empty")
                stat = path.stat()
                require((stat.st_uid, stat.st_gid) in {(0, 0), (10001, 10001)},
                        "a1_source_runtime_owner_invalid")
                if (stat.st_uid, stat.st_gid) == (0, 0):
                    os.chown(path, 10001, 10001)
                os.chmod(path, 0o700)
        for owner in CORE:
            path = self.root / "private" / (owner + ".env")
            require(path.is_file() and not path.is_symlink() and
                    path.resolve(strict=True).parent == self.root / "private" and
                    path.stat().st_uid == 0,
                    "a1_source_private_env_invalid")
            os.chmod(path, 0o600)
        raw = self.command([self.c["python"], "-B", str(self.root / "tools/release.py"),
            "preflight", "--bundle", str(self.root), "--check-permissions"], timeout=90)
        report = json.loads(raw)
        require(report.get("status") == "package_valid" and
                "linux_permissions" in report.get("checks", []),
                "a1_source_package_preflight_failed")
        self.save("package-preflight.json", report)

    def platform(self, action, name, document):
        request = self.save(name + "-request.json", document)
        value = product.run_platform_cli(self.root, action, request,
                                         docker_executable=self.docker)
        require(value.get("action") == action and type(value.get("receipt")) is dict,
                "a1_source_platform_receipt_invalid")
        self.save(name + "-receipt.json", value)
        return value["receipt"]

    def origin(self, name, entry="web-source-actor"):
        value = self.platform("issue", "issue-" + name, {"entry_id": entry})
        ref = value.get("assertion_ref")
        require(type(ref) is str and ref.startswith("origin:"),
                "a1_source_origin_missing")
        return ref

    def api(self, owner, url, bearer_env, body, name, *, timeout=45,
            headers=None):
        """Call the deployed product HTTPS endpoint from its authorized peer."""
        require((owner, url, bearer_env) in {
            ("gateway", "https://platform.internal:8443/internal/v1/model-config/snapshot",
             "TS_GATEWAY_PLATFORM"),
            ("companion", "https://gateway.internal:8443/v1/chat/completions",
             "TS_CORE_GATEWAY")},
                "a1_source_api_target_invalid")
        code = (
            "import json,os,ssl,sys,urllib.error,urllib.request\n"
            "frame=json.loads(sys.stdin.buffer.readline(262145))\n"
            "body=json.dumps(frame['body'],ensure_ascii=False,separators=(',',':')).encode()\n"
            "headers={'Authorization':'Bearer '+os.environ[frame['bearer_env']],"
            "'Content-Type':'application/json','Accept-Encoding':'identity'}\n"
            "headers.update(frame['headers'])\n"
            "request=urllib.request.Request(frame['url'],data=body,headers=headers,method='POST')\n"
            "context=ssl.create_default_context(cafile='/etc/tianshu/tls/ca.pem')\n"
            "try: response=urllib.request.urlopen(request,context=context,timeout=35)\n"
            "except urllib.error.HTTPError as error: response=error\n"
            "raw=response.read(65537)\n"
            "assert len(raw)<=65536\n"
            "try: document=json.loads(raw)\n"
            "except ValueError: document={}\n"
            "print(json.dumps({'http_status':response.status,'body':document}))\n"
        )
        frame = {"url": url, "bearer_env": bearer_env, "body": body,
                 "headers": headers or {}}
        self.save(name + "-request.json", frame)
        argv = [self.docker, "compose", "-p", self.c["source_project"],
                "--project-directory", str(self.root), "-f", str(self.root / "compose.json"),
                "exec", "-i", "-T", owner, "python", "-c", code]
        raw = self.command(argv, input_bytes=(json.dumps(frame, ensure_ascii=False) + "\n").encode(),
                           timeout=timeout)
        value = json.loads(raw)
        self.save(name + ".json", value)
        return value

    def reader(self, method, name, request):
        path = self.save(name + "-request.json", request)
        result = method(self.root, path, docker_executable=self.docker)
        self.save(name + ".json", result)
        return result

    def web(self, name, origin, conversation):
        request = self.report / (name + "-request.json")
        product.build_web_snapshot_request(origin, conversation, request)
        result = product.read_companion_web_snapshot(
            self.root, request, docker_executable=self.docker)
        self.save(name + ".json", result)
        return result

    def memory(self, stage, origin, scope, expected):
        selections = {}
        for label, term in MEMORY.items():
            request = {"query": {"schema_version": 1,
                        "request_id": f"{self.c['run_label']}-{stage}-{label}-{secrets.token_hex(5)}",
                        "origin": {"assertion_ref": origin}},
                "requested_scope": scope, "query_text": term,
                "selection": ["identity"], "known_scope_version": None,
                "budget": {"tokens": 4096, "bytes": 65536}}
            body = _result(self.reader(product.read_memory_selection,
                                       f"memory-select-{stage}-{label}", request))
            units = body.get("selected_units")
            require(type(units) is list and len(units) == expected[label],
                    "a1_source_memory_selection_invalid")
            selections[label] = units
        return selections

    def dispatch(self, label):
        path = self.report / ("register-" + label + ".json")
        registered = self.platform("register-input", "register-" + label, read_json(path))
        ref = registered.get("assertion_ref")
        require(type(ref) is str and ref.startswith("source-input:"),
                "a1_source_registration_invalid")
        fanout = self.report / ("fanout-" + label + "-request.json")
        product.build_fanout_request(self.root, ref, label, path, fanout)
        receipt = product.run_platform_cli(self.root, "dispatch-fanout", fanout,
                                           docker_executable=self.docker)
        self.save("fanout-" + label + "-receipt.json", receipt)
        outcomes = receipt.get("receipt", {}).get("outcomes", [])
        require(len(outcomes) == 1 and outcomes[0].get("state") == "accepted",
                "a1_source_fanout_not_accepted")
        return ref, receipt["receipt"], outcomes[0]

    def run(self, preflight=None):
        self.authority_id = str(uuid.uuid4())
        self.save("a1-source-attempt.json", {"schema_version": "a1-source-attempt/1",
            "scope_id": self.c["scope_id"], "run_label": self.c["run_label"],
            "source_project": self.c["source_project"],
            "authority_id": self.authority_id, "started_at": _utc()})
        if preflight is not None:
            self.save("host-preflight.json", preflight)
        product.bind_synthetic_classification(self.root)
        product.enable_synthetic_memory_candidates(self.root)
        product.bind_synthetic_model_version(self.root, 3)
        self.initial_permissions()
        self.compose("core", "config", "--quiet")
        self.compose("observability", "config", "--quiet")
        self.compose("core", "up", "-d", "platform", timeout=120)
        for name, schema in (("profiles", 2), ("sources", 3)):
            command = "migrate-" + name
            backup = "/srv/tianshu/first-install.pre-" + name + ".sqlite"
            output = self.compose("core", "run", "--rm", "--no-deps", "memory",
                "--config", "/etc/tianshu/settings.json", command, "--backup", backup,
                timeout=120)
            migration = json.loads(output)
            require(migration.get("schema") == schema and
                    migration.get("backup") == backup and
                    (name != "sources" or migration.get("unverified_admissions") == 0),
                    "a1_source_memory_migration_invalid")
            self.save("memory-" + command + "-receipt.json", migration)
        config_origin = self.origin("source-config", "config-entry")
        product.set_gateway_origin(self.root, config_origin)
        self.compose("core", "up", "-d", "memory", "gateway", "companion", timeout=120)
        self.compose("observability", "up", "-d", timeout=120)
        self.wait_owners((*CORE, *OBS))
        model = product.start_synthetic_model(self.root, docker_executable=self.docker)
        require(model.get("status") == "ready" and model.get("fixture_only") is True,
                "a1_source_synthetic_model_not_ready")
        self.save("synthetic-model-start.json", model)
        publication = product.publish_synthetic_config(self.root,
            self.scope / "inputs/model-publication-template.json", 3,
            docker_executable=self.docker)
        require(publication.get("action") == "publish" and
                publication.get("receipt", {}).get("config_version") == 3 and
                publication["receipt"].get("published") is True,
                "a1_source_model_v3_missing")
        self.save("publish-v3-receipt.json", publication)
        green_ref, green, green_outcome = self.dispatch("forget-success-v3")
        red_ref, red, red_outcome = self.dispatch("source-revoke-success-v3")
        scopes = [item["admission"]["scope"] for item in (green_outcome, red_outcome)]
        accounts = [item["receipt"]["collection_key"]["author"]
                    for item in (green_outcome, red_outcome)]
        require(scopes[0] == scopes[1] and accounts[0] == accounts[1] and
                green["conversation_id"] == red["conversation_id"],
                "a1_source_fanout_scope_mismatch")
        bound = product.bind_memory_scopes(self.root,
            {"scopes": [scopes[0]] * 3, "account": accounts[0]})
        require(bound.get("status") == "bound", "a1_source_memory_scope_not_bound")
        self.compose("core", "up", "-d", "--force-recreate", "memory", timeout=120)
        self.wait_owners(CORE)
        actor_origin = self.origin("initial-web")
        initial = self.web("web-snapshot-initial", actor_origin, green["conversation_id"])
        initial_unknown = _unknown(initial)
        facts_request = {"schema_version": 1,
            "request_id": self.c["run_label"] + "-source-facts",
            "mode": "snapshot", "selectors": [],
            "turn_ids": [row["turn_id"] for row in initial_unknown["turns"]],
            "include_content": True}
        facts = self.reader(product.read_companion_source_facts,
                            "source-facts", facts_request)
        turns = _result(facts).get("turns")
        require(type(turns) is list and len(turns) == 2 and
                {row.get("turn_id") for row in turns} == set(facts_request["turn_ids"]) and
                all(row.get("phase") == "closed_unknown" and
                    row.get("committed_event", {}).get("reality") == "fictional"
                    for row in turns), "a1_source_facts_invalid")
        events = {row["committed_event"]["turn_sequence"]: row["committed_event"]
                  for row in turns}
        require(set(events) == {1, 2}, "a1_source_event_sequence_invalid")
        for sequence, label, drafts in (
            (1, "green", (("a1_marker", "测试角色的标记是绿色"),
                           ("a1_reading", "测试角色喜欢阅读"))),
            (2, "red", (("a1_source_revoke_probe", "测试角色的标记是红色"),))):
            event = events[sequence]
            require(event.get("sources") and event.get("scope") == scopes[0],
                    "a1_source_trusted_event_invalid")
            request = {"event": event, "drafts": [{"scope": event["scope"],
                "category": "identity", "field_key": key, "item_key": None,
                "units": [{"statement": statement, "conditions": [], "negations": [],
                    "valid_time": "NAS-A1 synthetic fixture", "uncertainty": "confirmed",
                    "reality": "fictional", "sources": event["sources"]}],
                "relationship_delta": None} for key, statement in drafts]}
            receipt = product.trusted_memory_commit(self.root,
                self.save("trusted-memory-" + label + "-request.json", request),
                docker_executable=self.docker)
            require(receipt.get("state") == "committed" and
                    len(receipt.get("record_ids", [])) == len(drafts),
                    "a1_source_trusted_memory_commit_invalid")
            self.save("trusted-memory-" + label + "-receipt.json", receipt)
        baseline = self.memory("baseline", self.origin("memory-baseline"), scopes[0],
                               {"green": 1, "red": 1, "reading": 1})
        record_id = baseline["green"][0]["record_id"]
        forget_origin = self.origin("forget")
        nonce = secrets.token_hex(8)
        revision = {"command": {"schema_version": 1,
            "request_id": self.c["run_label"] + "-forget-" + nonce,
            "origin": {"assertion_ref": forget_origin},
            "idempotency_key": self.c["run_label"] + ":forget:" + nonce,
            "deadline_at": _utc(100)}, "record_id": record_id,
            "expected_version": 1, "revision_kind": "forget",
            "confirmation_ref": "confirmation:" + self.c["run_label"] + "-forget-" + nonce,
            "evidence_refs": events[1]["sources"], "replacement_statement": None}
        revision_path = self.save("forget-revision-request.json", revision)
        operation = self.save("forget-local-operation.json",
            {"operation": "confirm_revision", "request": revision,
             "expires_at": _utc(115)})
        approval = product.run_local_user_action(self.root, operation,
                                                  docker_executable=self.docker)
        require(approval.get("consumed") is False and
                approval.get("binding_version") == 1,
                "a1_source_forget_approval_invalid")
        self.save("forget-approval-result.json", approval)
        revised = product.post_memory_revision(self.root, revision_path,
                                               docker_executable=self.docker)
        require(_result(revised).get("authoritative_state") == "tombstoned",
                "a1_source_forget_not_tombstoned")
        self.save("forget-revision-result.json", revised)
        self.memory("after-forget", self.origin("after-forget"), scopes[0],
                    {"green": 0, "red": 1, "reading": 0})
        revoked = self.platform("revoke-origin", "revoke-red-source", {"id": red_ref})
        require(revoked.get("revoked") is True, "a1_source_credential_not_revoked")
        self.memory("after-source-revoke", self.origin("after-source-revoke"), scopes[0],
                    {"green": 0, "red": 1, "reading": 0})
        physical = copy.deepcopy(read_json(self.report / "register-source-revoke-success-v3.json"))
        require(physical["entry_id"] == "web-input-source" and
                physical["input"]["message_key"]["revision"] == 1 and
                physical["input"]["kind"] == "message", "a1_source_retract_input_invalid")
        physical["input"]["message_key"]["revision"] = 2
        physical["input"]["kind"] = "retract"
        physical["input"]["parts"] = []
        physical["input"]["sent_at"] = _utc()
        retracted = self.platform("register-input", "register-source-retract", physical)
        retract_ref = retracted.get("assertion_ref")
        require(type(retract_ref) is str and retract_ref.startswith("source-input:"),
                "a1_source_retract_registration_invalid")
        nonce = secrets.token_hex(8)
        fanout = {"schema_version": 1, "command": {"schema_version": 1,
            "request_id": self.c["run_label"] + "-retract-" + nonce,
            "idempotency_key": self.c["run_label"] + ":retract:" + nonce,
            "origin": {"assertion_ref": retract_ref}, "deadline_at": _utc(100)},
            "input": physical["input"], "target_actor_ids": []}
        receipt = self.platform("dispatch-fanout", "fanout-source-retract", fanout)
        require(type(receipt.get("outcomes")) is list,
                "a1_source_retract_fanout_invalid")
        self.memory("after-retract", self.origin("after-retract"), scopes[0],
                    {"green": 0, "red": 0, "reading": 0})
        before = _unknown(self.web("unknown-before-v4",
                                   self.origin("unknown-before-v4"), green["conversation_id"]))
        model_revoked = self.platform("revoke-config", "revoke-model-v3", {"id": 3})
        require(model_revoked.get("revoked") is True, "a1_source_model_v3_not_revoked")
        config_origin = self.origin("model-revoked", "config-entry")
        denied = self.api("gateway", "https://platform.internal:8443/internal/v1/model-config/snapshot",
            "TS_GATEWAY_PLATFORM", {"query": {"schema_version": 1,
                "request_id": self.c["run_label"] + "-model-revoked",
                "origin": {"assertion_ref": config_origin}}, "config_version": 3},
            "model-revoked-api-result")
        require(denied.get("http_status") == 410 and
                denied.get("body", {}).get("code") == "forbidden",
                "a1_source_model_revocation_not_enforced")
        template = read_json(self.scope / "inputs/model-publication-template.json")
        require(template.get("config_version") == 1 and
                template.get("status") == "published" and
                len(template.get("providers", [])) == 1 and
                template["providers"][0].get("provider_id") == "provider-synthetic" and
                template["providers"][0].get("model_id") == "synthetic-recorded-text" and
                template["providers"][0].get("capability_verification") == "fixture_only" and
                template["providers"][0].get("base_url") ==
                    "https://gateway.internal:9443/v1",
                "a1_source_model_template_invalid")
        template["config_version"] = 4
        template["published_at"] = _utc(-3)
        template["usable_until"] = _utc(870)
        v4_path = self.save("publish-v4-request.json", template)
        diagnosis = product.diagnose_platform_publication(self.root, v4_path,
                                                           docker_executable=self.docker)
        require(diagnosis.get("valid") is True, "a1_source_model_v4_invalid")
        self.save("publish-v4-diagnosis.json", diagnosis)
        published = product.run_platform_cli(self.root, "publish", v4_path,
                                             docker_executable=self.docker)
        require(published.get("action") == "publish" and
                published.get("receipt", {}).get("config_version") == 4 and
                published["receipt"].get("published") is True,
                "a1_source_model_v4_not_published")
        self.save("publish-v4-receipt.json", published)
        platform = read_json(self.root / "config/platform/settings.json")
        gateway = read_json(self.root / "config/gateway/settings.json")
        companion = read_json(self.root / "config/companion/settings.json")
        clients = [item for item in gateway["clients"]
                   if item.get("service") == "companion" and
                   item.get("provider_id") == "provider-synthetic" and
                   item.get("internal") is True]
        require(len(clients) == 1 and companion.get("automatic_memory_candidates") is True,
                "a1_source_model_v4_binding_invalid")
        principal = platform["principals"]["gateway"]
        principal["config_versions"] = sorted(set(principal["config_versions"]) | {4})
        clients[0]["allowed_versions"] = sorted(set(clients[0]["allowed_versions"]) | {4})
        companion["config_version"] = 4
        product._update_bundle(self.root, {
            "config/platform/settings.json": product._raw(platform),
            "config/gateway/settings.json": product._raw(gateway),
            "config/companion/settings.json": product._raw(companion)})
        product.set_gateway_origin(self.root, self.origin("v4-config", "config-entry"))
        self.compose("core", "up", "-d", "--force-recreate", "platform", "gateway",
                     "companion", timeout=120)
        self.wait_owners((*CORE, *OBS))
        after = _unknown(self.web("unknown-after-v4-snapshot",
                                  self.origin("unknown-after-v4"), green["conversation_id"]))
        time.sleep(2)
        again = _unknown(self.web("unknown-after-v4-second",
                                  self.origin("unknown-after-v4-second"), green["conversation_id"]))
        require(before == after == again, "a1_source_unknown_resend_detected")
        self.save("unknown-after-v4-readback.json",
                  {"before": before, "after": after, "matches_pre_restart": True})
        probe_argv = [self.docker, "compose", "-p", self.c["source_project"],
            "--project-directory", str(self.root), "-f", str(self.root / "compose.json"),
            "exec", "-T", "gateway", "python", "-c",
            (self.root / "tools/container_probe.py").read_text(encoding="utf-8"),
            "model-ready"]
        probe = subprocess.run(probe_argv, capture_output=True, timeout=20,
                               cwd=self.c["code_root"])
        require(probe.returncode == 1 and b"container_probe_failed" in probe.stdout,
                "a1_source_synthetic_fixture_still_online")
        self.save("model-fixture-offline.json", {"probe_exit": probe.returncode,
            "stdout": probe.stdout.decode("utf-8", "replace")})
        controls = []
        for index in (5, 6):
            request_id = f"{self.c['run_label']}-offline-control-{index}"
            response = self.api("companion", "https://gateway.internal:8443/v1/chat/completions",
                "TS_CORE_GATEWAY", {"model": "synthetic-recorded-text",
                    "messages": [{"role": "user", "content":
                                  f"Fictional offline Gateway control {index}."}],
                    "stream": False}, f"gateway-offline-control-{index}",
                headers={"X-Request-ID": request_id,
                         "X-Tianshu-Turn-ID": f"{self.c['run_label']}-offline-turn-{index}",
                         "X-Tianshu-Config-Version": "4",
                         "X-Tianshu-Workload": "companion.text"})
            require(response.get("http_status") >= 500 or
                    response.get("body", {}).get("status") == "unknown",
                    "a1_source_offline_control_not_unknown")
            controls.append({"request_id": request_id, "response": response})
        self.save("gateway-offline-controls.json", controls)
        from .a1_clone_prepare import _evidence

        checked = _evidence(self.c)
        self.save("gateway-usage-readback.json", checked["usage"])
        self.runtime_identity()
        self.register()
        self.save("a1-source-complete.json", {"schema_version": "a1-source-complete/1",
            "scope_id": self.c["scope_id"], "authority_id": self.authority_id,
            "registration_sha256":
            file_hash(self.root / ".recovery-registration.json"),
            "runtime_identity_sha256": file_hash(self.root / "reports/runtime-identity.json"),
            "completed_at": _utc()})
        return {"status": "source_complete", "scope_id": self.c["scope_id"],
                "registration_sha256": file_hash(self.root / ".recovery-registration.json")}

    def runtime_identity(self):
        cpuset = self.expected_cpuset()
        expected = {owner: (f"{self.c['source_project']}-{owner}-1" if owner in CORE else
                            f"{self.c['source_project']}-obs-{owner}-1")
                    for owner in (*CORE, *OBS)}
        names = set()
        for project in (self.c["source_project"], self.c["source_project"] + "-obs"):
            raw = self.command([self.docker, "ps", "-a", "--filter",
                "label=com.docker.compose.project=" + project, "--format", "{{.Names}}"])
            names.update(raw.decode().splitlines())
        require(names == set(expected.values()), "a1_source_nine_owner_mismatch")
        containers = json.loads(self.command([self.docker, "inspect", *expected.values()]))
        require(len(containers) == 9, "a1_source_nine_owner_mismatch")
        observed = {}
        memory = {"obs-vector": 805306368, "obs-loki": 1610612736,
                  "obs-grafana": 536870912, "obs-prometheus": 536870912,
                  "obs-guard": 268435456}
        for container in containers:
            name = container["Name"].lstrip("/")
            owner = next((key for key, value in expected.items() if value == name), None)
            require(owner is not None and owner not in observed,
                    "a1_source_owner_identity_invalid")
            project = self.c["source_project"] + ("" if owner in CORE else "-obs")
            labels = container["Config"]["Labels"]
            state = container["State"]
            host = container["HostConfig"]
            require(labels.get("com.docker.compose.project") == project and
                    labels.get("com.docker.compose.service") == owner and
                    labels.get("com.docker.compose.oneoff") == "False" and
                    state["Status"] == "running" and not state["OOMKilled"] and
                    container["RestartCount"] == 0 and
                    container["Config"]["User"] == "10001:10001" and
                    host["CpusetCpus"] == cpuset and host["ReadonlyRootfs"] and
                    host["CapDrop"] == ["ALL"] and
                    host["Memory"] == (1073741824 if owner in CORE else memory[owner]) and
                    all(mount["Source"].startswith(str(self.root) + os.sep)
                        for mount in container["Mounts"]),
                    "a1_source_owner_observation_invalid")
            if owner in CORE:
                require(state["Health"]["Status"] == "healthy",
                        "a1_source_core_not_healthy")
            pid = state["Pid"]
            status = {key.rstrip(":"): value.strip() for key, value in
                (line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines()
                 if ":" in line)}
            uid, gid = int(status["Uid"].split()[0]), int(status["Gid"].split()[0])
            require((uid, gid) == (10001, 10001), "a1_source_owner_uid_invalid")
            image = json.loads(self.command([self.docker, "image", "inspect", container["Image"]]))[0]
            require(image["Id"] == container["Image"] and image["Os"] == "linux" and
                    image["Architecture"] == "amd64", "a1_source_image_invalid")
            observed[owner] = {"image_id": image["Id"],
                "repo_digests": image.get("RepoDigests") or [], "platform": "linux/amd64",
                "uid": uid, "gid": gid, "container_id": container["Id"], "status": "observed"}
        identity = deploy_module(self.c["code_root"], "runtime_identity")
        with identity.lifecycle_lease(self.root):
            identity.save(self.root, observed=observed, state="planned", linux_stat=True)
        require((self.root / "reports/runtime-identity.json").is_file(),
                "a1_source_runtime_identity_missing")

    def register(self):
        identity = self.root / "reports/runtime-identity.json"
        argv = [self.c["python"], "-B", "-m", "ops.recovery", "--root", str(self.scope),
            "--scope-id", self.c["scope_id"], "--execute", "linux-prepare",
            "--deployment-directory", str(self.root), "--runtime-identity", str(identity),
            "--runtime-identity-sha256", file_hash(identity),
            "--authority-id", self.authority_id, "--docker-executable", self.docker,
            "--timeout", "150"]
        raw = self.command(argv, timeout=180)
        result = json.loads(raw)
        require(result.get("status") == "registered" and
                result.get("registration_sha256") ==
                    file_hash(self.root / ".recovery-registration.json"),
                "a1_source_registration_failed")
        self.save("linux-prepare-receipt.json", result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    config_path = Path(args.config).resolve(strict=True)
    code = read_json(config_path)["code_root"]
    require_entry_origin(code, __file__, "ops/recovery/a1_source_flow.py")
    c = load_preparation(config_path, phase="clone")
    source = Source(c)
    require(not (source.report / "a1-source-attempt.json").exists(),
            "a1_source_attempt_already_started")
    previous_umask = os.umask(0o077) if args.execute else None
    try:
        if args.execute:
            checked = source.preflight()
            try:
                result = source.run(checked)
            except Exception as error:
                if (source.report / "a1-source-attempt.json").exists():
                    try:
                        source.save("a1-source-failure-private.json", {
                            "error_type": type(error).__name__,
                            "detail": str(error)[:4000], "failed_at": _utc()})
                    except OSError:
                        pass
                raise
        else:
            result = {"status": "planned", "scope_id": c["scope_id"],
                "source_project": c["source_project"],
                "source_directory": str(source.root)}
    finally:
        if previous_umask is not None:
            os.umask(previous_umask)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RecoveryError as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
        raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError,
            subprocess.TimeoutExpired):
        print(json.dumps({"status": "rejected", "reason": "source_action_failed_review_private_receipts"}))
        raise SystemExit(2)
