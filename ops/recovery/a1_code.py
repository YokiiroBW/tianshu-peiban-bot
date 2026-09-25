"""Load only pinned deployment modules from the A1 scope's tooling tree."""

import importlib
import sys
from pathlib import Path

from .safety import require


def require_entry_origin(code_root, module_file, relative):
    expected = Path(code_root) / relative
    require(Path(module_file).resolve(strict=True) == expected.resolve(strict=True) and
            expected.is_file() and not expected.is_symlink(),
            "a1_entry_code_root_mismatch")
    root = Path(code_root).resolve(strict=True)
    for name, module in tuple(sys.modules.items()):
        if name == "ops" or name == "ops.recovery" or name.startswith("ops.recovery."):
            origin = getattr(module, "__file__", None)
            if origin is not None:
                require(Path(origin).resolve(strict=True).is_relative_to(root),
                        "a1_loaded_code_root_mismatch")


def deploy_module(code_root, name):
    root = Path(code_root).resolve(strict=True) / "deploy/tianshu"
    expected = root / (name + ".py")
    require(expected.is_file() and not expected.is_symlink(),
            "a1_deploy_module_missing")
    sys.path.insert(0, str(root))
    module = importlib.import_module(name)
    require(Path(module.__file__).resolve(strict=True) == expected,
            "a1_deploy_module_unpinned")
    # Deployment modules use top-level imports. Reject an already cached peer
    # from a different checkout instead of silently mixing code trees.
    for path in root.glob("*.py"):
        cached = sys.modules.get(path.stem)
        if cached is not None:
            origin = getattr(cached, "__file__", None)
            require(origin is not None and Path(origin).resolve(strict=True) == path,
                    "a1_deploy_module_cache_mismatch")
    return module
