"""Diagnostic-only old/new Contracts preparation over the same native source reads.

Never included in the formal image latency comparison. Both sides use one Store,
SourceAuthority, owner instance and installed Memory code; only Contracts.validate
is dispatched. Its nested process-CPU timer is included in the operation timer,
not an additive phase. No request, response, source key or document is exported.

The CLI verifies the existing optimized image proof, copies a sealed synthetic
source_full_flow template, and imports that copy into a new UUID PG namespace.
run_pairs accepts an already opened disposable fixture for local SQLite smoke;
that API does not replace the CLI's image proof or clone/import checks.
"""

import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import time
import traceback
import types
import uuid
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

from common import durability, memory_features, settings, verify_installation
from memory_sql import signature
from memory_write_fixture import open_fixture
from tianshu_memory.contracts import Contracts
from tianshu_storage import StorageOptions

SIDES = ("original", "optimized")
CASES = ("field", "zero_budget", "keyword")
EXPECTED_REVISIONS = 6
EXPECTED_OWNER_CALLS = 3


class DiagnosticMismatch(RuntimeError):
    """Fixed diagnostic codes contain no request or response data."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def require(condition, code):
    if not condition:
        raise DiagnosticMismatch(code)


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()


def file_hash(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def exception_details(error):
    """Retain failure types/locations without exception text containing documents."""
    chain, seen = [], set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        item = {
            "type": type(error).__name__,
            "module": type(error).__module__,
            "frames": [
                {
                    "file": Path(frame.filename).name,
                    "line": frame.lineno,
                    "function": frame.name,
                }
                for frame in traceback.extract_tb(error.__traceback__)
            ],
            "message_omitted": True,
        }
        code = getattr(error, "code", None)
        if (
            type(error).__module__
            in (__name__, "tianshu_memory.domain", "tianshu_storage.errors")
            and isinstance(code, str)
            and re.fullmatch(r"[a-z][a-z0-9_]{0,79}", code)
        ):
            item["code"] = code
        sqlstate = getattr(error, "sqlstate", None)
        if isinstance(sqlstate, str) and re.fullmatch(r"[A-Z0-9]{5}", sqlstate):
            item["sqlstate"] = sqlstate
        chain.append(item)
        error = error.__cause__ or (
            None if error.__suppress_context__ else error.__context__
        )
    return chain


def load_baseline(path, expected_sha256):
    source = Path(path).read_bytes()
    require(
        hashlib.sha256(source).hexdigest() == expected_sha256, "baseline_hash_mismatch"
    )
    module = types.ModuleType("source_validation_baseline")
    # Compile the frozen Git blob directly: do not create a __pycache__ beside it.
    exec(compile(source, str(path), "exec"), module.__dict__)
    return module.Contracts


class ValidationDispatch:
    def __init__(self, contracts, baseline_type, authority):
        self.contracts, self.authority = contracts, authority
        kinds = {"original": baseline_type, "optimized": type(contracts)}
        # Constructors retain independent locks and registry-identity invalidation.
        # Only the pure validate method uses these delegates, never source rules.
        self.delegates = {
            side: kind(contracts.directory) for side, kind in kinds.items()
        }
        self.methods = {side: kind.validate for side, kind in kinds.items()}
        self.side = self.metrics = None

    def validate(self, name, document):
        require(
            self.side in SIDES and self.metrics is not None, "validation_outside_sample"
        )
        delegate = self.delegates[self.side]
        delegate.schemas, delegate.registry = (
            self.contracts.schemas,
            self.contracts.registry,
        )
        self.metrics["validation_names"][name] += 1
        started = time.process_time_ns()
        try:
            return self.methods[self.side](delegate, name, document)
        finally:
            self.metrics["validation_process_cpu_ns"] += (
                time.process_time_ns() - started
            )

    def begin(self, side):
        require(self.metrics is None, "nested_sample")
        self.side = side
        self.metrics = {
            "validation_names": Counter(),
            "validation_process_cpu_ns": 0,
            "fresh_revision_calls": 0,
        }

    def finish(self):
        metrics, self.metrics, self.side = self.metrics, None, None
        names = dict(sorted(metrics.pop("validation_names").items()))
        return {
            **metrics,
            "validation_calls": sum(names.values()),
            "validation_names": names,
        }

    @contextmanager
    def installed(self):
        previous = {
            name: (name in vars(owner), vars(owner).get(name))
            for owner, name in (
                (self.contracts, "validate"),
                (self.authority, "revision"),
            )
        }
        revision = self.authority.revision

        def observed_revision(db):
            if self.metrics is not None:
                self.metrics["fresh_revision_calls"] += 1
            return revision(db)

        self.contracts.validate = self.validate
        self.authority.revision = observed_revision
        try:
            yield self
        finally:
            for owner, name in (
                (self.contracts, "validate"),
                (self.authority, "revision"),
            ):
                existed, value = previous[name]
                if existed:
                    setattr(owner, name, value)
                else:
                    delattr(owner, name)


def state_proof(fixture):
    return {
        "counts": fixture.counts(),
        "guard_sha256": file_hash(fixture.store.recovery_path),
    }


def result_proof(result):
    # request_id is identical within a pair and intentionally changes across pairs.
    stable = {key: value for key, value in result.items() if key != "request_id"}
    return {
        "result_sha256": digest(result),
        "stable_result_sha256": digest(stable),
        "content_signature": signature(result),
        "effective_scope_sha256": digest(result["effective_scope"]),
        "scope_version_sha256": digest(result["scope_version"]),
        "selected_units": len(result["selected_units"]),
        "dependency_groups": len(result["dependency_groups"]),
        "budget_used": result["budget_used"],
    }


def measure_selection(fixture, dispatch, side, request, before, metadata, emit):
    dispatch.begin(side)
    owner_before = fixture.owners.calls
    result, failure = None, None
    cpu, wall = time.process_time_ns(), time.perf_counter_ns()
    try:
        result = fixture.service.select(request, fixture.context)
    except BaseException as error:
        failure = error
    elapsed, cpu_elapsed = time.perf_counter_ns() - wall, time.process_time_ns() - cpu
    row = {
        "event": "sample",
        "diagnostic_only": True,
        **metadata,
        "side": side,
        "wall_ns": elapsed,
        "process_cpu_ns": cpu_elapsed,
        **dispatch.finish(),
        "owner_rpc_calls": fixture.owners.calls - owner_before,
        "validation_cpu_included_in_process_cpu": True,
    }
    violations = []
    try:
        after = state_proof(fixture)
        row.update(before=before, after=after)
        if after != before:
            violations.append("state_changed")
        if row["fresh_revision_calls"] != EXPECTED_REVISIONS:
            violations.append("fresh_revision_call_count")
        if row["owner_rpc_calls"] != EXPECTED_OWNER_CALLS:
            violations.append("owner_rpc_call_count")
        if not row["validation_calls"]:
            violations.append("missing_document_validation")
        if result is not None:
            row["result"] = result_proof(result)
            if result["effective_scope"] != fixture.scope:
                violations.append("effective_scope_mismatch")
            expected = {
                "field": 1,
                "zero_budget": 0,
                "keyword": (min(before["counts"]["groups"], 300) + 9) // 10,
            }[metadata["case"]]
            if (
                len(result["selected_units"]) != expected
                or len(result["dependency_groups"]) != expected
            ):
                violations.append("selection_shape_mismatch")
            if not all(group["complete"] for group in result["dependency_groups"]):
                violations.append("incomplete_group")
            if not expected and result["budget_used"] != {"tokens": 0, "bytes": 0}:
                violations.append("zero_budget_mismatch")
        elif failure is None:
            violations.append("missing_result")
    except BaseException as error:
        if failure is None:
            failure = error
        else:
            row["postcheck_exception"] = exception_details(error)
    row.update(
        status="failed" if failure or violations else "ok", violations=violations
    )
    if failure is not None:
        row["exception"] = exception_details(failure)
    emit(row)
    if failure is not None:
        raise DiagnosticMismatch("selection_failed") from failure
    require(not violations, violations[0] if violations else "selection_check_failed")
    return result, row, after


def run_pairs(fixture, baseline_type, *, blocks=3, pairs=20, warmup_pairs=3, emit=None):
    """Measure an already opened synthetic fixture; never copy/open inside timing."""
    require(blocks > 0 and pairs > 0 and warmup_pairs >= 0, "invalid_sample_counts")
    require(
        fixture.kind == "source" and fixture.owners is not None,
        "source_fixture_required",
    )
    require(
        all(
            owner.contracts is fixture.contracts
            for owner in (
                fixture.h,
                fixture.owners,
                fixture.service,
                fixture.service.source_authority,
            )
        ),
        "contracts_instance_mismatch",
    )
    emit = emit or (lambda row: None)
    initial = state_proof(fixture)
    dispatch = ValidationDispatch(
        fixture.contracts, baseline_type, fixture.service.source_authority
    )
    samples, comparisons, expected_results = [], [], {}
    with dispatch.installed():
        previous = initial
        for block in range(blocks):
            for index in range(-warmup_pairs if block == 0 else 0, pairs):
                cases = CASES if (block + index) % 2 == 0 else tuple(reversed(CASES))
                for case in cases:
                    order = (
                        SIDES
                        if (block + index + CASES.index(case)) % 2 == 0
                        else SIDES[::-1]
                    )
                    metadata = {
                        "block": block + 1,
                        "pair": index + 1,
                        "phase": "warmup" if index < 0 else "diagnostic",
                        "case": case,
                        "case_order": list(cases),
                        "order": list(order),
                    }
                    emit({"event": "pair_started", "diagnostic_only": True, **metadata})
                    request = fixture.h.selection(
                        budget=0 if case == "zero_budget" else 100000
                    )
                    request.update(
                        query_text="field:bench-0" if case == "field" else "Python",
                        selection=["evidence"],
                    )
                    requests = {side: copy.deepcopy(request) for side in SIDES}
                    results, rows = {}, {}
                    for side in order:
                        result, row, previous = measure_selection(
                            fixture,
                            dispatch,
                            side,
                            requests[side],
                            previous,
                            metadata,
                            emit,
                        )
                        results[side], rows[side] = result, row
                        samples.append(row)
                    checks = {
                        "full_result_equal": results[SIDES[0]] == results[SIDES[1]],
                        "validation_names_equal": rows[SIDES[0]]["validation_names"]
                        == rows[SIDES[1]]["validation_names"],
                        "revision_calls_equal": rows[SIDES[0]]["fresh_revision_calls"]
                        == rows[SIDES[1]]["fresh_revision_calls"],
                        "owner_rpc_calls_equal": rows[SIDES[0]]["owner_rpc_calls"]
                        == rows[SIDES[1]]["owner_rpc_calls"],
                    }
                    stable = rows[SIDES[0]]["result"]["stable_result_sha256"]
                    expected_results.setdefault(case, stable)
                    checks["stable_result_across_pairs"] = (
                        stable == expected_results[case]
                    )
                    comparison = {
                        "event": "pair_checked",
                        "diagnostic_only": True,
                        **metadata,
                        "checks": checks,
                    }
                    comparisons.append(comparison)
                    emit(comparison)
                    require(all(checks.values()), "paired_result_or_boundary_mismatch")
    final = state_proof(fixture)
    require(final == initial, "final_state_changed")
    return {
        "diagnostic_only": True,
        "formal_latency_sample": False,
        "same_store_authority_and_owners": True,
        "only_validate_dispatched": True,
        "expected_fresh_revisions": EXPECTED_REVISIONS,
        "expected_owner_rpc_calls": EXPECTED_OWNER_CALLS,
        "warmup_separate": True,
        "initial": initial,
        "final": final,
        "warmup_sample_count": sum(row["phase"] == "warmup" for row in samples),
        "diagnostic_sample_count": sum(row["phase"] == "diagnostic" for row in samples),
        "samples": samples,
        "comparisons": comparisons,
    }


def clone_template(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    require(
        source != destination and not destination.is_relative_to(source),
        "clone_inside_template",
    )
    require(
        not source.is_relative_to(destination) and not destination.exists(),
        "clone_destination_exists",
    )
    seal = json.loads((source / "snapshot.json").read_bytes())
    require(
        seal["schema"] == "tianshu-sqlite-write-template/1"
        and seal["case"] == "source_full_flow",
        "wrong_template",
    )
    expected_names = {
        "sync.sqlite",
        "sync.sqlite.source-guard.json",
        "fixture-state.json",
    }
    require(set(seal["files"]) == expected_names, "unexpected_template_files")
    for name, expected in seal["files"].items():
        path = source / name
        require(
            path.is_file() and not path.is_symlink() and file_hash(path) == expected,
            "template_hash_mismatch",
        )
    wal = source / "sync.sqlite-wal"
    require(not wal.exists() or wal.stat().st_size == 0, "template_wal_not_idle")
    destination.mkdir(parents=True)
    hashes = {**seal["files"], "snapshot.json": file_hash(source / "snapshot.json")}
    for name, expected in hashes.items():
        with (
            (source / name).open("rb") as reader,
            (destination / name).open("xb") as writer,
        ):
            shutil.copyfileobj(reader, writer)
            writer.flush()
            os.fsync(writer.fileno())
        require(
            file_hash(source / name) == file_hash(destination / name) == expected,
            "clone_hash_mismatch",
        )
    return seal


def open_cloned_fixture(
    template, directory, contracts, private, backend, *, expected_groups=300
):
    require(backend in ("sqlite", "postgresql"), "unknown_backend")
    require(private["redis_url"] == "", "redis_must_be_explicitly_empty")
    seal = clone_template(template, directory)
    require(seal["seed_groups"] == expected_groups > 0, "seed_group_count_mismatch")
    state = json.loads((Path(directory) / "fixture-state.json").read_bytes())
    require(
        state["packet"] is None and state["fixture"]["kind"] == "source",
        "wrong_fixture_state",
    )
    namespace = "svp" + uuid.uuid4().hex if backend == "postgresql" else "writecontrol"
    config = {
        "database_path": str(Path(directory) / "sync.sqlite"),
        **settings(private, namespace, backend),
    }
    explicit = StorageOptions.from_settings(config, environ={})
    require(
        StorageOptions.from_settings(config) == explicit, "storage_environment_override"
    )
    require(
        not explicit.redis_url
        and bool(explicit.postgresql_url) == (backend == "postgresql"),
        "storage_backend_mismatch",
    )
    fixture = open_fixture(state["fixture"], contracts, config)
    try:
        require(fixture.store.backend == backend, "actual_backend_mismatch")
        require(fixture.counts() == seal["initial_counts"], "imported_counts_mismatch")
        features = memory_features(fixture.store)
        require(features == seal["active_memory_features"], "memory_features_mismatch")
        with fixture.store.transaction() as db:
            sync = durability(db, backend)
        if backend == "sqlite":
            require(
                sync["journal_mode"] == "wal" and sync["synchronous"] == 2,
                "sqlite_durability_mismatch",
            )
        else:
            require(
                all(
                    sync[name] == "on"
                    for name in ("fsync", "synchronous_commit", "full_page_writes")
                ),
                "pg_durability_mismatch",
            )
        return fixture, {
            "seed_groups": seal["seed_groups"],
            "source_count": seal["source_count"],
            "cloned_files": seal["files"],
            "new_pg_uuid_namespace": backend == "postgresql",
            "namespace": namespace,
            "active_memory_features": features,
            "durability": sync,
        }
    except BaseException:
        fixture.store.close()
        raise


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in (
        "config",
        "template",
        "directory",
        "contract-directory",
        "baseline-contracts",
        "output",
    ):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--backend", choices=("sqlite", "postgresql"), required=True)
    parser.add_argument("--baseline-sha256", required=True)
    parser.add_argument(
        "--image-proof", type=Path, default=Path("/usr/share/ts249-candidate.json")
    )
    parser.add_argument("--blocks", type=int, default=3)
    parser.add_argument("--pairs", type=int, default=20)
    parser.add_argument("--warmup-pairs", type=int, default=3)
    parser.add_argument("--seed-groups", type=int, default=300)
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="Use three pairs per block; warmups stay separate",
    )
    return parser.parse_args()


def main():
    args = arguments()
    # No common.arguments(): local API smoke does not need a Linux image proof;
    # the actual CLI always performs the existing full installation verification.
    require(
        not args.output.resolve().is_relative_to(args.template.resolve()),
        "output_inside_template",
    )
    with args.output.open("x", encoding="utf-8") as journal:

        def emit(row):
            journal.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            journal.flush()

        fixture = None
        emit(
            {
                "event": "started",
                "diagnostic_only": True,
                "formal_latency_sample": False,
                "backend": args.backend,
                "blocks": args.blocks,
                "pairs_per_case_per_block": 3 if args.smoke else args.pairs,
                "cases": list(CASES),
                "warmup_pairs_per_case": args.warmup_pairs,
            }
        )
        try:
            private = json.loads(args.config.read_bytes())
            require(
                private["database"].startswith("ts_pgopt_"),
                "dedicated_database_required",
            )
            revision = verify_installation(private, "optimized", args.image_proof)
            baseline = load_baseline(args.baseline_contracts, args.baseline_sha256)
            contracts = Contracts(args.contract_directory)
            contracts.load_sources()
            fixture, proof = open_cloned_fixture(
                args.template,
                args.directory,
                contracts,
                private,
                args.backend,
                expected_groups=args.seed_groups,
            )
            emit(
                {
                    "event": "prepared",
                    "diagnostic_only": True,
                    "verified_revision": revision,
                    "baseline_contracts_sha256": args.baseline_sha256,
                    **proof,
                }
            )
            report = run_pairs(
                fixture,
                baseline,
                blocks=args.blocks,
                pairs=3 if args.smoke else args.pairs,
                warmup_pairs=args.warmup_pairs,
                emit=emit,
            )
            fixture.store.close()
            fixture = None
            emit(
                {
                    "event": "completed",
                    "diagnostic_only": True,
                    "status": "ok",
                    **{
                        key: value
                        for key, value in report.items()
                        if key not in ("samples", "comparisons")
                    },
                    "sample_count": len(report["samples"]),
                    "pair_count": len(report["comparisons"]),
                }
            )
            return 0
        except BaseException as error:
            emit(
                {
                    "event": "failed",
                    "diagnostic_only": True,
                    "exception": exception_details(error),
                }
            )
            return 1
        finally:
            if fixture is not None:
                try:
                    fixture.store.close()
                except BaseException as error:
                    emit(
                        {
                            "event": "close_failed",
                            "diagnostic_only": True,
                            "exception": exception_details(error),
                        }
                    )


if __name__ == "__main__":
    raise SystemExit(main())
