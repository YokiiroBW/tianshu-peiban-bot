"""Once-only synthetic bootstrap, via the installed platform CLI inside its image."""

import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from bootstrap import remaining
from bundle import verify_integrity
from configuration import resolve_config
from manifest import Refused, digest, read_json, require, write_json

# Inputs and CLI output travel on private pipes; no credential/ref in argv or report.
CLI = """import json,os,subprocess,sys,tempfile
action=sys.argv[1]
data=sys.stdin.buffer.readline(1048577)
assert data.endswith(b'\\n') and len(data)<=1048576
with tempfile.NamedTemporaryFile(dir='/tmp',suffix='.json') as f:
 f.write(data);f.flush()
 result=subprocess.run([sys.executable,'-m','services.platform','--settings',
 '/etc/tianshu/settings.json','local','--credential-env','TS_ADMIN_TOKEN',action,
 '--input',f.name],capture_output=True,timeout=25)
 if result.returncode==0:sys.stdout.buffer.write(result.stdout)
 sys.exit(result.returncode)
"""


def update(root, changes):
    """Allowlisted transaction; interrupted updates stay INCOMPLETE, never auto-repaired."""
    verify_integrity(root)
    allowed = {
        "config/platform/settings.json",
        "config/gateway/settings.json",
        "private/gateway.env",
        "release-manifest.json",
        "observability-input/legacy-view.json",
        "observability/binding.json",
        "observability-release.json",
    }
    require(set(changes) <= allowed, "bootstrap_mutation_out_of_scope")
    index = read_json(root / "bundle-integrity.json")
    (root / "INCOMPLETE").write_text("DEP-G controlled bootstrap update\n")
    for name, raw in changes.items():
        path = root / name
        with path.open("wb") as stream:
            stream.write(raw)
            stream.flush()
            import os

            os.fsync(stream.fileno())
        index["files"][name] = digest(raw)
    write_json(root / "bundle-integrity.json", index)
    (root / "INCOMPLETE").unlink()
    verify_integrity(root)


