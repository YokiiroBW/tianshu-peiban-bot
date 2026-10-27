"""Explicit owned-clone fresh lease using the original accepted origin protocol.

No action on import or source writes. Run only while the isolated candidate is
stopped. Original ORIGIN_ISSUE uses normal Auth/Origins.issue on the owned clone;
no origin row, expiry or permission is copied with SQL.
"""

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys
from uuid import uuid4

HELPER_SHA = "e855a88a6074bc4b1b2d5df3550db9b7cccde77dc15e9b3b41eb57fec84cc80f"
DIAGNOSTIC_SHA = "42718d276c51f54bf4b413b379920e7ee211a3830aaea91bc0413d0f6219f426"
SETTINGS_LINE = "settings=json.loads(Path('/etc/tianshu/settings.json').read_bytes())"
SCOPE = (
    "entry_id",
    "entry_digest",
    "owner",
    "owner_variable",
    "routes",
    "ttl_seconds",
    "revoked",
)


def load(path, name, expected):
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise RuntimeError("reviewed_origin_tool_required")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def program(source, settings, environment, *, operation="original_origin", **variables):
    """Only replace the original hard-coded settings read; secrets use stdin/env."""
    if source.count(SETTINGS_LINE) != 1:
        raise RuntimeError("original_settings_read_changed")
    prefix = "import json\nSETTINGS=json.loads(" + repr(json.dumps(settings)) + ")\n"
    prefix += "".join(
        key + "=" + repr(value) + "\n" for key, value in variables.items()
    )
    completed = subprocess.run(
        [sys.executable, "-I", "-B", "-"],
        input=(prefix + source.replace(SETTINGS_LINE, "settings=SETTINGS")).encode(),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        # Child exceptions may contain configured paths/credentials; never forward them.
        raise RuntimeError(operation + "_program_failed")
    return json.loads(completed.stdout)


def candidate_file(candidate, value):
    native = PurePosixPath(value)
    if not native.is_relative_to("/config") or ".." in native.parts:
        raise RuntimeError("candidate_config_path_required")
    path = (
        (candidate / "config")
        .joinpath(*native.relative_to("/config").parts)
        .resolve(strict=True)
    )
    if not path.is_relative_to(candidate / "config"):
        raise RuntimeError("candidate_config_path_escape")
    return path


def atomic_env(path, before, after):
    """Replace one inactive candidate env file, retaining its existing metadata."""
    if path.is_symlink() or path.read_bytes() != before:
        raise RuntimeError("candidate_gateway_env_changed")
    old = path.stat()
    temporary = path.with_name(".origin-lease-" + uuid4().hex)
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), stat.S_IMODE(old.st_mode))
            os.fchown(stream.fileno(), old.st_uid, old.st_gid)
        os.replace(temporary, path)
        fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)


