"""Build or verify the fixed-source infrastructure executor package."""

import argparse
import json
from pathlib import Path

from .bundle import Refused, build, verify


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="action", required=True)
    create = actions.add_parser("build")
    create.add_argument("--repo", type=Path, required=True)
    create.add_argument("--commit", required=True)
    create.add_argument("--output", type=Path, required=True)
    inspect = actions.add_parser("verify")
    inspect.add_argument("package", type=Path)
    args = parser.parse_args(argv)
    try:
        result = build(args.repo, args.commit, args.output) if args.action == "build" else verify(args.package)
    except Refused as error:
        print(json.dumps({"status": "refused", "reason": str(error)}, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
