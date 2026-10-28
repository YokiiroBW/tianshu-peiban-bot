"""Absolute-file launcher for the installed fixed remote package."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ops.infrastructure.control.remote import main


if __name__ == "__main__":
    raise SystemExit(main())
