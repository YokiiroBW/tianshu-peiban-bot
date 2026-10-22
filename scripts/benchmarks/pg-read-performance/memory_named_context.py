"""Native group context over 100 independently registered, source-proven names.

Only the upstream identities/owners and extractor output are synthetic. Every
binding and fact is written through the existing native HTTP fixture. Timed
requests retain real HTTP authorization, loopback TLS and source synchronization.
"""

import copy
import hashlib
import json
import os
from collections import Counter
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

from common import (
    arguments,
    durability,
    emit,
    measure,
    memory_features,
    prepare_memory_schema,
    settings,
)
from group_fixture import add, initialize, owner, query, recalled
from sync_fixture import SyncHarness
from tianshu_memory import recall_language, recall_relevance
from tianshu_memory.contracts import Contracts
from tianshu_memory.domain import canonical, fingerprint, utc
from tianshu_memory.fact_names import NameView
from tianshu_memory.source_authority import SourceAuthority

OWNERS = 100
CLOCK = datetime(2026, 10, 22, 0, 0, tzinfo=UTC)
UNKNOWN = "阿宁家那只猫叫什么来着？"
CAT = "栗团"


def inspection(h):
    with h.store.transaction() as db:
        counts = {
            table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in (
                "accounts",
                "groups",
                "records",
                "automatic_facts",
                "sources",
                "source_admissions",
                "write_ledger",
            )
        }
        revision = SourceAuthority.revision(db)
        sync = durability(db, h.store.backend)
    return dict(
        counts=counts,
        source_revision=revision,
        durability=sync,
        guard_sha256=hashlib.sha256(h.store.recovery_path.read_bytes()).hexdigest(),
        features=memory_features(h.store),
    )


def signature(value, contracts, people, service):
    """Prove native projection ownership before normalizing generated identities."""
    contracts.validate("memory-context#query_response", value)
    with service.store.transaction() as db:
        assert service._projections_current(db, value["selected_units"]), (
            "native_projection_not_current"
        )

    def replace(item, identities):
        if isinstance(item, str):
            return identities.get(item, item)
        if isinstance(item, list):
            return [replace(element, identities) for element in item]
        if isinstance(item, dict):
            return {key: replace(element, identities) for key, element in item.items()}
        return item

    identities = dict(people)
    units = {unit["record_id"]: unit for unit in value["selected_units"]}
    assert len(units) == len(value["selected_units"])
    covered = set()
    for group in value["dependency_groups"]:
        assert group["complete"] is True and group["record_ids"]
        members = []
        for identity in group["record_ids"]:
            assert identity in units and identity not in covered
            covered.add(identity)
            unit = units[identity]
            assert unit["semantic_group_id"] == group["semantic_group_id"]
            assert unit["subject_person_id"] in people
            assert unit["visibility"] == "shared_projection"
            assert unit["sources"] and all(
                source["kind"] == "shareable_projection" and source["owner"] == "memory"
                for source in unit["sources"]
            )
            # Native group sharing deliberately exposes projection references,
            # never another person's raw-message source. Normalize each generated
            # ref by its complete unit and source ordinal, retaining owner/version.
            business = replace(
                {
                    key: item
                    for key, item in unit.items()
                    if key not in {"record_id", "semantic_group_id"}
                },
                people,
            )
            for source in business["sources"]:
                source.pop("projection_ref")
            for ordinal, source in enumerate(unit["sources"]):
                identities[source["projection_ref"]] = "projection:" + fingerprint(
                    [business, ordinal]
                )
            body = replace(
                {
                    key: item
                    for key, item in unit.items()
                    if key not in {"record_id", "semantic_group_id"}
                },
                identities,
            )
            identities[identity] = "record:" + fingerprint(body)
            members.append(body)
        identities[group["semantic_group_id"]] = "group:" + fingerprint(
            sorted(members, key=canonical)
        )
    assert covered == set(units)
    normalized = replace(value, identities)
    normalized.pop("request_id")
    return fingerprint(normalized)


