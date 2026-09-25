"""Prepare A1 clone placeholders from this scope's independently checked source."""

import hashlib
import ipaddress
import json
import os
import re
import secrets
import shutil
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .a1_once import ASSERTIONS, _write_raw_once, load_config
from .a1_code import require_entry_origin
from .a1_prepare import make_tls, NETWORK_NAMES, PORT_NAMES, load as load_preparation
from .drill_inputs import OWNERS
from .safety import canonical, child, file_hash, read_json, require

COPY_PREFIXES = ("config/", "private/", "contracts/", "tools/",
                 "observability/config/", "observability/code/", "observability-input/")
TLS_MAP = {"ca.pem": "ca.pem", "client-ca.pem": "ca.pem",
    "guard.pem": "obs-guard/server.pem", "guard.key": "obs-guard/server.key",
    "loki.pem": "obs-loki/server.pem", "loki.key": "obs-loki/server.key",
    "vector.pem": "obs-vector/server.pem", "vector.key": "obs-vector/server.key",
    "prometheus.pem": "obs-prometheus/server.pem",
    "prometheus.key": "obs-prometheus/server.key",
    "grafana.pem": "logs/server.pem", "grafana.key": "logs/server.key",
    "client.pem": "obs-client/server.pem", "client.key": "obs-client/server.key"}


def _paths(c):
    scope = Path(c["scope_parent"]) / c["scope_name"]
    return {"scope": scope, "source": scope / "deployments/source",
        "clone": scope / "deployments" / c["clone_name"],
        "inputs": scope / "drill-inputs" / c["clone_inputs_name"],
        "tls": scope / "inputs" / ("tls-" + c["clone_inputs_name"]),
        "reports": scope / "deployments/source/reports" / c["run_label"]}


def _source_routes(source, turns):
    database = (source / "data/companion/companion.db").resolve(strict=True)
    found = set()
    with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as db:
        for turn in turns:
            rows = db.execute("SELECT body FROM turns WHERE id=?",
                              (turn["turn_id"],)).fetchall()
            require(len(rows) == 1, "a1_clone_source_turn_missing")
            route = json.loads(rows[0][0]).get("route_receipt")
            require(type(route) is dict and
                    route.get("provider_id") == "provider-synthetic" and
                    route.get("caller_service") == "companion" and
                    route.get("config_version") == 3 and
                    type(route.get("request_id")) is str and
                    re.fullmatch(r"model:[a-f0-9]{32}", route["request_id"]),
                    "a1_clone_source_route_invalid")
            found.add(route["request_id"])
    require(len(found) == 2, "a1_clone_source_routes_invalid")
    return found


def _usage(source, c, since, until):
    environment = os.environ.copy()
    for line in (source / "private/gateway.env").read_text().splitlines():
        require("=" in line, "a1_clone_source_env_invalid")
        key, value = line.split("=", 1)
        environment[key] = value.strip("\"'")
    for name in ("PYTHONOPTIMIZE", "PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE",
                 "PYTHONSTARTUP"):
        environment.pop(name, None)
    environment["PYTHONPATH"] = os.pathsep.join(c["gateway_pythonpath"])
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    argv = [c["python"], "-B", "-s", "-m", "tianshu_gateway.usage_report",
            "--settings", str(source / "config/gateway/settings.json"),
            "--database", str(source / "data/gateway/diagnostics.sqlite"),
            "--service", "companion", "--view", "attempts",
            "--since", since, "--until", until, "--compact"]
    completed = subprocess.run(argv, capture_output=True, env=environment, timeout=30)
    require(completed.returncode == 0 and len(completed.stdout) <= 1024 * 1024,
            "a1_clone_gateway_usage_failed")
    return json.loads(completed.stdout), completed.stdout


