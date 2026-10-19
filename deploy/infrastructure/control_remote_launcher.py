"""Use the existing privileged Python command to enter the separate pinned runtime."""

import argparse
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if not args.python.is_absolute() or not args.config.is_absolute():
        parser.error("absolute registered runtime and configuration are required")
    entry = Path(__file__).resolve().with_name("run_control.py")
    os.execv(str(args.python), [str(args.python), "-I", "-B", str(entry), "--remote", "--config", str(args.config)])


if __name__ == "__main__":
    main()
