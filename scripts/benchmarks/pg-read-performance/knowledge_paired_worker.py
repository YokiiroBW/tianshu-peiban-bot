"""Read-only paired control over completed original Knowledge SQLite trial43.

Frozen tests/test_knowledge.py owns private.json, knowledge.sqlite, registered/
and the native source guard; Store also owns knowledge.sqlite.files. No fixture
is seeded, migrated, checkpointed, copied or repaired here. common.arguments
verifies each installed image before this worker reads the shared fixture.

Protocol matches sqlite_paired_worker: ready, one {case: ...} command per sample,
then {case: stop} -> closed. Cases: exact/cohort20/fresh_application/http_exact.
The driver supplies all ordering/warmups and swaps two image roles per block.
Four untimed readiness calls are additional to the driver's recorded warmups.
"""

from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sys
import time

from common import arguments, durability
from fastapi.testclient import TestClient
from knowledge_fixture import SECRET
from knowledge_sql import signature
from sqlite_same_fixture import fixture_hashes, sha256
from tianshu_memory.domain import canonical
from tianshu_memory.knowledge import KnowledgeApplication
from tianshu_memory.knowledge_http import ACTION_PATH, create_app
from tianshu_memory.store import Store
from tianshu_storage import StorageOptions

STAGE = Path("/bench")
FIXTURE = STAGE / "runs/knowledge-original-43-sqlite"
CASES = ("exact", "cohort20", "fresh_application", "http_exact")
COUNTS = {"exact": 1, "cohort20": 20, "fresh_application": 1, "http_exact": 1}
BUDGET = 32768


def reply(event, **values):
    print(json.dumps({"event": event, **values}, ensure_ascii=False), flush=True)


def tree_proof(directory):
    assert directory.resolve() == directory and directory.is_dir()
    files = {}
    size = 0
    for path in sorted(directory.rglob("*")):
        assert path.resolve() == path, "shared source tree must not be redirected"
        assert path.is_file() or path.is_dir()
        if path.is_file():
            files[path.relative_to(directory).as_posix()] = sha256(path)
            size += path.stat().st_size
    return {
        "files": len(files),
        "bytes": size,
        "manifest_sha256": hashlib.sha256(canonical(files).encode("utf-8")).hexdigest(),
    }


def fixture_config(args):
    assert args.backend == "sqlite" and args.config.parent.resolve() == STAGE
    assert os.environ.get("PYTHONHASHSEED") == "0"
    assert FIXTURE.resolve() == FIXTURE
    path = FIXTURE / "private.json"
    database = FIXTURE / "knowledge.sqlite"
    guard = Path(str(database) + ".source-guard.json")
    sources = FIXTURE / "registered"
    files = Path(str(database) + ".files")
    for item in (path, database, guard, sources, files):
        assert item.resolve() == item, "shared Knowledge fixture must not be redirected"
        assert item.exists(), "completed original Knowledge trial43 is required"
    config = json.loads(path.read_bytes())
    assert Path(config["database_path"]).resolve() == database
    assert Path(config["knowledge"]["projects"]["demo"]["root"]).resolve() == sources
    assert not config.get("knowledge_files_directory")
    assert config.get("source_sync", {}).get("recovery_path", str(guard)) == str(guard)
    options = StorageOptions.from_settings(config)
    assert not options.postgresql_url and not options.redis_url
    assert options.namespace == "koriginal43" and options.pool_max_size == 4
    return path, config, database, guard, sources, files


def shared_proof(path, database, guard, sources, files):
    return {
        **fixture_hashes(database, guard),
        "config_sha256": sha256(path),
        "registered_sources": tree_proof(sources),
        "knowledge_files": tree_proof(files),
    }


def inspect_fixture(config):
    store = Store.from_config(config, service="knowledge")
    try:
        assert store.backend == "sqlite"
        with store.transaction() as db:
            metadata = dict(db.execute("SELECT key,value FROM metadata"))
            counts = {
                name: db.execute("SELECT COUNT(*) FROM " + name).fetchone()[0]
                for name in (
                    "knowledge_documents",
                    "knowledge_versions",
                    "knowledge_blocks",
                )
            }
            sync = durability(db, "sqlite")
        assert metadata["schema"] == "3" and metadata["knowledge_schema"] == "1"
        assert metadata["knowledge_files_schema"] == "1"
        # Full knowledge_sql adds a single closure block and ten five-block imports
        # after the original two 100-block files. Reject the small smoke fixture.
        assert counts == {
            "knowledge_documents": 13,
            "knowledge_versions": 13,
            "knowledge_blocks": 251,
        }
        assert sync["journal_mode"] == "wal" and sync["synchronous"] == 2
        return {
            "counts": counts,
            "source_revision": int(metadata["source_revision"]),
            "durability": sync,
        }
    finally:
        store.close()


