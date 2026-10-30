"""Build four pinned source wheels and one portable, offline Linux image context."""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

from .dependencies import PRODUCTS, STORAGE_WHEEL, lock, run, sha256, wheel_metadata
from .image import base_references, dockerfile
from .frontend import package_frontend

SHA = re.compile(r"[a-f0-9]{40}\Z")
BUILD_CONSTRAINTS = "setuptools==80.9.0\nwheel==0.45.1\n"


def archive(source, destination):
    """Export tracked bytes at the requested commit; the checkout is never modified."""
    if not isinstance(source, dict) or set(source) != {"repo", "commit"}:
        raise ValueError("release_source_invalid")
    commit, repository = source["commit"], Path(source["repo"]).resolve()
    if not isinstance(commit, str) or not SHA.fullmatch(commit):
        raise ValueError("exact_source_commit_required")
    actual = subprocess.check_output(
        ["git", "-C", str(repository), "rev-parse", "--verify", commit + "^{commit}"],
        text=True,
    ).strip()
    if actual != commit:
        raise ValueError("source_commit_mismatch")
    target = Path(destination)
    target.mkdir()
    packed = target.with_suffix(".tar")
    with packed.open("wb") as stream:
        subprocess.run(
            ["git", "-C", str(repository), "archive", "--format=tar", commit],
            stdout=stream,
            check=True,
        )
    with tarfile.open(packed) as content:
        content.extractall(target, filter="data")
    return {"commit": commit, "archive_sha256": sha256(packed)}


def application_files(source, target):
    exported = {}
    for package in ("single_container", "observability"):
        for path in sorted((source / "deploy" / package).rglob("*")):
            if not path.is_file() or path.is_symlink() or "__pycache__" in path.parts:
                continue
            if package == "observability" and path.name in {
                "monitor.py", "ledger_schema.py", "source_retention.py", "retained_query.py"
            }:
                continue
            if path.suffix not in {".py", ".sh", ".json"}:
                continue
            relative = path.relative_to(source)
            destination = target / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, destination)
            exported[relative.as_posix()] = sha256(destination)
    guard = Path("deploy/tianshu/runtime_guard.py")
    (target / guard).parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source / guard, target / guard)
    exported[guard.as_posix()] = sha256(target / guard)
    required = {
        "deploy/single_container/runtime.py",
        "deploy/single_container/process.py",
        "deploy/single_container/health.py",
        "deploy/single_container/host.py",
        "deploy/single_container/applications.py",
        "deploy/single_container/threaded_exchange.py",
        "deploy/single_container/host_settings.py",
        "deploy/single_container/edge.py",
        "deploy/observability/guard.py",
        "deploy/observability/clear_retention.py",
        "deploy/observability/logql_filter.py",
        "deploy/observability/source_capacity.py",
        "deploy/observability/source_segments.py",
        "deploy/observability/vector_checkpoints.py",
        "deploy/observability/vector_source_retention.py",
    }
    if not required <= exported.keys():
        raise ValueError("application_runtime_source_incomplete")
    return exported


def prepare(
    release,
    output,
    *,
    uv="uv",
    python=sys.executable,
    constraints=None,
    frontend=None,
    frontend_manifest=None,
):
    if (frontend is None) != (frontend_manifest is None):
        raise ValueError("frontend_bundle_and_manifest_required_together")
    definition = dockerfile(release, frontend=frontend is not None)
    if set(release.get("sources", {})) != set(PRODUCTS):
        raise ValueError("release_product_source_set_invalid")
    target = Path(output).resolve()
    if target.exists():
        raise ValueError("build_output_already_exists")
    target.mkdir(parents=True)
    (target / "wheels").mkdir()
    provenance, wheels = {}, {}
    frontend_receipt = None
    # Transient full source trees never enter the published image build context.
    with tempfile.TemporaryDirectory(prefix="tianshu-release-") as temporary:
        scratch = Path(temporary)
        build_constraints = scratch / "build-constraints.txt"
        build_constraints.write_text(BUILD_CONSTRAINTS, encoding="utf-8")
        for name, package in PRODUCTS.items():
            exported = scratch / name
            provenance[name] = archive(release["sources"][name], exported)
            if name == "platform" and frontend is not None:
                frontend_receipt = package_frontend(
                    frontend,
                    frontend_manifest,
                    exported,
                    target / "frontend",
                    release["sources"]["platform"]["commit"],
                )
            run(
                [
                    uv,
                    "build",
                    "--wheel",
                    "--python",
                    python,
                    "--no-python-downloads",
                    "--no-config",
                    "--no-sources",
                    "--default-index",
                    "https://pypi.org/simple",
                    "--build-constraints",
                    build_constraints,
                    "--out-dir",
                    target / "wheels",
                    exported,
                ],
                cwd=scratch,
            )
            matches = [
                path
                for path in (target / "wheels").glob("*.whl")
                if wheel_metadata(path)["name"] == package
            ]
            if len(matches) != 1:
                raise ValueError("product_wheel_build_invalid")
            wheels[name] = matches[0]
            storage = exported / "vendor" / STORAGE_WHEEL
            destination = target / "wheels" / STORAGE_WHEEL
            if destination.exists() and sha256(destination) != sha256(storage):
                raise ValueError("product_storage_artifacts_disagree")
            if not destination.exists():
                shutil.copyfile(storage, destination)
        wheels["storage"] = target / "wheels" / STORAGE_WHEEL
        provenance["application"] = archive(
            release.get("application"), scratch / "application"
        )
        files = application_files(scratch / "application", target)
    dependencies = lock(wheels, target, uv=uv, python=python, constraints=constraints)
    receipt = {
        "version": release["version"],
        "status": "prepared_not_built_on_linux",
        "images": release["images"],
        "base_references": base_references(release),
        "sources": provenance,
        "files": files,
        "dependencies_sha256": sha256(target / "dependencies.json"),
        "runtime_lock_sha256": dependencies["lock_sha256"],
        "constraints_sha256": dependencies["constraints_sha256"],
        "dockerfile_sha256": hashlib.sha256(definition.encode()).hexdigest(),
        "build_constraints": BUILD_CONSTRAINTS.splitlines(),
        "uv_version": subprocess.check_output([uv, "--version"], text=True).strip(),
        "inherited_platform_volumes": ["/var/lib/tianshu", "/var/log/tianshu"],
    }
    if frontend_receipt is not None:
        receipt["frontend"] = frontend_receipt
    (target / "Dockerfile").write_text(definition, encoding="utf-8", newline="\n")
    (target / ".dockerignore").write_text(
        "**/__pycache__\n**/*.pyc\n", encoding="utf-8"
    )
    (target / "release.json").write_text(
        json.dumps(receipt, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", default="uv")
    parser.add_argument("--python", default=sys.executable)
    parser.add_argument("--constraints", type=Path)
    parser.add_argument("--frontend", type=Path)
    parser.add_argument("--frontend-manifest", type=Path)
    args = parser.parse_args()
    result = prepare(
        json.loads(args.release.read_bytes()),
        args.output,
        uv=args.uv,
        python=args.python,
        constraints=args.constraints,
        frontend=args.frontend,
        frontend_manifest=args.frontend_manifest,
    )
    print(json.dumps({"status": result["status"], "version": result["version"]}))


if __name__ == "__main__":
    main()
