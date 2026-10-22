"""Separate formal SourceAuthority write study; original read fixtures stay intact."""

import json
import time

from common import (
    arguments,
    cpu_details,
    cpu_sample,
    durability,
    emit,
    measure,
    memory_features,
    settings,
    summarize,
)
from memory_write_fixture import contract_files, open_fixture, seed_fixture


def main():
    args, private, _ = arguments()
    directory = (
        args.config.parent
        / "runs"
        / f"source-write-{args.variant}-{args.trial}-{args.backend}"
    )
    directory.mkdir(parents=True)
    contracts = contract_files(args.config.parent)
    size, count = (20, 3) if args.smoke else (300, 30)
    fixture = seed_fixture(directory, contracts, "source", size=size)
    config = dict(
        database_path=fixture.store.path,
        **settings(private, "w" + args.namespace, args.backend),
    )
    fixture.store.close()
    fixture = open_fixture(fixture.state(), contracts, config)
    try:
        assert fixture.store.backend == args.backend
        features = memory_features(fixture.store)
        with fixture.store.transaction() as db:
            sync = durability(db, args.backend)
        values, written, process, thread = [], [], [], []
        for index in range(count):
            packet = fixture.prepare([size + index])
            if args.cpu_clocks:
                result, wall_ms, process_ms, thread_ms = cpu_sample(
                    lambda: fixture.commit(packet)
                )
                values.append(wall_ms)
                process.append(process_ms)
                thread.append(thread_ms)
            else:
                started = time.perf_counter_ns()
                result = fixture.commit(packet)
                values.append((time.perf_counter_ns() - started) / 1e6)
            assert result["state"] == "committed" and len(result["group_ids"]) == 1
            written.append((packet, result))
        results = [
            summarize(
                "source_sync_complete_group_commit",
                values,
                sum(values) / 1000,
                **(cpu_details(process, thread) if args.cpu_clocks else {}),
            )
        ]

        def full_flow(index):
            packet, result = fixture.full_flow(10000 + index)
            assert result["state"] == "committed" and len(result["group_ids"]) == 1
            written.append((packet, result))
            return result

        results.append(
            measure(
                "source_sync_source_candidate_commit",
                full_flow,
                count,
                cpu_clocks=args.cpu_clocks,
            )
        )
        counts = fixture.counts()
        assert counts["groups"] == counts["records"] == size + count * 2 + 5
        assert counts["write_ledger"] == (size + 9) // 10 + count * 2 + 5
        # Reopen native storage and verify durable ledgers and selected content,
        # outside all timers. IDs may differ across independent fixture instances.
        state = fixture.state()
        owner_reads = fixture.owners.calls
        fixture.store.close()
        fixture = open_fixture(state, contracts, config)
        with fixture.store.transaction() as db:
            for packet, result in written:
                row = db.execute(
                    "SELECT result FROM write_ledger WHERE job_id=?",
                    (packet["job_id"],),
                ).fetchone()
                assert row is not None and json.loads(row[0]) == result
        signatures = {
            "first_commit": fixture.content_signature(written[0][0]),
            "last_full_flow": fixture.content_signature(written[-1][0]),
        }
        assert fixture.counts()["groups"] == counts["groups"]
    finally:
        fixture.store.close()
    emit(
        args,
        "source_write",
        results,
        seed_groups=size,
        verified_groups=counts["groups"],
        active_memory_features=features,
        durability=sync,
        logical_result_signatures=signatures,
        native_reopen_verified=True,
        owner_reads=owner_reads,
        owner_transport="published synthetic in-process responses; native SourceAuthority, no TLS or real upstream latency",
        independent_study="SourceAuthority writes; excluded from the original two-suite 24-run denominator",
        models_called=0,
    )


if __name__ == "__main__":
    main()