def query(application, text):
    return application.execute(
        {
            "operation": "query",
            "project_id": "demo",
            "arguments": {"text": text, "budget_bytes": BUDGET},
        },
        client="writer",
        credential=SECRET,
    )


def fresh_query(path):
    application = KnowledgeApplication(path)
    try:
        return query(application, "unique0x0")
    finally:
        application.close()


def http_query(client):
    response = client.post(
        ACTION_PATH,
        json={
            "operation": "query",
            "project_id": "demo",
            "arguments": {"text": "unique0x0", "budget_bytes": BUDGET},
        },
        headers={"Authorization": "Bearer " + SECRET},
    )
    # Keep native HTTP serialization/dispatch/response decoding in the operation.
    # Status, budget, shape and complete signature checks run after the timer.
    return response.status_code, response.json()


def check(result, expected, count):
    assert result["project_id"] == "demo" and len(result["blocks"]) == count
    assert (
        result["omissions"] == [] and len(canonical(result).encode("utf-8")) <= BUDGET
    )
    actual = signature(result)
    assert actual == expected, "paired Knowledge content or ordering changed"
    return actual


def main():
    args, _, _ = arguments()
    path, config, database, guard, sources, files = fixture_config(args)
    before = shared_proof(path, database, guard, sources, files)
    calls = 0
    try:
        with ExitStack() as stack:
            state = inspect_fixture(config)
            application = KnowledgeApplication(path)
            stack.callback(application.close)
            client = stack.enter_context(
                TestClient(
                    create_app(path, "writer", 8130), base_url="http://127.0.0.1:8130"
                )
            )
            after_constructor = shared_proof(path, database, guard, sources, files)
            assert after_constructor == before, (
                "Knowledge construction changed shared bytes"
            )
            operations = {
                "exact": lambda: query(application, "unique0x0"),
                "cohort20": lambda: query(application, "cohort3"),
                "fresh_application": lambda: fresh_query(path),
                "http_exact": lambda: http_query(client),
            }
            expected = {}
            for case, operation in operations.items():
                value = operation()
                if case == "http_exact":
                    status, value = value
                    assert status == 200, "Knowledge HTTP readiness failed"
                expected[case] = check(value, signature(value), COUNTS[case])
            assert (
                expected["fresh_application"]
                == expected["http_exact"]
                == expected["exact"]
            )
            after_readiness = shared_proof(path, database, guard, sources, files)
            assert after_readiness == before, "Knowledge readiness changed shared bytes"
            reply(
                "ready",
                suite="knowledge_sqlite_paired_control",
                backend="sqlite",
                trial=args.trial,
                variant=args.variant,
                verified_revision=args.verified_revision,
                logical_result_signatures=expected,
                **state,
                shared_fixture={
                    "directory": str(FIXTURE),
                    "configuration": str(path),
                    "database": str(database),
                    "guard": str(guard),
                    "registered_sources": str(sources),
                    "knowledge_files": str(files),
                    "settings_namespace": "koriginal43",
                    "pool_max_size": 4,
                    "before_constructor": before,
                    "after_constructor": after_constructor,
                    "after_readiness": after_readiness,
                    "constructor_noop_verified": True,
                },
                cases=list(CASES),
                untimed_readiness_calls=4,
                excluded_from_formal_summary=True,
                python_hash_seed=0,
            )
            for line in sys.stdin:
                command = json.loads(line)
                assert isinstance(command, dict) and set(command) == {"case"}
                case = command["case"]
                if case == "stop":
                    break
                assert case in operations, "unknown paired Knowledge case"
                cpu_started, started = time.process_time_ns(), time.perf_counter_ns()
                result = operations[case]()
                latency_ns = time.perf_counter_ns() - started
                cpu_ns = time.process_time_ns() - cpu_started
                if case == "http_exact":
                    status, result = result
                    assert status == 200, "Knowledge HTTP request failed"
                actual = check(result, expected[case], COUNTS[case])
                calls += 1
                reply(
                    "sample",
                    case=case,
                    latency_ns=latency_ns,
                    cpu_ns=cpu_ns,
                    logical_result_signature=actual,
                )
            else:
                raise RuntimeError("paired Knowledge input ended before stop")
    finally:
        after = shared_proof(path, database, guard, sources, files)
        assert after == before, "Knowledge selection changed shared fixture bytes"
    reply("closed", calls=calls, shared_fixture=after, byte_unchanged=True)


if __name__ == "__main__":
    main()
