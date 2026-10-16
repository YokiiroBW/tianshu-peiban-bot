"""Isolated-Python entry point shipped as run_node.py in the source package."""

from __future__ import annotations

import importlib.metadata
import json
import sys
from pathlib import Path


def main(argv=None):
    root = Path(__file__).resolve().parent
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        manifest = json.loads((root / "package-manifest.json").read_bytes())
        runtime = manifest["runtime"]
        if args == ["--version"]:
            print(json.dumps({
                "package": manifest["package"], "source_commit": manifest["source_commit"],
                "protocol_version": manifest["protocol_version"], "dependencies": runtime["dependencies"],
            }, sort_keys=True))
            return 0
        if sys.version_info[:2] < tuple(runtime["python_minimum"]):
            print("infrastructure_python_unsupported: Python 3.11+ is required", file=sys.stderr)
            return 2
        for dependency in runtime["dependencies"]:
            try:
                actual = importlib.metadata.version(dependency["name"])
            except importlib.metadata.PackageNotFoundError:
                actual = None
            if actual != dependency["version"]:
                print(
                    "infrastructure_dependency_unavailable: " + dependency["name"] + "==" + dependency["version"]
                    + "; install this package's requirements.lock in the selected Python environment",
                    file=sys.stderr,
                )
                return 2
        # -I excludes the script directory and PYTHONPATH. Add only this package's
        # own code root; author checkout paths never enter the process command.
        sys.path.insert(0, str(root))
        from ops.infrastructure.__main__ import main as execute
        return execute(args)
    except (OSError, ValueError, KeyError, TypeError, ImportError):
        print("infrastructure_package_unavailable: verify and unpack the source package", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
