"""Coordinator-run NAS build worker, following the existing resident candidate build.

Run only after reviewing/staging the fixed build-context archive. No resident
mount, production configuration or service is opened by this worker.
Use --verify-image with a new --receipt-name to recheck an existing immutable
candidate without building it or replacing the earlier build evidence.
"""

import argparse
import hashlib
import inspect
import json
import os
import re
import subprocess
import traceback
from pathlib import Path


def command(arguments, **kwargs):
    return subprocess.run(arguments, capture_output=True, text=True, check=True, **kwargs)


def in_image(config, image, program, *, python="python", timeout=45):
    return command([config["docker"], "run", "--rm", "-i", "--read-only", "--network", "none",
                    "--entrypoint", python, image, "-I", "-B", "-"], input=program, timeout=timeout)


def finish(path, value):
    """Publish a complete new receipt without replacing earlier evidence."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, sort_keys=True, indent=2) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.link(temporary, path)
    temporary.unlink()


def archive_fingerprint(path):
    """Hash source semantics without gzip/mtime/ownership or non-executable modes."""
    import hashlib
    import json
    import tarfile

    members = []
    names = set()
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive:
            assert member.name not in names, "duplicate_music_source_member"
            names.add(member.name)
            content = None
            if member.isfile():
                digest = hashlib.sha256()
                with archive.extractfile(member) as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
                content = digest.hexdigest()
            members.append({
                "path": member.name, "kind": member.type.hex(), "content_sha256": content,
                "link": member.linkname,
                "executable": bool(member.mode & 0o111) if member.isfile() or member.isdir() else None,
            })
    members.sort(key=lambda member: member["path"])
    raw = json.dumps(members, sort_keys=True, separators=(",", ":")).encode()
    return {"members": len(members), "content_sha256": hashlib.sha256(raw).hexdigest()}


MUSIC_PROBE = "import hashlib,json,shutil,subprocess\nfrom pathlib import Path\n" + inspect.getsource(archive_fingerprint) + r'''
paths=['/usr/local/bin/tianshu-musicbridge','/usr/local/bin/tianshu-musicdecode']
ffmpeg=Path(shutil.which('ffmpeg')).resolve(strict=True)
print(json.dumps({
 'binaries':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in paths},
 'source_archive':archive_fingerprint('/usr/share/tianshu-music-tools/corresponding-source.tar.gz'),
 'ffmpeg_sha256':hashlib.sha256(ffmpeg.read_bytes()).hexdigest(),
 'ffmpeg':subprocess.check_output([str(ffmpeg),'-version'],text=True).splitlines()[0],
}))
'''


LINUX_CHECKS = r'''
import hashlib,json,os,subprocess,sysconfig
from pathlib import Path
assert os.getuid()==10001
purelib=Path(sysconfig.get_paths()['purelib'])
for prefix,root in [('services/platform/',purelib/'services/platform'),('contracts/',Path('/srv/tianshu/contracts'))]:
 expected={name[len(prefix):]:value for name,value in CONFIG['source_hashes'].items() if name.startswith(prefix)}
 actual={str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts}
 assert actual==expected,'installed_source_changed'
web=Path('/srv/tianshu/web')
expected=json.loads(Path('/srv/tianshu/web-hashes.json').read_bytes())
actual={str(p.relative_to(web)):hashlib.sha256(p.read_bytes()).hexdigest() for p in web.rglob('*') if p.is_file()}
assert actual==expected,'installed_web_changed'
node=Path('/opt/tianshu-infrastructure-node')
manifest=json.loads((node/'package-manifest.json').read_bytes())
assert manifest['source_commit']==CONFIG['node_commit']
for name,item in manifest['files'].items():
 assert hashlib.sha256((node/name).read_bytes()).hexdigest()==item['sha256'],'installed_node_changed'
 assert (node/name).stat().st_uid==0 and (node/name).stat().st_mode&0o222==0o200,'node_file_mode_changed'
version=json.loads(subprocess.check_output(['/opt/tianshu-infrastructure-venv/bin/python','-I','-B',str(node/'run_node.py'),'--version']))
assert version['source_commit']==CONFIG['node_commit']
yaml=subprocess.check_output(['/opt/tianshu-infrastructure-venv/bin/python','-I','-B','-c','import yaml;print(yaml.__version__)'],text=True).strip()
assert yaml=='6.0.3'
for python in ['python','/opt/tianshu-infrastructure-venv/bin/python']:
 subprocess.run([python,'-I','-m','pip','check'],capture_output=True,check=True)
ssh=subprocess.run(['/usr/bin/ssh','-V'],capture_output=True,text=True,check=True)
ssh_package=subprocess.check_output(['/usr/bin/dpkg-query','--show','--showformat=${Version}','openssh-client'],text=True).strip()
print(json.dumps({'uid':os.getuid(),'node_source':version['source_commit'],'yaml':yaml,'openssh':(ssh.stdout+ssh.stderr).strip(),'openssh_package':ssh_package,'installed_web_files':len(actual),'source_hashes_verified':True,'main_venv_preserved':True,'independent_node_venv':True}))
'''


def candidate_labels(config):
    return {
        "org.opencontainers.image.revision": config["revision"],
        "org.tianshu.contracts.revision": config["contracts_revision"],
        "org.tianshu.infrastructure.revision": config["node_commit"],
        "org.tianshu.infrastructure.package-sha256": config["node_package_sha256"],
    }


def image_inspect(config, reference):
    return json.loads(command([config["docker"], "image", "inspect", reference], timeout=15).stdout)[0]


def check_inputs(stage, config):
    assert str(stage) == config["stage"], "build_stage_changed"
    for name, expected in config["source_hashes"].items():
        assert hashlib.sha256((stage / name).read_bytes()).hexdigest() == expected, "build_source_changed"
    base = image_inspect(config, config["base_image"])
    assert base["Id"] == config["base_image"] and base["Architecture"] == "amd64"
    assert base["Config"]["Labels"]["org.opencontainers.image.revision"] == config["base_revision"]
    assert base["Config"]["User"] == "10001:10001"
    return base


def build_candidate(stage, config):
    assert command([config["docker"], "image", "ls", "--quiet", config["tag"]], timeout=15).stdout.strip() == "", "existing_candidate_tag"
    arguments = [config["docker"], "build", "--pull=false", "--network=bridge", "--file", str(stage / "Dockerfile.candidate")]
    for label, value in candidate_labels(config).items():
        arguments.extend(["--label", label + "=" + value])
    arguments.extend(["--tag", config["tag"], str(stage)])
    with (stage / "build.log").open("xb") as log:
        result = subprocess.run(arguments, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, "DOCKER_BUILDKIT": "0"}, timeout=1800)
    assert result.returncode == 0, "candidate_build_failed"
    return config["tag"]


def verify_candidate(config, base, reference, music_before):
    """The same complete acceptance applies after building or to an existing ID."""
    image = image_inspect(config, reference)
    if reference.startswith("sha256:"):
        assert image["Id"] == reference, "candidate_image_changed"
    assert image["Architecture"] == "amd64"
    for label, expected in candidate_labels(config).items():
        assert image["Config"]["Labels"].get(label) == expected, "candidate_label_changed"
    for field in config["inherited_image_fields"]:
        assert image["Config"].get(field) == base["Config"].get(field), "inherited_image_config_changed"
    music_after = json.loads(in_image(config, image["Id"], MUSIC_PROBE).stdout)
    assert music_after == music_before, "music_runtime_changed"
    program = "CONFIG=" + repr(config) + "\n" + LINUX_CHECKS
    verified = json.loads(in_image(config, image["Id"], program).stdout)
    return {
        "status": "built", "image_id": image["Id"], "revision": config["revision"],
        "base_image_id": config["base_image"], "base_revision": config["base_revision"],
        "contracts_revision": config["contracts_revision"], "node_commit": config["node_commit"],
        "node_package_sha256": config["node_package_sha256"], "stage": config["stage"], "tag": config["tag"],
        "linux_checks": verified, "music_runtime_preserved": True,
        "music_runtime": music_after,
        "production_changed": False, "production_data_mounted": False, "production_service_started": False,
    }


def run(stage, *, verify_image=None, receipt_name=None):
    stage = Path(stage).resolve(strict=True)
    if verify_image is None:
        assert receipt_name is None, "receipt_name_requires_verify_image"
        receipt_name, error_name = "build-result.json", "controller-error.log"
    else:
        assert re.fullmatch(r"sha256:[0-9a-f]{64}", verify_image), "immutable_candidate_image_required"
        assert receipt_name and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\.json", receipt_name), "new_receipt_basename_required"
        assert receipt_name != "build-result.json", "original_build_receipt_must_be_preserved"
        error_name = receipt_name[:-5] + "-controller-error.log"
    receipt, error_log = stage / receipt_name, stage / error_name
    # Refuse before entering the failure writer, preserving any previous attempt.
    assert not any(path.exists() for path in (receipt, error_log, receipt.with_name(receipt.name + ".tmp"))), "build_receipt_exists"
    built_this_run = False
    try:
        config = json.loads((stage / "build-inputs.json").read_bytes())
        base = check_inputs(stage, config)
        music_before = json.loads(in_image(config, config["base_image"], MUSIC_PROBE).stdout)
        reference = verify_image
        if reference is None:
            reference = build_candidate(stage, config)
            built_this_run = True
        result = verify_candidate(config, base, reference, music_before)
        result.update({
            "verification_only": verify_image is not None, "image_rebuilt": built_this_run,
            "controller_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "build_inputs_sha256": hashlib.sha256((stage / "build-inputs.json").read_bytes()).hexdigest(),
        })
        if verify_image is not None:
            result["prior_evidence_sha256"] = {
                name: hashlib.sha256((stage / name).read_bytes()).hexdigest()
                for name in ("build-result.json", "controller-error.log", "build-controller.py", "build.log")
                if (stage / name).is_file()
            }
        finish(receipt, result)
        return result
    except BaseException as error:
        with error_log.open("x", encoding="utf-8") as stream:
            stream.write(traceback.format_exc())
        if not receipt.exists():
            finish(receipt, {"status": "failed", "error_type": type(error).__name__,
                             "private_error_log": str(error_log), "production_changed": False,
                             "verification_only": verify_image is not None, "image_rebuilt": built_this_run})
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-image", help="Verify an already built immutable sha256 image; never build or tag")
    parser.add_argument("--receipt-name", help="New stage-local JSON basename, required with --verify-image")
    args = parser.parse_args(argv)
    result = run(Path(__file__).resolve().parent, verify_image=args.verify_image, receipt_name=args.receipt_name)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
