"""Prepare fixed Platform build inputs locally; the coordinator runs the controller.

This keeps the existing resident release's repository Dockerfile, web build and
image inspection approach. Preparation has no Docker, SSH or network call.
"""

from __future__ import annotations

import argparse
import base64
import gzip
import io
import json
import re
import subprocess
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

from .bundle import Refused, digest, git_files, json_bytes, require, verify
from .control_bundle import verify as verify_control


HERE = Path(__file__).resolve().parent
PLATFORM_PATHS = (
    "services/", "apps/web/", "scripts/build/", "tools/", "package.json",
    "package-lock.json", "pyproject.toml", "Dockerfile", ".dockerignore",
)
DEPENDENCY_FILES = (
    "Dockerfile", "package.json", "package-lock.json", "pyproject.toml",
    ".dockerignore", "scripts/build/runtime.lock", "scripts/build/tools.lock",
    "scripts/build/base-images.json",
)
IMAGE_FIELDS = (
    "User", "Entrypoint", "Cmd", "WorkingDir", "Healthcheck", "Env",
    "Volumes", "ExposedPorts", "StopSignal",
)
NODE_COMMIT = "47564d07257a43ad99dde63d6ea96cf8c169be5a"
NODE_SHA256 = "9aac3dc090a0317804b5787dd5136b4916ee686b718a11b18b74e976806cebf5"


def snapshot_files(repository, commit, prefixes):
    """Enumerate literal committed paths, then reuse the source ZIP's blob reader."""
    require(bool(re.fullmatch(r"[0-9a-f]{40}", commit)), "full_commit_required")
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository),
             "ls-tree", "-rz", commit],
            capture_output=True, check=False, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise Refused("git_unavailable") from None
    require(result.returncode == 0, "git_snapshot_unavailable")
    modes = {}
    for entry in result.stdout.split(b"\0"):
        if entry:
            header, raw_name = entry.split(b"\t", 1)
            modes[raw_name.decode("utf-8")] = int(header.split()[0], 8) & 0o777
    names = list(modes)
    selected = [name for name in names if any(
        name.startswith(prefix) if prefix.endswith("/") else name == prefix
        for prefix in prefixes
    )]
    require(all(any(name.startswith(prefix) if prefix.endswith("/") else name == prefix
                    for name in selected) for prefix in prefixes), "snapshot_files_missing")
    return git_files(repository, commit, selected), {name: modes[name] for name in selected}


def candidate_dockerfile(original, *, control=False):
    """Extend the same fixed repository recipe; never edit its worktree copy."""
    require(original.count("RUN npm run build\n") == 1, "web_build_recipe_changed")
    require(original.count("USER 10001:10001\n") == 1, "runtime_user_recipe_changed")
    require(" AS music-tools\n" in original, "music_tools_recipe_missing")
    web_hash = (
        "const fs=require('fs'),crypto=require('crypto'),path=require('path');"
        "const root='/build/apps/web/dist',hashes={};"
        "function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){"
        "const p=path.join(d,e.name);if(e.isDirectory())walk(p);"
        "else hashes[path.relative(root,p)]=crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex')}}"
        "walk(root);fs.writeFileSync('/build/web-hashes.json',JSON.stringify(hashes))"
    )
    recipe = original.replace("RUN npm run build\n", "RUN npm run build\nRUN node -e \"" + web_hash + "\"\n")
    addition = """COPY node-package/ /opt/tianshu-infrastructure-node/
RUN apt-get update \\
    && apt-get install --yes --no-install-recommends openssh-client \\
    && rm -rf /var/lib/apt/lists/* \\
    && /usr/local/bin/python -m venv /opt/tianshu-infrastructure-venv \\
    && /opt/tianshu-infrastructure-venv/bin/python -m pip install --no-cache-dir --only-binary=:all: --no-deps --require-hashes -r /opt/tianshu-infrastructure-node/requirements.lock \\
    && /opt/tianshu-infrastructure-venv/bin/python -m pip check \\
    && find /opt/tianshu-infrastructure-node -type d -exec chmod 0755 {} + \\
    && find /opt/tianshu-infrastructure-node -type f -exec chmod 0644 {} +
COPY --from=console /build/web-hashes.json /srv/tianshu/web-hashes.json
"""
    if control:
        addition += """COPY control-package/ /opt/tianshu-infrastructure-control/
RUN find /opt/tianshu-infrastructure-control -type d -exec chmod 0755 {} + \\
    && find /opt/tianshu-infrastructure-control -type f -exec chmod 0644 {} +
"""
    return recipe.replace("USER 10001:10001\n", addition + "USER 10001:10001\n")


