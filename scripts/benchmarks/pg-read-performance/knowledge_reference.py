"""Independent native Knowledge state/recover/check study over 20 current references.

The native state limit is 16 evidence refs. Recover therefore returns all 16
state refs plus four current query extras; check verifies the complete 20-ref
package. Existing measured runners and their bytes remain unchanged.
"""

import copy
import hashlib
from collections import Counter
from unittest.mock import patch

from common import arguments, durability, emit, measure, settings
from knowledge_fixture import knowledge
from knowledge_sql import signature as query_signature
from tianshu_memory import knowledge_sources
from tianshu_memory.contracts import Contracts
from tianshu_memory.domain import Fault, canonical, fingerprint
from tianshu_memory.knowledge import KnowledgeApplication
from tianshu_memory.store import Store

BUDGET = 32768
TEXT = "cohort3"
CURRENT = {"valid": True, "reason": "current"}


def close_fixture(run):
    # The existing exported native fixture owns its supported app in a closure.
    for cell in run.__closure__ or ():
        if isinstance(cell.cell_contents, KnowledgeApplication):
            cell.cell_contents.close()


def source_hashes(root):
    return {
        f"synthetic-{document}.txt": hashlib.sha256(
            (root / f"synthetic-{document}.txt").read_bytes()
        ).hexdigest()
        for document in range(2)
    }


def inspection(config, backend):
    store = Store.from_config(config, service="knowledge")
    try:
        assert store.backend == backend
        with store.transaction() as db:
            counts = {
                name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                for name in (
                    "knowledge_documents",
                    "knowledge_versions",
                    "knowledge_blocks",
                    "knowledge_states",
                    "knowledge_state_history",
                )
            }
            revision = db.execute(
                "SELECT revision FROM knowledge_projects WHERE id='demo'"
            ).fetchone()[0]
            sync = durability(db, backend)
        return dict(
            counts=counts,
            project_revision=revision,
            guard_sha256=hashlib.sha256(store.recovery_path.read_bytes()).hexdigest(),
            durability=sync,
        )
    finally:
        store.close()


def stable_package_signature(package, *, project, principal, state, receipt, query):
    """Called only after native check; preserve every business field in the hash."""
    assert set(package) == set(query) | {
        "revision",
        "registration",
        "state",
        "authority",
        "url_freshness",
        "seal",
    }
    assert package["project_id"] == query["project_id"] == "demo"
    assert package["registration"] == fingerprint(project)
    assert package["revision"] == 3 and package["state"] == dict(version=1, **state)
    assert package["authority"] == "explicit_project_note"
    assert package["url_freshness"] == "last_explicit_import"
    assert package["omissions"] == [] and len(package["blocks"]) == 20
    assert {canonical(block["reference"]) for block in package["blocks"]} == {
        canonical(block["reference"]) for block in query["blocks"]
    }
    assert [block["reference"] for block in package["blocks"][:16]] == state["evidence"]
    normalized = copy.deepcopy(package)
    seal = normalized.pop("seal")
    assert isinstance(seal, str) and len(seal) == 64 and int(seal, 16) >= 0
    normalized_project = dict(project, root="<synthetic-registered-root>")
    normalized["registration"] = fingerprint(normalized_project)
    normalized["benchmark_authority"] = {
        "client": "writer",
        "principal": {
            key: value for key, value in principal.items() if key != "credential_sha256"
        },
        "project_registration": normalized_project,
        "write_receipt": receipt,
        "native_check": CURRENT,
    }
    # This existing signature removes imported_at only. Keep all provenance,
    # deterministic block/document/source IDs, versions, hashes, state and scope.
    return query_signature(normalized)


def untimed_counts(operation):
    """One separate call, no timing; restore both methods even on an exception."""
    counts = Counter(reference_calls=0, source_file_reads=0)
    native_reference = KnowledgeApplication._reference
    native_read = knowledge_sources.read_file

    def reference(*args, **kwargs):
        counts["reference_calls"] += 1
        return native_reference(*args, **kwargs)

    def read(*args, **kwargs):
        counts["source_file_reads"] += 1
        return native_read(*args, **kwargs)

    with (
        patch.object(KnowledgeApplication, "_reference", reference),
        patch.object(knowledge_sources, "read_file", read),
    ):
        result = operation()
    return result, dict(counts, timing_included=False)


def denied(operation, code):
    try:
        operation()
    except Fault as error:
        assert error.code == code
        return error.code
    raise AssertionError("native Knowledge permission refusal expected")


