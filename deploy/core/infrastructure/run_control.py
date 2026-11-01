"""Isolated entry point for the persistent registered-node control relay."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

from tianshu_infrastructure.control.__main__ import main


if __name__ == "__main__":
    raise SystemExit(main())