def _evidence(c, *, route_reader=None, usage_reader=None, now=None):
    p = _paths(c)
    source, reports = p["source"], p["reports"]
    facts = read_json(reports / "source-facts.json")
    turns = facts["body"]["turns"]
    require(type(turns) is list and len(turns) == 2 and
            all(turn.get("phase") == "closed_unknown" and
                turn.get("committed_event", {}).get("reality") == "fictional"
                for turn in turns) and
            len({turn["turn_id"] for turn in turns}) == 2,
            "a1_clone_source_facts_invalid")
    unknown = read_json(reports / "unknown-after-v4-readback.json")["after"]["turns"]
    require(type(unknown) is list and len(unknown) == 2 and
            {row["turn_id"] for row in unknown} ==
            {row["turn_id"] for row in turns} and
            all(row.get("phase") == "closed_unknown" and
                row.get("delivery_state") == "unknown" and
                len(row.get("replies", [])) == 1 and
                row["replies"][0].get("state") == "unknown"
                for row in unknown),
            "a1_clone_unknown_invalid")
    history = read_json(reports / "web-snapshot-initial.json")["body"]["history"]
    history_expected = []
    for entry in history:
        turn = entry["turn"]
        if turn["turn_sequence"] in (1, 2):
            history_expected.append({"turn": {key: turn[key] for key in
                ("turn_id", "turn_sequence", "phase", "delivery_state")},
                "replies": [{"reply_id": reply["reply_id"], "state": reply["state"]}
                            for reply in entry["replies"]]})
    require(len(history_expected) == 2 and
            {row["turn"]["turn_id"] for row in history_expected} ==
            {row["turn_id"] for row in turns} and
            all(row["turn"]["phase"] == "closed_unknown" and
                row["turn"]["delivery_state"] == "unknown" and
                len(row["replies"]) == 1 and row["replies"][0]["state"] == "unknown"
                for row in history_expected), "a1_clone_web_history_invalid")
    route_ids = (route_reader or _source_routes)(source, turns)
    require(type(route_ids) is set and len(route_ids) == 2 and
            all(re.fullmatch(r"model:[a-f0-9]{32}", name) for name in route_ids),
            "a1_clone_routes_invalid")
    day = (now or datetime.now(timezone.utc)).date()
    since = day.isoformat() + "T00:00:00Z"
    until = (day + timedelta(days=1)).isoformat() + "T00:00:00Z"
    report, raw = (usage_reader or _usage)(source, c, since, until)
    require(type(raw) is bytes and json.loads(raw) == report,
            "a1_clone_gateway_usage_frame_invalid")
    reasons = {name: "completed" for name in route_ids}
    reasons.update({f"{c['run_label']}-offline-control-5": "transport_unknown",
                    f"{c['run_label']}-offline-control-6": "transport_unknown"})
    outcomes = {name: ("succeeded" if name in route_ids else "unknown")
                for name in reasons}
    window = {"since": since, "until": until,
              "since_ms": int(datetime.fromisoformat(since[:-1] + "+00:00").timestamp() * 1000),
              "until_ms": int(datetime.fromisoformat(until[:-1] + "+00:00").timestamp() * 1000),
              "limit": 500, "offset": 0}
    require(report.get("schema_version") == 1 and
            report.get("key_space") == "chat" and
            report.get("identity") == {"service": "companion"} and
            report.get("counts") == {"total": 4, "succeeded": 2, "failed": 0,
                                     "cancelled": 0, "unknown": 2} and
            report.get("coverage") == {"matching": 4, "scanned": 4,
                                       "truncated": False, "unmetered_total": 0} and
            report.get("window") == window and
            type(report.get("attempts")) is list and len(report["attempts"]) == 4 and
            {row.get("request_id") for row in report["attempts"]} == set(reasons) and
            all(row.get("reason") == reasons[row["request_id"]] and
                row.get("outcome") == outcomes[row["request_id"]] and
                since <= row.get("completed_at", "") < until
                for row in report["attempts"]),
            "a1_clone_gateway_usage_invalid")
    return {"facts": facts, "turns": turns, "unknown": unknown,
            "history": history_expected, "usage": report, "usage_raw": raw,
            "expected_attempts": [{"request_id": row["request_id"],
                "reason": reasons[row["request_id"]],
                "outcome": outcomes[row["request_id"]]}
                for row in report["attempts"]], "since": since, "until": until}


