"""Single product entrypoint with independently supervised runtime roles."""

import argparse
import asyncio

from .runtime.config import Settings
from .runtime.process import run
from .runtime.logging import configure
from .runtime.execution import PROCESS_ROLES


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tianshu")
    parser.add_argument(
        "role", choices=("serve", *PROCESS_ROLES, "migrate")
    )
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    configure()
    settings = Settings.load(args.config)
    try:
        if args.role == "serve":
            from .runtime.supervisor import Supervisor

            asyncio.run(Supervisor(settings, args.config).run())
        else:
            asyncio.run(
                run(
                    settings,
                    "interactive" if args.role == "migrate" else args.role,
                    initialize=args.role == "migrate",
                )
            )
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
