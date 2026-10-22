"""Native sourced facts and whole groups through the real two-operation Retrieval port.

Only upstream owner/model responses are synthetic. No authority result, source
admission, fact snapshot or product database call is replaced or cached here.
The old memory/source/knowledge workloads remain independent comparisons.
"""

import copy
import hashlib
import re
from collections import Counter

from common import (
    arguments,
    durability,
    emit,
    measure,
    memory_features,
    pg_calls,
    settings,
)
from memory_source_sync import SyntheticOwners
from memory_write_fixture import open_fixture, seed_fixture
from tianshu_memory.candidate_extraction import CandidateExtraction
from tianshu_memory.contracts import Contracts
from tianshu_memory.domain import Fault, canonical, fingerprint
from tianshu_memory.memory_context import MemoryContext
from tianshu_memory.retrieval import Retrieval
from tianshu_memory.source_authority import SourceAuthority

QUERY = "benchbatch"


class ContentOwners(SyntheticOwners):
    """Published owner schemas, including immutable synthetic extraction content."""

    def __init__(self, harness, contracts):
        super().__init__(harness, contracts)
        self.contents = {}
        self.planned = {}

    def read(self, schema, kind, request):
        self.calls += 1
        self.contracts.validate(f"sync-sources#{schema}_request", request)
        # Prepare new immutable owner content before its first native snapshot.
        # Existing physicals are never rewritten under the same revision.
        if self.planned:
            for physical in self.harness.physicals:
                key = canonical(physical["key"])
                statement = self.planned.get(physical["key"]["message_id"])
                if statement is not None and key not in self.contents:
                    content = dict(
                        message_key=dict(
                            physical["key"], revision=physical["revision"]
                        ),
                        author=physical["author"],
                        sent_at="2026-09-14T01:00:00Z",
                        kind=physical["kind"],
                        parts=[dict(kind="text", text=statement)],
                        reply_refs=[],
                        mentioned_accounts=[],
                    )
                    physical["content_digest"] = fingerprint(content)
                    self.contents[key] = content
                    del self.planned[physical["key"]["message_id"]]
        response = self.harness.response(kind, request)
        if request.get("include_content"):
            for physical in response["physicals"]:
                physical["content"] = copy.deepcopy(
                    self.contents[canonical(physical["key"])]
                )
        self.contracts.validate(f"sync-sources#{schema}_response", response)
        return response


class SyntheticReranker:
    """A deterministic external model port; it never supplies authority or IDs."""

    def __init__(self):
        self.calls = Counter()
        self.hook = None

    def call(self, operation, payload):
        self.calls[operation] += 1
        if operation == "settings":
            return dict(
                mode="keyword",
                rerank=True,
                candidates=100,
                revision="synthetic-batch-one",
                embedding_revision="synthetic-batch-one",
            )
        assert operation == "rerank"
        # This is the real port's scoring work. It retains no documents and uses
        # no generated group/record identities.
        documents = payload["documents"]
        assert documents
        scores = [
            -int(re.search(r"batchorder(\d+)", text).group(1)) for text in documents
        ]
        if self.hook:
            hook, self.hook = self.hook, None
            hook()
        return {"scores": scores}


