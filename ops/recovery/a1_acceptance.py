"""A1-only preparation for fixed-source, synthetic NAS acceptance.

This utility touches only a fresh A1 scope. It never opens product databases; business
state must be created through the product CLIs, HTTPS APIs, or the trusted Memory worker.
"""

import argparse
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
import re
import secrets
import subprocess
import tempfile
from pathlib import Path


def _imports(root):
    import sys

    sys.path.insert(0, str(root.parents[2] / "tooling" / "deploy" / "tianshu"))
    from bundle import verify_integrity
    from compose import compose_document
    from manifest import digest, read_json

    return verify_integrity, compose_document, digest, read_json


def _update_bundle(root, changes):
    verify_integrity, _, digest, read_json = _imports(root)
    verify_integrity(root)
    index = read_json(root / "bundle-integrity.json")
    marker = root / "INCOMPLETE"
    with marker.open("xb") as stream:
        stream.write(b"A1 bundle update in progress. Do not start services.\n")
        stream.flush()
        os.fsync(stream.fileno())
    try:
        for name, raw in changes.items():
            if name not in index["files"]:
                raise ValueError("a1_bundle_path_unlisted")
            path = root / name
            temporary = path.with_name(path.name + ".a1-tmp")
            with temporary.open("xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            index["files"][name] = digest(raw)
        index_raw = (json.dumps(index, ensure_ascii=False, indent=2) + "\n").encode()
        index_path = root / "bundle-integrity.json"
        temporary = index_path.with_name(index_path.name + ".a1-tmp")
        with temporary.open("xb") as stream:
            stream.write(index_raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, index_path)
        marker.unlink()
    except BaseException:
        # Leave INCOMPLETE as a stop marker. Recovery is deliberate and manual.
        raise
    verify_integrity(root)


def rebind_image_tags(root, suffix):
    """Give this new package unique tags so DEP-A can build its pinned source contexts."""
    root = Path(root).resolve(strict=True)
    verify_integrity, compose_document, digest, read_json = _imports(root)
    verify_integrity(root)
    if not re.fullmatch(r"[a-z0-9-]{1,24}", suffix):
        raise ValueError("a1_image_suffix_invalid")
    metadata = read_json(root / "deployment.json")
    if not metadata["project_name"].startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    manifest = read_json(root / "release-manifest.json")
    expected = {
        "platform": "c1c7547680911462439ee9c9a09f4e72f44f36a3",
        "companion": "e94b609099365f75ca933d9fed03cdfbc83ec235",
        "memory": "9a3b2bed6aebff9f0677f2c62e979859769e0c9c",
        "gateway": "601974194042641c5a85cc3c061cbd1880d7daf1",
    }
    if any(
        manifest["products"][p]["source"]["commit"] != commit
        for p, commit in expected.items()
    ):
        raise ValueError("fixed_source_commit_mismatch")
    for product, short_commit in ((p, commit[:12]) for p, commit in expected.items()):
        image = manifest["products"][product]["image"]
        prefix = image["reference"].split(":", 1)[0]
        image["reference"] = f"{prefix}:{short_commit}-{suffix}"
    site = metadata["compose_inputs"]
    compose = compose_document(manifest, site)
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + "\n").encode()
    compose_raw = (json.dumps(compose, ensure_ascii=False, indent=2) + "\n").encode()
    _update_bundle(
        root,
        {"release-manifest.json": manifest_raw, "compose.json": compose_raw},
    )
    return {p: manifest["products"][p]["image"]["reference"] for p in expected}


def bind_a1_networks(root, egress_subnet, frontend_subnet):
    """Pin all three package networks to the new, preflighted A1 subnets."""
    root = Path(root).resolve(strict=True)
    verify_integrity, compose_document, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    if not metadata["project_name"].startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    metadata["compose_inputs"]["auxiliary_subnets"] = {
        "egress": egress_subnet,
        "frontend": frontend_subnet,
    }
    manifest = read_json(root / "release-manifest.json")
    compose = compose_document(manifest, metadata["compose_inputs"])
    changes = {
        "deployment.json": (
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n"
        ).encode(),
        "compose.json": (
            json.dumps(compose, ensure_ascii=False, indent=2) + "\n"
        ).encode(),
    }
    _update_bundle(root, changes)
    return metadata["compose_inputs"]["auxiliary_subnets"]


def bind_a1_ports(root):
    """Expose only the existing Platform web port plus loopback API ports."""
    root = Path(root).resolve(strict=True)
    verify_integrity, compose_document, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    if not metadata["project_name"].startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    site = metadata["compose_inputs"]
    if site["bind_address"] != "127.0.0.1":
        raise ValueError("a1_loopback_scope_required")
    site["a1_loopback_api_ports"] = {
        "companion": 19512,
        "memory": 19513,
        "gateway": 19514,
    }
    manifest = read_json(root / "release-manifest.json")
    compose = compose_document(manifest, site)
    _update_bundle(
        root,
        {
            "deployment.json": _raw(metadata),
            "compose.json": _raw(compose),
            "tools/bundle.py": _deployment_tool_bytes("bundle.py"),
            "tools/compose.py": _deployment_tool_bytes("compose.py"),
            "tools/configuration.py": _deployment_tool_bytes("configuration.py"),
        },
    )
    return {
        "platform": site["web_port"],
        "companion": 19512,
        "memory": 19513,
        "gateway": 19514,
    }


