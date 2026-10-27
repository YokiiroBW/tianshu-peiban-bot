"""Resolve the actual product wheels once for CPython 3.12 on Debian amd64."""

from email.parser import BytesParser
from email.policy import default
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import zipfile


PRODUCTS = {
    "platform": "tianshu-platform-backend",
    "companion": "tianshu-companion",
    "memory": "tianshu-memory",
    "gateway": "tianshu-model-gateway",
}
STORAGE_WHEEL = "tianshu_storage-0.1.3-py3-none-any.whl"
PLATFORM = "x86_64-manylinux_2_36"
PYTHON_VERSION = "3.12"
INDEX = "https://pypi.org/simple"


def normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def wheel_metadata(path):
    with zipfile.ZipFile(path) as archive:
        names = [
            name for name in archive.namelist() if name.endswith(".dist-info/METADATA")
        ]
        if len(names) != 1:
            raise ValueError("wheel_metadata_invalid")
        metadata = BytesParser(policy=default).parsebytes(archive.read(names[0]))
        wheel = BytesParser(policy=default).parsebytes(
            archive.read(names[0].removesuffix("METADATA") + "WHEEL")
        )
        if not metadata["Name"] or not metadata["Version"]:
            raise ValueError("wheel_metadata_invalid")
        return {
            "name": normalized(str(metadata["Name"])),
            "version": str(metadata["Version"]),
            "requires_python": str(metadata.get("Requires-Python", "")),
            "requires_dist": list(metadata.get_all("Requires-Dist", [])),
            "provides_extra": list(metadata.get_all("Provides-Extra", [])),
            "tags": list(wheel.get_all("Tag", [])),
            "sha256": sha256(path),
            "file": Path(path).name,
        }


def run(argv, *, cwd):
    subprocess.run([str(value) for value in argv], cwd=cwd, check=True)


def lock(wheels, output, *, uv="uv", python=sys.executable, constraints=None):
    """Use wheel METADATA (including Companion's NoneBot extra), then verify binaries.

    Resolution is explicitly Linux. The download's --no-deps prevents pip from
    re-evaluating Linux markers against a Windows build machine; uv has already
    resolved the complete graph. Linux installation and pip check remain image steps.
    """
    root = Path(output)
    inventory = {name: wheel_metadata(path) for name, path in wheels.items()}
    if set(inventory) != {*PRODUCTS, "storage"}:
        raise ValueError("distribution_wheel_set_invalid")
    for key, name in {**PRODUCTS, "storage": "tianshu-storage"}.items():
        metadata = inventory[key]
        if metadata["name"] != name or metadata["tags"] != ["py3-none-any"]:
            raise ValueError("distribution_product_wheel_invalid")
    if inventory["storage"]["version"] != "0.1.3":
        raise ValueError("distribution_storage_version_invalid")
    if "nonebot" not in inventory["companion"]["provides_extra"]:
        raise ValueError("companion_nonebot_extra_required")
    inputs = []
    for name, metadata in inventory.items():
        extras = "[nonebot]" if name == "companion" else ""
        inputs.append(f"./wheels/{metadata['file']}{extras}")
    (root / "requirements.in").write_text("\n".join(inputs) + "\n", encoding="utf-8")
    command = [
        uv,
        "pip",
        "compile",
        "requirements.in",
        "--output-file",
        "runtime.lock",
        "--python",
        python,
        "--python-version",
        PYTHON_VERSION,
        "--python-platform",
        PLATFORM,
        "--only-binary",
        ":all:",
        "--generate-hashes",
        "--no-header",
        "--no-annotate",
        "--no-config",
        "--no-python-downloads",
        "--default-index",
        INDEX,
    ]
    # Omit only the local output lines, not their dependency graph. This keeps
    # machine-specific file:// URLs out of the final, hash-locked context.
    for metadata in inventory.values():
        command.extend(["--no-emit-package", metadata["name"]])
    constraint_hash = None
    if constraints is not None:
        raw = Path(constraints).read_bytes()
        (root / "constraints.txt").write_bytes(raw)
        constraint_hash = sha256(root / "constraints.txt")
        command.extend(["--constraint", "constraints.txt"])
    run(command, cwd=root)
    path = root / "runtime.lock"
    public = path.read_text(encoding="utf-8")
    if "file:" in public or re.search(r"[A-Za-z]:[/\\]", public) or " @ " in public:
        raise ValueError("nonportable_dependency_lock")
    if constraint_hash is not None:
        public = f"# constraints-sha256: {constraint_hash}\n" + public
    local = ["\n# Products and the one coordinator-supplied storage artifact.\n"]
    for requirement, metadata in zip(inputs, inventory.values(), strict=True):
        local.append(f"{requirement} --hash=sha256:{metadata['sha256']}\n")
    path.write_text(public + "".join(local), encoding="utf-8", newline="\n")
    download = [
        python,
        "-m",
        "pip",
        "download",
        "--no-deps",
        "--only-binary=:all:",
        "--require-hashes",
        "--index-url",
        INDEX,
        "--python-version",
        PYTHON_VERSION,
        "--implementation",
        "cp",
        "--abi",
        "cp312",
        "--abi",
        "abi3",
        "--abi",
        "none",
    ]
    # Bookworm/glibc 2.36 can use wheels built for any older manylinux policy.
    for version in range(36, 4, -1):
        download.extend(["--platform", f"manylinux_2_{version}_x86_64"])
    for platform in (
        "manylinux2014_x86_64",
        "manylinux2010_x86_64",
        "manylinux1_x86_64",
        "linux_x86_64",
    ):
        download.extend(["--platform", platform])
    run([*download, "--dest", "wheels", "-r", "runtime.lock"], cwd=root)
    all_wheels = [
        wheel_metadata(item) for item in sorted((root / "wheels").glob("*.whl"))
    ]
    names = [item["name"] for item in all_wheels]
    if len(names) != len(set(names)):
        raise ValueError("duplicate_distribution_package")
    receipt = {
        "target": {"python": PYTHON_VERSION, "platform": PLATFORM},
        "status": "resolved_and_binary_hashes_verified_not_installed_on_linux",
        "products": inventory,
        "wheels": all_wheels,
        "lock_sha256": sha256(path),
        "constraints_sha256": constraint_hash,
    }
    (root / "dependencies.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt
