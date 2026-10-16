"""Coordinator-run NAS build worker, following the existing resident candidate build.

Run only after reviewing/staging the fixed build-context archive. No resident
mount, production configuration or service is opened by this worker.
"""

import hashlib
import json
import os
import subprocess
import traceback
from pathlib import Path


STAGE = Path(__file__).resolve().parent
CONFIG = json.loads((STAGE / "build-inputs.json").read_bytes())
DOCKER = CONFIG["docker"]


def command(arguments, **kwargs):
    return subprocess.run(arguments, capture_output=True, text=True, check=True, **kwargs)


def in_image(image, program, *, python="python", timeout=45):
    return command([DOCKER, "run", "--rm", "-i", "--read-only", "--network", "none",
                    "--entrypoint", python, image, "-I", "-B", "-"], input=program, timeout=timeout)


def finish(value):
    temporary = STAGE / "build-result.tmp"
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    temporary.replace(STAGE / "build-result.json")


MUSIC_PROBE = """import hashlib,json,subprocess
from pathlib import Path
paths=['/usr/local/bin/tianshu-musicbridge','/usr/local/bin/tianshu-musicdecode','/usr/share/tianshu-music-tools/corresponding-source.tar.gz']
print(json.dumps({'files':{name:hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in paths},'ffmpeg':subprocess.check_output(['ffmpeg','-version'],text=True).splitlines()[0]}))
"""


try:
    assert str(STAGE) == CONFIG["stage"], "build_stage_changed"
    assert not (STAGE / "build-result.json").exists(), "build_receipt_exists"
    for name, expected in CONFIG["source_hashes"].items():
        assert hashlib.sha256((STAGE / name).read_bytes()).hexdigest() == expected, "build_source_changed"
    base = json.loads(command([DOCKER, "image", "inspect", CONFIG["base_image"]], timeout=15).stdout)[0]
    assert base["Id"] == CONFIG["base_image"] and base["Architecture"] == "amd64"
    assert base["Config"]["Labels"]["org.opencontainers.image.revision"] == CONFIG["base_revision"]
    assert base["Config"]["User"] == "10001:10001"
    assert command([DOCKER, "image", "ls", "--quiet", CONFIG["tag"]], timeout=15).stdout.strip() == "", "existing_candidate_tag"
    music_before = json.loads(in_image(CONFIG["base_image"], MUSIC_PROBE).stdout)
    arguments = [DOCKER, "build", "--pull=false", "--network=bridge", "--file", str(STAGE / "Dockerfile.candidate")]
    for label, value in {
        "org.opencontainers.image.revision": CONFIG["revision"],
        "org.tianshu.contracts.revision": CONFIG["contracts_revision"],
        "org.tianshu.infrastructure.revision": CONFIG["node_commit"],
        "org.tianshu.infrastructure.package-sha256": CONFIG["node_package_sha256"],
    }.items():
        arguments.extend(["--label", label + "=" + value])
    arguments.extend(["--tag", CONFIG["tag"], str(STAGE)])
    with (STAGE / "build.log").open("xb") as log:
        result = subprocess.run(arguments, stdout=log, stderr=subprocess.STDOUT,
                                env={**os.environ, "DOCKER_BUILDKIT": "0"}, timeout=1800)
    assert result.returncode == 0, "candidate_build_failed"
    image = json.loads(command([DOCKER, "image", "inspect", CONFIG["tag"]], timeout=15).stdout)[0]
    assert image["Architecture"] == "amd64"
    for field in CONFIG["inherited_image_fields"]:
        assert image["Config"].get(field) == base["Config"].get(field), "inherited_image_config_changed"
    music_after = json.loads(in_image(image["Id"], MUSIC_PROBE).stdout)
    assert music_after == music_before, "music_runtime_changed"
    program = "CONFIG=" + repr(CONFIG) + "\n" + r'''
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
    verified = json.loads(in_image(image["Id"], program).stdout)
    finish({
        "status": "built", "image_id": image["Id"], "revision": CONFIG["revision"],
        "base_image_id": CONFIG["base_image"], "base_revision": CONFIG["base_revision"],
        "contracts_revision": CONFIG["contracts_revision"], "node_commit": CONFIG["node_commit"],
        "node_package_sha256": CONFIG["node_package_sha256"], "stage": str(STAGE), "tag": CONFIG["tag"],
        "linux_checks": verified, "music_runtime_preserved": True,
        "production_changed": False, "production_data_mounted": False, "production_service_started": False,
    })
except BaseException as error:
    (STAGE / "controller-error.log").write_text(traceback.format_exc())
    if not (STAGE / "build-result.json").exists():
        finish({"status": "failed", "error_type": type(error).__name__,
                "private_error_log": str(STAGE / "controller-error.log"), "production_changed": False})
    raise