def _raw(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


def _deployment_tool_bytes(name):
    return (
        Path(__file__).resolve().parents[2] / "deploy/tianshu" / name
    ).read_bytes()


def _update_env(raw, values):
    lines = raw.decode("utf-8").splitlines()
    retained = [
        line
        for line in lines
        if not ("=" in line and line.split("=", 1)[0] in values)
    ]
    retained.extend(f"{name}={value}" for name, value in sorted(values.items()))
    return ("\n".join(retained) + "\n").encode()


def prepare_synthetic(root):
    """Prepare only this new A1 source package for explicitly fictional dialogue."""
    root = Path(root).resolve(strict=True)
    verify_integrity, compose_document, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    site = metadata["compose_inputs"]
    if (
        not metadata["project_name"].startswith("tianshu-qa-a1-")
        or site["bind_address"] != "127.0.0.1"
    ):
        raise ValueError("a1_loopback_scope_required")
    manifest = read_json(root / "release-manifest.json")
    expected = {
        "platform": "c1c7547680911462439ee9c9a09f4e72f44f36a3",
        "companion": "e94b609099365f75ca933d9fed03cdfbc83ec235",
        "memory": "9a3b2bed6aebff9f0677f2c62e979859769e0c9c",
        "gateway": "601974194042641c5a85cc3c061cbd1880d7daf1",
    }
    if any(
        manifest["products"][product]["source"]["commit"] != commit
        for product, commit in expected.items()
    ):
        raise ValueError("fixed_source_commit_mismatch")
    platform_path = root / "config/platform/settings.json"
    companion_path = root / "config/companion/settings.json"
    memory_path = root / "config/memory/settings.json"
    platform = read_json(platform_path)
    companion = read_json(companion_path)
    memory = read_json(memory_path)
    if (
        platform["providers"]
        or platform["web"]["dialogue_enabled"]
        or "web-source-actor" in platform["entries"]
        or "web-input-source" in platform["input_entries"]
        or "web-source" in companion["bindings"]
        or memory["callers"]["companion"]["event_scopes"]
    ):
        raise ValueError("a1_synthetic_configuration_not_fresh")

    # DEP-G owns synthetic model settings and its secret; no real provider is used.
    import sys

    sys.path.insert(0, str(root / "tooling/deploy/tianshu"))
    from linux_bootstrap import prepare

    publication, _ = prepare(root, True)
    platform = read_json(platform_path)
    companion = read_json(companion_path)
    memory = read_json(memory_path)

    account = copy.deepcopy(platform["principals"]["admin"]["account"])
    channel = {
        "namespace": "web",
        "binding_id": "web-source",
        "channel_conversation_id": "a1-synthetic-private",
        "thread_id": None,
    }
    actor = copy.deepcopy(platform["entries"]["web-actor"])
    actor.update(account=account, channel=channel, actor_id="actor:a1-source")
    input_entry = copy.deepcopy(platform["input_entries"]["web-input"])
    input_entry.update(
        account=account,
        channel=channel,
        actor_entries=["web-source-actor"],
        default_actor_ids=["actor:a1-source"],
    )
    platform["entries"]["web-source-actor"] = actor
    platform["input_entries"]["web-input-source"] = input_entry
    platform["web"]["input_entries"] = sorted(
        set(platform["web"]["input_entries"]) | {"web-input-source"}
    )

    companion["roles"]["actor:a1-source"] = {
        "version": 1,
        "persona": "Use only the explicitly fictional A1 input and identify synthetic facts.",
    }
    companion["bindings"]["web-source"] = {
        "namespace": "web",
        "service": "platform",
        "audience": "self_private",
        "actor_ids": ["actor:a1-source"],
        "classification": {
            "value": "fictional",
            "basis": "registered_input_mode",
            "policy_ref": "input-mode:a1-synthetic",
            "policy_version": 1,
        },
    }
    companion["policy"]["silence_ms"] = 500
    companion["policy"]["delivery_reconcile_timeout_ms"] = 3000
    companion["services"]["platform_sender"] = {
        "url": "https://gateway.internal:9444",
        "token_env": "TS_A1_SINK_TOKEN",
        "ca_file": "/etc/tianshu/tls/ca.pem",
    }
    memory["callers"]["companion"]["allowed_actors"] = ["actor:a1-source"]
    memory["callers"]["companion"]["event_scopes"] = []

    sink_token = secrets.token_urlsafe(48)
    gateway_env = root / "private/gateway.env"
    companion_env = root / "private/companion.env"
    gateway_env_raw = _update_env(
        gateway_env.read_bytes(), {"TS_A1_SINK_TOKEN": sink_token}
    )
    companion_env_raw = _update_env(
        companion_env.read_bytes(), {"TS_A1_SINK_TOKEN": sink_token}
    )
    site["a1_loopback_api_ports"] = {
        "companion": 19512,
        "memory": 19513,
        "gateway": 19514,
    }
    compose = compose_document(manifest, site)
    _update_bundle(
        root,
        {
            "deployment.json": _raw(metadata),
            "config/platform/settings.json": _raw(platform),
            "config/companion/settings.json": _raw(companion),
            "config/memory/settings.json": _raw(memory),
            "private/gateway.env": gateway_env_raw,
            "private/companion.env": companion_env_raw,
            "compose.json": _raw(compose),
            "tools/bundle.py": _deployment_tool_bytes("bundle.py"),
            "tools/compose.py": _deployment_tool_bytes("compose.py"),
            "tools/configuration.py": _deployment_tool_bytes("configuration.py"),
        },
    )
    return {
        "status": "prepared",
        "synthetic_only": True,
        "classification": "fictional",
        "actor_id": "actor:a1-source",
        "channel": channel,
        "publication": publication,
        "sink_token_sha256": hashlib.sha256(sink_token.encode()).hexdigest(),
    }


def build_synthetic_inputs(root, output):
    """Write three explicitly fictional product-CLI inputs, never product state."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    platform = read_json(root / "config/platform/settings.json")
    source = platform["input_entries"].get("web-input-source")
    if not source or source["default_actor_ids"] != ["actor:a1-source"]:
        raise ValueError("a1_synthetic_input_binding_required")
    out = Path(output).resolve()
    out.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    messages = {
        "forget": "虚构事实 A：测试角色的标记是蓝色，仅用于 NAS-A1 恢复验收。",
        "source-revoke": "虚构事实 B：测试角色的标记是圆形，仅用于 NAS-A1 恢复验收。",
        "active-readback": "虚构事实 C：测试角色的标记是方形，仅用于 NAS-A1 恢复验收。",
        "forget-success": (
            "虚构事实 D：测试角色的标记是绿色，测试角色喜欢阅读，"
            "仅用于 NAS-A1 恢复验收。"
        ),
        "source-revoke-success": "虚构事实 E：测试角色的标记是红色，仅用于 NAS-A1 恢复验收。",
        "forget-success-v2": (
            "虚构事实 F：测试角色的标记是绿色，测试角色喜欢阅读，"
            "仅用于 NAS-A1 恢复验收。"
        ),
        "source-revoke-success-v2": "虚构事实 G：测试角色的标记是红色，仅用于 NAS-A1 恢复验收。",
        "forget-success-v3": (
            "虚构事实 H：测试角色的标记是绿色，测试角色喜欢阅读，"
            "仅用于 NAS-A1 恢复验收。"
        ),
        "source-revoke-success-v3": "虚构事实 I：测试角色的标记是红色，仅用于 NAS-A1 恢复验收。",
    }
    result = []
    for index, (label, text_value) in enumerate(messages.items()):
        sent = (now + timedelta(milliseconds=index)).isoformat(timespec="milliseconds")
        sent = sent.replace("+00:00", "Z")
        physical = {
            "message_key": {
                "channel": source["channel"],
                "message_id": "a1-" + label,
                "revision": 1,
            },
            "author": source["account"],
            "sent_at": sent,
            "kind": "message",
            "parts": [{"kind": "text", "text": text_value}],
            "reply_refs": [],
            "mentioned_accounts": [],
        }
        path = out / f"register-{label}.json"
        path.write_bytes(_raw({"entry_id": "web-input-source", "input": physical}))
        result.append({"label": label, "path": str(path)})
    return {"synthetic_only": True, "inputs": result}


def build_fanout_request(root, source_ref, label, input_path, output):
    """Bind one registered synthetic Platform input to its normal fanout command."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    platform = read_json(root / "config/platform/settings.json")
    if not isinstance(source_ref, str) or not source_ref.startswith("source-input:"):
        raise ValueError("a1_platform_source_ref_required")
    allowed = {
        "forget",
        "source-revoke",
        "active-readback",
        "forget-success",
        "source-revoke-success",
        "forget-success-v2",
        "source-revoke-success-v2",
        "forget-success-v3",
        "source-revoke-success-v3",
    }
    if label not in allowed:
        raise ValueError("a1_synthetic_label_invalid")
    now = datetime.now(timezone.utc)
    deadline = (now + timedelta(minutes=2)).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )
    request = {
        "schema_version": 1,
        "request_id": "a1-dispatch-" + label,
        "idempotency_key": "a1-dispatch-key-" + label,
        "origin": {"assertion_ref": source_ref},
        "deadline_at": deadline,
    }
    registration = json.loads(Path(input_path).read_text(encoding="utf-8"))
    if registration.get("entry_id") != "web-input-source":
        raise ValueError("a1_registered_input_required")
    physical_path = Path(output).resolve()
    physical_path.write_bytes(
        _raw(
            {
                "schema_version": 1,
                "command": request,
                "input": registration["input"],
                "target_actor_ids": ["actor:a1-source"],
            }
        )
    )
    return {"path": str(physical_path), "request_id": request["request_id"]}


