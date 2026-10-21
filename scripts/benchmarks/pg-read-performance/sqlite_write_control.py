"""Seed, clone, commit and reopen independent SQLite write pairs.

The private driver owns block/order/warmup scheduling and container mounts.
Templates are immutable; every sample (including warmup) needs a fresh clone.
No transaction rollback, idempotent replay, or durability adjustment is used.
"""

import argparse
import gc
import json
import os
import shutil
import sys
import time
from pathlib import Path

from common import arguments, durability, memory_features, settings
from memory_write_fixture import (
    CASES,
    contract_files,
    open_fixture,
    require_sqlite_settings,
    seed_fixture,
)
from sqlite_same_fixture import fixture_hashes, sha256
from tianshu_storage.files import sync_directory

STAGE = Path("/bench")
ACTIVE = STAGE / "write-active"
CONTROL = STAGE / "write-control"
STATE = "fixture-state.json"
SEAL = "snapshot.json"


class ValidationProfile:
    """Observe the timed write only; never retain input or validation results."""

    def __init__(self, contracts):
        self.contracts = contracts
        self.original = contracts.validate
        self.had_override = "validate" in vars(contracts)
        self.override = vars(contracts).get("validate")
        self.active = False
        self.depth = 0
        self.calls = 0
        self.cpu_ns = 0
        self.by_contract = {}
        self.gc_started = {}
        self.gc_generations = {
            str(generation): {"collections": 0, "cpu_ns": 0} for generation in range(3)
        }

    def __enter__(self):
        self.contracts.validate = self.validate
        gc.callbacks.append(self.collection)
        return self

    def __exit__(self, *exception):
        self.active = False
        try:
            if self.collection in gc.callbacks:
                gc.callbacks.remove(self.collection)
        finally:
            if self.had_override:
                self.contracts.validate = self.override
            else:
                del self.contracts.validate

    def validate(self, name, document):
        if not self.active:
            return self.original(name, document)
        started = time.process_time_ns()
        self.depth += 1
        try:
            return self.original(name, document)
        finally:
            elapsed = time.process_time_ns() - started
            self.depth -= 1
            self.calls += 1
            entry = self.by_contract.get(name)
            if entry is None:
                entry = self.by_contract[name] = {"calls": 0, "inclusive_cpu_ns": 0}
            entry["calls"] += 1
            entry["inclusive_cpu_ns"] += elapsed
            # Inclusive per-contract timings can nest. Count only outer calls
            # in the total, so nested validation is not added twice.
            if self.depth == 0:
                self.cpu_ns += elapsed

    def collection(self, phase, info):
        if not self.active:
            return
        generation = str(info["generation"])
        if phase == "start":
            self.gc_started[generation] = time.process_time_ns()
        elif phase == "stop":
            started = self.gc_started.pop(generation, None)
            if started is not None:
                entry = self.gc_generations[generation]
                entry["collections"] += 1
                entry["cpu_ns"] += time.process_time_ns() - started

    def result(self):
        return {
            "diagnostic_only": True,
            "clock": "process_time_ns",
            "validate_calls": self.calls,
            "validate_cpu_ns": self.cpu_ns,
            "by_contract": self.by_contract,
            "gc": {
                "collections": sum(
                    x["collections"] for x in self.gc_generations.values()
                ),
                "cpu_ns": sum(x["cpu_ns"] for x in self.gc_generations.values()),
                "by_generation": self.gc_generations,
                "unfinished_collections": len(self.gc_started),
            },
        }


