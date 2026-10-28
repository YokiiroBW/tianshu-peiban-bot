"""Fixed Git control package, using the observation package's exporter and ZIP rules."""

from __future__ import annotations

import argparse
import ast
import json
import re
import zipfile
from pathlib import Path

from . import bundle


HERE = Path(__file__).resolve().parent
PROTOCOL = "infrastructure-control.v1"
CONTRACT = "contracts/infrastructure-control/candidate-v1"
CONTROL_FILES = tuple("ops/infrastructure/control/" + name for name in (
    "README.md", "__init__.py", "__main__.py", "client.py", "compose-probe.js",
    "compose-wrapper.js", "config.py", "engine.py", "files.py", "lifecycle.py",
    "protocol.py", "relay_entry.py", "remote.py", "remote_entry.py", "service.py", "state.py",
))
CONTRACT_FILES = tuple(CONTRACT + "/" + name for name in (
    "README.md", "request.schema.json", "response.schema.json", "config.schema.json",
    "remote-config.schema.json", "examples/config.json", "examples/remote-config.json",
))
SOURCE_FILES = (*bundle.SOURCE_FILES, "ops/infrastructure/acceptance.py", "ops/infrastructure/ACCEPTANCE.md",
                *bundle.CONTRACT_FILES, *CONTROL_FILES, *CONTRACT_FILES)
ASSETS = {"run_control.py": "control_launcher.py", "run_remote.py": "control_remote_launcher.py",
          "requirements.lock": "requirements.lock"}
EXTRA_FILES = frozenset({"ops/__init__.py", *ASSETS})
RUNTIME = {**bundle.RUNTIME, "entrypoint": "run_control.py", "remote_flag": "--remote"}


def versions(files):
    bundle._versions(files)
    try:
        tree = ast.parse(files["ops/infrastructure/control/protocol.py"].decode())
        values = [node.value.value for node in tree.body
                  if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                  and any(isinstance(target, ast.Name) and target.id == "VERSION" for target in node.targets)]
        bundle.require(values == [PROTOCOL], "control_protocol_version_mismatch")
        for name, version in (("request.schema.json", PROTOCOL), ("response.schema.json", PROTOCOL),
                              ("config.schema.json", "infrastructure-control.config.v1"),
                              ("remote-config.schema.json", "infrastructure-control.remote.v1")):
            schema = json.loads(files[CONTRACT + "/" + name])
            bundle.require(schema["properties"]["version"]["const"] == version, "control_contract_version_mismatch")
    except bundle.Refused:
        raise
    except (KeyError, TypeError, ValueError, SyntaxError, UnicodeError):
        raise bundle.Refused("control_snapshot_metadata_invalid") from None


def contract_metadata(files, commit):
    return {"path": CONTRACT, "source_commit": commit,
            "files_sha256": bundle.digest(bundle.json_bytes({name: bundle.digest(files[name]) for name in sorted(CONTRACT_FILES)})),
            "schemas": {name: bundle.digest(files[CONTRACT + "/" + name]) for name in (
                "request.schema.json", "response.schema.json", "config.schema.json", "remote-config.schema.json")}}


def build(repository, commit, output):
    output = Path(output)
    bundle.require(output.is_absolute() and output.parent.is_dir() and not output.exists(), "new_output_required")
    files = bundle.git_files(repository, commit, SOURCE_FILES)
    versions(files)
    origins = {name: "source_git" for name in files}
    files["ops/__init__.py"] = b'"""Packaged TianShu operations namespace."""\n'
    origins["ops/__init__.py"] = "package"
    for name, asset in ASSETS.items():
        files[name] = (HERE / asset).read_bytes().replace(b"\r\n", b"\n")
        origins[name] = "package"
    manifest = {
        "format_version": 1, "package": "tianshu-infrastructure-control", "source_commit": commit,
        "protocol_version": PROTOCOL, "observation_protocol_version": bundle.PROTOCOL,
        "contract": contract_metadata(files, commit), "runtime": RUNTIME,
        "files": {name: {"sha256": bundle.digest(raw), "bytes": len(raw), "mode": "0644", "origin": origins[name]}
                  for name, raw in sorted(files.items())},
    }
    files[bundle.MANIFEST] = bundle.json_bytes(manifest)
    bundle.write_archive(output, files)
    return verify(output)


def verify(path):
    try:
        manifest, files = bundle.read_archive(path, {*SOURCE_FILES, *EXTRA_FILES, bundle.MANIFEST})
        bundle.require(manifest["format_version"] == 1 and manifest["package"] == "tianshu-infrastructure-control"
                       and manifest["protocol_version"] == PROTOCOL and manifest["observation_protocol_version"] == bundle.PROTOCOL
                       and manifest["runtime"] == RUNTIME, "control_package_version_mismatch")
        bundle.require(bool(re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"])), "full_commit_required")
        versions(files)
        bundle.require(manifest["contract"] == contract_metadata(files, manifest["source_commit"]), "control_contract_hash_mismatch")
    except bundle.Refused:
        raise
    except (OSError, ValueError, KeyError, TypeError, UnicodeError, zipfile.BadZipFile):
        raise bundle.Refused("control_package_invalid") from None
    return {"status": "package_verified", "source_commit": manifest["source_commit"], "protocol_version": PROTOCOL,
            "package_sha256": bundle.digest(Path(path).read_bytes()), "contract": manifest["contract"],
            "file_count": len(files), "deployed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("build")
    create.add_argument("--repo", type=Path, required=True)
    create.add_argument("--commit", required=True)
    create.add_argument("--output", type=Path, required=True)
    inspect = actions.add_parser("verify")
    inspect.add_argument("package", type=Path)
    args = parser.parse_args(argv)
    try:
        result = build(args.repo, args.commit, args.output) if args.action == "build" else verify(args.package)
    except bundle.Refused as error:
        print(json.dumps({"status": "refused", "reason": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