def issue(*, origin_helper, candidate_root, output, **inputs):
    diagnostic = load(
        Path(__file__).with_name("linux_origin_diagnostic.py"),
        "_ts367_diagnostic",
        DIAGNOSTIC_SHA,
    )
    clone = diagnostic.clone
    helper = load(Path(origin_helper), "_ts367_original_origin", HELPER_SHA)
    clone.require(
        sys.platform == "linux" and os.geteuid() == 0,
        "linux_root_operational_invocation_required",
    )
    output = Path(output).resolve()
    clone.require(not output.exists(), "new_origin_receipt_required")
    candidate = Path(candidate_root).resolve(strict=True)
    source_root = Path(inputs["source_root"]).resolve(strict=True)
    clone.require(
        not candidate.is_relative_to(source_root)
        and not output.is_relative_to(source_root),
        "candidate_must_be_outside_production_root",
    )
    proof = diagnostic.diagnose(**inputs)
    clone.require(
        proof["missing_same_ref_clone_registry_matches"]
        or proof["expired_same_ref_clone_candidate"],
        "readonly_same_scope_clone_proof_required",
    )
    owners = clone.read(inputs["owners_inspect"])
    p, g = owners["platform"], owners["gateway"]
    source = clone.read(
        diagnostic.physical(p, diagnostic.native_settings(p), source_root)
    )
    gateway = clone.read(
        diagnostic.physical(g, diagnostic.native_settings(g), source_root)
    )
    p_env, g_env = diagnostic.environment(p), diagnostic.environment(g)
    ref = g_env[gateway["platform_origin_env"]]
    source["database_path"] = str(
        diagnostic.physical(
            p, str(PurePosixPath(source["database_path"]).parent), source_root
        )
        / PurePosixPath(source["database_path"]).name
    )
    base = {
        key: value
        for key, value in os.environ.items()
        if key.upper()
        in {"PATH", "HOME", "LANG", "LC_ALL", "TEMP", "TMP", "SYSTEMROOT", "WINDIR"}
    }
    source_env = {**base, **p_env, "PYTHONDONTWRITEBYTECODE": "1"}
    before = program(
        helper.ORIGIN_READ, source, source_env, operation="source_origin_read", REF=ref
    )
    clone.require(
        before["ttl_seconds"] == 300 and before["routes"] == [diagnostic.ROUTE],
        "original_model_lease_scope_required",
    )
    host = clone.read(candidate / "config/host.json")
    p_spec, g_spec = host["modules"]["platform"], host["modules"]["gateway"]
    candidate_p = clone.read(candidate_file(candidate, p_spec["settings"]))
    candidate_g = clone.read(candidate_file(candidate, g_spec["settings"]))
    entry, owner = before["entry_id"], before["owner"]
    clone.require(
        candidate_p["entries"][entry] == source["entries"][entry]
        and candidate_p["principals"][owner] == source["principals"][owner],
        "complete_candidate_entry_and_owner_must_match",
    )
    variable = gateway["platform_origin_env"]
    clone.require(
        candidate_g["platform_origin_env"] == variable
        and variable not in g_spec.get("env", {}),
        "candidate_origin_env_override_refused",
    )
    env_files = [candidate_file(candidate, name) for name in g_spec["env_file"]]
    matching = [
        path
        for path in env_files
        if any(
            line.startswith(variable.encode() + b"=")
            for line in path.read_bytes().splitlines()
        )
    ]
    clone.require(len(matching) == 1, "exact_candidate_gateway_origin_file_required")
    env_path, raw = matching[0], matching[0].read_bytes()
    # Validate the exact original line before issuing any clone lease.
    helper.replace_origin_line(raw, variable, ref, "origin:" + "0" * 32)
    target = clone.read(inputs["clone_settings"])
    for key in ("contract_directory", "source_contract_directory"):
        if candidate_p.get(key) is not None:
            target[key] = str(candidate_file(candidate, candidate_p[key]))
    binding, _ = clone.owned_binding(inputs["binding"], inputs["state"])
    target_env = {
        **source_env,
        "TIANSHU_POSTGRES_URL": binding["postgresql_url"],
        "TIANSHU_REDIS_URL": "",
    }
    marker = Path(target["database_path"]).parent / ".tianshu-storage-platform.json"
    marker_before, metadata = clone.read(marker), marker.stat()
    try:
        issued = program(
            helper.ORIGIN_ISSUE,
            target,
            target_env,
            OWNER_VARIABLE=before["owner_variable"],
            ENTRY_ID=entry,
            operation="clone_origin_issue",
        )
        new = issued["assertion_ref"]
        clone.require(
            diagnostic.REF.fullmatch(new) is not None and new != ref,
            "fresh_clone_origin_required",
        )
        after = program(
            helper.ORIGIN_READ,
            target,
            target_env,
            operation="clone_origin_read",
            REF=new,
        )
        clone.require(
            not after["expired"] and all(after[key] == before[key] for key in SCOPE),
            "issued_clone_scope_changed",
        )
    finally:
        clone.require(
            clone.read(marker) == marker_before, "clone_marker_identity_changed"
        )
        os.chown(marker, metadata.st_uid, metadata.st_gid)
        marker.chmod(stat.S_IMODE(metadata.st_mode))
    updated = helper.replace_origin_line(raw, variable, ref, new)
    atomic_env(env_path, raw, updated)
    clone.require(env_path.read_bytes() == updated, "candidate_origin_line_not_written")
    receipt = {
        "version": 1,
        "status": "owned_clone_fresh_model_lease_ready_for_immediate_native_start",
        "original_helper_sha256": HELPER_SHA,
        "readonly_diagnostic": proof,
        "same_complete_entry_and_owner": True,
        "same_entry_scope": True,
        "ttl_seconds": 300,
        "new_permissions": 0,
        "source_modified": False,
        "clone_normal_auth_origin_issue": True,
        "candidate_env_exact_one_line": True,
        "candidate_env_before_sha256": hashlib.sha256(raw).hexdigest(),
        "candidate_env_after_sha256": hashlib.sha256(updated).hexdigest(),
        "clone_marker_identity_and_metadata_preserved": True,
        "original_clone_proof_replaced": False,
        "full_current_data_recloned": False,
        "native_readiness_or_http_claimed": False,
    }
    clone.write_new(output, receipt)
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "owners-inspect",
        "source-root",
        "clone-settings",
        "clone-root",
        "binding",
        "state",
        "candidate-root",
        "origin-helper",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    values = vars(parser.parse_args(argv))
    try:
        result = issue(**values)
    except Exception as error:
        reason = str(error)
        if not re.fullmatch(r"[a-z_]{1,100}", reason):
            reason = "clone_origin_protocol_failed"
        print(
            json.dumps(
                {
                    "status": "failed_no_automatic_retry",
                    "reason": reason,
                    "source_modified": False,
                }
            )
        )
        return 2
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
