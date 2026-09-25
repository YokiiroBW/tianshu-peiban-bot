"""A1 host Gateway CLI readiness using the fixed runtime and a temporary ledger."""

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .safety import RecoveryError, file_hash, read_json, require

LOCK_SHA256 = "20321ac2bbb40bd801ccbb5804c415af8144b226b3469516f8ffe92317f06280"
GATEWAY_TREE_SHA256 = "39355bc3de216f18c37ee8d2f9f70673ae5aeb2eeaee8b8beacff314e7915755"
GATEWAY_UV_LOCK_SHA256 = "56de5d63a49b0f4544ca3926eadfab4ff6b8f5b7506f976cb9b8832002700e29"
GATEWAY_COMMIT = "601974194042641c5a85cc3c061cbd1880d7daf1"
IMPORTS = {
    "aiohappyeyeballs": "aiohappyeyeballs", "aiohttp": "aiohttp",
    "aiosignal": "aiosignal", "attrs": "attrs", "frozenlist": "frozenlist",
    "idna": "idna", "jsonschema": "jsonschema",
    "jsonschema-specifications": "jsonschema_specifications",
    "multidict": "multidict", "propcache": "propcache",
    "referencing": "referencing", "rpds-py": "rpds",
    "typing-extensions": "typing_extensions", "yarl": "yarl",
}

RUNTIME_PROBE = r'''
import importlib,importlib.metadata as metadata,json,platform,sys
from pathlib import Path
if sys.flags.optimize != 0: raise RuntimeError('optimized_python_not_allowed')
expected=json.loads(sys.argv[1]); names=json.loads(sys.argv[2]); gateway=Path(sys.argv[3])
assert sys.implementation.name=='cpython' and sys.version.startswith('3.12.')
assert sys.prefix!=sys.base_prefix
assert Path(sys.executable).resolve()==Path(sys.argv[4]).resolve()
root=Path(sys.prefix).resolve()
actual={name:metadata.version(name) for name in expected}
assert actual==expected,(actual,expected)
for name,module_name in names.items():
 module=importlib.import_module(module_name)
 origin=Path(module.__file__).resolve()
 assert origin.is_relative_to(root),(name,str(origin))
module=importlib.import_module('tianshu_gateway.usage_report')
assert Path(module.__file__).resolve().is_relative_to(gateway)
print(json.dumps({'versions':actual,'python':sys.executable,
 'gateway_module':str(Path(module.__file__).resolve()),
 'system':sys.platform,'machine':platform.machine()},sort_keys=True))
'''

FIXTURE = r'''
import json,sys
from pathlib import Path
from tianshu_gateway.diagnostics import Diagnostics
if sys.flags.optimize != 0: raise RuntimeError('optimized_python_not_allowed')
path,request_id,settings_path=sys.argv[1:4]
clients=json.loads(Path(settings_path).read_bytes())['clients']
versions=[row['config_version'] for row in clients if row['service']=='companion']
assert len(versions)==1 and type(versions[0]) is int and versions[0]>=1
ledger=Diagnostics(path)
receipt={'caller_service':'companion','request_id':request_id,
 'config_version':versions[0],'outcome':'succeeded'}
ledger.begin(receipt,None)
ledger.finish(receipt,'completed',1,200)
ledger.close()
print(json.dumps({'fixture':'one_synthetic_succeeded_attempt'}))
'''


def _hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_preparation(c):
    """Bind the portable 14-wheel lock and Gateway source to this preparation."""
    from .a1_prepare import _tree
    code = Path(c["code_root"])
    lock_path = code / "ops/recovery/a1_gateway_runtime.lock.json"
    require(c.get("gateway_runtime_lock") == str(lock_path) and
            c.get("gateway_runtime_lock_sha256") == LOCK_SHA256 and
            lock_path.is_file() and not lock_path.is_symlink() and
            file_hash(lock_path) == LOCK_SHA256,
            "a1_gateway_runtime_lock_invalid")
    lock = read_json(lock_path)
    require(lock.get("schema_version") == "a1-gateway-runtime-lock/1" and
            lock.get("gateway_commit") == GATEWAY_COMMIT and
            lock.get("gateway_uv_lock_sha256") == GATEWAY_UV_LOCK_SHA256 and
            lock.get("gateway_source_tree_sha256") == GATEWAY_TREE_SHA256 and
            lock.get("platform") == "CPython 3.12/linux-x86_64" and
            type(lock.get("wheels")) is list and len(lock["wheels"]) == 14 and
            {row["project"] for row in lock["wheels"]} == set(IMPORTS) and
            len({row["filename"] for row in lock["wheels"]}) == 14,
            "a1_gateway_runtime_lock_invalid")
    paths = c.get("gateway_pythonpath")
    require(type(paths) is dict and len(paths) == 1 and
            list(paths.values()) == [GATEWAY_TREE_SHA256],
            "a1_gateway_source_not_fixed")
    gateway = Path(next(iter(paths)))
    require(gateway.is_absolute() and gateway.is_dir() and not gateway.is_symlink() and
            _tree(gateway) == GATEWAY_TREE_SHA256,
            "a1_gateway_source_not_fixed")
    return lock, gateway


def _environment(gateway):
    environment = dict(os.environ)
    for name in ("PYTHONOPTIMIZE", "PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE",
                 "PYTHONSTARTUP"):
        environment.pop(name, None)
    environment.update({"PYTHONPATH": str(gateway), "PYTHONNOUSERSITE": "1",
                        "PYTHONDONTWRITEBYTECODE": "1"})
    return environment