def untimed_counts(operation):
    """One separate native HTTP call; hooks never enter the formal samples."""
    counts = Counter(
        reference_body_plain=0,
        reference_body_lexical=0,
        name_matches=0,
        fresh_revision=0,
    )
    views = []
    body = recall_language._reference_body
    prepared = getattr(recall_language, "NamedReferenceQuery", None)
    owner, method = (
        (prepared, "for_name") if prepared else (recall_relevance, "named_reference")
    )
    named = getattr(owner, method)
    current = NameView.current
    revision = SourceAuthority.revision

    def counted_body(value, *, lexical=False):
        counts["reference_body_lexical" if lexical else "reference_body_plain"] += 1
        return body(value, lexical=lexical)

    def counted_named(*args, **kwargs):
        counts["name_matches"] += 1
        return named(*args, **kwargs)

    def counted_current(self, *args, **kwargs):
        result = current(self, *args, **kwargs)
        views.append(
            dict(
                entries=len(self.entries),
                current_names=len(result),
                incomplete=len(self.incomplete),
            )
        )
        return result

    def counted_revision(db):
        counts["fresh_revision"] += 1
        return revision(db)

    with ExitStack() as stack:
        stack.enter_context(
            patch.object(recall_language, "_reference_body", counted_body)
        )
        stack.enter_context(patch.object(owner, method, counted_named))
        stack.enter_context(patch.object(NameView, "current", counted_current))
        stack.enter_context(
            patch.object(SourceAuthority, "revision", staticmethod(counted_revision))
        )
        result = operation()
    return result, dict(
        counts, name_views=views, name_match_counter=method, timing_included=False
    )