def set_gateway_origin(root, assertion_ref):
    """Store only the fresh Platform-issued config origin in the Gateway env file."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    platform = read_json(root / "config/platform/settings.json")
    if not isinstance(assertion_ref, str) or not assertion_ref.startswith("origin:"):
        raise ValueError("a1_platform_origin_required")
    config_entry = platform["entries"].get("config-entry")
    if not config_entry or config_entry["routes"] != [
        {"caller": "gateway", "receiver": "platform", "purpose": "config.snapshot"}
    ]:
        raise ValueError("a1_gateway_origin_route_required")
    env_path = root / "private/gateway.env"
    _update_bundle(
        root,
        {
            "private/gateway.env": _update_env(
                env_path.read_bytes(), {"TS_GATEWAY_ORIGIN": assertion_ref}
            )
        },
    )
    return {"status": "bound", "origin_ref_sha256": hashlib.sha256(assertion_ref.encode()).hexdigest()}


def bind_synthetic_classification(root):
    """Complete the shared-contract fields for this fictional registered input mode."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    if not metadata.get("project_name", "").startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    companion_path = root / "config/companion/settings.json"
    companion = read_json(companion_path)
    binding = companion.get("bindings", {}).get("web-source")
    if (
        not binding
        or binding.get("actor_ids") != ["actor:a1-source"]
        or binding.get("classification", {}).get("value") != "fictional"
        or binding.get("classification", {}).get("basis") != "registered_input_mode"
    ):
        raise ValueError("a1_fictional_binding_required")
    binding["classification"]["policy_ref"] = "input-mode:a1-synthetic"
    binding["classification"]["policy_version"] = 1
    _update_bundle(root, {"config/companion/settings.json": _raw(companion)})
    return {"status": "bound", "classification": "fictional", "policy_version": 1}


