"""Schedule the existing durable-write worker across two physical positions.

This host-side coordinator is never a timed product implementation. The caller
supplies an explicitly owned test stage, frozen images and a lifecycle receipt.
All commits, durability checks and reopen proofs remain in sqlite_write_control.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess

CASES = ("local_commit", "local_full_flow", "source_commit", "source_full_flow")
VARIANTS = ("original", "optimized")
ROLES = ("a", "b")


def assignments(block, leg):
    original = ROLES[(block + leg) % 2]
    return {"original": original, "optimized": ROLES[1 - ROLES.index(original)]}


def leg_schedule(block, leg, *, smoke=False):
    """One real warmup and ten measured pairs per case and placement."""
    for index in range(-1, 1 if smoke else 10):
        forward = (block + leg + index) % 2 == 0
        for case in CASES if forward else reversed(CASES):
            yield {
                "block": block,
                "assignment": leg,
                "case": case,
                "index": -2 + leg if index < 0 else leg * 10 + index,
                "phase": "warmup" if index < 0 else "measured",
                "order": list(VARIANTS if forward else reversed(VARIANTS)),
            }


def read_event(process, timeout=180):
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        if not selector.select(timeout):
            raise TimeoutError("owned write worker response deadline")
        line = process.stdout.readline()
    if not line:
        raise RuntimeError("owned write worker ended before its result")
    return json.loads(line)


def run(*, stage, owner_token, docker, mode, block, owners, images):
    root = Path(stage)
    assert root.resolve().parent == Path("/volume2")
    assert root.name == "tianshu-business-flow-performance-20261022-" + owner_token
    assert (root / "owner-token").read_text() == owner_token
    assert mode in ("seed", "block", "smoke")
    assert (mode != "block" or block in (1, 2, 3)) and (mode != "smoke" or block == 901)
    assert set(images) == set(VARIANTS)
    control = root / "write-control"
    control.mkdir(mode=0o700, exist_ok=True)
    os.chown(control, 10001, 10001)
    prefix = "ts279-" + owner_token[:8] + "-write-"

    def continuity():
        current = subprocess.check_output(
            [
                docker,
                "inspect",
                "--format",
                "{{.Id}} {{.RestartCount}} {{.State.StartedAt}} {{.State.Running}}",
                *[row.split()[0] for row in owners],
            ],
            text=True,
        ).splitlines()
        assert current == owners, "production lifecycle changed; stop benchmark"

    def owned(path):
        path = Path(path)
        assert path.is_absolute() and path.resolve() == path
        assert path.is_relative_to(control) and path != control
        return path

    def mkdir(path):
        path = owned(path)
        path.mkdir(parents=True, mode=0o700, exist_ok=False)
        os.chown(path, 10001, 10001)
        return path

    def command(name, variant, active=None):
        cmd = [
            docker,
            "run",
            "--rm",
            "-i",
            "--name",
            name,
            "--no-healthcheck",
            "--read-only",
            "--network",
            "none",
            "--cpuset-cpus",
            "0-7",
            "--cpu-shares",
            "128",
            "--memory",
            "512m",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges:true",
            "--user",
            "10001:10001",
            "--label",
            "tianshu.ts279.owner=" + owner_token,
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--env",
            "TMPDIR=/bench/tmp",
            "--env",
            "PYTHONHASHSEED=0",
            "--mount",
            "type=bind,src=" + str(root) + ",dst=/bench",
        ]
        if active is not None:
            cmd += [
                "--mount",
                "type=bind,src=" + str(owned(active)) + ",dst=/bench/write-active",
            ]
        return cmd + ["--entrypoint", "python", images[variant]["image_id"], "-B"]

    def cli(variant, trial):
        return [
            "--config",
            "/bench/private.json",
            "--backend",
            "sqlite",
            "--variant",
            variant,
            "--trial",
            str(trial),
        ]

    class Worker:
        def __init__(self, name, cmd):
            self.name = name
            self.log = (control / (name + ".stderr.log")).open("x")
            self.p = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=self.log,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
            try:
                self.ready = read_event(self.p)
                assert self.ready["event"] == "ready"
            except BaseException:
                self.remove()
                self.log.close()
                raise

        def remove(self):
            subprocess.run(
                [docker, "rm", "-f", self.name], capture_output=True, timeout=20
            )
            try:
                self.p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait(timeout=10)

        def call(self, message):
            self.p.stdin.write(json.dumps(message) + "\n")
            self.p.stdin.flush()
            return read_event(self.p)

        def close(self):
            try:
                reply = self.call({"case": "stop"})
                assert reply["event"] == "closed"
                self.p.stdin.close()
                assert self.p.wait(timeout=30) == 0
                return reply
            except BaseException:
                self.remove()
                raise
            finally:
                self.log.close()

    def activate(source, active):
        source, active = owned(source), owned(active)
        assert source.is_dir() and active.is_dir()
        identity = (active.stat().st_dev, active.stat().st_ino)
        for path in active.iterdir():
            assert path.resolve() == path and path.is_relative_to(active)
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        for path in source.iterdir():
            assert path.is_file() and path.resolve() == path
            target = active / path.name
            with path.open("rb") as reader, target.open("xb") as writer:
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
            os.chown(target, 10001, 10001)
            assert (
                hashlib.sha256(path.read_bytes()).digest()
                == hashlib.sha256(target.read_bytes()).digest()
            )
        fd = os.open(active, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        assert (active.stat().st_dev, active.stat().st_ino) == identity

    continuity()
    if mode == "seed":
        templates = control / "templates"
        templates.mkdir(mode=0o700, exist_ok=True)
        os.chown(templates, 10001, 10001)
        records = []
        for case in CASES:
            active = mkdir(templates / case)
            name = prefix + "seed-" + case
            cmd = command(name, "original", active) + [
                "/bench/code/sqlite_write_control.py",
                *cli("original", 190),
                "--mode",
                "seed",
                "--case",
                case,
            ]
            try:
                response = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=600
                )
            except subprocess.TimeoutExpired:
                subprocess.run(
                    [docker, "rm", "-f", name], capture_output=True, timeout=20
                )
                raise
            (control / (case + "-seed.private.log")).write_text(response.stderr)
            assert response.returncode == 0, "seed failed; owned diagnostics retained"
            record = json.loads(response.stdout)
            assert record["event"] == "seeded" and record["native_reopen_verified"]
            assert record["seed_groups"] == 300
            records.append(record)
            continuity()
        return {
            "mode": mode,
            "records": records,
            "production_lifecycle_unchanged": True,
        }

    folder = mkdir(control / ("block-" + str(block)))
    active_paths = {role: mkdir(folder / ("active-" + role)) for role in ROLES}
    identities = {
        role: (path.stat().st_dev, path.stat().st_ino)
        for role, path in active_paths.items()
    }
    results, proofs, placements = [], [], []
    helper = None
    helper_script = """import sys,json
