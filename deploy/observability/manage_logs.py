"""Operator command for this deployment's runtime logs; never business databases."""

import argparse
import json
from pathlib import Path

from query import LokiClient


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["clear"])
    parser.add_argument("--settings", type=Path, required=True)
    parser.add_argument("--guard-url", default="https://obs-guard:8445")
    args = parser.parse_args(argv)
    settings = json.loads(args.settings.read_bytes())
    client = LokiClient(
        args.guard_url,
        settings["ca"],
        token=Path(settings["tokens"]["writer"]).read_text().strip(),
    )
    try:
        status, body, _ = client.request("/retention/clear", "POST", body=b"")
        if status != 202:
            print(json.dumps({"status": "clear_rejected", "http_status": status}))
            return 1
        response = json.loads(body)
        print(
            json.dumps(
                {
                    "status": response["status"],
                    "before": response["before"],
                    "known_events": response["known_events"],
                    "known_pending_events": response["known_pending_events"],
                    "old_pending_destroyed_by_operator": True,
                    "physical_reclamation": "asynchronous",
                }
            )
        )
        return 0
    except Exception:  # noqa: BLE001 - command boundary must not dump credentials/configuration
        print(json.dumps({"status": "clear_unavailable"}))
        return 1
    finally:
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