def _copy_and_rotate(c, p, tls_factory):
    source, inputs, clone = p["source"], p["inputs"], p["clone"]
    tls_factory(p["tls"])
    inputs.mkdir(mode=0o700, parents=True)
    inventory = read_json(source / "bundle-integrity.json")["files"]
    names = sorted({name for name in inventory if name.startswith(COPY_PREFIXES)} |
                   {"compose.json", "observability/compose.yaml"})
    for name in names:
        source_file = child(source, name)
        if name in inventory:
            require(file_hash(source_file) == inventory[name],
                    "a1_clone_source_bundle_changed")
        target = child(inputs, name, exists=False)
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        target.write_bytes(source_file.read_bytes())
        target.chmod(0o600)
    for owner in ("platform", "companion", "memory", "gateway"):
        base = inputs / "config" / owner / "tls"
        for name in ("server.pem", "server.key"):
            (base / name).write_bytes((p["tls"] / owner / name).read_bytes())
        (base / "ca.pem").write_bytes((p["tls"] / "ca.pem").read_bytes())
    for name, relative in TLS_MAP.items():
        (inputs / "observability-input/tls" / name).write_bytes(
            (p["tls"] / relative).read_bytes())
    old_to_new, environment = {}, {}
    for owner in ("platform", "companion", "memory", "gateway"):
        path = inputs / "private" / (owner + ".env")
        values, lines = {}, []
        for line in path.read_text().splitlines():
            require("=" in line, "a1_clone_env_invalid")
            key, old = line.split("=", 1)
            old = old.strip("\"'")
            new = ("origin:clone-config-pending" if key == "TS_GATEWAY_ORIGIN"
                   else old_to_new.setdefault(old, secrets.token_urlsafe(48)))
            values[key] = new
            lines.append(key + "=" + new)
        path.write_text("\n".join(lines) + "\n")
        path.chmod(0o600)
        environment[owner] = values
    require(environment["gateway"]["TS_A1_SINK_TOKEN"] ==
            environment["companion"]["TS_A1_SINK_TOKEN"],
            "a1_clone_sink_token_mismatch")

    def replace_refs(value):
        if type(value) is dict:
            return {key: replace_refs(item) for key, item in value.items()}
        if type(value) is list:
            return [replace_refs(item) for item in value]
        return old_to_new.get(value, value) if type(value) is str else value

    def update(name, change):
        path = inputs / name
        value = replace_refs(read_json(path))
        change(value)
        path.write_bytes(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    indent=2).encode() + b"\n")
        path.chmod(0o600)

    clone_core = ipaddress.ip_network(c["networks"]["clone"][0])
    source_core = ipaddress.ip_network(c["networks"]["source"][0])
    source_ips = {name: str(source_core.network_address + 10 + i)
                  for i, name in enumerate(PORT_NAMES[:4])}
    clone_ips = {name: str(clone_core.network_address + 10 + i)
                 for i, name in enumerate(PORT_NAMES[:4])}
    clone_ports = dict(zip(PORT_NAMES, c["ports"]["clone"]))

    def platform(value):
        value["providers"]["provider-synthetic"]["reviewed_addresses"] = [
            clone_ips["gateway"]]
        value["web"]["origin"] = f"https://console.synthetic.test:{clone_ports['platform']}"
        versions = value["principals"]["gateway"]["config_versions"]
        value["principals"]["gateway"]["config_versions"] = sorted(set(versions) | {5})

    def gateway(value):
        mapping = {source_ips[name]: clone_ips[name] for name in ("platform", "gateway")}
        mapped = False
        for target in value["targets"]:
            addresses = target.get("addresses", [])
            require(type(addresses) is list and all(type(ip) is str for ip in addresses),
                    "a1_clone_gateway_addresses_invalid")
            mapped |= source_ips["platform"] in addresses
            target["addresses"] = [mapping.get(ip, ip) for ip in addresses]
            require(not any(ipaddress.ip_address(ip) in source_core
                            for ip in target["addresses"]),
                    "a1_clone_source_address_retained")
        require(mapped, "a1_clone_source_platform_address_missing")
        clients = [row for row in value["clients"] if row.get("service") == "companion"
                   and row.get("provider_id") == "provider-synthetic"
                   and row.get("internal") is True]
        require(len(clients) == 1, "a1_clone_gateway_client_invalid")
        clients[0]["allowed_versions"] = sorted(set(clients[0]["allowed_versions"]) | {5})

    def memory(value):
        value["local_users"]["a1-local-owner"]["credential_sha256"] = hashlib.sha256(
            environment["memory"]["TIANSHU_LOCAL_OWNER_CREDENTIAL"].encode()).hexdigest()

    def observability(value):
        value["network_subnets"] = {"observe": c["networks"]["clone"][4],
                                    "storage": c["networks"]["clone"][5]}
        value["ports"] = {"grafana": clone_ports["obs-grafana"],
                          "query": clone_ports["obs-guard"]}

    for name, action in (("config/platform/settings.json", platform),
        ("config/gateway/settings.json", gateway),
        ("config/companion/settings.json", lambda value: value.update(config_version=5)),
        ("config/memory/settings.json", memory),
        ("observability-input/settings.json", observability)):
        update(name, action)
    m = read_json(inputs / "config/memory/settings.json")
    caller = m["callers"]["companion"]
    require(caller["token"] == environment["companion"]["TS_CORE_MEMORY"] and
            m["source_sync"]["core"]["token"] == environment["companion"]["TS_MEMORY_CORE"] and
            m["source_sync"]["platform"]["token"] == environment["platform"]["TS_MEMORY_PLATFORM"] and
            caller["issuer_token"] == environment["platform"]["TS_MEMORY_PLATFORM"],
            "a1_clone_cross_service_credential_mismatch")
    for name in ("writer_token", "query_token", "metrics_token", "grafana_admin_password"):
        path = inputs / "observability-input/secrets" / name
        path.write_text(secrets.token_urlsafe(48) + "\n")
        path.chmod(0o600)
    tokens = {"companion-source-facts": environment["companion"]["TS_MEMORY_CORE"],
        "companion-web-snapshot": environment["companion"]["TS_PLATFORM_CORE"],
        "memory-select": environment["companion"]["TS_CORE_MEMORY"],
        "platform-model-snapshot": environment["gateway"]["TS_GATEWAY_PLATFORM"],
        "companion-readiness": environment["companion"]["TIANSHU_DIAGNOSTICS_TOKEN"],
        "gateway-usage": environment["companion"]["TS_CORE_GATEWAY"]}
    for name, token in tokens.items():
        path = inputs / "private" / ("token-" + name)
        path.write_text(token + "\n")
        path.chmod(0o600)
    return clone_ips, clone_ports