def dependency_delta(source, previous, *, base_revision, revision, manifest=None):
    """Pin intentional lock/manifest changes while preserving the default unchanged build."""
    changed = {
        name: {"before": digest(previous[name]), "after": digest(source[name])}
        for name in DEPENDENCY_FILES if source[name] != previous[name]
    }
    if manifest is None:
        require(not changed, "music_dependency_input_changed")
        return {}
    expected = {"base_revision": base_revision, "revision": revision, "files": changed}
    require(manifest == expected and bool(changed), "dependency_change_manifest_mismatch")
    return changed


def prepare(*, platform_repo, platform_commit, music_commit, music_image,
            contracts_repo, contracts_commit, node_package, stage, docker, remote_python, tag, output,
            dependency_changes=None, control_package=None):
    output = Path(output)
    require(output.is_absolute() and output.parent.is_dir() and not output.exists(), "new_output_required")
    require(bool(re.fullmatch(r"sha256:[0-9a-f]{64}", music_image)), "immutable_music_image_required")
    require(PurePosixPath(stage).is_absolute() and ".." not in PurePosixPath(stage).parts, "absolute_stage_required")
    require(PurePosixPath(docker).is_absolute(), "absolute_docker_required")
    require(PurePosixPath(remote_python).is_absolute(), "absolute_remote_python_required")
    require(bool(re.fullmatch(r"[a-z0-9][a-z0-9./:_-]{1,200}", tag)), "image_tag_invalid")
    source, modes = snapshot_files(platform_repo, platform_commit, PLATFORM_PATHS)
    previous = git_files(platform_repo, music_commit, DEPENDENCY_FILES)
    changed_dependencies = dependency_delta(
        source, previous, base_revision=music_commit, revision=platform_commit,
        manifest=json.loads(Path(dependency_changes).read_bytes()) if dependency_changes else None,
    )
    ancestry = subprocess.run(
        ["git", "--no-replace-objects", "-C", str(platform_repo), "merge-base", "--is-ancestor", music_commit, platform_commit],
        capture_output=True, check=False, timeout=30,
    )
    require(ancestry.returncode == 0, "final_music_history_missing")
    contracts, contract_modes = snapshot_files(contracts_repo, contracts_commit, ("contracts/",))
    modes.update(contract_modes)
    node = verify(node_package)
    require(node["source_commit"] == NODE_COMMIT and node["package_sha256"] == NODE_SHA256,
            "accepted_node_package_required")
    files = {**source, **contracts}
    with zipfile.ZipFile(node_package) as archive:
        files.update({"node-package/" + name: archive.read(name) for name in archive.namelist()})
    control = verify_control(control_package) if control_package else None
    if control:
        with zipfile.ZipFile(control_package) as archive:
            files.update({"control-package/" + name: archive.read(name) for name in archive.namelist()})
    files["Dockerfile.candidate"] = candidate_dockerfile(source["Dockerfile"].decode("utf-8"), control=bool(control)).encode()
    config = {
        "revision": platform_commit, "base_revision": music_commit, "base_image": music_image,
        "contracts_revision": contracts_commit, "node_commit": NODE_COMMIT,
        "node_package_sha256": NODE_SHA256,
        "stage": stage, "docker": docker, "tag": tag,
        "source_hashes": {name: digest(raw) for name, raw in files.items()},
        "source_modes": {name: modes.get(name, 0o644) for name in files},
        "dependency_hashes": {name: digest(source[name]) for name in DEPENDENCY_FILES},
        "dependency_changes": changed_dependencies,
        "inherited_image_fields": list(IMAGE_FIELDS),
    }
    if control:
        config["control_package"] = control
    files["build-inputs.json"] = json_bytes(config)
    files["build-controller.py"] = (HERE / "image_controller.py").read_bytes().replace(b"\r\n", b"\n")
    compile(files["build-controller.py"], "build-controller.py", "exec")
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:") as archive:
        for name, raw in sorted(files.items()):
            member = tarfile.TarInfo(name)
            member.size = len(raw)
            member.mode = modes.get(name, 0o644)
            archive.addfile(member, io.BytesIO(raw))
    packed = gzip.compress(buffer.getvalue(), mtime=0)
    output.mkdir(mode=0o700)
    (output / "build-context.tar.gz").write_bytes(packed)
    (output / "build-inputs.json").write_bytes(files["build-inputs.json"])
    (output / "Dockerfile.candidate").write_bytes(files["Dockerfile.candidate"])
    (output / "build-controller.py").write_bytes(files["build-controller.py"])
    receipt = {
        "status": "prepared_locally", "production_changed": False,
        "source_commit": platform_commit, "music_commit": music_commit,
        "contracts_commit": contracts_commit, "node_commit": NODE_COMMIT,
        "node_package_sha256": NODE_SHA256, "archive_sha256": digest(packed),
        "controller_sha256": digest(files["build-controller.py"]), "stage": stage,
        "file_count": len(files), "output": str(output),
        "dependency_changes": changed_dependencies,
    }
    if control:
        receipt["control_package"] = control
    (output / "prepared.json").write_bytes(json_bytes(receipt))
    prefix = ("CONFIG=" + repr(config) + "\nARCHIVE_SHA256=" + repr(receipt["archive_sha256"])
              + "\nCONTROLLER_SHA256=" + repr(receipt["controller_sha256"])
              + "\nREMOTE_PYTHON=" + repr(remote_python) + "\n")
    for action, body in BUILD_ACTIONS.items():
        payload = prefix + ("DATA=" + repr(base64.b64encode(packed).decode()) + "\n" if action == "stage" else "") + body
        compile(payload, "remote-build-" + action, "exec")
        (output / ("remote-build-" + action + ".py")).write_text(payload, encoding="utf-8", newline="\n")
    return receipt


