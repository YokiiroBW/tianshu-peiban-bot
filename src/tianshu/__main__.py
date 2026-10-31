"""Single installed product entrypoint; production supervision owns the three roles."""

import argparse
import asyncio
import logging

from .runtime.config import Settings
from .runtime.process import run


def main(argv=None):
    parser = argparse.ArgumentParser(prog="tianshu")
    parser.add_argument("role", choices=("interactive", "model-broker", "worker", "migrate"))
    parser.add_argument("--config", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    settings = Settings.load(args.config)
    try:
        asyncio.run(run(settings, "interactive" if args.role == "migrate" else args.role,
                        initialize=args.role == "migrate"))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