def reply(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def write_json(path, value):
    payload = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    with path.open("xb") as output:
        output.write(payload)
        output.flush()
        os.fsync(output.fileno())
    sync_directory(path.parent)


def owned(path, root):
    path = Path(path)
    assert path.is_absolute() and path.resolve() == path
    assert path.is_relative_to(root) and path != root
    return path


def locations(case):
    name = "sync.sqlite" if case.startswith("source_") else "memory.sqlite"
    database = ACTIVE / name
    return database, Path(str(database) + ".source-guard.json")


def configuration(case, private):
    database, _ = locations(case)
    config = dict(
        database_path=str(database), **settings(private, "writecontrol", "sqlite")
    )
    require_sqlite_settings(config)
    return config


def load_seal(directory, case, *, initial):
    assert case in CASES
    assert directory.resolve() == directory
    seal = json.loads((directory / SEAL).read_bytes())
    assert seal["schema"] == "tianshu-sqlite-write-template/1" and seal["case"] == case
    database, guard = locations(case)
    assert set(seal["files"]) == {database.name, guard.name, STATE}
    if initial:
        for name, expected in seal["files"].items():
            path = directory / name
            assert path.resolve() == path and path.is_file()
            assert sha256(path) == expected, (
                "fresh clone does not match sealed initial bytes"
            )
        assert (
            fixture_hashes(directory / database.name, directory / guard.name)
            == seal["initial_hashes"]
        )
    return seal


def seed(case, private, contracts, *, size=300):
    assert case in CASES and ACTIVE.resolve() == ACTIVE
    ACTIVE.mkdir(parents=True, exist_ok=True)
    assert not any(ACTIVE.iterdir()), "seed requires a new empty dedicated mount"
    config = configuration(case, private)
    fixture = seed_fixture(
        ACTIVE,
        contracts,
        "source" if case.startswith("source_") else "local",
        size=size,
    )
    try:
        seed_counts = fixture.counts()
        packet = fixture.prepare([10000]) if case.endswith("_commit") else None
        before = fixture.counts()
        features = memory_features(fixture.store)
        with fixture.store.transaction() as db:
            sync = durability(db, "sqlite")
        state = fixture.state()
    finally:
        client = getattr(fixture.h, "client", None)
        if client:
            client.close()
        fixture.store.close()
    database, guard = locations(case)
    initial = fixture_hashes(database, guard)
    # Native reopen must verify the guard and leave both authority files unchanged.
    fixture = open_fixture(state, contracts, config)
    try:
        assert fixture_hashes(database, guard) == initial, (
            "constructor changed seed bytes"
        )
        assert fixture.counts() == before
        assert memory_features(fixture.store) == features
    finally:
        fixture.store.close()
    assert fixture_hashes(database, guard) == initial
    # This workload uses SQL-only synthetic Memory groups. Refuse silently
    # dropping any future external authority/file content from a clone.
    for suffix in (".files", ".storage"):
        path = Path(str(database) + suffix)
        assert not path.exists() or not any(path.iterdir()), (
            "template has additional storage files"
        )
    write_json(ACTIVE / STATE, {"fixture": state, "packet": packet, "index": 10000})
    seal = {
        "schema": "tianshu-sqlite-write-template/1",
        "case": case,
        "seed_groups": size,
        "seed_source_count": seed_counts["sources"],
        "source_count": before["sources"],
        "initial_counts": before,
        "initial_hashes": initial,
        "active_memory_features": features,
        "durability": sync,
        "namespace": "writecontrol",
        "pool_max_size": 4,
        "files": {
            name: sha256(ACTIVE / name) for name in (database.name, guard.name, STATE)
        },
    }
    write_json(ACTIVE / SEAL, seal)
    reply("seeded", **seal, native_reopen_verified=True)


def prepare(case, source, destination):
    source = owned(source, CONTROL / "templates")
    destination = owned(destination, CONTROL / "pairs")
    assert source.name == case
    seal = load_seal(source, case, initial=True)
    assert not destination.exists(), "pair destination must be new"
    destination.mkdir(parents=True)
    names = [*seal["files"], SEAL]
    for variant in ("original", "optimized"):
        directory = destination / variant
        directory.mkdir()
        for name in names:
            with (
                (source / name).open("rb") as reader,
                (directory / name).open("xb") as writer,
            ):
                shutil.copyfileobj(reader, writer)
                writer.flush()
                os.fsync(writer.fileno())
        sync_directory(directory)
        assert load_seal(directory, case, initial=True) == seal
    sync_directory(destination)
    sync_directory(destination.parent)
    reply(
        "prepared",
        case=case,
        directory=str(destination),
        variants=["original", "optimized"],
        initial_hashes=seal["initial_hashes"],
        seed_groups=seal["seed_groups"],
        seed_source_count=seal["seed_source_count"],
        source_count=seal["source_count"],
        copied_bytes_per_variant=sum((source / name).stat().st_size for name in names),
        file_hashes={name: sha256(source / name) for name in names},
        byte_identical=True,
        copies_fsynced=True,
    )


def reopen_proof(
    case, private, contracts, state, packet, result, before, expected_hashes
):
    database, guard = locations(case)
    assert fixture_hashes(database, guard) == expected_hashes
    fixture = open_fixture(state, contracts, configuration(case, private))
    try:
        assert fixture_hashes(database, guard) == expected_hashes, (
            "constructor changed committed bytes"
        )
        persisted = fixture.assert_committed(packet, result, before)
        content = fixture.content_signature(packet)
        after = fixture.counts()
        assert after["groups"] == persisted["groups"]
        assert after["records"] == persisted["records"]
        assert after["write_ledger"] == persisted["write_ledger"]
        state = fixture.state()
    finally:
        fixture.store.close()
    # SourceAuthority's verification itself remains native and may persist a
    # synchronization update outside timing. Record it, never suppress it.
    return {
        "native_reopen_verified": True,
        "constructor_bytes_unchanged": True,
        "persisted_counts": persisted,
        "after_verification_counts": after,
        "logical_result_signature": content,
        "closed_hashes": fixture_hashes(database, guard),
    }, state


def run_one(case, args, private, contracts, *, validation_profile=False):
    seal = load_seal(ACTIVE, case, initial=True)
    payload = json.loads((ACTIVE / STATE).read_bytes())
    before_hashes = seal["initial_hashes"]
    database, guard = locations(case)
    fixture = open_fixture(payload["fixture"], contracts, configuration(case, private))
    try:
        assert fixture.store.backend == "sqlite"
        assert fixture_hashes(database, guard) == before_hashes, (
            "constructor changed clone bytes"
        )
        before = fixture.counts()
        assert before == seal["initial_counts"]
        assert memory_features(fixture.store) == seal["active_memory_features"]
        with fixture.store.transaction() as db:
            assert durability(db, "sqlite") == seal["durability"]
            assert (
                seal["durability"]["journal_mode"] == "wal"
                and seal["durability"]["synchronous"] == 2
            )
            if payload["packet"]:
                assert (
                    db.execute(
                        "SELECT 1 FROM write_ledger WHERE job_id=?",
                        (payload["packet"]["job_id"],),
                    ).fetchone()
                    is None
                )
        owner_before = fixture.owners.calls if fixture.owners else 0
        if validation_profile:
            packet = payload["packet"]
            assert (packet is not None) == case.endswith("_commit")
            with ValidationProfile(contracts) as profile:
                cpu_started, started = time.process_time_ns(), time.perf_counter_ns()
                profile.active = True
                try:
                    if case.endswith("_commit"):
                        result = fixture.commit(packet)
                    else:
                        packet, result = fixture.full_flow(payload["index"])
                finally:
                    profile.active = False
                    latency_ns = time.perf_counter_ns() - started
                    cpu_ns = time.process_time_ns() - cpu_started
            validation = profile.result()
        elif case.endswith("_commit"):
            packet = payload["packet"]
            assert packet is not None
            cpu_started, started = time.process_time_ns(), time.perf_counter_ns()
            result = fixture.commit(packet)
            latency_ns = time.perf_counter_ns() - started
            cpu_ns = time.process_time_ns() - cpu_started
        else:
            assert payload["packet"] is None
            cpu_started, started = time.process_time_ns(), time.perf_counter_ns()
            packet, result = fixture.full_flow(payload["index"])
            latency_ns = time.perf_counter_ns() - started
            cpu_ns = time.process_time_ns() - cpu_started
        owner_reads = fixture.owners.calls - owner_before if fixture.owners else 0
        assert not case.startswith("source_") or owner_reads > 0
        after = fixture.assert_committed(packet, result, before)
        assert after["jobs"] == before["jobs"] + (
            1 if case.endswith("_full_flow") else 0
        )
        state = fixture.state()
    finally:
        fixture.store.close()
    committed_hashes = fixture_hashes(database, guard)
    assert committed_hashes["database_sha256"] != before_hashes["database_sha256"]
    assert committed_hashes["guard_sha256"] != before_hashes["guard_sha256"]
    proof, state = reopen_proof(
        case, private, contracts, state, packet, result, before, committed_hashes
    )
    record = {
        "case": case,
        "variant": args.variant,
        "verified_revision": args.verified_revision,
        "seed_groups": seal["seed_groups"],
        "initial_source_count": seal["source_count"],
        "latency_ns": latency_ns,
        "cpu_ns": cpu_ns,
        "owner_reads": owner_reads,
        "before_counts": before,
        "after_counts": after,
        "initial_hashes": before_hashes,
        "committed_hashes": committed_hashes,
        "proof": proof,
        "packet": packet,
        "result": result,
        "durability": seal["durability"],
        "models_called": 0,
    }
    if validation_profile:
        record["validation_profile"] = validation
    write_json(ACTIVE / "fixture-after.json", state)
    write_json(ACTIVE / "result.json", record)
    reply(
        "sample",
        **{
            key: value
            for key, value in record.items()
            if key not in ("packet", "result")
        },
    )


def verify(case, private, contracts):
    load_seal(ACTIVE, case, initial=False)
    state = json.loads((ACTIVE / "fixture-after.json").read_bytes())
    result = json.loads((ACTIVE / "result.json").read_bytes())
    assert result["case"] == case
    proof, _ = reopen_proof(
        case,
        private,
        contracts,
        state,
        result["packet"],
        result["result"],
        result["before_counts"],
        result["proof"]["closed_hashes"],
    )
    assert (
        proof["logical_result_signature"] == result["proof"]["logical_result_signature"]
    )
    reply("verified", case=case, **proof)


def main():
    parser = argparse.ArgumentParser(add_help=False, allow_abbrev=False)
    parser.add_argument(
        "--mode", choices=("seed", "prepare", "run", "verify"), required=True
    )
    parser.add_argument("--case", choices=CASES)
    parser.add_argument("--source", type=Path)
    parser.add_argument("--destination", type=Path)
    parser.add_argument("--stream", action="store_true")
    parser.add_argument("--validation-profile", action="store_true")
    control, remaining = parser.parse_known_args()
    if control.validation_profile and control.mode != "run":
        parser.error("--validation-profile requires --mode run")
    previous = sys.argv
    try:
        sys.argv = [sys.argv[0], *remaining]
        args, private, _ = arguments()
    finally:
        sys.argv = previous
    assert args.backend == "sqlite" and args.config.parent.resolve() == STAGE
    assert os.environ.get("PYTHONHASHSEED") == "0"
    require_sqlite_settings(settings(private, "writecontrol", "sqlite"))
    assert ACTIVE.resolve() == ACTIVE
    if control.mode == "prepare":
        assert (
            control.case
            and control.source
            and control.destination
            and not control.stream
        )
        prepare(control.case, control.source, control.destination)
        return
    contracts = contract_files(STAGE)
    if control.mode == "seed":
        assert control.case and not control.stream
        seed(control.case, private, contracts, size=20 if args.smoke else 300)
    elif control.mode == "verify":
        assert control.case and not control.stream
        verify(control.case, private, contracts)
    elif control.stream:
        reply(
            "ready",
            protocol="sqlite-write-control/1",
            variant=args.variant,
            verified_revision=args.verified_revision,
            cases=list(CASES),
            active_path=str(ACTIVE),
            store_closed_between_commands=True,
        )
        for line in sys.stdin:
            command = json.loads(line)
            assert isinstance(command, dict) and set(command) == {"case"}
            if command["case"] == "stop":
                reply("closed", stores_closed=True)
                return
            assert command["case"] in CASES
            run_one(
                command["case"],
                args,
                private,
                contracts,
                validation_profile=control.validation_profile,
            )
        raise RuntimeError("write worker input ended before stop")
    else:
        assert control.case
        run_one(
            control.case,
            args,
            private,
            contracts,
            validation_profile=control.validation_profile,
        )


if __name__ == "__main__":
    main()
