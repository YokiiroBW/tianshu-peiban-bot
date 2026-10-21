"""Real SourceAuthority barrier with published synthetic owner responses.

Only the remote owners are in-process fixtures. Source synchronization, contract
validation, revision/guard checks and the final business transaction are native.
No real Core/Platform, TLS timing, model or production record is used.
"""

import copy
import hashlib
import json
from datetime import UTC, datetime

from common import (
    arguments,
    durability,
    emit,
    measure,
    memory_features,
    pg_calls,
    prepare_memory_schema,
    python_profile,
    settings,
    sqlite_calls,
)
from memory_sql import signature
from sync_fixture import SyncHarness
from tianshu_memory.contracts import Contracts
from tianshu_memory.service import MemoryService
from tianshu_memory.source_authority import SourceAuthority
from tianshu_memory.source_lookup_migration import migrate as migrate_lookup
from tianshu_memory.store import Store
from tianshu_memory.workflow import TrustedWorkflow


class SyntheticOwners:
    def __init__(self, harness, contracts):
        self.harness, self.contracts = harness, contracts
        self.calls = 0

    def read(self, schema, kind, request):
        self.calls += 1
        self.contracts.validate(f"sync-sources#{schema}_request", request)
        response = self.harness.response(kind, request)
        self.contracts.validate(f"sync-sources#{schema}_response", response)
        return response

    def facts(self, request):
        return self.read("facts", request["mode"], request)

    def current(self, request):
        return self.read("current_access", "current", request)