def run_study(
    directory,
    contracts,
    private,
    backend,
    namespace,
    certificates,
    *,
    count=30,
    warmup=5,
    cpu_clocks=False,
    owners=OWNERS,
):
    assert 2 <= owners <= OWNERS
    contracts.load_sources()
    contracts.load_context()
    examples = {
        row["id"]: row["document"]
        for row in json.loads(
            (
                contracts.directory.parent.parent
                / "source-sync/v1/examples/documents.json"
            ).read_bytes()
        )
    }
    h = SyncHarness(directory, contracts, examples, certificates, "group")
    try:
        initialize(h)
        prepare_memory_schema(h.store, directory)
        h.config["callers"]["companion"]["operations"].append("context_query")
        h.config.update(settings(private, namespace, backend))
    finally:
        h.store.close()
    people = {}
    with (
        patch.dict(os.environ),
        h.owners(),
        h.runtime(SimpleNamespace(setenv=os.environ.__setitem__)),
    ):
        h.store = h.service.store
        assert h.store.backend == backend
        h.service.clock = lambda: CLOCK
        people[h.scope()["person_id"]] = "requester"
        for index in range(owners):
            actor = owner(h)
            name = f"基准成员{index:03d}"
            people[h.scope(actor)["person_id"]] = f"owner:{index:03d}"
            add(
                h,
                "我叫" + name,
                "identity.name",
                name,
                actor=actor,
                second=index + 1,
                sent_at=utc(CLOCK - timedelta(days=1) + timedelta(seconds=index)),
            )
            h.calls.clear()
        add(
            h,
            "我的猫叫" + CAT,
            "pet.cat.name",
            CAT,
            actor=actor,
            second=owners + 1,
            sent_at=utc(CLOCK - timedelta(days=1) + timedelta(seconds=owners)),
        )
        target = h.scope(actor)
        known = name + "家那只猫叫什么来着？"
        h.calls.clear()
        expected, rpc_counts = {}, {}

        def operation(text):
            return recalled(h, query(h, text))

        def checked(value, label):
            kinds = Counter(kind for kind, _, _ in h.calls)
            assert {key: kinds[key] for key in ("snapshot", "current", "head")} == dict(
                snapshot=1, current=1, head=1
            )
            assert kinds["origin"] == 1 and sum(kinds.values()) == 4
            rpc_counts[label] = dict(kinds)
            h.calls.clear()
            assert value["coverage"]["complete"] is True
            assert value["effective_scope"] == h.scope()
            if label == "unknown":
                assert value["selected_units"] == value["dependency_groups"] == []
                assert [entry["scope"] for entry in value["scope_checks"]] == [
                    h.scope()
                ]
            else:
                assert {unit["statement"] for unit in value["selected_units"]} == {
                    "我叫" + name,
                    "我的猫叫" + CAT,
                }
                assert {
                    unit["subject_person_id"] for unit in value["selected_units"]
                } == {target["person_id"]}
                assert [entry["scope"] for entry in value["scope_checks"]] == [
                    h.scope(),
                    target,
                ]
                assert len(value["dependency_groups"]) == 2
            signed = signature(value, contracts, people, h.service)
            assert signed == expected.setdefault(label, signed)

        for label, text in (("unknown", UNKNOWN), ("known", known)):
            ready = operation(text)
            checked(ready, label)
        # The last readiness result is the real, source-proven two-unit known
        # response. Keep its body/owner/version intact and exchange only the two
        # valid projection IDs: the public contract still passes, native ownership
        # must reject before any generated ref can disappear into normalization.
        swapped = copy.deepcopy(ready)
        first, second = [unit["sources"][0] for unit in swapped["selected_units"]]
        assert first["projection_ref"] != second["projection_ref"]
        first["projection_ref"], second["projection_ref"] = (
            second["projection_ref"],
            first["projection_ref"],
        )
        contracts.validate("memory-context#query_response", swapped)
        try:
            signature(swapped, contracts, people, h.service)
        except AssertionError as error:
            assert str(error) == "native_projection_not_current"
        else:
            raise AssertionError("swapped projection ownership must be rejected")
        before = inspection(h)
        assert (
            before["counts"]["groups"]
            == before["counts"]["records"]
            == before["counts"]["automatic_facts"]
            == owners + 1
        )
        results = [
            measure(
                "memory_named_context_" + label,
                lambda _, text=text: operation(text),
                count,
                warmup=warmup,
                cpu_clocks=cpu_clocks,
                check=lambda value, label=label: checked(value, label),
            )
            for label, text in (("unknown", UNKNOWN), ("known", known))
        ]
        diagnostics = {}
        for label, text in (("unknown", UNKNOWN), ("known", known)):
            value, counts = untimed_counts(lambda: operation(text))
            checked(value, label)
            assert any(
                view == dict(entries=owners, current_names=owners, incomplete=0)
                for view in counts["name_views"]
            )
            diagnostics[label] = dict(counts, owner_rpc_counts=rpc_counts[label])
        after = inspection(h)
        assert after == before
        wrong = query(h, known, requested_scope=dict(h.scope(), actor_id="actor:b"))
        denied = h.post("memory/context/query", wrong)
        assert denied.status_code == 403 and denied.json()["code"] == "forbidden"
        h.calls.clear()
        after = inspection(h)
        assert after == before
    return results, dict(
        registered_name_owners=owners,
        native_fact_groups=owners + 1,
        returned_subjects=dict(unknown=0, known=1),
        returned_scope_checks=dict(unknown=1, known=2),
        logical_result_signatures=expected,
        untimed_call_counts=diagnostics,
        storage_before=before,
        storage_after=after,
        wrong_actor="forbidden",
        projection_ownership_proof=dict(
            native_check="MemoryService._projections_current: active projection, actual record_id, version",
            boundary="New native transaction per response, outside formal clocks and without owner RPC",
            swapped_refs_contract_valid=True,
            swapped_refs="rejected_before_normalization",
        ),
        models_called=0,
        timed_boundary="query construction + native TestClient HTTP + actual auth + synthetic loopback TLS source owners + MemoryContext query; result/signature/count checks outside timer",
        cpu_denominator="process CPU includes this process's ASGI and synthetic TLS owner threads; calling-thread CPU is the coordinator only; clocks overlap; no PG server CPU",
        normalization="Only generated person/record/group/projection identities and per-request request_id; business clock fixed; complete public envelope, ordering, projection owner/version, scope/version/budget retained",
    )


def main():
    args, private, _ = arguments()
    directory = (
        args.config.parent
        / "runs"
        / f"memory-named-context-{args.variant}-{args.trial}-{args.backend}"
    )
    directory.mkdir(parents=True)
    contracts = Contracts(args.config.parent / "contracts/text-dialogue/v1")
    results, details = run_study(
        directory,
        contracts,
        private,
        args.backend,
        "n" + args.namespace,
        args.config.parent / "synthetic-tls",
        count=3 if args.smoke else 30,
        cpu_clocks=args.cpu_clocks,
    )
    emit(args, "memory_named_context", results, **details)


if __name__ == "__main__":
    main()