def _child(c, gateway, script, args, *, cwd):
    try:
        result = subprocess.run([c["python"], "-B", "-s", "-c", script, *args],
                                capture_output=True, timeout=30, cwd=cwd,
                                env=_environment(gateway))
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RecoveryError("a1_gateway_cli_preflight_unavailable") from error
    require(result.returncode == 0 and len(result.stdout) <= 65536,
            "a1_gateway_cli_preflight_unavailable")
    try:
        return json.loads(result.stdout)
    except (ValueError, UnicodeError) as error:
        raise RecoveryError("a1_gateway_cli_preflight_invalid") from error


def verify_runtime(c, *, _allow_windows_fixture=False):
    """Fail before source creation if the host venv lacks any fixed CLI dependency."""
    lock, gateway = verify_preparation(c)
    versions = {row["project"]: row["version"] for row in lock["wheels"]}
    result = _child(c, gateway, RUNTIME_PROBE,
                    [json.dumps(versions), json.dumps(IMPORTS), str(gateway), c["python"]],
                    cwd=c["code_root"])
    correct_linux = result.get("system") == "linux" and result.get("machine") == "x86_64"
    local_fixture = (_allow_windows_fixture and result.get("system") == "win32" and
                     result.get("machine") == "AMD64")
    require(result.get("versions") == versions and
            Path(result.get("python", "")).resolve() == Path(c["python"]).resolve() and
            Path(result.get("gateway_module", "")).resolve().is_relative_to(gateway) and
            (correct_linux or local_fixture),
            "a1_gateway_cli_preflight_invalid")
    return {"status": "gateway_runtime_ready", "gateway_runtime_lock_sha256": LOCK_SHA256,
            "gateway_package_count": 14}


def _source_directory_snapshot(directory):
    require(directory.is_dir() and not directory.is_symlink(),
            "a1_gateway_source_data_invalid")
    rows = {}
    for path in directory.iterdir():
        require(path.is_file() and not path.is_symlink(), "a1_gateway_source_data_invalid")
        stat = path.stat()
        rows[path.name] = (stat.st_ino, stat.st_size, stat.st_mtime_ns, _hash(path))
    require(not any(name.startswith("diagnostics.sqlite") for name in rows),
            "a1_gateway_source_ledger_already_exists")
    return rows


def verify_usage_before_owners(c, source, *, _allow_windows_fixture=False):
    """Run the real _usage entry against a disposable synthetic SQLite copy."""
    verify_runtime(c, _allow_windows_fixture=_allow_windows_fixture)
    source = Path(source)
    gateway = Path(next(iter(c["gateway_pythonpath"])))
    inventory = read_json(source / "bundle-integrity.json")["files"]
    settings = source / "config/gateway/settings.json"
    environment = source / "private/gateway.env"
    require(settings.is_file() and environment.is_file() and
            not settings.is_symlink() and not environment.is_symlink() and
            inventory.get("config/gateway/settings.json") == file_hash(settings) and
            inventory.get("private/gateway.env") == file_hash(environment),
            "a1_gateway_source_settings_changed")
    original = _source_directory_snapshot(source / "data/gateway")
    report = source / "reports" / c["run_label"]
    require(report.is_dir() and not report.is_symlink(),
            "a1_gateway_preflight_report_missing")
    with tempfile.TemporaryDirectory(prefix="gateway-cli-preflight-", dir=report) as temporary:
        temp_source = Path(temporary) / "source"
        for name in ("config/gateway", "private", "data/gateway"):
            (temp_source / name).mkdir(parents=True, mode=0o700)
        for original_file, relative in ((settings, "config/gateway/settings.json"),
                                        (environment, "private/gateway.env")):
            target = temp_source / relative
            shutil.copyfile(original_file, target)
            target.chmod(0o600)
            require(file_hash(target) == file_hash(original_file),
                    "a1_gateway_preflight_copy_mismatch")
        request_id = "a1-gateway-preflight-synthetic"
        fixture = _child(c, gateway, FIXTURE,
                         [str(temp_source / "data/gateway/diagnostics.sqlite"), request_id,
                          str(temp_source / "config/gateway/settings.json")],
                         cwd=temporary)
        require(fixture == {"fixture": "one_synthetic_succeeded_attempt"},
                "a1_gateway_preflight_fixture_invalid")
        from .a1_clone_prepare import _usage
        day = datetime.now(timezone.utc).date()
        since = (day - timedelta(days=1)).isoformat() + "T00:00:00Z"
        until = (day + timedelta(days=2)).isoformat() + "T00:00:00Z"
        usage, raw = _usage(temp_source, c, since, until)
        require(type(raw) is bytes and usage.get("schema_version") == 1 and
                usage.get("key_space") == "chat" and
                usage.get("identity") == {"service": "companion"} and
                usage.get("counts") == {"total": 1, "succeeded": 1, "failed": 0,
                                        "cancelled": 0, "unknown": 0} and
                usage.get("coverage") == {"matching": 1, "scanned": 1,
                                          "truncated": False, "unmetered_total": 0} and
                len(usage.get("attempts", [])) == 1 and
                usage["attempts"][0].get("request_id") == request_id and
                usage["attempts"][0].get("reason") == "completed" and
                usage["attempts"][0].get("outcome") == "succeeded",
                "a1_gateway_cli_preflight_report_invalid")
    require(_source_directory_snapshot(source / "data/gateway") == original and
            inventory.get("config/gateway/settings.json") == file_hash(settings) and
            inventory.get("private/gateway.env") == file_hash(environment),
            "a1_gateway_source_changed_during_preflight")
    return {"status": "gateway_usage_cli_ready", "synthetic_attempts": 1,
            "real_usage_entry": True, "original_gateway_ledger_untouched": True,
            "gateway_runtime_lock_sha256": LOCK_SHA256}