def run_study(
    directory,
    contracts,
    private,
    backend,
    namespace,
    *,
    count=20,
    warmup=5,
    cpu_clocks=False,
    concurrency=1,
):
    """Explicit local API for a SQLite smoke; main alone verifies installed images."""
    assert concurrency in (1, 4)
    run, root, original_store, path, config = knowledge(directory, contracts)
    original_store.close()
    try:
        config.update(settings(private, namespace, backend))
        path.write_text(canonical(config), encoding="utf-8")
        selected = Store.from_config(config, service="knowledge")
        try:
            assert selected.backend == backend
        finally:
            selected.close()
        documents = {}
        for document in range(2):
            name = f"synthetic-{document}.txt"
            (root / name).write_text(
                "\n".join(
                    f"cohort{index % 10} unique{document}x{index} synthetic evidence line {index}."
                    for index in range(100)
                )
                + "\n",
                encoding="utf-8",
            )
            imported = run(
                "import",
                dict(
                    key=f"seed-{document}",
                    kind="file",
                    locator=name,
                    expected_version=0,
                    groups=[
                        dict(start=i + 1, end=i + 1, depends_on=[]) for i in range(100)
                    ],
                ),
            )
            assert imported["status"] == "imported" and imported["version"] == 1
            documents[imported["document_id"]] = name
        query = run("query", dict(text=TEXT, budget_bytes=BUDGET))
        assert len(query["blocks"]) == 20 and query["omissions"] == []
        hashes = source_hashes(root)
        for block in query["blocks"]:
            assert (
                block["reference"]["hash"]
                == hashes[documents[block["reference"]["document_id"]]]
            )
        expected_query = query_signature(query)
        state = dict(
            goal="Synthetic referenced project continuation",
            constraints=["Keep every referenced version and hash current"],
            recent_verification=["Synthetic current-file evidence prepared"],
            unfinished=["Explicit synthetic follow-up"],
            evidence=[
                copy.deepcopy(block["reference"]) for block in query["blocks"][:16]
            ],
            pitfalls=[
                dict(
                    trigger="Source changed",
                    symptom="Stale evidence",
                    cause="Old version",
                    correction="Read current sources",
                    verification="Native package check",
                    evidence=[copy.deepcopy(query["blocks"][0]["reference"])],
                )
            ],
        )
        receipt = run(
            "write_state", dict(key="reference-state", expected_version=0, state=state)
        )
        assert receipt == dict(
            status="written",
            project_id="demo",
            state_version=1,
            authority="explicit_project_note",
            sharing="project_only",
            verified_worktrees=[],
        )
        project = config["knowledge"]["projects"]["demo"]
        principal = config["knowledge"]["clients"]["writer"]

        def recover():
            return run("recover", dict(text=TEXT, budget_bytes=BUDGET))

        def checked_package(package):
            assert len(canonical(package).encode()) <= BUDGET
            assert run("check", {"package": package}) == CURRENT
            return stable_package_signature(
                package,
                project=project,
                principal=principal,
                state=state,
                receipt=receipt,
                query=query,
            )

        package = recover()
        expected = checked_package(package)
        before = inspection(config, backend)
        assert before["counts"] == dict(
            knowledge_documents=2,
            knowledge_versions=2,
            knowledge_blocks=200,
            knowledge_states=1,
            knowledge_state_history=1,
        )
        results = [
            measure(
                "knowledge_reference_recover20",
                lambda _: recover(),
                count,
                warmup=warmup,
                cpu_clocks=cpu_clocks,
                check=lambda value: checked_package(value) == expected or fail(),
            ),
            measure(
                "knowledge_reference_check20",
                lambda _: run("check", {"package": package}),
                count,
                warmup=warmup,
                cpu_clocks=cpu_clocks,
                check=lambda value: value == CURRENT or fail(),
            ),
        ]
        concurrent_results = []
        if concurrency == 4:
            from knowledge_reference_concurrent import measure_four

            concurrent_results = [
                measure_four(
                    "knowledge_reference_recover20_concurrent4",
                    recover,
                    count,
                    warmup=warmup,
                    cpu_clocks=cpu_clocks,
                    check=lambda value: checked_package(value) == expected or fail(),
                ),
                measure_four(
                    "knowledge_reference_check20_concurrent4",
                    lambda: run("check", {"package": package}),
                    count,
                    warmup=warmup,
                    cpu_clocks=cpu_clocks,
                    check=lambda value: value == CURRENT or fail(),
                ),
            ]
        counted_recovery, recover_counts = untimed_counts(recover)
        assert checked_package(counted_recovery) == expected
        counted_check, check_counts = untimed_counts(
            lambda: run("check", {"package": package})
        )
        assert counted_check == CURRENT
        # Collection lookup may legitimately reduce individual method calls.
        # The 16 state refs / complete 20-ref package and native checks above
        # remain mandatory; this implementation count is diagnostic only.
        assert (
            recover_counts["source_file_reads"]
            == check_counts["source_file_reads"]
            == 4
        )
        assert inspection(config, backend) == before and source_hashes(root) == hashes
        # Only this run's synthetic files/config are changed, after all timers.
        wrong_project = denied(
            lambda: run(
                "recover", dict(text=TEXT, budget_bytes=BUDGET), project_id="other"
            ),
            "forbidden",
        )
        wrong_secret = denied(
            lambda: run(
                "check", {"package": package}, credential="incorrect-project-credential"
            ),
            "unauthorized",
        )
        reader_check = run("check", {"package": package}, client="reader")
        assert reader_check == dict(valid=False, reason="stale_or_tampered")
        original_config = path.read_bytes()
        try:
            changed = copy.deepcopy(config)
            changed["knowledge"]["clients"]["writer"]["permissions"].remove("recover")
            path.write_text(canonical(changed), encoding="utf-8")
            revoked = denied(recover, "forbidden")
        finally:
            path.write_bytes(original_config)
        forged = copy.deepcopy(package)
        forged["state"]["goal"] += " changed"
        assert run("check", {"package": forged}) == reader_check
        reordered = copy.deepcopy(package)
        reordered["blocks"].reverse()
        assert run("check", {"package": reordered}) == reader_check
        refused = {}
        for label, field, value, code in (
            (
                "missing_document",
                "document_id",
                "document:synthetic-missing",
                "not_found",
            ),
            ("stale_version", "version", 2, "stale_evidence"),
            ("wrong_hash", "hash", "0" * 64, "stale_evidence"),
            ("malformed", "unexpected", True, "invalid_input"),
        ):
            proposal = copy.deepcopy(state)
            proposal["evidence"][0][field] = value
            refused[label] = denied(
                lambda proposed=proposal, key=label: run(
                    "write_state",
                    dict(key="refused-" + key, expected_version=1, state=proposed),
                ),
                code,
            )
        source = root / "synthetic-0.txt"
        original = source.read_bytes()
        try:
            source.write_bytes(original + b"synthetic changed source\n")
            stale = run("check", {"package": package})
            assert stale == reader_check
        finally:
            source.write_bytes(original)
        assert run("check", {"package": package}) == CURRENT
        assert (
            query_signature(run("query", dict(text=TEXT, budget_bytes=BUDGET)))
            == expected_query
        )
        after = inspection(config, backend)
        assert after == before and source_hashes(root) == hashes
        return results, dict(
            seed_blocks=200,
            source_files=2,
            state_evidence_refs=16,
            recovered_refs=20,
            query_extra_refs=4,
            native_state_limit=16,
            models_called=0,
            logical_result_signatures=dict(
                query_cohort=expected_query,
                recover=expected,
                check=fingerprint(CURRENT),
            ),
            untimed_call_counts=dict(recover=recover_counts, check=check_counts),
            individual_reference_count_is_diagnostic=True,
            **({"concurrent_results": concurrent_results} if concurrency == 4 else {}),
            native_negative_proofs=dict(
                wrong_project=wrong_project,
                wrong_credential=wrong_secret,
                revoked_permission=revoked,
                other_client_seal=reader_check,
                changed_source=stale,
                changed_state_seal=reader_check,
                reordered_package=reader_check,
                rejected_first_reference=refused,
            ),
            storage_before=before,
            storage_after=after,
            synthetic_source_hashes=hashes,
            timed_boundary="Native KnowledgeApplication.execute; recover's additional native check and signature run outside its timer; check case times check only; no formal hooks",
            normalization="imported_at omitted; per-DB seal omitted only after native check; actual registration verified then registered root replaced for cross-run fingerprint; all other fields retained",
        )
    finally:
        close_fixture(run)


def fail():
    raise AssertionError("synthetic Knowledge reference package mismatch")


def main():
    args, private, _ = arguments()
    directory = (
        args.config.parent
        / "runs"
        / f"knowledge-reference-{args.variant}-{args.trial}-{args.backend}"
    )
    directory.mkdir(parents=True)
    contracts = Contracts(args.config.parent / "contracts/text-dialogue/v1")
    results, details = run_study(
        directory,
        contracts,
        private,
        args.backend,
        "j" + args.namespace,
        count=3 if args.smoke else 20,
        cpu_clocks=args.cpu_clocks,
    )
    emit(args, "knowledge_reference", results, **details)


if __name__ == "__main__":
    main()