def enable_synthetic_memory_candidates(root):
    """Enable the real A1 candidate handoff only for the synthetic source binding."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    if not metadata.get("project_name", "").startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    companion_path = root / "config/companion/settings.json"
    companion = read_json(companion_path)
    binding = companion.get("bindings", {}).get("web-source")
    if (
        not binding
        or binding.get("actor_ids") != ["actor:a1-source"]
        or binding.get("classification", {}).get("value") != "fictional"
        or binding.get("classification", {}).get("basis") != "registered_input_mode"
        or binding.get("classification", {}).get("policy_ref") != "input-mode:a1-synthetic"
    ):
        raise ValueError("a1_fictional_binding_required")
    companion["automatic_memory_candidates"] = True
    _update_bundle(root, {"config/companion/settings.json": _raw(companion)})
    return {"status": "enabled", "synthetic_actor": "actor:a1-source"}


def bind_synthetic_model_version(root, config_version):
    """Align the A1 Gateway caller and Platform principal with one synthetic version."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    if (
        not metadata.get("project_name", "").startswith("tianshu-qa-a1-")
        or config_version not in {1, 2, 3}
    ):
        raise ValueError("a1_model_version_required")
    platform_path = root / "config/platform/settings.json"
    gateway_path = root / "config/gateway/settings.json"
    companion_path = root / "config/companion/settings.json"
    platform = read_json(platform_path)
    gateway = read_json(gateway_path)
    companion = read_json(companion_path)
    principal = platform.get("principals", {}).get("gateway")
    clients = [
        client
        for client in gateway.get("clients", [])
        if client.get("service") == "companion"
        and client.get("provider_id") == "provider-synthetic"
        and client.get("internal") is True
    ]
    if not principal or len(clients) != 1:
        raise ValueError("a1_synthetic_gateway_binding_required")
    principal["config_versions"] = sorted(
        set(principal.get("config_versions", [])) | {1, config_version}
    )
    clients[0]["allowed_versions"] = sorted(
        set(clients[0].get("allowed_versions", [])) | {1, config_version}
    )
    if companion.get("automatic_memory_candidates") is not True:
        raise ValueError("a1_synthetic_memory_handoff_required")
    companion["config_version"] = config_version
    _update_bundle(
        root,
        {
            "config/platform/settings.json": _raw(platform),
            "config/gateway/settings.json": _raw(gateway),
            "config/companion/settings.json": _raw(companion),
        },
    )
    return {
        "status": "bound",
        "config_version": config_version,
        "platform_allowed_versions": principal["config_versions"],
        "gateway_allowed_versions": clients[0]["allowed_versions"],
    }


def set_companion_model_version(root, config_version):
    """Pin this A1 Companion turn adapter to the published synthetic version."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    if (
        not metadata.get("project_name", "").startswith("tianshu-qa-a1-")
        or config_version not in {1, 2, 3}
    ):
        raise ValueError("a1_model_version_required")
    companion_path = root / "config/companion/settings.json"
    companion = read_json(companion_path)
    binding = companion.get("bindings", {}).get("web-source")
    if (
        companion.get("automatic_memory_candidates") is not True
        or not binding
        or binding.get("actor_ids") != ["actor:a1-source"]
        or binding.get("classification", {}).get("value") != "fictional"
    ):
        raise ValueError("a1_fictional_memory_handoff_required")
    companion["config_version"] = config_version
    _update_bundle(root, {"config/companion/settings.json": _raw(companion)})
    return {"status": "bound", "config_version": config_version}


def bind_memory_scopes(root, document):
    """Bind only observed A1 scopes and one independent synthetic local owner."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    scopes = document["scopes"]
    account = document["account"]
    if (
        not isinstance(scopes, list)
        or len(scopes) != 3
        or any(
            not isinstance(scope, dict)
            or set(scope) != {"actor_id", "person_id", "audience", "conversation_id"}
            or scope["actor_id"] != "actor:a1-source"
            or scope["audience"] != "self_private"
            or not scope["person_id"]
            or not scope["conversation_id"]
            for scope in scopes
        )
        or len({json.dumps(scope, sort_keys=True) for scope in scopes}) != 1
    ):
        raise ValueError("a1_exact_scopes_required")
    if (
        not isinstance(account, dict)
        or set(account) != {"namespace", "immutable_account_id"}
    ):
        raise ValueError("a1_stable_account_required")
    memory_path = root / "config/memory/settings.json"
    memory = read_json(memory_path)
    registrations = memory.setdefault("local_users", {})
    if "a1-local-owner" in registrations:
        raise ValueError("a1_local_owner_already_registered")
    credential = secrets.token_urlsafe(48)
    exact_scope = scopes[0]
    memory["callers"]["companion"]["event_scopes"] = [exact_scope]
    registrations["a1-local-owner"] = {
        "credential_sha256": hashlib.sha256(credential.encode()).hexdigest(),
        "account": account,
        "actors": ["actor:a1-source"],
        "revision_scopes": [exact_scope],
        "profile_permissions": [],
        "disabled": False,
    }
    env_path = root / "private/memory.env"
    env_raw = _update_env(
        env_path.read_bytes(), {"TIANSHU_LOCAL_OWNER_CREDENTIAL": credential}
    )
    _update_bundle(
        root,
        {
            "config/memory/settings.json": _raw(memory),
            "private/memory.env": env_raw,
        },
    )
    return {
        "status": "bound",
        "event_scopes": 1,
        "local_owner": "a1-local-owner",
        "credential_sha256": hashlib.sha256(credential.encode()).hexdigest(),
    }