sys.path.insert(0,'/bench/code')
import sqlite_write_control as api
from common import arguments
args,private,_=arguments()
assert args.backend=='sqlite'
print(json.dumps({'event':'ready'}),flush=True)
for line in sys.stdin:
    message=json.loads(line)
    if message=={'case':'stop'}:
        print(json.dumps({'event':'closed'}),flush=True);break
    assert set(message)=={'case','source','destination'}
    api.prepare(message['case'],api.Path(message['source']),api.Path(message['destination']))
"""
    try:
        name = prefix + "prepare-" + str(block)
        helper = Worker(
            name,
            command(name, "original")
            + ["-c", helper_script, *cli("original", 190 + block)],
        )
        for leg in (0, 1):
            workers, placement = {}, {}
            closed = {}
            try:
                for variant, role in assignments(block, leg).items():
                    active = active_paths[role]
                    name = prefix + str(block) + "-" + str(leg) + "-" + role
                    worker = Worker(
                        name,
                        command(name, variant, active)
                        + [
                            "/bench/code/sqlite_write_control.py",
                            *cli(variant, 190 + block),
                            "--mode",
                            "run",
                            "--stream",
                        ],
                    )
                    workers[variant] = worker
                    assert worker.ready["store_closed_between_commands"]
                    assert (
                        worker.ready["verified_revision"] == images[variant]["revision"]
                    )
                    actual = json.loads(
                        subprocess.check_output([docker, "inspect", name], text=True)
                    )[0]
                    mounts = {
                        mount["Destination"]: mount["Source"]
                        for mount in actual["Mounts"]
                    }
                    assert actual["Image"] == images[variant]["image_id"]
                    assert mounts["/bench/write-active"] == str(active) and mounts[
                        "/bench"
                    ] == str(root)
                    placement[variant] = {
                        "role": role,
                        "image_id": actual["Image"],
                        "container_id": actual["Id"],
                        "container_name": name,
                        "physical_active_path": str(active),
                        "container_active_path": "/bench/write-active",
                        "directory_device": identities[role][0],
                        "directory_inode": identities[role][1],
                    }
                placements.append(
                    {
                        "assignment": leg,
                        "placement": placement,
                        "ready": {v: w.ready for v, w in workers.items()},
                    }
                )
                for row in leg_schedule(block, leg, smoke=mode == "smoke"):
                    case, index = row["case"], row["index"]
                    label = (
                        "w" + str(index + 3)
                        if index < 0
                        else "p" + str(index + 1).zfill(3)
                    )
                    pair = control / "pairs" / ("b" + str(block)) / case / label
                    copied = helper.call(
                        {
                            "case": case,
                            "source": "/bench/write-control/templates/" + case,
                            "destination": "/bench/"
                            + pair.relative_to(root).as_posix(),
                        }
                    )
                    assert (
                        copied["event"] == "prepared"
                        and copied["byte_identical"]
                        and copied["copies_fsynced"]
                    )
                    proofs.append(copied)
                    row = dict(row, samples={}, placement=placement)
                    for variant in row["order"]:
                        role = placement[variant]["role"]
                        activate(pair / variant, active_paths[role])
                        sample = workers[variant].call({"case": case})
                        assert (
                            sample["event"] == "sample" and sample["variant"] == variant
                        )
                        assert (
                            sample["verified_revision"] == images[variant]["revision"]
                        )
                        assert sample["proof"]["native_reopen_verified"]
                        assert sample["initial_hashes"] == copied["initial_hashes"]
                        row["samples"][variant] = sample
                        # Preserve even a one-sided pair if its peer later fails.
                        (folder / "partial-results.json").write_text(
                            json.dumps(
                                {
                                    "results": results,
                                    "pending_pair": row,
                                    "placements": placements,
                                }
                            )
                        )
                    old, new = (row["samples"][variant] for variant in VARIANTS)
                    assert (
                        old["proof"]["logical_result_signature"]
                        == new["proof"]["logical_result_signature"]
                    )
                    assert (
                        old["before_counts"] == new["before_counts"]
                        and old["after_counts"] == new["after_counts"]
                    )
                    assert old["durability"] == new["durability"]
                    assert (
                        old["durability"]["journal_mode"] == "wal"
                        and old["durability"]["synchronous"] == 2
                    )
                    results.append(row)
                    (folder / "partial-results.json").write_text(
                        json.dumps(
                            {
                                "results": results,
                                "pending_pair": None,
                                "placements": placements,
                            }
                        )
                    )
                    continuity()
            finally:
                errors = []
                for variant, worker in workers.items():
                    try:
                        closed[variant] = worker.close()
                    except BaseException as error:
                        errors.append(type(error).__name__)
                (folder / ("closed-" + str(leg) + ".json")).write_text(
                    json.dumps(
                        {
                            "assignment": leg,
                            "closed": closed,
                            "errors": errors,
                        }
                    )
                )
                if errors:
                    raise RuntimeError(
                        "owned worker close failed; diagnostics retained"
                    )
            assert all(
                (path.stat().st_dev, path.stat().st_ino) == identities[role]
                for role, path in active_paths.items()
            )
            continuity()
    finally:
        if helper is not None:
            helper.close()
    continuity()
    return {
        "mode": mode,
        "block": block,
        "results": results,
        "prepare_proofs": proofs,
        "worker_assignments": placements,
        "production_lifecycle_unchanged": True,
        "contracts_lifecycle": "one fresh worker per image per assignment; one warmup pair per case per assignment",
        "physical_position_crossover": True,
        "one_active_operation_at_a_time": True,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--control", type=Path, required=True)
    parser.add_argument("--mode", choices=("seed", "smoke", "block"), required=True)
    parser.add_argument("--block", type=int, default=1)
    args = parser.parse_args()
    config = json.loads(args.control.read_bytes())
    print(
        json.dumps(
            run(
                **config,
                mode=args.mode,
                block=901 if args.mode == "smoke" else args.block,
            )
        )
    )


if __name__ == "__main__":
    main()
