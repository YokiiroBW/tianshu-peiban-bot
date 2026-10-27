"""Read-only same-ref model-origin diagnosis on explicit live and owned clone inputs.

No action on import, SSH, HTTP renewal, resource creation or mutation mode. The
coordinator runs this with installed product wheels and the actual thirteen-owner
inspect map. Credentials/URLs/source rows never appear in the result or errors.
"""

from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timezone
import hmac
import importlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
import sys
import time


def clone_helper():
    """Load the reviewed sibling tool explicitly, including under Python -I."""
    import hashlib

    path = Path(__file__).with_name("linux_clone.py")
    if (
        hashlib.sha256(path.read_bytes()).hexdigest()
        != "a6f5d366df67469851e4629b52d26b27af88a944aa3c86c2987535852440fdc5"
    ):
        raise RuntimeError("reviewed_clone_helper_required")
    spec = importlib.util.spec_from_file_location("_ts367_clone", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


clone = clone_helper()

ROUTE = {"caller": "gateway", "receiver": "platform", "purpose": "config.snapshot"}
REF = re.compile(r"origin:[0-9a-f]{32}\Z")


def environment(row):
    result = {}
    for item in row["Config"]["Env"]:
        name, separator, value = item.partition("=")
        clone.require(separator and name not in result, "owner_environment_invalid")
        result[name] = value
    return result


def physical(row, native, source_root):
    requested = PurePosixPath(native)
    clone.require(
        requested.is_absolute() and ".." not in requested.parts,
        "absolute_native_path_required",
    )
    matches = []
    for mount in row["Mounts"]:
        target = PurePosixPath(mount["Destination"])
        if mount["Type"] == "bind" and requested.is_relative_to(target):
            matches.append(
                (
                    len(target.parts),
                    Path(mount["Source"]).joinpath(
                        *requested.relative_to(target).parts
                    ),
                )
            )
    clone.require(matches, "source_mount_mapping_missing")
    depth = max(item[0] for item in matches)
    exact = [path for length, path in matches if length == depth]
    clone.require(len(exact) == 1, "source_mount_mapping_ambiguous")
    path = exact[0].resolve(strict=True)
    clone.require(path.is_relative_to(source_root), "explicit_source_root_required")
    return path


def native_settings(row):
    command = [
        *(row["Config"].get("Entrypoint") or []),
        *(row["Config"].get("Cmd") or []),
    ]
    values = []
    for index, item in enumerate(command):
        if item == "--settings":
            clone.require(index + 1 < len(command), "native_settings_option_invalid")
            values.append(command[index + 1])
        elif item.startswith("--settings="):
            values.append(item.split("=", 1)[1])
    clone.require(len(values) <= 1, "native_settings_option_ambiguous")
    return values[0] if values else "/etc/tianshu/settings.json"


def secret_valid(value):
    # Auth.secret is process-global; this diagnosis must not replace os.environ.
    return (
        isinstance(value, str)
        and 24 <= len(value) <= 4096
        and all(33 <= ord(character) <= 126 for character in value)
    )


def row_dict(cursor):
    row = cursor.fetchone()
    return (
        None
        if row is None
        else dict(zip((column[0] for column in cursor.description), row))
    )


def facts(db, settings, env, ref, credential, now, *, source_origin=None):
    """Observe the product renewal predicates without invoking its write transaction."""
    from services.platform.contracts import digest, epoch

    row = row_dict(db.execute("SELECT * FROM origins WHERE ref=?", (ref,)))
    principals = settings.get("principals", {})
    matches = [
        (name, principal)
        for name, principal in principals.items()
        if secret_valid(env.get(principal["token_env"]))
        and hmac.compare_digest(credential, env[principal["token_env"]])
    ]
    caller = matches[0] if len(matches) == 1 else None
    caller_revoked = (
        bool(
            db.execute(
                "SELECT 1 FROM revoked_principals WHERE id=?", (caller[0],)
            ).fetchone()
        )
        if caller
        else None
    )
    registration = row if row is not None else source_origin
    entry = (
        settings.get("entries", {}).get(registration["entry_id"])
        if registration
        else None
    )
    owner = principals.get(entry["owner"]) if entry else None
    checks = {
        "renewal_enabled": settings.get("model_origin_renewal_http") is True,
        "service_https_mode": settings.get("mode") == "service_https",
        "credential_matches_unique_principal": caller is not None,
        "caller_gateway_service": bool(
            caller
            and caller[1].get("kind") == "service"
            and caller[1].get("service") == "gateway"
        ),
        "caller_snapshot_action": bool(
            caller and "config.snapshot" in caller[1].get("actions", [])
        ),
        "caller_not_revoked": caller_revoked is False,
        "origin_exists": row is not None,
        "origin_not_revoked": bool(row is not None and not row["revoked"]),
        "origin_unexpired": bool(row is not None and row["expires_at"] > now),
        "entry_exists": entry is not None,
        "entry_digest_matches": bool(
            entry and registration["entry_digest"] == digest(entry)
        ),
        "entry_not_revoked": bool(
            registration
            and not db.execute(
                "SELECT 1 FROM revoked_entries WHERE id=?", (registration["entry_id"],)
            ).fetchone()
        ),
        "entry_unexpired": bool(
            entry and ("expires_at" not in entry or epoch(entry["expires_at"]) > now)
        ),
        "entry_exact_model_route": bool(entry and entry.get("routes") == [ROUTE]),
        "owner_exists": owner is not None,
        "owner_not_revoked": bool(
            entry
            and not db.execute(
                "SELECT 1 FROM revoked_principals WHERE id=?", (entry["owner"],)
            ).fetchone()
        ),
        "owner_credential_available": bool(
            owner and secret_valid(env.get(owner["token_env"]))
        ),
    }
    return {"checks": checks, "row": row, "entry": entry}


def comparison(source, target, now):
    left, right = source["row"], target["row"]
    equal_except_expiry = bool(
        left is not None
        and right is not None
        and {key: value for key, value in left.items() if key != "expires_at"}
        == {key: value for key, value in right.items() if key != "expires_at"}
    )
    checks = {
        "same_complete_origin_row_except_expiry": equal_except_expiry,
        "same_complete_registered_entry": source["entry"] is not None
        and source["entry"] == target["entry"],
        "source_all_observed_renewal_predicates": all(source["checks"].values()),
        "clone_nonexpiry_predicates": all(
            value
            for key, value in target["checks"].items()
            if key != "origin_unexpired"
        ),
        "clone_expired": bool(right and right["expires_at"] <= now),
        "clone_expiry_not_newer_than_source": bool(
            left and right and right["expires_at"] <= left["expires_at"]
        ),
    }
    return {
        "checks": checks,
        "expired_same_ref_clone_candidate": all(checks.values()),
        "missing_same_ref_clone_registry_matches": bool(
            left
            and right is None
            and checks["same_complete_registered_entry"]
            and checks["source_all_observed_renewal_predicates"]
            and all(
                value
                for key, value in target["checks"].items()
                if key
                not in {"origin_exists", "origin_not_revoked", "origin_unexpired"}
            )
        ),
        "source_failed_checks": [
            key for key, value in source["checks"].items() if not value
        ],
        "clone_failed_checks": [
            key for key, value in target["checks"].items() if not value
        ],
        "source_expires_in_seconds": left["expires_at"] - now if left else None,
        "clone_expires_in_seconds": right["expires_at"] - now if right else None,
    }


def open_database(settings, env, directory):
    from services.platform.postgres import dialect
    from tianshu_storage import StorageOptions, StorageRuntime
    from tianshu_storage.runtime import read_state, state_filename

    options = replace(StorageOptions.from_settings(settings, environ=env), redis_url="")
    state = read_state(
        directory / state_filename("platform"),
        namespace=options.namespace,
        service="platform",
    )
    clone.require(
        state is not None
        and state["backend"] == "postgresql"
        and state["state"] == "active",
        "existing_active_platform_pg_required",
    )
    return StorageRuntime.open_existing(
        options,
        service="platform",
        state_directory=directory,
        name="authority",
        path=settings["database_path"],
        dialect=dialect,
    ), options


def snapshot_origin(snapshot, gateway_relative, env_relative, live_settings, live_ref):
    """Read only the two original, manifest-hashed backup files; print no ref."""
    snapshot = Path(snapshot).resolve(strict=True)
    manifest = clone.read(snapshot / "manifest.json")
    receipt = clone.read(snapshot / "receipt.json")
    clone.require(
        receipt.get("status") == "offline_restore_verified"
        and receipt.get("manifest_sha256") == clone.sha(snapshot / "manifest.json"),
        "verified_snapshot_manifest_required",
    )
    files = []
    for name in (gateway_relative, env_relative):
        path = clone.relative(snapshot / "backup", name)
        entry = manifest["inventory"]["entries"][name]
        clone.require(
            entry["kind"] == "file" and clone.sha(path) == entry["sha256"],
            "snapshot_gateway_input_changed",
        )
        files.append(path)
    settings = clone.read(files[0])
    variable = settings["platform_origin_env"]
    # Original activate.group.py:env_reference restricted exact-line semantics.
    rows = [
        line[len(variable) + 1 :]
        for line in files[1].read_bytes().splitlines()
        if line.startswith(variable.encode() + b"=")
    ]
    clone.require(len(rows) == 1, "exact_snapshot_origin_env_line_required")
    ref = rows[0]
    if ref[:1] in (b"'", b'"'):
        clone.require(
            len(ref) >= 2 and ref[-1:] == ref[:1], "snapshot_origin_env_quoting_invalid"
        )
        ref = ref[1:-1]
    ref = ref.decode()
    return ref, {
        "snapshot_gateway_ref_present": REF.fullmatch(ref) is not None,
        "snapshot_gateway_ref_equals_live": ref == live_ref,
        "snapshot_gateway_origin_env_name_equals_live": variable
        == live_settings["platform_origin_env"],
    }


def diagnose(
    *,
    owners_inspect,
    source_root,
    clone_settings,
    clone_root,
    binding,
    state,
    snapshot=None,
    snapshot_gateway_env=None,
):
    from tianshu_gateway.config import EnvSecrets
    from tianshu_storage import StorageOptions

    for name in (
        "tianshu_storage.runtime",
        "services.platform.contracts",
        "tianshu_gateway.config",
    ):
        module = importlib.import_module(name)
        clone.require(
            Path(module.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()),
            "installed_product_wheels_required",
        )
    source_root, clone_root = (
        Path(source_root).resolve(strict=True),
        Path(clone_root).resolve(strict=True),
    )
    clone.require(
        not source_root.is_relative_to(clone_root)
        and not clone_root.is_relative_to(source_root),
        "separate_source_and_clone_roots_required",
    )
    owners = clone.read(owners_inspect)
    clone.require(
        isinstance(owners, dict)
        and len(owners) == 13
        and {"platform", "gateway"} <= owners.keys(),
        "exact_thirteen_original_owner_map_required",
    )
    platform, gateway = owners["platform"], owners["gateway"]
    source_settings = clone.read(
        physical(platform, native_settings(platform), source_root)
    )
    gateway_settings = clone.read(
        physical(gateway, native_settings(gateway), source_root)
    )
    source_env, gateway_env = environment(platform), environment(gateway)
    ref = gateway_env.get(gateway_settings["platform_origin_env"], "")
    clone.require(REF.fullmatch(ref) is not None, "gateway_original_origin_required")
    credential = EnvSecrets(
        gateway_settings["secret_references"], environ=gateway_env
    ).resolve(gateway_settings["platform_credential_ref"])
    snapshot_checks = {}
    old_ref = None
    if snapshot is not None:
        clone.require(
            snapshot_gateway_env is not None, "explicit_snapshot_gateway_env_required"
        )
        old_ref, snapshot_checks = snapshot_origin(
            snapshot,
            physical(gateway, native_settings(gateway), source_root)
            .relative_to(source_root)
            .as_posix(),
            snapshot_gateway_env,
            gateway_settings,
            ref,
        )
    native_directory = str(PurePosixPath(source_settings["database_path"]).parent)
    directory = physical(platform, native_directory, source_root)
    target_settings = clone.read(clone_settings)
    target_directory = Path(target_settings["database_path"]).resolve().parent
    clone.require(
        target_directory.is_relative_to(clone_root),
        "exact_clone_platform_directory_required",
    )
    owned, resource = clone.owned_binding(binding, state)
    target_options = StorageOptions.from_settings(target_settings, environ={})
    clone.require(
        target_options.postgresql_url == owned["postgresql_url"]
        and not target_options.redis_url,
        "clone_settings_owned_pg_and_no_redis_required",
    )
    source_connection, source_options = open_database(
        source_settings, source_env, directory
    )
    with closing(source_connection) as source_db:
        target_connection, _ = open_database(target_settings, {}, target_directory)
        with closing(target_connection) as target_db:
            clone.require(
                source_options.postgresql_url != target_options.postgresql_url,
                "source_and_clone_pg_must_differ",
            )
            clone.require(
                source_db.execute("SELECT current_database()").fetchone()[0]
                != resource["database"],
                "source_and_clone_database_must_differ",
            )
            expected = [
                resource["database"],
                resource["role"],
                resource["role"],
                resource["role"],
                resource["comment"],
                False,
                False,
                False,
                False,
                False,
                True,
                resource["comment"],
            ]
            clone.require(
                target_db.execute(clone.IDENTITY_SQL).fetchone()[0] == expected,
                "live_owned_pg2_identity_mismatch",
            )
            for db in (source_db, target_db):
                clone.require(
                    db.execute("SHOW default_transaction_read_only").fetchone()[0]
                    == "on",
                    "readonly_connection_required",
                )
            now = time.time()
            source = facts(source_db, source_settings, source_env, ref, credential, now)
            # The original source credential/environment is only a comparison input.
            # Candidate private module env is intentionally not silently substituted.
            target = facts(
                target_db,
                target_settings,
                source_env,
                ref,
                credential,
                now,
                source_origin=source["row"],
            )
            if old_ref is not None:
                snapshot_checks["clone_snapshot_gateway_ref_exists"] = (
                    target_db.execute(
                        "SELECT 1 FROM origins WHERE ref=?", (old_ref,)
                    ).fetchone()
                    is not None
                )
    return {
        "version": 1,
        "status": "read_only_diagnosed",
        "observed_at": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "owners_inspect_sha256": clone.sha(owners_inspect),
        "clone_settings_sha256": clone.sha(clone_settings),
        "binding_input_sha256": clone.sha(binding),
        "state_input_sha256": clone.sha(state),
        "source": source["checks"],
        "clone_using_original_credential_comparison": target["checks"],
        **comparison(source, target, now),
        "snapshot_comparison": snapshot_checks,
        "clone_gateway_environment_checked": False,
        "http_authentication_or_readiness_claimed": False,
        "redis_used": False,
        "database_or_marker_modified": False,
        "expiry_copied": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "owners-inspect",
        "source-root",
        "clone-settings",
        "clone-root",
        "binding",
        "state",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--snapshot-gateway-env")
    args = parser.parse_args(argv)
    values = vars(args)
    output = values.pop("output")
    try:
        result = diagnose(**values)
        clone.write_new(output, result)
    except Exception as error:
        reason = (
            str(error)
            if isinstance(error, clone.CloneError)
            else "readonly_diagnostic_failed"
        )
        print(
            json.dumps(
                {
                    "status": "refused",
                    "reason": reason,
                    "error_type": type(error).__name__,
                }
            )
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