def read_companion_source_facts(root, input_path):
    """Read real Companion owner events with Memory's registered source-facts identity."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    request = json.loads(Path(input_path).resolve(strict=True).read_bytes())
    if (
        request.get("schema_version") != 1
        or request.get("mode") != "snapshot"
        or not isinstance(request.get("selectors"), list)
        or not isinstance(request.get("turn_ids"), list)
        or request.get("include_content") is not True
    ):
        raise ValueError("a1_source_facts_snapshot_required")
    frame = (json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    code = (
        "import json,os,ssl,sys,urllib.error,urllib.request\n"
        "body=sys.stdin.buffer.readline(1048577)\n"
        "assert body.endswith(b'\\n') and len(body)<=1048576\n"
        "request=urllib.request.Request('https://companion.internal:8765/internal/v1/source-facts/read',data=body,headers={'Authorization':'Bearer '+os.environ['TS_MEMORY_CORE'],'Content-Type':'application/json'},method='POST')\n"
        "context=ssl.create_default_context(cafile='/etc/tianshu/tls/ca.pem')\n"
        "try:\n"
        " response=urllib.request.urlopen(request,context=context,timeout=20)\n"
        "except urllib.error.HTTPError as error:\n"
        " response=error\n"
        "result=response.read(1048577)\n"
        "assert len(result)<=1048576\n"
        "sys.stdout.buffer.write(json.dumps({'http_status':response.status,'body':json.loads(result)},ensure_ascii=False).encode())"
    )
    command = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
        "exec",
        "-i",
        "-T",
        "companion",
        "python",
        "-c",
        code,
    ]
    result = subprocess.run(command, input=frame, capture_output=True, timeout=45)
    if result.returncode:
        diagnostic = (result.stderr or b"").decode("utf-8", errors="replace").splitlines()
        detail = diagnostic[-1][:180] if diagnostic else "container_command_failed"
        raise RuntimeError("companion_source_facts_read_failed:" + detail)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("companion_source_facts_read_invalid") from exc


def read_memory_selection(root, input_path):
    """Read the real Memory select API for the one bound fictional A1 scope."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    request = json.loads(Path(input_path).resolve(strict=True).read_bytes())
    scope = request.get("requested_scope")
    query = request.get("query")
    origin_ref = (
        query.get("origin", {}).get("assertion_ref")
        if isinstance(query, dict) and isinstance(query.get("origin"), dict)
        else None
    )
    if (
        not isinstance(scope, dict)
        or scope.get("actor_id") != "actor:a1-source"
        or scope.get("person_id") != "person:a8894fdd11b1479191994548603f42c1"
        or scope.get("audience") != "self_private"
        or scope.get("conversation_id") != "conv:d530b7572e244c27a441a49909d187a8"
        or request.get("selection") != ["identity"]
        or not isinstance(origin_ref, str)
        or not origin_ref.startswith("origin:")
    ):
        raise ValueError("a1_exact_memory_selection_required")
    frame = (json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    code = (
        "import json,os,ssl,sys,urllib.error,urllib.request\n"
        "body=sys.stdin.buffer.readline(262145)\n"
        "assert body.endswith(b'\\n') and len(body)<=262144\n"
        "request=urllib.request.Request('https://memory.internal:8130/internal/v1/memory/select',data=body,headers={'Authorization':'Bearer '+os.environ['TS_CORE_MEMORY'],'Content-Type':'application/json'},method='POST')\n"
        "context=ssl.create_default_context(cafile='/etc/tianshu/tls/ca.pem')\n"
        "try:\n"
        " response=urllib.request.urlopen(request,context=context,timeout=20)\n"
        "except urllib.error.HTTPError as error:\n"
        " response=error\n"
        "result=response.read(262145)\n"
        "assert len(result)<=262144\n"
        "sys.stdout.buffer.write(json.dumps({'http_status':response.status,'body':json.loads(result)},ensure_ascii=False).encode())"
    )
    command = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
        "exec",
        "-i",
        "-T",
        "companion",
        "python",
        "-c",
        code,
    ]
    result = subprocess.run(command, input=frame, capture_output=True, timeout=45)
    if result.returncode:
        diagnostic = (result.stderr or b"").decode("utf-8", errors="replace").splitlines()
        detail = diagnostic[-1][:180] if diagnostic else "container_command_failed"
        raise RuntimeError("a1_memory_select_failed:" + detail)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("a1_memory_select_invalid") from exc


