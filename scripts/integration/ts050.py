"""TS-050 real source-sync chain; previous partial and TLS evidence stays immutable."""

import argparse
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tarfile
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[2]
PINS = {
    "companion": "a759f1755c3e9b2afa6b6e5b3fd5e2b6b8072d79",
    "memory": "ba0e50d56d6a4e816267d710c41c6b0c49035431",
    "model-gateway": "b3b101faf3902f05d80818b39fe7c91367865d4e",
    "platform": "a94d34534ba0b6002bdcdab9db1d5dd899a06a16",
}
MANIFEST = "81e6cc4ddef7c6f82e055d4cb04b090db036dd5c52763473ce697aa02db478a1"
SOURCE_MANIFEST = "178d0ce66210bdfad4cfb85d8b5f0905b0b67f834e2a530efe5636ff0373633d"
PROFILE_MANIFEST = "488d05438dd5b5abaa43a66a7eab0eb5cf615d5af01a964a7286cd23e68f7eb7"
RUNTIME = ROOT / ".runtime/ts050-source"


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


def verify_inputs():
    context = json.loads((ROOT / ".runtime/workspace-context.json").read_text("utf-8"))
    if context["task"] != "TS-050" or git(ROOT, "branch", "--show-current") != "work/ts-050":
        raise SystemExit("Refusing a workspace other than allocated TS-050")
    workspace = Path(context["workspace"])
    contract = workspace / "contracts/text-dialogue/v1"
    normalized = (contract / "manifest.json").read_bytes().replace(b"\r\n", b"\n")
    if hashlib.sha256(normalized).hexdigest() != MANIFEST:
        raise SystemExit("Published text contract manifest changed")
    for package, expected in (
        ("source-sync/v1", SOURCE_MANIFEST),
        ("profile-memory/v1", PROFILE_MANIFEST),
    ):
        content = (workspace / "contracts" / package / "manifest.json").read_bytes()
        if hashlib.sha256(content.replace(b"\r\n", b"\n")).hexdigest() != expected:
            raise SystemExit(f"Published {package} manifest changed")
    products = {}
    for name, commit in PINS.items():
        source = workspace / "projects" / ("tianshu-" + name)
        head = git(source, "rev-parse", "HEAD")
        if head != commit:
            # Coordinator authorized keeping the original integrated slice after TS-031 merges.
            # Only an ancestor pin is accepted; imported code remains the pinned Git snapshot.
            ancestor = subprocess.run(
                ["git", "-C", str(source), "merge-base", "--is-ancestor", commit, head],
                capture_output=True,
            )
            if ancestor.returncode != 0 or git(source, "branch", "--show-current") != "main":
                raise SystemExit(
                    f"Integrated {name} no longer contains reviewed TS-050 pin: {head}"
                )
        if git(source, "status", "--porcelain", "--untracked-files=all"):
            raise SystemExit(f"Integrated {name} checkout is dirty; review before running")
        products[name] = source
    return workspace, contract, products


def prepare():
    workspace, contract, products = verify_inputs()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    # git archive produces an immutable committed snapshot, no editable/build side effects.
    for name, source in products.items():
        destination = RUNTIME / "sources" / name
        marker = destination / ".ts050-commit"
        if destination.exists():
            if not marker.exists() or marker.read_text("utf-8") != PINS[name]:
                raise SystemExit(f"Unexpected existing snapshot: {destination}")
        archive = RUNTIME / (name + ".tar")
        subprocess.run(
            [
                "git",
                "-C",
                str(source),
                "-c",
                "core.autocrlf=false",
                "archive",
                "--format=tar",
                "-o",
                str(archive),
                PINS[name],
            ],
            check=True,
        )
        destination.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive) as bundle:
            bundle.extractall(destination, filter="data")
        marker.write_text(PINS[name], "utf-8")
        archive.unlink()
    # Lock-compatible runtime union. ruff is a runner tool, not an application dependency.
    pins = {}
    for name in ("companion", "platform"):
        source = RUNTIME / "sources" / name / "requirements-dev.txt"
        for line in source.read_text("utf-8").splitlines():
            if not line or line[0].isspace() or line.startswith("#"):
                continue
            package, version = line.split("==")
            package = package.lower().replace("_", "-")
            if package == "ruff":
                continue
            if package in pins and pins[package] != version:
                raise SystemExit(f"Dependency conflict {package}; use separate environments")
            pins[package] = version
    pins.update({"ruff": "0.15.7", "cryptography": "50.0.1", "cffi": "2.1.1", "pycparser": "3.0"})
    requirements = RUNTIME / "requirements.txt"
    requirements.write_text("".join(f"{p}=={v}\n" for p, v in sorted(pins.items())), "utf-8")
    (RUNTIME / "inputs.json").write_text(
        json.dumps(
            {
                "workspace": str(workspace),
                "contract": str(contract),
                "pins": PINS,
                "manifest_sha256": MANIFEST,
            },
            indent=2,
        )
        + "\n",
        "utf-8",
    )
    print(f"Prepared pinned snapshots and runtime requirements in {RUNTIME}")


