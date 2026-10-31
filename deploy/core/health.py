"""One bounded real readiness request for Docker, without secrets or repeated probes."""

import argparse
import json
from pathlib import Path
from urllib.request import build_opener, ProxyHandler, Request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text())
    port = config.get("http", {}).get("port", 18446)
    if port is None:
        raise ValueError("loopback_health_http_port_required")
    opener = build_opener(ProxyHandler({}))
    with opener.open(Request(f"http://127.0.0.1:{port}/health/ready"), timeout=4) as response:
        result = json.loads(response.read(1024 * 1024))
        if response.status != 200 or result.get("status") != "ready":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
