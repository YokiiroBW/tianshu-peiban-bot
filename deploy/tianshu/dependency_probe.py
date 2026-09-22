"""Check the image's actual installed interpreter, including pip-free application venvs."""

import importlib
import importlib.metadata
import json
import subprocess
import sys

MODULES = {
    "platform": "services.platform",
    "companion": "tianshu_companion.runtime_cli",
    "memory": "tianshu_memory.cli",
    "gateway": "tianshu_gateway",
}

try:
    importlib.import_module(MODULES[sys.argv[1]])
    # The official Python base has pip; --python checks the *application* interpreter's
    # distribution set without adding pip or any dependency to its runtime environment.
    result = subprocess.run(
        ["/usr/local/bin/python", "-m", "pip", "--python", sys.executable, "check"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    )
    assert result.returncode == 0
    distributions = sorted(
        (d.metadata["Name"], d.version) for d in importlib.metadata.distributions()
    )
    assert distributions
    print(json.dumps({"interpreter": sys.executable, "distributions": distributions}))
except Exception:
    print("installed_dependency_probe_failed")
    raise SystemExit(1)
