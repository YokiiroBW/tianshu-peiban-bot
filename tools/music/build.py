"""Build pinned, static local music tools and bundle their corresponding source."""

import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request

VERSION = "go1.27.2"
SHA256 = "ecbadb99091a3f46e31f5f934b068b1864eafa7995211b39eaddf76996045fe5"
ROOT = Path("/build")
SOURCE = Path(__file__).resolve().parent
archive = ROOT / (VERSION + ".linux-amd64.tar.gz")
with (
    urllib.request.urlopen("https://go.dev/dl/" + archive.name, timeout=120) as source,
    archive.open("wb") as target,
):
    shutil.copyfileobj(source, target)
assert hashlib.file_digest(archive.open("rb"), "sha256").hexdigest() == SHA256
with tarfile.open(archive) as bundle:
    bundle.extractall(ROOT, filter="data")
environment = {**os.environ, "GOTOOLCHAIN": "local", "CGO_ENABLED": "0", "GOPATH": "/build/gopath"}
go = str(ROOT / "go/bin/go")
output = ROOT / "music-tools"
output.mkdir(exist_ok=True)
for name in ("musicbridge", "musicdecode"):
    directory = SOURCE / name
    subprocess.run([go, "mod", "verify"], cwd=directory, env=environment, check=True)
    subprocess.run(
        [
            go,
            "build",
            "-trimpath",
            "-mod=readonly",
            "-ldflags=-s -w",
            "-o",
            str(output / ("tianshu-" + name)),
            ".",
        ],
        cwd=directory,
        env=environment,
        check=True,
    )
    subprocess.run([go, "mod", "vendor"], cwd=directory, env=environment, check=True)
with tarfile.open(output / "corresponding-source.tar.gz", "w:gz") as bundle:
    bundle.add(SOURCE, arcname="tools/music")