BUILD_ACTIONS = {
    "stage": r'''
import base64,hashlib,io,json,os,tarfile
from pathlib import Path
stage=Path(CONFIG['stage'])
assert stage.parent.resolve() in {Path('/volume2/tianshu-v2-resident-updates'),Path('/volume1/Download/Tianshu/updates')}
assert not stage.exists(),'candidate stage exists; inspect it without overwriting'
raw=base64.b64decode(DATA)
assert hashlib.sha256(raw).hexdigest()==ARCHIVE_SHA256
os.umask(0o077)
stage.mkdir(mode=0o700)
with tarfile.open(fileobj=io.BytesIO(raw),mode='r:gz') as archive:
 for member in archive:
  relative=Path(member.name)
  assert member.isfile() and not relative.is_absolute() and '..' not in relative.parts
  assert member.mode in {0o644,0o755}
  target=stage/relative
  directory=stage
  for part in relative.parts[:-1]:
   directory=directory/part
   directory.mkdir(mode=0o755,exist_ok=True)
   directory.chmod(0o755)
  with target.open('xb') as stream:stream.write(archive.extractfile(member).read())
  target.chmod(member.mode)
assert json.loads((stage/'build-inputs.json').read_bytes())==CONFIG
assert hashlib.sha256((stage/'build-controller.py').read_bytes()).hexdigest()==CONTROLLER_SHA256
print(json.dumps({'status':'staged','stage':str(stage),'source_archive_sha256':ARCHIVE_SHA256,'production_changed':False}))
''',
    "launch": r'''
import hashlib,json,os,subprocess,time
from pathlib import Path
stage=Path(CONFIG['stage'])
assert stage.parent.resolve() in {Path('/volume2/tianshu-v2-resident-updates'),Path('/volume1/Download/Tianshu/updates')}
assert json.loads((stage/'build-inputs.json').read_bytes())==CONFIG
script=stage/'build-controller.py'
assert hashlib.sha256(script.read_bytes()).hexdigest()==CONTROLLER_SHA256
assert not any((stage/name).exists() for name in ['launch-intent.json','start.json','build-result.json'])
os.umask(0o077)
with (stage/'launch-intent.json').open('x') as stream:
 json.dump({'time':time.time(),'controller_sha256':CONTROLLER_SHA256},stream)
 stream.flush();os.fsync(stream.fileno())
with (stage/'controller.log').open('xb') as log:
 worker=subprocess.Popen([REMOTE_PYTHON,'-B',str(script)],cwd=stage,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
receipt={'status':'started','stage':str(stage),'pid':worker.pid,'source_archive_sha256':ARCHIVE_SHA256,'production_changed':False}
with (stage/'start.json').open('x') as stream:
 json.dump(receipt,stream);stream.flush();os.fsync(stream.fileno())
print(json.dumps(receipt))
''',
    "poll": r'''
import json
from pathlib import Path
stage=Path(CONFIG['stage'])
assert json.loads((stage/'build-inputs.json').read_bytes())==CONFIG
if (stage/'build-result.json').exists():print((stage/'build-result.json').read_text())
elif (stage/'start.json').exists():
 receipt=json.loads((stage/'start.json').read_bytes())
 try:running=str(stage/'build-controller.py').encode() in (Path('/proc')/str(receipt['pid'])/'cmdline').read_bytes().split(b'\0')
 except FileNotFoundError:running=False
 receipt['status']='running' if running else 'worker_exited_without_receipt'
 print(json.dumps(receipt))
else:print(json.dumps({'status':'launch_outcome_unknown' if (stage/'launch-intent.json').exists() else 'staged','stage':str(stage),'production_changed':False}))
''',
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("platform-repo", "platform-commit", "music-commit", "music-image",
                 "contracts-repo", "contracts-commit", "node-package", "stage", "docker", "remote-python", "tag", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--dependency-changes", help="Exact base/candidate commits and before/after hashes for intentional dependency input changes")
    parser.add_argument("--control-package")
    print(json.dumps(prepare(**vars(parser.parse_args())), sort_keys=True))


if __name__ == "__main__":
    main()
