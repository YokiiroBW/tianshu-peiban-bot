"""Once-only synthetic bootstrap, via the installed platform CLI inside its image."""

import json
import secrets
from datetime import datetime, timedelta, timezone

from bootstrap import remaining
from bundle import verify_integrity
from configuration import resolve_config
from manifest import digest, read_json, require, write_json

# Inputs and CLI output travel on private pipes; no credential/ref in argv or report.
CLI = """import json,os,subprocess,sys,tempfile
action=sys.argv[1]
data=sys.stdin.buffer.read(1048577)
assert len(data)<=1048576
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


def prepare(root, dialogue):
    """Only a fresh explicit synthetic deployment can call this, under the shared lease."""
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
    if dialogue:
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
        platform["providers"] = {
            "provider-synthetic": {
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
                "reviewed_addresses": [address],
            }
        }
        platform["web"]["dialogue_enabled"] = True
        gateway["targets"].append(
            dict(
                base_url=model["base_url"],
                addresses=[address],
                allow_private_http=False,
            )
        )
        gateway["clients"][0]["provider_id"] = "provider-synthetic"
        gateway["secret_references"]["secret-ref:synthetic"] = "TS_SYNTHETIC_MODEL"
        changes["private/gateway.env"] = (
            env + "TS_SYNTHETIC_MODEL='" + secrets.token_urlsafe(32) + "'\n"
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
            input=raw(data),
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