def main():
    args, private, _ = arguments(allow_profile=True)
    directory = (
        args.config.parent
        / "runs"
        / f"source-{args.variant}-{args.trial}-{args.backend}"
    )
    directory.mkdir(parents=True)
    contracts = Contracts(args.config.parent / "contracts/text-dialogue/v1")
    contracts.load_sources()
    rows = json.loads(
        (
            args.config.parent / "contracts/source-sync/v1/examples/documents.json"
        ).read_bytes()
    )
    examples = {row["id"]: row["document"] for row in rows}
    h = SyncHarness(
        directory, contracts, examples, directory / "unused-synthetic-certificates"
    )
    features = prepare_memory_schema(h.store, directory)
    if args.backend == "postgresql":
        h.store.close()
        h.store = Store.from_config(
            dict(
                database_path=str(directory / "sync.sqlite"),
                **settings(private, "s" + args.namespace, args.backend),
            )
        )
    assert h.store.backend == args.backend
    assert memory_features(h.store) == features
    with h.store.transaction() as db:
        installed = db.execute(
            "SELECT value FROM metadata WHERE key='source_lookup_schema'"
        ).fetchone()
    if not installed:
        migrate_lookup(h.store, directory / "lookup.backup")
    owners = SyntheticOwners(h, contracts)
    h.service = MemoryService(
        h.store,
        contracts,
        source_authority=SourceAuthority(owners, contracts),
        clock=lambda: datetime(2026, 9, 14, 1, 4, tzinfo=UTC),
    )
    workflow = TrustedWorkflow(h.service)
    registered = h.service.register(
        {"command": h.command(first=True), "account": h.account},
        h.contexts["synthetic-first"],
    )
    h.bind_person(registered["person_id"])
    context = h.contexts["synthetic-viewer:0"]
    scope = h.scope()
    template = tuple(
        copy.deepcopy(rows[0])
        for rows in (h.physicals, h.admissions, h.grants, h.turns)
    )
    h.physicals, h.admissions, h.grants, h.turns = [], [], [], []
    size = 20 if args.smoke else 300
    for offset in range(0, size, 10):
        physical, admission, grant, turn = map(copy.deepcopy, template)
        key = dict(physical["key"], message_id=f"synthetic-bench-source:{offset}")
        physical.update(
            key=key,
            physical_receipt_id=f"physical-bench:{offset}",
            content_digest=hashlib.sha256(str(offset).encode()).hexdigest(),
        )
        source = dict(
            admission["source"],
            message_key=dict(key, revision=1),
            receipt_id=f"admission-bench:{offset}",
        )
        admission.update(
            selector=dict(admission["selector"], key=key),
            scope=scope,
            source=source,
            physical_receipt_id=physical["physical_receipt_id"],
        )
        grant.update(
            selector=admission["selector"],
            scope=scope,
            entry_id=f"synthetic-entry:{offset}",
        )
        event = turn["committed_event"]
        with h.store.transaction() as db:
            version = h.service._scope_version(db, scope)
        event.update(
            event_id=f"event-bench:{offset}",
            aggregate_id=f"turn-bench:{offset}",
            sources=[source],
            scope=scope,
            scope_version=version,
            turn_sequence=offset + 1,
        )
        turn.update(turn_id=event["aggregate_id"], scope=scope, input_sources=[source])
        h.physicals.append(physical)
        h.admissions.append(admission)
        h.grants.append(grant)
        h.turns.append(turn)
        h.core_head["sequence"] += 1
        h.platform_head["sequence"] += 1
        job = h.service.consume(
            event, {"authenticated_service": "companion", "allowed_scopes": [scope]}
        )["candidate_job_ref"]
        drafts = []
        for index in range(offset, min(size, offset + 10)):
            draft = copy.deepcopy(examples["memory/candidate-commit"]["drafts"][0])
            draft.update(scope=scope, category="evidence", field_key=f"bench-{index}")
            draft["units"][0].update(
                statement=f"synthetic fact {index} "
                + ("Python" if index % 10 == 0 else "garden"),
                conditions=[],
                negations=[],
                sources=[source],
            )
            drafts.append(draft)
        assert (
            workflow.commit_candidate({"job_id": job, "drafts": drafts})["state"]
            == "committed"
        )

    def select(text="Python", budget=100000):
        request = h.selection(budget=budget)
        request.update(query_text=text, selection=["evidence"])
        return h.service.select(request, context)

    keyword = select()
    exact = select("field:bench-0")
    assert (
        len(keyword["dependency_groups"]) == size // 10
        and len(exact["dependency_groups"]) == 1
    )
    count = 3 if args.smoke else 20
    signatures = {"keyword": signature(keyword), "exact": signature(exact)}
    results = [] if args.profile_only else [
        measure(
            "source_sync_authorized_keyword_recall",
            lambda _: select(),
            count,
            check=lambda result: signature(result) == signatures["keyword"] or fail(),
        ),
        measure(
            "source_sync_authorized_field_recall",
            lambda _: select("field:bench-0"),
            count,
            check=lambda result: signature(result) == signatures["exact"] or fail(),
        ),
        measure(
            "source_sync_zero_budget",
            lambda _: select(budget=0),
            count,
            check=lambda result: result["selected_units"] == [] or fail(),
        ),
    ]
    profile = {
        "keyword": pg_calls(lambda: select(), args.backend),
        "exact": pg_calls(lambda: select("field:bench-0"), args.backend),
    }
    sqlite_profile = {
        "keyword": sqlite_calls(lambda: select(), args.backend),
        "exact": sqlite_calls(lambda: select("field:bench-0"), args.backend),
    }
    python_profiles = {}
    if args.profile_only:
        profile["zero_budget"] = pg_calls(lambda: select(budget=0), args.backend)
        sqlite_profile["zero_budget"] = sqlite_calls(lambda: select(budget=0), args.backend)
        # Profile the complete original corpus before changing any physical source.
        # Native/SQLite hooks have already been restored; these are separate calls.
        python_profiles = {
            "keyword": python_profile(
                lambda: select(),
                check=lambda result: signature(result) == signatures["keyword"] or fail(),
            ),
            "exact": python_profile(
                lambda: select("field:bench-0"),
                check=lambda result: signature(result) == signatures["exact"] or fail(),
            ),
            "zero_budget": python_profile(
                lambda: select(budget=0),
                check=lambda result: result["selected_units"] == [] or fail(),
            ),
        }
    # Retain historical coverage and native durable invalidation after withdrawal.
    for physical in h.physicals[: max(1, len(h.physicals) * 2 // 3)]:
        physical.update(state="withdrawn", kind="retract", revision=2)
    h.core_head["sequence"] += 1
    withdrawn = select()
    assert 0 < len(withdrawn["dependency_groups"]) < len(keyword["dependency_groups"])
    assert not select("field:bench-0")["selected_units"]
    signatures["withdrawn_keyword"] = signature(withdrawn)
    expected = signature(withdrawn)
    if args.profile_only:
        python_profiles["withdrawn_keyword"] = python_profile(
            lambda: select(),
            check=lambda result: signature(result) == expected or fail(),
        )
    else:
        results.append(
            measure(
                "source_sync_recall_after_withdrawal",
                lambda _: select(),
                count,
                check=lambda result: signature(result) == expected or fail(),
            )
        )
    with h.store.transaction() as db:
        sync = durability(db, args.backend)
        retained = db.execute("SELECT COUNT(*) FROM source_admissions").fetchone()[0]
    assert retained == size // 10
    h.store.close()
    emit(
        args,
        "source_sync",
        results,
        seed_groups=size,
        active_memory_features=features,
        retained_source_admissions=retained,
        logical_result_signatures=signatures,
        untimed_pg_adapter_calls=profile,
        untimed_sqlite_trace=sqlite_profile,
        **({"untimed_python_profiles": python_profiles} if args.profile_only else {}),
        owner_transport="published synthetic in-process responses; no TLS or real upstream latency",
        owner_reads=owners.calls,
        durability=sync,
        models_called=0,
    )


def fail():
    raise AssertionError("synthetic source-authority result mismatch")


if __name__ == "__main__":
    main()
