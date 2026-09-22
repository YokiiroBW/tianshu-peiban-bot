"""Launch an unchanged product CLI with a passive JSON-parse observation.

The observer records only a hash when the actual process successfully parses the exact
configuration bytes. Product readiness must separately confirm valid loaded configuration.
It does not replace the parser, issuer, authenticator, factory, service, or business ports.
"""

import hashlib
import json
import os
from pathlib import Path
import runpy
import sys


def main():
    role, settings, evidence, module, *arguments = sys.argv[1:]
    path = Path(settings)
    expected = path.read_bytes()
    expected_text = path.read_text(encoding="utf-8")
    sha = hashlib.sha256(expected).hexdigest()
    original = json.loads
    seen = False

    def observed(value, *args, **kwargs):
        nonlocal seen
        result = original(value, *args, **kwargs)
        matches = (
            value == expected_text if isinstance(value, str) else value == expected
        )
        if not seen and matches:
            seen = True
            Path(evidence).write_text(
                json.dumps(
                    {
                        "role": role,
                        "pid": os.getpid(),
                        "parent_pid": os.getppid(),
                        "config_sha256": sha,
                        "basis": "successful_product_json_parse",
                        "module": module,
                    }
                ),
                encoding="utf-8",
            )
        return result

    json.loads = observed
    sys.argv = [module, *arguments]
    runpy.run_module(module, run_name="__main__")


if __name__ == "__main__":
    main()