def prepare(directory, contracts, private, backend, namespace, *, smoke=False):
    """Use the shared native initializer, then perform every new write on backend."""
    fixture = seed_fixture(directory, contracts, "source", size=0)
    fixture.h.config["callers"]["companion"]["operations"].append("context_query")
    fixture.h.save()
    config = dict(
        database_path=fixture.store.path,
        **settings(private, namespace, backend),
    )
    state = fixture.state()
    fixture.store.close()
    fixture = open_fixture(state, contracts, config)
    try:
        assert fixture.store.backend == backend
        assert memory_features(fixture.store)["automatic_fact_schema"] == "2"
        owners = ContentOwners(fixture.h, contracts)
        fixture.owners = owners
        fixture.service.source_authority = SourceAuthority(owners, contracts)
        fact_count, whole_count = (4, 2) if smoke else (16, 8)
        extractor = CandidateExtraction(fixture.service)
        for index in range(fact_count + whole_count):
            statement = f"{QUERY} batchorder{index:03d} synthetic evidence {index}"
            if index < fact_count:
                owners.planned[f"write-source:{fixture.sequence + 1}"] = statement
            packet = fixture.prepare([index])
            unit = packet["drafts"][0]["units"][0]
            unit.update(
                statement=statement, conditions=["synthetic condition"], negations=[]
            )
            if index < fact_count:
                result = extractor.execute(
                    dict(
                        job_id=packet["job_id"],
                        output=dict(
                            memories=[
                                dict(
                                    statement=statement,
                                    conditions=unit["conditions"],
                                    negations=[],
                                    valid_time="当前",
                                    evidence=[dict(source_index=0, quote=statement)],
                                    fact=dict(
                                        key=f"bench.topic{index}",
                                        value=f"synthetic evidence {index}",
                                        state="current",
                                        valid_from=None,
                                        valid_to=None,
                                        keywords=[QUERY],
                                    ),
                                )
                            ]
                        ),
                    ),
                    dict(
                        authenticated_service="companion",
                        allowed_scopes=[fixture.scope],
                    ),
                )
                assert (
                    result["state"] == "completed"
                    and result["result"]["state"] == "committed"
                )
            else:
                second = copy.deepcopy(unit)
                second.update(
                    statement=statement + " required companion", negations=["not alone"]
                )
                packet["drafts"][0]["units"].append(second)
                result = fixture.commit(packet)
                assert result["state"] == "committed" and len(result["record_ids"]) == 2
        with fixture.store.transaction() as db:
            assert (
                db.execute("SELECT COUNT(*) FROM automatic_facts").fetchone()[0]
                == fact_count
            )
        fixture.backend = SyntheticReranker()
        fixture.service.retrieval = Retrieval(fixture.backend)
        contracts.load_context()
        fixture.context_application = MemoryContext(fixture.service)
        fixture.expected_groups = fact_count + whole_count
        fixture.expected_units = fact_count + whole_count * 2
        return fixture
    except BaseException:
        fixture.store.close()
        raise


def select(fixture, *, budget=100000, scope=None):
    request = fixture.h.selection(budget=budget)
    request.update(query_text=QUERY, selection=["evidence"])
    if scope is not None:
        request["requested_scope"] = scope
    fixture.contracts.validate("identity-memory#select_request", request)
    return fixture.service.select(request, fixture.context)


def context_query(fixture):
    request = fixture.h.selection(budget=100000)
    request.update(
        query_text=QUERY,
        selection=["evidence"],
        include_associated=False,
        time_range=None,
        limit=32,
        known_association_version=None,
        known_scope_checks=None,
    )
    fixture.contracts.validate("memory-context#query_request", request)
    return fixture.context_application.query(request, fixture.context)