def _compose(c, p, clone_ips, clone_ports):
    source, inputs, clone = p["source"], p["inputs"], p["clone"]
    runtime = read_json(source / "reports/runtime-identity.json")
    images = {name: row["image_id"] for name, row in runtime["services"].items()}
    require(set(images) == OWNERS, "a1_clone_runtime_owner_set_invalid")
    nets = dict(zip(NETWORK_NAMES["clone"], c["networks"]["clone"]))
    source_core = ipaddress.ip_network(c["networks"]["source"][0])

    def mount(spec):
        path = Path(spec["source"])
        if not path.is_absolute():
            path = source / path
        relative = path.resolve().relative_to(source.resolve())
        spec["source"] = str(clone / relative)
        return spec

    def port(spec, published):
        result = []
        for entry in spec.get("ports", []):
            require(type(entry) is str and len(entry.split(":")) == 3 and
                    entry.startswith("127.0.0.1:"), "a1_clone_source_port_invalid")
            result.append({"host_ip": "127.0.0.1", "published": str(published),
                           "target": int(entry.split(":")[2]), "protocol": "tcp"})
        spec["ports"] = result

    core = read_json(source / "compose.json")
    core["name"] = c["clone_project"]
    for name in ("core", "egress", "frontend"):
        core["networks"][name]["internal"] = True
        core["networks"][name]["ipam"]["config"][0]["subnet"] = nets[name]
    core["networks"]["access"] = {"internal": False,
        "ipam": {"config": [{"subnet": nets["access"]}]}}
    for i, (owner, spec) in enumerate(core["services"].items()):
        spec["image"] = images[owner]
        spec["pull_policy"] = "never"
        spec["env_file"] = [str(clone / "private" / (owner + ".env"))]
        spec["volumes"] = [mount(value) for value in spec["volumes"]]
        if "core" in spec["networks"] and "ipv4_address" in spec["networks"]["core"]:
            expected = str(source_core.network_address + 10 + PORT_NAMES.index(owner))
            require(spec["networks"]["core"]["ipv4_address"] == expected,
                    "a1_clone_source_ip_mismatch")
            spec["networks"]["core"]["ipv4_address"] = clone_ips[owner]
        spec["networks"]["access"] = {}
        if owner == "platform":
            spec["networks"]["frontend"] = {"aliases": ["console.synthetic.test"]}
        port(spec, clone_ports[owner])
    obs = read_json(source / "observability/compose.yaml")
    obs["name"] = c["clone_project"] + "-obs"
    for name in ("observe", "storage"):
        obs["networks"][name]["internal"] = True
        obs["networks"][name]["ipam"]["config"][0]["subnet"] = nets[name]
    obs["networks"]["access"] = {"internal": False,
        "ipam": {"config": [{"subnet": nets["observability_access"]}]}}
    for owner, spec in obs["services"].items():
        spec["image"] = images[owner]
        spec["pull_policy"] = "never"
        spec["restart"] = "no"
        spec["scale"] = 1
        spec["volumes"] = [mount(value) for value in spec["volumes"]]
        if spec.get("ports"):
            if "access" not in spec["networks"]:
                spec["networks"].append("access")
            port(spec, clone_ports[owner])
    for name, doc in (("compose.json", core), ("observability/compose.yaml", obs)):
        path = inputs / name
        path.write_bytes(json.dumps(doc, ensure_ascii=False, sort_keys=True,
                                    indent=2).encode() + b"\n")
        path.chmod(0o600)