def raw(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode()


def request_frame(document):
    """One bounded JSON line; Docker's open stdin must not require EOF to proceed."""
    data = (
        json.dumps(document, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode()
    require(len(data) <= 1048576, "bootstrap_input_too_large")
    return data


def prepare(root, dialogue, *, real_provider=None):
    """Only a fresh explicit synthetic deployment can call this, under the shared lease."""
    require(not (dialogue and real_provider is not None), "model_scenarios_conflict")
    if real_provider is not None:
        from real_provider import validate

        validate(real_provider)
    platform = read_json(root / "config/platform/settings.json")
    gateway = read_json(root / "config/gateway/settings.json")
    require(
        platform["providers"] == {} and platform["web"]["dialogue_enabled"] is False,
        "fresh_model_free_configuration_required",
    )
    require(
        platform["principals"]["admin"]["token_env"] == "TS_ADMIN_TOKEN",
        "admin_binding_changed",
    )
    require(
        len(gateway["targets"]) == 1
        and gateway["targets"][0]["base_url"] == gateway["platform_base_url"],
        "external_model_target_refused",
    )
    require(
        read_json(root / "config/companion/settings.json")[
            "automatic_memory_candidates"
        ]
        is False,
        "memory_candidates_must_remain_disabled",
    )
    # Replace any operator-supplied placeholder before the gateway can start. It is not
    # authority: only the later issue result may populate this variable.
    env_path = root / "private/gateway.env"
    prefix = gateway["platform_origin_env"] + "="
    env = "".join(
        line + "\n"
        for line in env_path.read_text().splitlines()
        if not line.startswith(prefix)
    )
    password = secrets.token_urlsafe(32)
    platform["web"]["username"] = "synthetic-dep-g"
    platform["web"]["password_hash"] = resolve_config(
        {"$password_env": "PASSWORD"}, {"PASSWORD": password}
    )
    changes = {"private/gateway.env": env.encode()}
    publication = None
    if dialogue or real_provider is not None:
        address = read_json(root / "deployment.json")["compose_inputs"]["service_ips"][
            "gateway"
        ]
        model = dict(
            provider_id="provider-synthetic",
            base_url="https://gateway.internal:9443/v1",
            credential_ref="secret-ref:synthetic",
            credential_namespace="synthetic-local",
            protocol="openai-chat-completions",
            model_id="synthetic-recorded-text",
            capability_verification="fixture_only",
            verified_capabilities=["text", "stream"],
            model_policy=dict(mode="preserve_client", fields={}),
            reasoning_policy=dict(mode="preserve_client", fields={}),
        )
        addresses = [address]
        secret_name, secret = "TS_SYNTHETIC_MODEL", secrets.token_urlsafe(32)
        if real_provider is not None:
            model.update(
                provider_id="provider-acceptance",
                base_url=real_provider["base_url"],
                credential_ref="secret-ref:acceptance",
                credential_namespace="operator-acceptance",
                model_id=real_provider["model_id"],
                capability_verification="verified_test_account",
                verified_capabilities=["text"],
            )
            addresses = real_provider["addresses"]
            secret_name, secret = "TS_ACCEPTANCE_MODEL", real_provider["secret"]
        platform["providers"] = {
            model["provider_id"]: {
                **{
                    k: model[k]
                    for k in (
                        "base_url",
                        "credential_ref",
                        "credential_namespace",
                        "protocol",
                        "capability_verification",
                        "verified_capabilities",
                    )
                },
                "model_ids": [model["model_id"]],
                "reviewed_addresses": addresses,
            }
        }
        platform["web"]["dialogue_enabled"] = True
        gateway["targets"].append(
            dict(
                base_url=model["base_url"],
                addresses=addresses,
                allow_private_http=False,
            )
        )
        gateway["clients"][0]["provider_id"] = model["provider_id"]
        gateway["secret_references"][model["credential_ref"]] = secret_name
        changes["private/gateway.env"] = (
            env + secret_name + "='" + secret + "'\n"
        ).encode()
        manifest = read_json(root / "release-manifest.json")
        require(manifest["status"] == "candidate", "candidate_required")
        next(f for f in manifest["features"] if f["id"] == "web_text_dialogue")[
            "enabled"
        ] = True
        changes["release-manifest.json"] = raw(manifest)
        if (root / "observability-release.json").exists():
            from observability_contract import legacy_view

            view = legacy_view(manifest)
            changes["observability-input/legacy-view.json"] = raw(view)
            binding = read_json(root / "observability/binding.json")
            binding["manifest_sha256"] = digest(
                json.dumps(view, sort_keys=True).encode()
            )
            changes["observability/binding.json"] = raw(binding)
            release = read_json(root / "observability-release.json")
            release["release_manifest_sha256"] = digest(
                changes["release-manifest.json"]
            )
            release["legacy_view_sha256"] = digest(
                changes["observability-input/legacy-view.json"]
            )
            changes["observability-release.json"] = raw(release)
        now = datetime.now(timezone.utc)

        def stamp(t):
            return t.isoformat(timespec="milliseconds").replace("+00:00", "Z")

        publication = dict(
            schema_version=1,
            request_id="dep-g-bootstrap",
            config_version=1,
            status="published",
            published_at=stamp(now),
            usable_until=stamp(now + timedelta(minutes=15)),
            providers=[model],
            bindings=[
                dict(
                    workload="companion.text",
                    provider_id=model["provider_id"],
                    model_id=model["model_id"],
                    timeout_ms=30000,
                    fallback="disabled",
                )
            ],
        )
    changes["config/platform/settings.json"] = raw(platform)
    changes["config/gateway/settings.json"] = raw(gateway)
    update(root, changes)
    return publication, password


def first_install_container(root, compose, runner, publication):
    work = root / "reports/bootstrap"
    require(not work.exists(), "bootstrap_already_attempted")
    work.mkdir(mode=0o700)
    write_json(work / "attempt.json", dict(state="started", automatic_retry=False))
    actions = ([("publish", publication)] if publication is not None else []) + [
        ("issue", {"entry_id": "config-entry"})
    ]
    for action, data in actions:
        # The marker precedes the product side effect, so any exception is non-replayable.
        write_json(work / (action + "-attempt.json"), {"state": "started"})
        receipt = runner(
            "platform_" + action,
            [
                *compose,
                "run",
                "--rm",
                "--no-deps",
                "-T",
                "--entrypoint",
                "python",
                "platform",
                "-c",
                CLI,
                action,
            ],
            35,
            input=request_frame(data),
            capture=True,
        )
        if action == "issue":
            receipt = json.loads(receipt)
            remaining(receipt, minimum_seconds=120)
    ref = receipt["assertion_ref"]
    gateway = read_json(root / "config/gateway/settings.json")
    env = root / "private/gateway.env"
    prefix = gateway["platform_origin_env"] + "="
    require(
        not any(line.startswith(prefix) for line in env.read_text().splitlines()),
        "origin_already_present",
    )
    update(
        root,
        {
            "private/gateway.env": env.read_bytes()
            + (prefix + "'" + ref + "'\n").encode()
        },
    )
    write_json(
        work / "result.json",
        dict(
            state="authority_initialized",
            issuer="product_platform_cli",
            published=publication is not None,
            expires_at=receipt["expires_at"],
            automatic_retry=False,
        ),
    )
    return receipt


def begin_reauthorization(root, *, now=None):
    """Record a one-shot operator recovery attempt after the boot ref has expired."""
    from bundle import verify_integrity

    verify_integrity(root)
    work = root / "reports/bootstrap"
    prior_path = work / "result.json"
    marker_path = work / "reauthorization-attempt.json"
    require(prior_path.is_file(), "bootstrap_result_required")
    require(not marker_path.exists(), "reauthorization_already_attempted")
    prior = read_json(prior_path)
    require(
        prior.get("state") == "authority_initialized"
        and prior.get("issuer") == "product_platform_cli",
        "bootstrap_result_invalid",
    )
    try:
        expiry = datetime.fromisoformat(prior["expires_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        raise Refused("bootstrap_expiry_invalid") from None
    current = now or datetime.now(timezone.utc)
    require(
        expiry.tzinfo is not None and expiry <= current,
        "bootstrap_origin_not_expired",
    )
    write_json(
        marker_path,
        {
            "state": "started",
            "issuer": "product_platform_cli",
            "old_expires_at": prior["expires_at"],
            "automatic_retry": False,
        },
    )
    return marker_path


def store_reauthorization_receipt(root, receipt, *, now=None, minimum_seconds=120):
    """Replace only the expired private gateway ref with a fresh public-CLI receipt."""
    from bundle import verify_integrity

    verify_integrity(root)
    work = root / "reports/bootstrap"
    marker_path = work / "reauthorization-attempt.json"
    result_path = work / "reauthorization-result.json"
    require(marker_path.is_file(), "reauthorization_attempt_required")
    require(not result_path.exists(), "reauthorization_already_completed")
    marker = read_json(marker_path)
    require(marker.get("state") == "started", "reauthorization_attempt_invalid")

    current = now or datetime.now(timezone.utc)
    try:
        expiry = datetime.fromisoformat(marker["old_expires_at"].replace("Z", "+00:00"))
    except (KeyError, TypeError, ValueError):
        raise Refused("reauthorization_expiry_invalid") from None
    require(expiry.tzinfo is not None and expiry <= current, "bootstrap_origin_not_expired")
    require(
        isinstance(receipt, dict)
        and set(receipt) <= {"assertion_ref", "expires_at", "mode"}
        and {"assertion_ref", "expires_at"} <= set(receipt),
        "product_issue_receipt_invalid",
    )
    remaining(receipt, now=current.timestamp(), minimum_seconds=minimum_seconds)

    gateway = read_json(root / "config/gateway/settings.json")
    variable = gateway["platform_origin_env"]
    prefix = variable + "="
    env_path = root / "private/gateway.env"
    original = env_path.read_bytes()
    try:
        lines = original.decode("utf-8").splitlines(keepends=True)
    except UnicodeError:
        raise Refused("gateway_environment_invalid") from None
    matches = [i for i, line in enumerate(lines) if line.rstrip("\r\n").startswith(prefix)]
    require(len(matches) == 1, "origin_environment_single_value_required")
    index = matches[0]
    old_line = lines[index].rstrip("\r\n")
    old_match = re.fullmatch(re.escape(prefix) + r"'(origin:[0-9a-f]{32})'", old_line)
    require(old_match is not None, "origin_environment_value_invalid")
    require(receipt["assertion_ref"] != old_match.group(1), "new_assertion_required")

    updated = list(lines)
    updated[index] = prefix + "'" + receipt["assertion_ref"] + "'\n"
    update(root, {"private/gateway.env": "".join(updated).encode("utf-8")})
    result = {
        "state": "reauthorized",
        "issuer": "product_platform_cli",
        "old_expires_at": marker["old_expires_at"],
        "expires_at": receipt["expires_at"],
        "previous_ref_replaced": True,
        "ref_in_report": False,
        "automatic_retry": False,
    }
    write_json(result_path, result)
    marker["state"] = "completed"
    write_json(marker_path, marker)
    verify_integrity(root)
    return result
