"""Real disposable writer process with a test-only stop inbox, never product code."""

import argparse
import os
import sqlite3
import sys
import time
from contextlib import ExitStack
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from ops.recovery.process_identity import birth_of
from ops.recovery.safety import (
    canonical,
    child,
    digest,
    lease,
    read_json,
    require,
    safe_path,
    write_new,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", required=True)
    parser.add_argument("--service", required=True)
    parser.add_argument("--ignore-stop", action="store_true")
    parser.add_argument("--exit-delay", type=float, default=0)
    args = parser.parse_args()
    root = safe_path(args.directory)
    boundary = Path(__file__).resolve().parent / ".runtime"
    require(root.is_relative_to(boundary), "test_fixture_directory_required")
    home = child(root, ".lifecycle")
    binding = read_json(child(home, "binding.json"))
    service = args.service
    require(
        binding["backend"] == "local-process" and service in binding["services"],
        "fixture_binding_required",
    )
    owner = {
        "service": service,
        "pid": os.getpid(),
        "birth": birth_of(os.getpid()),
        "binding_sha256": digest(canonical(binding)),
        "runtime": "synthetic-fixture/1",
    }
    with ExitStack() as stack:
        with lease(child(home, "action.lock")):
            require(not (home / "MAINTENANCE.json").exists(), "maintenance_active")
            stack.enter_context(lease(child(home, "owners/" + service + ".lock")))
            write_new(home / "owners" / (service + ".json"), canonical(owner))
        obs = service.startswith("obs-")
        database = root / (
            "observability/data/" + service[4:] + "/fixture.sqlite"
            if obs
            else "data/" + service + "/main.db"
        )
        db = sqlite3.connect(database)
        db.execute("PRAGMA journal_mode=WAL")
        if obs:
            db.execute("CREATE TABLE sequence(value INTEGER)")
            db.execute("INSERT INTO sequence VALUES(0)")
            db.commit()
        logpath = root / (
            "observability/data/" + service[4:] + "/events.log"
            if obs
            else "logs/" + service + "/events.log"
        )
        with logpath.open("ab") as log:
            count = 0
            while True:
                request = home / "owners" / (service + ".stop")
                if request.exists() and not args.ignore_stop:
                    require(read_json(request) == owner, "stop_identity_mismatch")
                    break
                count += 1
                if obs:
                    db.execute("UPDATE sequence SET value=?", (count,))
                else:
                    db.execute(
                        "UPDATE facts SET value=? WHERE id='sequence'", (str(count),)
                    )
                db.commit()
                log.write(canonical({"synthetic_sequence": count}) + b"\n")
                log.flush()
                time.sleep(0.025)
            log.write(b'{"synthetic_shutdown":"complete"}\n')
            log.flush()
            os.fsync(log.fileno())
        db.close()
        write_new(
            home / "owners" / (service + ".exited"),
            canonical({"owner": owner, "graceful": True}),
        )
        # Adversarial test: a receipt exists but the process and its lease are still alive.
        time.sleep(args.exit_delay)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
