"""Complete Memory groups and authorized, source-checked keyword selection.

memory_fixture.py is exported from the deployed commit's native test Harness.
Only pytest imports/decorated fixtures are omitted; Harness is byte-for-byte AST
equivalent to that commit. Its HTTP registration uses synthetic identities only.
"""

import hashlib
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
    pg_calls,
    prepare_memory_schema,
    python_profile,
    settings,
    sqlite_calls,
    summarize,
)
from memory_fixture import Harness
from tianshu_memory.contracts import Contracts
from tianshu_memory.store import Store


def main():
    args, config, _ = arguments(allow_profile=True)
    root = (
        args.config.parent
        / "runs"
        / f"memory-{args.variant}-{args.trial}-{args.backend}"
    )
    root.mkdir(parents=True)
    contracts = Contracts(args.config.parent / "contracts" / "text-dialogue" / "v1")
    h = Harness(root, contracts)
    # The same native schema 2 -> 3 migration runs before either measured backend.
    h.store.migrate_profiles(root / "profiles.backup")
    contracts.load_sources()
    h.store.migrate_sources(root / "sources.backup", contracts)
    features = prepare_memory_schema(h.store, root)
    if args.backend == "postgresql":
        h.store.close()
        h.store = h.service.store = Store.from_config(
            dict(
                database_path=str(root / "memory.sqlite"),
                **settings(config, f"m{args.namespace}", args.backend),
            )
        )
    assert h.store.backend == args.backend
    assert memory_features(h.store) == features
    context = h.config["origins"]["origin-private"]
    size, count = (20, 3) if args.smoke else (300, 30)
    sequence = 0
    sources = []

    def prepare(drafts):
        nonlocal sequence
        sequence += 1
        source = h.source(message=f"bench-message-{sequence}")
        sources.append(source)
        h.workflow.observe_source(source, h.private)
        event = h.event(
            [source], turn=f"bench-turn-{sequence}", event_id=f"bench-event-{sequence}"
        )
        event["scope_version"] = h.service.select(
            h.selection(query="field:unused", budget=0), context
        )["scope_version"]
        job = h.service.consume(
            event, {"authenticated_service": "companion", "allowed_scopes": [h.private]}
        )["candidate_job_ref"]
        units = [
            h.draft(
                field_key=f"bench-{index}",
                units=[
                    h.unit(
                        source=source,
                        statement=f"合成主题 {index} "
                        + ("Python" if index < size and index % 10 == 0 else "garden")
                        + " fixed fact",
                        conditions=[],
                        negations=[],
                    )
                ],
            )
            for index in drafts
        ]
        return job, units

    for offset in range(0, size, 10):
        job, units = prepare(range(offset, min(size, offset + 10)))
        assert h.workflow.commit_candidate(job, units)["state"] == "committed"
    with h.store.transaction() as db:
        sync = durability(db, args.backend)
    values, process, thread = [], [], []
    for index in range(count):
        job, units = prepare([size + index])
        if args.profile_only:
            result = h.workflow.commit_candidate(job, units)
        elif args.cpu_clocks:
            result, wall_ms, process_ms, thread_ms = cpu_sample(
                lambda: h.workflow.commit_candidate(job, units)
            )
            values.append(wall_ms)
            process.append(process_ms)
            thread.append(thread_ms)
        else:
            before = time.perf_counter_ns()
            result = h.workflow.commit_candidate(job, units)
            values.append((time.perf_counter_ns() - before) / 1e6)
        assert result["state"] == "committed"
    # Prepared source/candidate work is deliberately outside this commit measurement.
    results = (
        []
        if args.profile_only
        else [
            summarize(
                "memory_complete_group_commit",
                values,
                sum(values) / 1000,
                **(cpu_details(process, thread) if args.cpu_clocks else {}),
            )
        ]
    )

    def full_flow(index):
        job, units = prepare([10000 + index])
        result = h.workflow.commit_candidate(job, units)
        assert result["state"] == "committed"
        return result

    if args.profile_only:
        # Preserve the formal fixture's post-write corpus without recording samples.
        for index in range(-5, count):
            full_flow(index)
    else:
        results.append(
            measure(
                "memory_source_candidate_commit",
                full_flow,
                count,
                cpu_clocks=args.cpu_clocks,
            )
        )

    def select(query):
        return h.service.select(h.selection(query=query), context)

    exact = select("field:bench-0")
    keyword = select("Python")
    # Native result envelope/whole-group validation remains in select().
    assert exact["scope_version"] == keyword["scope_version"]
    assert len(keyword["dependency_groups"]) == size // 10
    signatures = {"keyword": signature(keyword), "exact": signature(exact)}
    if not args.profile_only:
        results.append(
            measure(
                "memory_authorized_keyword_recall",
                lambda _: select("Python"),
                5 if args.smoke else 40,
                cpu_clocks=args.cpu_clocks,
                check=lambda result: (
                    signature(result) == signatures["keyword"] or fail()
                ),
            )
        )
        results.append(
            measure(
                "memory_authorized_field_recall",
                lambda _: select("field:bench-0"),
                5 if args.smoke else 80,
                cpu_clocks=args.cpu_clocks,
                check=lambda result: signature(result) == signatures["exact"] or fail(),
            )
        )
        results.append(
            measure(
                "memory_zero_budget",
                lambda _: h.service.select(
                    h.selection(query="Python", budget=0), context
                ),
                5 if args.smoke else 80,
                cpu_clocks=args.cpu_clocks,
                check=lambda result: result["selected_units"] == [] or fail(),
            )
        )
    sql_profile = {
        "keyword": pg_calls(lambda: select("Python"), args.backend),
        "exact": pg_calls(lambda: select("field:bench-0"), args.backend),
    }
    sqlite_profile = {
        "keyword": sqlite_calls(lambda: select("Python"), args.backend),
        "exact": sqlite_calls(lambda: select("field:bench-0"), args.backend),
    }
    python_profiles = {}
    if args.profile_only:

        def zero():
            return h.service.select(h.selection(query="Python", budget=0), context)

        sql_profile["zero_budget"] = pg_calls(zero, args.backend)
        sqlite_profile["zero_budget"] = sqlite_calls(zero, args.backend)
        python_profiles = {
            "keyword": python_profile(
                lambda: select("Python"),
                check=lambda result: (
                    signature(result) == signatures["keyword"] or fail()
                ),
            ),
            "exact": python_profile(
                lambda: select("field:bench-0"),
                check=lambda result: signature(result) == signatures["exact"] or fail(),
            ),
            "zero_budget": python_profile(
                zero, check=lambda result: result["selected_units"] == [] or fail()
            ),
        }
    with h.store.transaction() as db:
        groups = db.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
        writes = db.execute("SELECT COUNT(*) FROM write_ledger").fetchone()[0]
    assert groups == size + count * 2 + 5 and writes == (size + 9) // 10 + count * 2 + 5
    # Native source withdrawal (outside the timing window), never a direct DB edit.
    # Most seeded groups are now invalid, retaining a small authorized corpus.
    for source in sources[: max(1, (size // 10) * 2 // 3)]:
        h.workflow.observe_source(source, h.private, state="withdrawn")
    remaining = select("Python")
    assert 0 < len(remaining["dependency_groups"]) < len(keyword["dependency_groups"])
    signatures["withdrawn_keyword"] = signature(remaining)
    if args.profile_only:
        python_profiles["withdrawn_keyword"] = python_profile(
            lambda: select("Python"),
            check=lambda result: (
                signature(result) == signatures["withdrawn_keyword"] or fail()
            ),
        )
    else:
        results.append(
            measure(
                "memory_recall_after_source_withdrawal",
                lambda _: select("Python"),
                5 if args.smoke else 40,
                cpu_clocks=args.cpu_clocks,
                check=lambda result: (
                    signature(result) == signatures["withdrawn_keyword"] or fail()
                ),
            )
        )
    h.client.close()
    h.store.close()
    emit(
        args,
        "memory",
        results,
        seed_groups=size,
        active_memory_features=features,
        verified_groups=groups,
        keyword_groups=len(keyword["dependency_groups"]),
        durability=sync,
        models_called=0,
        logical_result_signatures=signatures,
        untimed_pg_adapter_calls=sql_profile,
        untimed_sqlite_trace=sqlite_profile,
        **({"untimed_python_profiles": python_profiles} if args.profile_only else {}),
        withdrawn_keyword_groups=len(remaining["dependency_groups"]),
    )


def signature(result):
    """Compare complete content and group membership independently of generated IDs."""
    by_id = {unit["record_id"]: unit for unit in result["selected_units"]}
    groups = []
    for group in result["dependency_groups"]:
        units = []
        for record_id in group["record_ids"]:
            unit = by_id[record_id]
            units.append(
                {
                    key: unit[key]
                    for key in (
                        "statement",
                        "conditions",
                        "negations",
                        "valid_time",
                        "uncertainty",
                        "reality",
                    )
                }
            )
        groups.append(
            sorted(units, key=lambda value: json.dumps(value, sort_keys=True))
        )
    groups.sort(key=lambda value: json.dumps(value, sort_keys=True))
    raw = json.dumps(groups, ensure_ascii=False, sort_keys=True).encode()
    return hashlib.sha256(raw).hexdigest()


def fail():
    raise AssertionError("synthetic memory result mismatch")


if __name__ == "__main__":
    main()
