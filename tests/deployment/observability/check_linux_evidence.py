"""Verify saved artifacts without promoting a plan/partial run to a Linux pass."""

import argparse
import json
from pathlib import Path

import helpers  # noqa: F401
from acceptance import verify_evidence


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify_evidence(args.directory)))