def run_local_user_action(root, input_path):
    """Run Memory's explicit user-action CLI with the private configured owner token."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    operation = json.loads(Path(input_path).resolve(strict=True).read_bytes())
    if (
        not isinstance(operation, dict)
        or set(operation) != {"operation", "request", "expires_at"}
        or operation.get("operation") != "confirm_revision"
        or not isinstance(operation.get("request"), dict)
    ):
        raise ValueError("a1_local_user_operation_required")
    frame = (json.dumps(operation, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    compose = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
    ]
    writer = (
        "import os,sys\n"
        "body=sys.stdin.buffer.readline(262145)\n"
        "assert body.endswith(b'\\n') and len(body)<=262144\n"
        "path='/tmp/tianshu-a1-local-user-action.json'\n"
        "fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)\n"
        "with os.fdopen(fd,'wb') as stream: stream.write(body);stream.flush();os.fsync(stream.fileno())\n"
        "os.chmod(path,0o600)"
    )
    staged = subprocess.run(
        [*compose, "exec", "-i", "-T", "memory", "python", "-c", writer],
        input=frame,
        capture_output=True,
        timeout=30,
    )
    if staged.returncode:
        raise RuntimeError("a1_local_user_operation_stage_failed")
    result = subprocess.run(
        [
            *compose,
            "exec",
            "-T",
            "memory",
            "tianshu-memory",
            "--config",
            "/etc/tianshu/settings.json",
            "user-action",
            "/tmp/tianshu-a1-local-user-action.json",
            "--principal",
            "a1-local-owner",
            "--credential-env",
            "TIANSHU_LOCAL_OWNER_CREDENTIAL",
        ],
        capture_output=True,
        timeout=45,
    )
    if result.returncode:
        output = (result.stdout or result.stderr or b"").decode("utf-8", errors="replace").splitlines()
        detail = output[-1][:180] if output else "user_action_failed"
        raise RuntimeError("a1_local_user_action_failed:" + detail)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("a1_local_user_action_invalid") from exc


def post_memory_revision(root, input_path):
    """Post the same confirmed request through the existing Companion→Memory API."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    request_body = json.loads(Path(input_path).resolve(strict=True).read_bytes())
    if not isinstance(request_body, dict) or not isinstance(request_body.get("command"), dict):
        raise ValueError("a1_revision_request_required")
    frame = (json.dumps(request_body, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    code = (
        "import json,os,ssl,sys,urllib.error,urllib.request\n"
        "body=sys.stdin.buffer.readline(262145)\n"
        "assert body.endswith(b'\\n') and len(body)<=262144\n"
        "request=urllib.request.Request('https://memory.internal:8130/internal/v1/memory/revise',data=body,headers={'Authorization':'Bearer '+os.environ['TS_CORE_MEMORY'],'Content-Type':'application/json'},method='POST')\n"
        "context=ssl.create_default_context(cafile='/etc/tianshu/tls/ca.pem')\n"
        "try:\n"
        " response=urllib.request.urlopen(request,context=context,timeout=20)\n"
        "except urllib.error.HTTPError as error:\n"
        " response=error\n"
        "result=response.read(262145)\n"
        "assert len(result)<=262144\n"
        "sys.stdout.buffer.write(json.dumps({'http_status':response.status,'body':json.loads(result)},ensure_ascii=False).encode())"
    )
    command = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
        "exec",
        "-i",
        "-T",
        "companion",
        "python",
        "-c",
        code,
    ]
    result = subprocess.run(command, input=frame, capture_output=True, timeout=45)
    if result.returncode:
        diagnostic = (result.stderr or b"").decode("utf-8", errors="replace").splitlines()
        detail = diagnostic[-1][:180] if diagnostic else "container_command_failed"
        raise RuntimeError("a1_memory_revision_post_failed:" + detail)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("a1_memory_revision_post_invalid") from exc


def trusted_memory_commit(root, input_path):
    """Use the deployed Memory API and its actual TrustedWorkflow, no SQL writes."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    payload = json.loads(Path(input_path).resolve(strict=True).read_bytes())
    if not isinstance(payload, dict) or not (
        set(payload) == {"event", "draft"}
        or set(payload) == {"event", "drafts"}
    ):
        raise ValueError("a1_trusted_memory_frame_required")
    drafts = payload.get("drafts", [payload.get("draft")])
    if not isinstance(drafts, list) or not drafts or any(not isinstance(item, dict) for item in drafts):
        raise ValueError("a1_trusted_memory_drafts_required")
    event_frame = (json.dumps(payload["event"], ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n").encode()
    companion_code = (
        "import json,os,ssl,sys,urllib.request\n"
        "body=sys.stdin.buffer.readline(1048577)\n"
        "assert body.endswith(b'\\n') and len(body)<=1048576\n"
        "context=ssl.create_default_context(cafile='/etc/tianshu/tls/ca.pem')\n"
        "request=urllib.request.Request('https://memory.internal:8130/internal/v1/memory/turn-commits',data=body,headers={'Authorization':'Bearer '+os.environ['TS_CORE_MEMORY'],'Content-Type':'application/json','Accept-Encoding':'identity'},method='POST')\n"
        "with urllib.request.urlopen(request,context=context,timeout=20) as response: result=response.read(65537)\n"
        "assert len(result)<=65536\n"
        "sys.stdout.buffer.write(result)"
    )
    base_command = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
        "exec",
        "-i",
        "-T",
    ]
    job_result = subprocess.run(
        [*base_command, "companion", "python", "-c", companion_code],
        input=event_frame,
        capture_output=True,
        timeout=45,
    )
    if job_result.returncode:
        raise RuntimeError("a1_candidate_job_request_failed")
    try:
        receipt = json.loads(job_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("a1_candidate_job_receipt_invalid") from exc
    job_id = receipt.get("candidate_job_ref")
    if not isinstance(job_id, str) or not job_id:
        raise ValueError("a1_candidate_job_missing")
    commit_frame = (
        json.dumps(
            {"job_id": job_id, "drafts": drafts},
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode()
    memory_code = (
        "import json,sys\n"
        "from tianshu_memory.app import configured_app\n"
        "from tianshu_memory.domain import strict_json\n"
        "from tianshu_memory.workflow import TrustedWorkflow\n"
        "body=sys.stdin.buffer.readline(1048577)\n"
        "assert body.endswith(b'\\n') and len(body)<=1048576\n"
        "payload=strict_json(body)\n"
        "app=configured_app()\n"
        "result=TrustedWorkflow(app.state.memory).commit_candidate(payload)\n"
        "sys.stdout.write(json.dumps(result,ensure_ascii=False))"
    )
    commit_result = subprocess.run(
        [*base_command, "memory", "python", "-c", memory_code],
        input=commit_frame,
        capture_output=True,
        timeout=45,
    )
    if commit_result.returncode:
        detail = (commit_result.stderr or b"").decode("utf-8", errors="replace").splitlines()
        reason = detail[-1][:180] if detail else "container_command_failed"
        raise RuntimeError("a1_trusted_memory_commit_failed:" + reason)
    try:
        committed = json.loads(commit_result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("a1_trusted_memory_commit_invalid") from exc
    return {
        "state": committed["state"],
        "job_id": job_id,
        "record_ids": committed.get("record_ids", []),
        "event_id": payload["event"]["event_id"],
        "scope": payload["event"]["scope"],
    }


def run_platform_cli(root, action, input_path):
    """Run an allowlisted Platform action through its supported in-container CLI."""
    root = Path(root).resolve(strict=True)
    _, _, _, read_json = _imports(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    if action not in {
        "publish",
        "issue",
        "register-input",
        "dispatch-fanout",
        "revoke-origin",
        "revoke-config",
        "view-config",
    }:
        raise ValueError("platform_cli_action_not_allowlisted")
    request_path = Path(input_path).resolve(strict=True)
    try:
        document = json.loads(request_path.read_bytes())
        request = (
            json.dumps(document, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
            + "\n"
        ).encode("utf-8")
    except (UnicodeError, ValueError) as exc:
        raise ValueError("platform_cli_request_invalid") from exc
    if len(request) > 1048576:
        raise ValueError("platform_cli_request_frame_invalid")
    container_cli = (
        "import hashlib,json,os,subprocess,sys,tempfile\n"
        "action=sys.argv[1]\n"
        "data=sys.stdin.buffer.readline(1048577)\n"
        "assert data.endswith(b'\\n') and len(data)<=1048576\n"
        "sys.stderr.write('request_frame='+str(len(data))+':'+hashlib.sha256(data).hexdigest()+'\\n')\n"
        "with tempfile.NamedTemporaryFile(dir='/tmp',suffix='.json') as f:\n"
        " f.write(data);f.flush()\n"
        " result=subprocess.run([sys.executable,'-m','services.platform','--settings',"
        "'/etc/tianshu/settings.json','local','--credential-env','TS_ADMIN_TOKEN',"
        "action,'--input',f.name],capture_output=True,timeout=25)\n"
        " if result.returncode==0:sys.stdout.buffer.write(result.stdout)\n"
        " else:\n"
        "  sys.stderr.buffer.write(result.stdout)\n"
        "  sys.stderr.buffer.write(result.stderr)\n"
        " sys.exit(result.returncode)"
    )
    command = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
        "exec",
        "-i",
        "-T",
        "platform",
        "python",
        "-c",
        container_cli,
        action,
    ]
    result = subprocess.run(command, input=request, capture_output=True, timeout=45)
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace")[-2000:]
        expected = hashlib.sha256(request).hexdigest()
        raise RuntimeError(
            f"platform_cli_failed request_frame={len(request)}:{expected} " + detail
        )
    try:
        receipt = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("platform_cli_receipt_invalid") from exc
    return {"action": action, "receipt": receipt}


def diagnose_platform_publication(root, input_path):
    """Run Platform's pure publication validators without opening its product store."""
    root = Path(root).resolve(strict=True)
    _, _, _, read_json = _imports(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    request = Path(input_path).resolve(strict=True).read_bytes()
    if not request.endswith(b"\n") or len(request) > 1048576:
        raise ValueError("platform_cli_request_frame_invalid")
    json.loads(request)
    validator = (
        "import json,sys,time\n"
        "from pathlib import Path\n"
        "from services.platform.auth import Auth\n"
        "from services.platform.contracts import Contracts,Fault,loads\n"
        "from services.platform.models import Models\n"
        "settings=loads(Path('/etc/tianshu/settings.json').read_bytes())\n"
        "document=loads(sys.stdin.buffer.read())\n"
        "contracts=Contracts(settings['contract_directory'],settings.get('source_contract_directory'))\n"
        "models=Models(None,Auth(settings,contracts),contracts,None,settings,time.time)\n"
        "def check(name,fn):\n"
        " try: return fn()\n"
        " except Fault as exc:\n"
        "  print(json.dumps({'step':name,'code':exc.code,'status':exc.status}));sys.exit(1)\n"
        "check('contract',lambda:contracts.check('model#config_response',document))\n"
        "check('window',lambda:models._validate_window(document))\n"
        "providers=check('providers',lambda:models._validate_providers(document['providers'],'openai-chat-completions',pinned=False))\n"
        "check('bindings',lambda:models._validate_bindings(document,providers))\n"
        "check('secret_scan',lambda:models._validate_no_secrets(document))\n"
        "print(json.dumps({'valid':True}))"
    )
    command = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
        "exec",
        "-i",
        "-T",
        "platform",
        "python",
        "-c",
        validator,
    ]
    result = subprocess.run(command, input=request, capture_output=True, timeout=45)
    output = result.stdout or result.stderr
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("platform_validation_diagnostic_failed") from exc


def publish_synthetic_config(root, template_path, config_version=1):
    """Refresh only the A1 fixture's short validity window, then use Platform's CLI."""
    document = json.loads(Path(template_path).read_text(encoding="utf-8"))
    providers = document.get("providers")
    if (
        config_version not in {1, 2, 3}
        or document.get("config_version") != 1
        or document.get("status") != "published"
        or not isinstance(providers, list)
        or len(providers) != 1
        or providers[0].get("provider_id") != "provider-synthetic"
        or providers[0].get("model_id") != "synthetic-recorded-text"
        or providers[0].get("capability_verification") != "fixture_only"
        or providers[0].get("base_url") != "https://gateway.internal:9443/v1"
    ):
        raise ValueError("a1_synthetic_model_template_required")
    document["config_version"] = config_version
    now = datetime.now(timezone.utc)
    document["published_at"] = (now - timedelta(seconds=3)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    document["usable_until"] = (now + timedelta(minutes=14, seconds=30)).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", dir="/tmp") as stream:
        stream.write(_raw(document))
        stream.flush()
        validation = diagnose_platform_publication(root, stream.name)
        if validation.get("valid") is not True:
            raise ValueError("a1_publication_validation_failed:" + json.dumps(validation))
        result = run_platform_cli(root, "publish", stream.name)
    result["published_at"] = document["published_at"]
    result["usable_until"] = document["usable_until"]
    return result


def read_companion_web_snapshot(root, input_path):
    """Read the published Companion snapshot API from the owned A1 service container."""
    root = Path(root).resolve(strict=True)
    _, _, _, read_json = _imports(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    request = json.loads(Path(input_path).resolve(strict=True).read_bytes())
    frame = (
        json.dumps(request, ensure_ascii=False, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()
    code = (
        "import json,os,ssl,sys,urllib.error,urllib.request\n"
        "body=sys.stdin.buffer.readline(1048577)\n"
        "assert body.endswith(b'\\n') and len(body)<=1048576\n"
        "request=urllib.request.Request('https://companion.internal:8765/internal/v1/conversation/web-snapshot',data=body,headers={'Authorization':'Bearer '+os.environ['TS_PLATFORM_CORE'],'Content-Type':'application/json'},method='POST')\n"
        "context=ssl.create_default_context(cafile='/etc/tianshu/tls/ca.pem')\n"
        "try:\n"
        " response=urllib.request.urlopen(request,context=context,timeout=20)\n"
        "except urllib.error.HTTPError as error:\n"
        " response=error\n"
        "result=response.read(1048577)\n"
        "assert len(result)<=1048576\n"
        "sys.stdout.buffer.write(json.dumps({'http_status':response.status,'body':json.loads(result)},ensure_ascii=False).encode())"
    )
    command = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
        "exec",
        "-i",
        "-T",
        "companion",
        "python",
        "-c",
        code,
    ]
    result = subprocess.run(command, input=frame, capture_output=True, timeout=45)
    if result.returncode:
        raise RuntimeError("companion_web_snapshot_failed")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("companion_web_snapshot_invalid") from exc


def start_synthetic_model(root):
    """Start and health-check the packaged offline Gateway upstream fixture."""
    root = Path(root).resolve(strict=True)
    verify_integrity, _, _, read_json = _imports(root)
    verify_integrity(root)
    metadata = read_json(root / "deployment.json")
    project = metadata.get("project_name", "")
    if not project.startswith("tianshu-qa-a1-"):
        raise ValueError("a1_scope_required")
    fixture_path = root / "tools/synthetic_model.py"
    probe_path = root / "tools/container_probe.py"
    if not fixture_path.is_file() or not probe_path.is_file():
        raise ValueError("a1_synthetic_model_fixture_required")
    compose = [
        "/volume2/@appstore/ContainerManager/usr/bin/docker",
        "compose",
        "-p",
        project,
        "--project-directory",
        str(root),
        "-f",
        str(root / "compose.json"),
    ]
    started = subprocess.run(
        [
            *compose,
            "exec",
            "-T",
            "-d",
            "gateway",
            "python",
            "-c",
            fixture_path.read_text(encoding="utf-8"),
        ],
        capture_output=True,
        timeout=20,
    )
    if started.returncode:
        raise RuntimeError("a1_synthetic_model_start_failed")
    probe = subprocess.run(
        [
            *compose,
            "exec",
            "-T",
            "gateway",
            "python",
            "-c",
            probe_path.read_text(encoding="utf-8"),
            "model-ready",
        ],
        capture_output=True,
        timeout=30,
    )
    if probe.returncode:
        raise RuntimeError("a1_synthetic_model_health_failed")
    return {"status": "ready", "fixture_only": True}


def build_web_snapshot_request(source_ref, conversation_id, output):
    """Build a bounded read request for the accepted fictional A1 actor scope."""
    if not isinstance(source_ref, str) or not source_ref.startswith("origin:"):
        raise ValueError("a1_actor_origin_required")
    if not isinstance(conversation_id, str) or not conversation_id.startswith("conv:"):
        raise ValueError("a1_conversation_required")
    now = datetime.now(timezone.utc)
    request = {
        "schema_version": 1,
        "query": {
            "schema_version": 1,
            "request_id": "a1-web-snapshot-" + str(int(now.timestamp())),
            "origin": {"assertion_ref": source_ref},
        },
        "deadline_at": (now + timedelta(minutes=1)).isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "actor_id": "actor:a1-source",
        "conversation_id": conversation_id,
        "before_turn_sequence": None,
        "limit": 20,
    }
    path = Path(output).resolve()
    path.write_bytes(_raw(request))
    return {"path": str(path), "request_id": request["query"]["request_id"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "rebind-image-tags",
            "bind-a1-networks",
            "bind-a1-ports",
            "prepare-synthetic",
            "make-synthetic-inputs",
            "make-fanout-request",
            "set-gateway-origin",
            "bind-synthetic-classification",
            "enable-synthetic-memory-candidates",
            "bind-synthetic-model-version",
            "set-companion-model-version",
            "bind-memory-scopes",
            "trusted-memory-commit",
            "run-platform-cli",
            "diagnose-platform-publication",
            "publish-synthetic-config",
            "read-companion-web-snapshot",
            "read-companion-source-facts",
            "read-memory-selection",
            "local-user-action",
            "post-memory-revision",
            "make-web-snapshot-request",
            "start-synthetic-model",
        ),
    )
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--image-suffix", default="a1")
    parser.add_argument("--egress-subnet", default="10.204.41.0/24")
    parser.add_argument("--frontend-subnet", default="10.204.42.0/24")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--input", type=Path)
    parser.add_argument("--source-ref")
    parser.add_argument("--label")
    parser.add_argument("--config-version", type=int, default=1)
    args = parser.parse_args()
    if args.command == "rebind-image-tags":
        result = rebind_image_tags(args.bundle, args.image_suffix)
    elif args.command == "bind-a1-networks":
        result = bind_a1_networks(args.bundle, args.egress_subnet, args.frontend_subnet)
    elif args.command == "bind-a1-ports":
        result = bind_a1_ports(args.bundle)
    elif args.command == "prepare-synthetic":
        result = prepare_synthetic(args.bundle)
    elif args.command == "make-synthetic-inputs":
        result = build_synthetic_inputs(args.bundle, args.output)
    elif args.command == "make-fanout-request":
        result = build_fanout_request(
            args.bundle, args.source_ref, args.label, args.input, args.output
        )
    elif args.command == "set-gateway-origin":
        result = set_gateway_origin(args.bundle, args.source_ref)
    elif args.command == "bind-synthetic-classification":
        result = bind_synthetic_classification(args.bundle)
    elif args.command == "enable-synthetic-memory-candidates":
        result = enable_synthetic_memory_candidates(args.bundle)
    elif args.command == "bind-synthetic-model-version":
        result = bind_synthetic_model_version(args.bundle, args.config_version)
    elif args.command == "set-companion-model-version":
        result = set_companion_model_version(args.bundle, args.config_version)
    elif args.command == "bind-memory-scopes":
        result = bind_memory_scopes(args.bundle, json.loads(args.input.read_text()))
    elif args.command == "run-platform-cli":
        result = run_platform_cli(args.bundle, args.label, args.input)
    elif args.command == "diagnose-platform-publication":
        result = diagnose_platform_publication(args.bundle, args.input)
    elif args.command == "publish-synthetic-config":
        result = publish_synthetic_config(args.bundle, args.input, args.config_version)
    elif args.command == "read-companion-web-snapshot":
        result = read_companion_web_snapshot(args.bundle, args.input)
    elif args.command == "read-companion-source-facts":
        result = read_companion_source_facts(args.bundle, args.input)
    elif args.command == "read-memory-selection":
        result = read_memory_selection(args.bundle, args.input)
    elif args.command == "local-user-action":
        result = run_local_user_action(args.bundle, args.input)
    elif args.command == "post-memory-revision":
        result = post_memory_revision(args.bundle, args.input)
    elif args.command == "make-web-snapshot-request":
        result = build_web_snapshot_request(args.source_ref, args.label, args.output)
    elif args.command == "start-synthetic-model":
        result = start_synthetic_model(args.bundle)
    else:
        result = trusted_memory_commit(args.bundle, args.input)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
