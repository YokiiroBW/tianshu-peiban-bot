"""Installed NAS entry point; optional explicit Python belongs to deployment configuration."""

import os
from pathlib import Path
import sys


def main():
    arguments = sys.argv[1:]
    if "--python" in arguments:
        index = arguments.index("--python")
        if index + 1 >= len(arguments) or not Path(arguments[index + 1]).is_absolute():
            raise SystemExit("absolute --python executable required")
        executable = arguments[index + 1]
        del arguments[index : index + 2]
        # Remove the option before exec: one explicit environment selection, no fallback/loop.
        os.execv(executable, [executable, "-I", "-B", str(Path(__file__).resolve()), *arguments])
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from tianshu_infrastructure.control.remote import main as remote_main

    return remote_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())