def _assertions(c, p, evidence):
    source = p["source"]
    inputs = p["inputs"]
    facts, turns, unknown = evidence["facts"], evidence["turns"], evidence["unknown"]
    scope = read_json(source / "config/memory/settings.json")["callers"]["companion"][
        "event_scopes"][0]
    conversation = scope["conversation_id"]
    ports = dict(zip(PORT_NAMES, c["ports"]["clone"]))
    prefix = c["run_label"] + "-clone-"
    actor, config = "origin:clone-actor-pending", "origin:clone-config-pending"

    def url(service, path):
        return f"https://127.0.0.1:{ports[service]}{path}"

    def select(term, label):
        return {"query": {"schema_version": 1, "request_id": prefix + label,
                          "origin": {"assertion_ref": actor}},
                "requested_scope": scope, "query_text": term,
                "selection": ["identity"], "known_scope_version": None,
                "budget": {"tokens": 4096, "bytes": 65536}}

    data_expected = {"schema_version": facts["body"]["schema_version"],
        "turns": [{"turn_id": row["turn_id"], "phase": row["phase"],
                   "committed_event": {"reality": row["committed_event"]["reality"],
                   "turn_sequence": row["committed_event"]["turn_sequence"]}}
                  for row in turns]}
    assertions = [
        {"id": "data_readback", "service": "companion", "method": "POST",
         "url": url("companion", "/internal/v1/source-facts/read"),
         "ca_file": "config/companion/tls/ca.pem",
         "token_file": "private/token-companion-source-facts",
         "request_json": {"schema_version": 1, "request_id": prefix + "source-facts",
             "mode": "snapshot", "selectors": [],
             "turn_ids": [row["turn_id"] for row in turns], "include_content": True},
         "expected_status": 200, "expected_json": data_expected},
        {"id": "forgotten", "service": "memory", "method": "POST",
         "url": url("memory", "/internal/v1/memory/select"),
         "ca_file": "config/memory/tls/ca.pem", "token_file": "private/token-memory-select",
         "request_json": select("绿色", "forgotten"), "expected_status": 200,
         "expected_json": {"selected_units": [], "omissions": ["no_match"]}},
        {"id": "source_revoked", "service": "memory", "method": "POST",
         "url": url("memory", "/internal/v1/memory/select"),
         "ca_file": "config/memory/tls/ca.pem", "token_file": "private/token-memory-select",
         "request_json": select("红色", "source-revoked"), "expected_status": 200,
         "expected_json": {"selected_units": [], "omissions": ["no_match"]}},
        {"id": "model_revoked", "service": "platform", "method": "POST",
         "url": url("platform", "/internal/v1/model-config/snapshot"),
         "ca_file": "config/platform/tls/ca.pem",
         "token_file": "private/token-platform-model-snapshot",
         "request_json": {"query": {"schema_version": 1,
             "request_id": prefix + "model-revoked",
             "origin": {"assertion_ref": config}}, "config_version": 3},
         "expected_status": 410, "expected_json": {"code": "forbidden"}},
        {"id": "unknown_no_resend", "service": "companion", "method": "POST",
         "url": url("companion", "/internal/v1/conversation/web-snapshot"),
         "ca_file": "config/companion/tls/ca.pem",
         "token_file": "private/token-companion-web-snapshot",
         "request_json": {"schema_version": 1,
             "query": {"schema_version": 1, "request_id": prefix + "unknown",
                       "origin": {"assertion_ref": actor}},
             "deadline_at": "2099-01-01T00:00:00Z", "actor_id": "actor:a1-source",
             "conversation_id": conversation, "before_turn_sequence": None, "limit": 20},
         "expected_status": 200, "expected_json": {"history": evidence["history"]}},
        {"id": "gateway_usage_readback", "service": "gateway", "method": "GET",
         "url": url("gateway", "/internal/v1/model-usage") +
                f"?view=attempts&since={evidence['since']}&until={evidence['until']}",
         "ca_file": "config/gateway/tls/ca.pem",
         "token_file": "private/token-gateway-usage", "expected_status": 200,
         "expected_json": {"schema_version": 1, "key_space": "chat",
             "identity": {"service": "companion"},
             "window": evidence["usage"]["window"],
             "coverage": evidence["usage"]["coverage"],
             "counts": evidence["usage"]["counts"],
             "attempts": evidence["expected_attempts"]}},
    ]
    require([row["id"] for row in assertions] == ASSERTIONS,
            "a1_clone_assertion_order_invalid")
    observation = {"window_seconds": 10,
        "worker_readiness": {"url": url("companion", "/health/ready"),
                             "ca_file": "config/companion/tls/ca.pem",
                             "token_file": "private/token-companion-readiness"},
        "unknown_turns": [{"turn_id": row["turn_id"],
                           "turn_sequence": row["sequence"],
                           "phase": row["phase"],
                           "delivery_state": row["delivery_state"],
                           "replies": row["replies"]} for row in unknown]}
    files = {path.relative_to(inputs).as_posix(): file_hash(path)
             for path in inputs.rglob("*") if path.is_file() and path.name != "inputs.json"}
    index = {"schema_version": "dep-j-drill-inputs/1", "files": files,
             "assertions": assertions, "runtime_observation": observation}
    path = inputs / "inputs.json"
    require(not path.exists(), "a1_clone_index_exists")
    path.write_bytes(json.dumps(index, ensure_ascii=False, sort_keys=True,
                                indent=2).encode() + b"\n")
    path.chmod(0o600)
    return index