def run(pattern="test_ts050_source*.py"):
    _, _, products = verify_inputs()
    verify_snapshots(products)
    from packaging.requirements import Requirement

    for name in PINS:
        manifest = tomllib.loads((RUNTIME / "sources" / name / "pyproject.toml").read_text("utf-8"))
        for declaration in manifest["project"].get("dependencies", []):
            requirement = Requirement(declaration)
            if requirement.marker and not requirement.marker.evaluate():
                continue
            installed = importlib.metadata.version(requirement.name)
            if installed not in requirement.specifier:
                raise SystemExit(
                    f"Runtime dependency mismatch: {name} {declaration}, installed {installed}"
                )
    inputs = json.loads((RUNTIME / "inputs.json").read_text("utf-8"))
    env = dict(
        os.environ,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONIOENCODING="utf-8",
        TS050_RUNTIME=str(RUNTIME),
        TS050_CONTRACTS=inputs["contract"],
    )
    source = RUNTIME / "sources"
    env["PYTHONPATH"] = os.pathsep.join(
        str(path)
        for path in (
            source / "companion/src",
            source / "companion/integrations/nonebot",
            source / "memory/src",
            source / "model-gateway/src",
            source / "platform",
            ROOT / "tests/integration",
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "unittest",
            "discover",
            "-s",
            "tests/integration",
            "-p",
            pattern,
            "-v",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    (RUNTIME / "unittest.txt").write_text(result.stdout + result.stderr, "utf-8")
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    verify_inputs()
    verify_snapshots(products)
    environment = {
        "python": sys.version,
        "source": "PyPI pinned companion/platform runtime union; Memory manifest ranges checked",
        "memory_lock_reproduced": False,
        "distributions": dict(
            sorted((d.metadata["Name"], d.version) for d in importlib.metadata.distributions())
        ),
        "pins": PINS,
        "observed_coordinator_heads": {
            name: git(path, "rev-parse", "HEAD") for name, path in products.items()
        },
        "manifest_sha256": MANIFEST,
        "test_exit_code": result.returncode,
        "slice": "real_source_sync",
        "source_manifest_sha256": SOURCE_MANIFEST,
        "profile_manifest_sha256": PROFILE_MANIFEST,
        "test_pattern": pattern,
        "historical_evidence_commits": [
            "dc357e2cefb3e3df7c427938da38dd2c67d28341",
            "3c4e5920af8b3c5f236c2e8959cfadb8154bda6d",
        ],
        "classification": "partial_not_full_L0",
    }
    (RUNTIME / "environment.json").write_text(json.dumps(environment, indent=2) + "\n", "utf-8")
    return result.returncode


def verify_snapshots(products, *, snapshot_root=None, pins=None):
    """Check the entire snapshot before imports: Git blobs plus one exact commit marker."""
    snapshot_root = RUNTIME / "sources" if snapshot_root is None else Path(snapshot_root)
    pins = PINS if pins is None else pins
    for name, repository in products.items():
        snapshot = snapshot_root / name
        entries = subprocess.check_output(
            ["git", "-C", str(repository), "ls-tree", "-r", "-z", pins[name]]
        )
        tracked = {}
        directories = {"."}
        for entry in entries.split(b"\0"):
            if not entry:
                continue
            metadata, filename = entry.split(b"\t", 1)
            mode, kind, expected = metadata.split()
            if kind != b"blob" or mode == b"120000":
                raise SystemExit(f"Unsupported source tree entry in {name}")
            relative = filename.decode("utf-8")
            tracked[relative] = expected.decode()
            directories.update(parent.as_posix() for parent in Path(relative).parents)
        if snapshot.is_symlink() or snapshot.is_junction() or not snapshot.is_dir():
            raise SystemExit(f"Invalid snapshot directory: {name}")
        allowed_files = set(tracked) | {".ts050-commit"}
        for directory, subdirectories, files in os.walk(snapshot, followlinks=False):
            for child in [*subdirectories, *files]:
                path = Path(directory) / child
                relative = path.relative_to(snapshot).as_posix()
                if path.is_symlink() or path.is_junction():
                    raise SystemExit(f"Snapshot link is not allowed: {name}/{relative}")
                allowed = directories if path.is_dir() else allowed_files
                if relative not in allowed:
                    raise SystemExit(f"Unexpected snapshot entry: {name}/{relative}")
        marker = snapshot / ".ts050-commit"
        if not marker.is_file() or marker.read_text("utf-8") != pins[name]:
            raise SystemExit(f"Snapshot commit marker mismatch: {name}")
        for relative, expected in tracked.items():
            path = snapshot / relative
            if not path.is_file():
                raise SystemExit(f"Snapshot file missing: {name}/{relative}")
            content = path.read_bytes()
            actual = hashlib.sha1(
                b"blob " + str(len(content)).encode() + b"\0" + content
            ).hexdigest()
            if actual != expected:
                raise SystemExit(f"Snapshot changed: {name}/{relative}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--pattern", default="test_ts050_source*.py")
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    else:
        raise SystemExit(run(args.pattern))