def signature(result, contracts, person):
    """Check full groups/scope/source metadata; normalize generated identities only."""
    contracts.validate(
        "memory-context#query_response"
        if result.get("version_domain") == "memory-context/v1"
        else "identity-memory#select_response",
        result,
    )
    by_id = {unit["record_id"]: unit for unit in result["selected_units"]}
    assert len(by_id) == len(result["selected_units"])

    def normalized(value):
        if isinstance(value, dict):
            return {
                key: (
                    "registered-person"
                    if key in {"person_id", "subject_person_id"} and item == person
                    else normalized(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [normalized(item) for item in value]
        return value

    covered, groups = set(), []
    for group in result["dependency_groups"]:
        assert group["complete"] is True and group["record_ids"]
        members = []
        for identity in group["record_ids"]:
            assert identity in by_id and identity not in covered
            covered.add(identity)
            unit = by_id[identity]
            assert unit["semantic_group_id"] == group["semantic_group_id"]
            assert unit["subject_person_id"] == person
            assert unit["visibility"] == "self_private"
            assert unit["sources"] and all(
                s["kind"] == "raw_message" for s in unit["sources"]
            )
            members.append(
                normalized(
                    {
                        key: value
                        for key, value in unit.items()
                        if key not in {"record_id", "semantic_group_id"}
                    }
                )
            )
        groups.append(sorted(members, key=canonical))
    assert covered == set(by_id)
    envelope = {
        key: normalized(value)
        for key, value in result.items()
        if key not in {"request_id", "selected_units", "dependency_groups"}
    }
    envelope["groups"] = groups
    ordered = hashlib.sha256(canonical(envelope).encode()).hexdigest()
    envelope["groups"] = sorted(groups, key=canonical)
    return {
        "ordered": ordered,
        "content": hashlib.sha256(canonical(envelope).encode()).hexdigest(),
    }


def expect_fault(operation, code):
    try:
        operation()
    except Fault as error:
        assert error.code == code
        return error.code
    raise AssertionError("expected native authority failure")


def withdraw(fixture):
    fixture.h.physicals[0].update(state="withdrawn", kind="retract", revision=2)
    fixture.h.core_head["sequence"] += 1


def authority_state(fixture):
    """Untimed, value-free source/epoch/durability receipt; never repair the guard."""
    with fixture.store.transaction() as db:
        sources = db.execute("SELECT state,revision,epoch FROM sources").fetchall()
        physicals = db.execute("SELECT state,revision FROM physical_sources").fetchall()
        groups = db.execute("SELECT state FROM groups").fetchall()
        revision = SourceAuthority.revision(db)
    return dict(
        source_revision=revision,
        source_states=dict(Counter(row[0] for row in sources)),
        source_revisions=dict(Counter(str(row[1]) for row in sources)),
        source_epochs=dict(Counter(str(row[2]) for row in sources)),
        physical_states=dict(Counter(row[0] for row in physicals)),
        physical_revisions=dict(Counter(str(row[1]) for row in physicals)),
        group_states=dict(Counter(row[0] for row in groups)),
        guard_sha256=hashlib.sha256(
            fixture.store.recovery_path.read_bytes()
        ).hexdigest(),
    )


def negative_sync(
    directory, contracts, private, backend, namespace, *, smoke=False, cpu_clocks=False
):
    """One first-negative transition per trial, separately seeded; no tail estimate."""
    directory.mkdir()
    fixture = prepare(directory, contracts, private, backend, namespace, smoke=smoke)
    try:
        # This case isolates native SourceAuthority + one select operation. The
        # two-operation external retrieval case above remains a different case.
        fixture.service.retrieval = None
        groups, units = fixture.expected_groups, fixture.expected_units
        positive = select(fixture)
        assert len(positive["dependency_groups"]) == groups
        assert len(positive["selected_units"]) == units
        before = authority_state(fixture)
        assert before["source_states"] == {"active": groups}
        assert before["source_epochs"] == {"1": groups}
        withdrawn = groups * 2 // 3
        for physical in fixture.h.physicals[:withdrawn]:
            physical.update(state="withdrawn", kind="retract", revision=2)
        fixture.h.core_head["sequence"] += 1
        returned = []

        def checked(value):
            # Every withdrawn source belongs to a single-unit fact group in
            # both bounded sizes. All two-unit groups must remain indivisible.
            assert len(value["dependency_groups"]) == groups - withdrawn
            assert len(value["selected_units"]) == units - withdrawn
            assert value["scope_version"] > positive["scope_version"]
            returned.append(value)

        owners = fixture.owners.calls
        measured = measure(
            "source_negative_sync",
            lambda _: select(fixture),
            1,
            warmup=0,
            check=checked,
            cpu_clocks=cpu_clocks,
            interpretation="one first-negative transition per trial; p95/p99 are not tail estimates",
        )
        owner_calls = fixture.owners.calls - owners
        assert owner_calls == 3
        result = returned[0]
        expected = {
            "content": signature(result, contracts, fixture.h.person)["content"]
        }
        after = authority_state(fixture)
        assert after["source_revision"] > before["source_revision"]
        assert after["source_states"] == {
            "active": groups - withdrawn,
            "withdrawn": withdrawn,
        }
        assert after["source_epochs"] == {"1": groups - withdrawn, "2": withdrawn}
        assert after["physical_revisions"] == {"1": groups - withdrawn, "2": withdrawn}
        assert after["group_states"]["active"] == groups - withdrawn
        assert after["guard_sha256"] != before["guard_sha256"]
        state, config = (
            fixture.state(),
            dict(
                database_path=fixture.store.path,
                **settings(private, namespace, backend),
            ),
        )
        counts = fixture.counts()
        fixture.store.close()
        fixture = open_fixture(state, contracts, config)
        assert authority_state(fixture) == after
        reopened = select(fixture)
        assert (
            signature(reopened, contracts, fixture.h.person)["content"]
            == expected["content"]
        )
        assert authority_state(fixture) == after and fixture.counts() == counts
        proof = dict(
            before=before,
            after=after,
            withdrawn_selectors=withdrawn,
            original_selectors=groups,
            owner_calls_in_measurement=owner_calls,
            same_native_guard_reopened=True,
            unchanged_on_repeat_read=True,
            logical_result_signature=expected,
        )
        return measured, proof
    finally:
        fixture.store.close()


def main():
    args, private, _ = arguments()
    directory = (
        args.config.parent
        / "runs"
        / f"retrieval-{args.variant}-{args.trial}-{args.backend}"
    )
    directory.mkdir(parents=True)
    contracts = Contracts(args.config.parent / "contracts/text-dialogue/v1")
    contracts.load_sources()
    fixture = prepare(
        directory,
        contracts,
        private,
        args.backend,
        "r" + args.namespace,
        smoke=args.smoke,
    )
    try:
        count = 3 if args.smoke else 30
        first = select(fixture)
        assert len(first["dependency_groups"]) == fixture.expected_groups
        assert len(first["selected_units"]) == fixture.expected_units
        expected = signature(first, contracts, fixture.h.person)

        def checked(value):
            assert signature(value, contracts, fixture.h.person) == expected

        before_owners = fixture.owners.calls
        before_model = fixture.backend.calls.copy()
        results = [
            measure(
                "memory_retrieval_sourced_facts_whole_groups",
                lambda _: select(fixture),
                count,
                check=checked,
                cpu_clocks=args.cpu_clocks,
            )
        ]
        native_calls = count + 5
        owner_calls = fixture.owners.calls - before_owners
        model_calls = fixture.backend.calls - before_model
        assert owner_calls == native_calls * 6
        assert model_calls == {"settings": native_calls, "rerank": native_calls}
        context_first = context_query(fixture)
        assert len(context_first["dependency_groups"]) == fixture.expected_groups
        assert len(context_first["selected_units"]) == fixture.expected_units
        assert context_first["coverage"]["complete"] is True
        context_expected = signature(context_first, contracts, fixture.h.person)
        before_owners = fixture.owners.calls
        before_model = fixture.backend.calls.copy()
        results.append(
            measure(
                "memory_context_retrieval_sourced_facts_whole_groups",
                lambda _: context_query(fixture),
                count,
                cpu_clocks=args.cpu_clocks,
                check=lambda value: (
                    signature(value, contracts, fixture.h.person) == context_expected
                    or fail()
                ),
            )
        )
        context_owner_calls = fixture.owners.calls - before_owners
        context_model_calls = fixture.backend.calls - before_model
        assert context_owner_calls == native_calls * 6
        assert context_model_calls == {"settings": native_calls, "rerank": native_calls}
        # Native authorization must reject another actor before any document is
        # sent to the external ranking port. Settings alone contain no content.
        reranks = fixture.backend.calls["rerank"]
        wrong_scope = dict(fixture.scope, actor_id="actor:b")
        forbidden = expect_fault(
            lambda: select(fixture, scope=wrong_scope), "forbidden"
        )
        assert fixture.backend.calls["rerank"] == reranks
        # Mutate the synthetic owner only while the real external port is called,
        # after operation one has closed. Operation two must synchronize again.
        fixture.backend.hook = lambda: withdraw(fixture)
        changed = expect_fault(lambda: select(fixture), "scope_changed")
        remaining = select(fixture)
        assert remaining["scope_version"] > first["scope_version"]
        assert len(remaining["dependency_groups"]) == fixture.expected_groups - 1
        assert len(remaining["selected_units"]) == fixture.expected_units - 1
        expected_after = signature(remaining, contracts, fixture.h.person)
        results.append(
            measure(
                "memory_retrieval_after_source_withdrawal",
                lambda _: select(fixture),
                count,
                cpu_clocks=args.cpu_clocks,
                check=lambda value: (
                    signature(value, contracts, fixture.h.person) == expected_after
                    or fail()
                ),
            )
        )
        profile = pg_calls(lambda: select(fixture), args.backend)
        counts = fixture.counts()
        with fixture.store.transaction() as db:
            sync = durability(db, args.backend)
        features = memory_features(fixture.store)
    finally:
        fixture.store.close()
    negative_result, negative_proof = negative_sync(
        directory / "negative-sync",
        contracts,
        private,
        args.backend,
        "n" + args.namespace,
        smoke=args.smoke,
        cpu_clocks=args.cpu_clocks,
    )
    results.append(negative_result)
    emit(
        args,
        "memory_retrieval",
        results,
        active_memory_features=features,
        seed_groups=fixture.expected_groups,
        seed_units=fixture.expected_units,
        source_selectors=fixture.expected_groups,
        durability=sync,
        verified_counts=counts,
        logical_result_signatures={
            "two_stage": expected,
            "context_two_stage": context_expected,
            "withdrawn": expected_after,
            "first_negative_sync": negative_proof["logical_result_signature"],
        },
        native_negative_proofs={
            "wrong_actor": forbidden,
            "between_operations_withdrawal": changed,
        },
        measured_and_warmup_owner_calls=owner_calls,
        measured_and_warmup_model_calls=dict(model_calls),
        context_measured_and_warmup_owner_calls=context_owner_calls,
        context_measured_and_warmup_model_calls=dict(context_model_calls),
        owner_calls_per_two_stage_call=6,
        first_negative_sync_proof=negative_proof,
        untimed_pg_adapter_calls={"withdrawn_two_stage": profile},
        owner_transport="published synthetic in-process owners; full native authority, no TLS latency",
        model_transport="deterministic synthetic rerank responses through native Retrieval.rank and backend.call",
        models_called=0,
    )


def fail():
    raise AssertionError("synthetic two-stage retrieval mismatch")


if __name__ == "__main__":
    main()
