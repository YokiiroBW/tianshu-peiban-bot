"""Optional deployment diagnostics; never initialize/migrate an application's store."""

import argparse
import json
from pathlib import Path

from .errors import StorageError
from .options import StorageOptions
from .runtime import StorageRuntime


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="existing application JSON configuration")
    parser.add_argument("--service", default="connection-test")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("test", help="test configured PostgreSQL/Redis, without activation")
    status = commands.add_parser("status", help="read current backend binding without migration")
    status.add_argument("--state-directory", required=True)
    args = parser.parse_args(argv)
    try:
        document = json.loads(Path(args.config).read_bytes()) if args.config else {}
        options = StorageOptions.from_settings(document)
        if args.command == "test":
            result = StorageRuntime.test_connections(options, service=args.service)
            success = all(state in {"ok", "not_configured"} for state in result.values())
        else:
            result = StorageRuntime.inspect(
                options, service=args.service, state_directory=args.state_directory
            )
            success = result["postgresql"] in {"ok", "not_configured"}
        print(json.dumps(result, sort_keys=True))
        return 0 if success else 1
    except (StorageError, OSError, ValueError, TypeError):
        print(json.dumps({"code": "storage_configuration_unavailable"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