def prepare_clone(c, *, route_reader=None, usage_reader=None, tls_factory=None, now=None):
    p = _paths(c)
    scope, source, inputs = p["scope"], p["source"], p["inputs"]
    require(source.is_dir() and not source.is_symlink() and
            not inputs.exists() and not p["clone"].exists() and
            not p["tls"].exists(), "a1_clone_target_not_empty")
    require((source / ".recovery-registration.json").is_file() and
            (source / "reports/runtime-identity.json").is_file() and
            (scope / "inputs/model-publication-template.json").is_file(),
            "a1_clone_source_not_ready")
    registration = read_json(source / ".recovery-registration.json")
    require(registration["scope_id"] == c["scope_id"] and
            registration["runtime_identity"]["sha256"] ==
            file_hash(source / "reports/runtime-identity.json"),
            "a1_clone_scope_or_runtime_mismatch")
    preparation_path = Path(c["scope_parent"]) / "preparations" / (
        c["scope_name"] + ".json")
    source_attempt = read_json(source / "reports" / c["run_label"] /
                               "a1-source-attempt.json")
    require(source_attempt.get("scope_id") == c["scope_id"] and
            source_attempt.get("preparation_config_sha256") ==
                file_hash(preparation_path) and
            source_attempt.get("allocation_sha256") == c["allocation_sha256"],
            "a1_clone_source_config_mismatch")
    evidence = _evidence(c, route_reader=route_reader, usage_reader=usage_reader,
                         now=now)
    require(type(evidence["usage_raw"]) is bytes and
            len(evidence["usage_raw"]) <= 1024 * 1024,
            "a1_clone_usage_frame_invalid")
    clone_ips, clone_ports = _copy_and_rotate(c, p, tls_factory or make_tls)
    _compose(c, p, clone_ips, clone_ports)
    _write_raw_once(scope / "inputs/gateway-usage-source-probe-private.json",
                    evidence["usage_raw"])
    index = _assertions(c, p, evidence)
    lock_source = Path(c["scope_parent"]) / "preparations" / (
        c["scope_name"] + ".code-lock.json")
    _write_raw_once(scope / "inputs/a1-code-files.json", lock_source.read_bytes())
    driver = {"schema_version": "a1-once/1", "scope_root": str(scope),
        "scope_id": c["scope_id"], "code_root": c["code_root"],
        "allocation_file": c["allocation_file"],
        "allocation_sha256": c["allocation_sha256"],
        "preparation_config_sha256": file_hash(preparation_path),
        "execution_id": c["execution_id"],
        "code_tree_sha256": c["code_tree_sha256"],
        "code_lock_sha256": file_hash(scope / "inputs/a1-code-files.json"),
        "python": c["python"], "docker": c["docker"],
        "source_name": "source", "restored_name": c["restored_name"],
        "clone_name": c["clone_name"], "inputs_name": c["clone_inputs_name"],
        "backup_name": c["backup_name"], "permit_name": c["permit_name"],
        "receipt_name": c["receipt_name"],
        "registration_sha256": file_hash(source / ".recovery-registration.json"),
        "source_manifest_sha256": file_hash(source / "release-manifest.json"),
        "source_runtime_sha256": file_hash(source / "reports/runtime-identity.json"),
        "source_deployment_sha256": file_hash(source / "deployment.json"),
        "model_template_sha256": file_hash(scope / "inputs/model-publication-template.json"),
        "initial_inputs_sha256": file_hash(inputs / "inputs.json"),
        "projects": {"core": c["clone_project"],
                     "observability": c["clone_project"] + "-obs"},
        "networks": c["networks"], "ports": c["ports"]}
    driver_path = scope / "inputs/a1-once.json"
    _write_raw_once(driver_path, json.dumps(driver, sort_keys=True,
                                            indent=2).encode() + b"\n")
    load_config(driver_path)
    return {"status": "clone_placeholders_prepared", "scope_id": c["scope_id"],
            "input_files": len(index["files"]), "assertions": len(index["assertions"]),
            "initial_inputs_sha256": driver["initial_inputs_sha256"],
            "driver_config_sha256": file_hash(driver_path)}


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    require_entry_origin(read_json(args.config)["code_root"], __file__,
                         "ops/recovery/a1_clone_prepare.py")
    c = load_preparation(args.config, phase="clone")
    p = _paths(c)
    require(not p["inputs"].exists() and not p["clone"].exists() and
            not p["tls"].exists(), "a1_clone_target_not_empty")
    if not args.execute:
        output = {"status": "planned", "scope_id": c["scope_id"],
                  "inputs_directory": str(p["inputs"])}
    else:
        output = prepare_clone(c)
    print(json.dumps(output, sort_keys=True))
    return 0


if __name__ == "__main__":
    from .safety import RecoveryError
    try:
        raise SystemExit(main())
    except RecoveryError as error:
        print(json.dumps({"status": "rejected", "reason": str(error)}))
        raise SystemExit(2)
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error):
        print(json.dumps({"status": "rejected", "reason": "invalid_or_unavailable_input"}))
        raise SystemExit(2)
