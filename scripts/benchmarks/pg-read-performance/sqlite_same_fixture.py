"""Independent old/new control over one completed, byte-unchanged SQLite fixture.

The public Store constructor runs outside timing. Its existing preparation must
be a byte-for-byte no-op; the runner never migrates, reseeds, checkpoints, or
repairs this fixture. Native selection still owns transactions and guard checks.
"""

import hashlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path

from common import arguments, durability, emit, measure, memory_features, settings
from memory_fixture import Harness
from memory_sql import signature
from tianshu_memory.contracts import Contracts
from tianshu_memory.domain import canonical
from tianshu_memory.service import MemoryService
from tianshu_memory.sources import LocalFixtureSources
from tianshu_memory.store import Store
from tianshu_storage import StorageOptions

STAGE = Path("/bench")
FIXTURE = STAGE / "runs/memory-original-43-sqlite"


class ExistingHarness(Harness):
    """Reuse native request builders without Harness's registration/seed writes."""

    def __init__(self, config):
        self.config = config
        self.private = config["origins"]["origin-private"]["allowed_scope"]
        self.counter = 0


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fixture_hashes(database, guard):
    # Each native transaction closes its connection. Refuse an unsettled WAL
    # rather than checkpointing it and hiding a pre/post database byte change.
    wal = Path(str(database) + "-wal")
    assert not wal.exists() or wal.stat().st_size == 0, "shared fixture WAL is not idle"
    return {"database_sha256": sha256(database), "guard_sha256": sha256(guard)}


def fixture_config(args, private):
    assert args.backend == "sqlite", "same-fixture control is SQLite only"
    assert os.environ.get("PYTHONHASHSEED") == "0", "control requires PYTHONHASHSEED=0"
    assert args.config.parent.resolve() == STAGE, "control stage must be /bench"
    assert FIXTURE.resolve() == FIXTURE, "shared fixture path must not be redirected"
    path = FIXTURE / "config.json"
    assert path.resolve() == path, "shared fixture config must not be redirected"
    config = json.loads(path.read_bytes())
    assert config["mode"] == "local_fixture"
    database = FIXTURE / "memory.sqlite"
    guard = Path(str(database) + ".source-guard.json")
    contracts = STAGE / "contracts/text-dialogue/v1"
    assert Path(config["database_path"]).resolve() == database
    assert database.resolve() == database and guard.resolve() == guard
    assert database.is_file() and guard.is_file(), "completed shared fixture is required"
    assert Path(config["contract_directory"]).resolve() == contracts
    assert contracts.resolve() == contracts
    assert config.get("source_sync", {}).get("recovery_path", str(guard)) == str(guard)
    assert not config.get("role_grants_database_path")
    assert not config.get("knowledge_files_directory")
    # The original Harness config may omit storage_backends. Use the exact
    # original-43 namespace/pool settings, independent of this control's trial.
    config = dict(config, **settings(private, "moriginal43", "sqlite"))
    options = StorageOptions.from_settings(config)
    assert not options.postgresql_url and not options.redis_url
    assert options.namespace == "moriginal43" and options.pool_max_size == 4
    return config, database, guard, contracts


def valid_field(store, scope):
    with store.transaction() as db:
        row = db.execute(
            "SELECT g.field_key FROM groups g "
            "JOIN lineage l ON l.group_id=g.id JOIN sources s ON s.key=l.source_key "
            "WHERE g.scope=? AND g.state='active' AND g.category='evidence' "
            "AND g.field_key IS NOT NULL AND s.state='active' "
            "AND l.revision=s.revision AND l.epoch=s.epoch "
            "AND NOT EXISTS (SELECT 1 FROM lineage stale "
            "LEFT JOIN sources current ON current.key=stale.source_key "
            "WHERE stale.group_id=g.id AND (current.key IS NULL OR current.state!='active' "
            "OR stale.revision!=current.revision OR stale.epoch!=current.epoch)) "
            "ORDER BY g.field_key,g.id LIMIT 1",
            (canonical(scope),),
        ).fetchone()
        assert row is not None, "shared fixture has no current field"
        return row[0]


def check(result, expected, groups):
    assert len(result["dependency_groups"]) == groups
    assert signature(result) == expected, "shared fixture result changed"


def main():
    args, private, _ = arguments()
    config, database, guard, contract_path = fixture_config(args, private)
    before = fixture_hashes(database, guard)
    store = Store.from_config(config)
    try:
        after_constructor = fixture_hashes(database, guard)
        assert after_constructor == before, "Store preparation changed shared fixture bytes"
        assert store.backend == "sqlite"
        assert Path(store.path) == database and store.recovery_path == guard
        features = memory_features(store)
        with store.transaction() as db:
            schema = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0]
            assert schema == "3"
            groups = db.execute("SELECT COUNT(*) FROM groups").fetchone()[0]
            assert groups == 365, "control requires the full completed trial43 fixture"
            sync = durability(db, "sqlite")
        contracts = Contracts(contract_path)
        contracts.load_sources()
        h = ExistingHarness(config)
        service = MemoryService(
            store,
            contracts,
            source_authority=LocalFixtureSources(),
            clock=lambda: datetime(2026, 9, 14, 1, tzinfo=UTC),
        )
        context = config["origins"]["origin-private"]
        field = valid_field(store, h.private)

        def select(query="Python", budget=100000):
            return service.select(h.selection(query=query, budget=budget), context)

        exact, zero, keyword = select("field:" + field), select(budget=0), select()
        assert len(exact["selected_units"]) == len(exact["dependency_groups"]) == 1
        assert zero["selected_units"] == zero["dependency_groups"] == []
        assert zero["budget_used"] == {"tokens": 0, "bytes": 0}
        assert zero["omissions"] == ["budget"]
        assert len(keyword["dependency_groups"]) == len(keyword["selected_units"]) == 10
        assert {unit["statement"] for unit in keyword["selected_units"]} == {
            f"合成主题 {index} Python fixed fact" for index in range(200, 300, 10)
        }
        assert exact["scope_version"] == zero["scope_version"] == keyword["scope_version"]
        signatures = {name: signature(value) for name, value in (
            ("exact", exact), ("zero_budget", zero), ("withdrawn_keyword", keyword)
        )}
        results = [
            measure(
                "sqlite_same_fixture_authorized_field_recall",
                lambda _: select("field:" + field),
                3 if args.smoke else 500,
                check=lambda value: check(value, signatures["exact"], 1),
            ),
            measure(
                "sqlite_same_fixture_zero_budget",
                lambda _: select(budget=0),
                3 if args.smoke else 500,
                check=lambda value: check(value, signatures["zero_budget"], 0),
            ),
            measure(
                "sqlite_same_fixture_authorized_keyword_recall",
                lambda _: select(),
                3 if args.smoke else 100,
                check=lambda value: check(value, signatures["withdrawn_keyword"], 10),
            ),
        ]
    finally:
        store.close()
    after_measurement = fixture_hashes(database, guard)
    assert after_measurement == before, "selection changed shared fixture bytes"
    emit(
        args,
        "sqlite_same_fixture",
        results,
        independent_control=True,
        excluded_from_formal_summary=True,
        schema=schema,
        active_memory_features=features,
        durability=sync,
        verified_groups=groups,
        keyword_groups=10,
        field_key=field,
        logical_result_signatures=signatures,
        shared_fixture={
            "source_trial": 43,
            "directory": str(FIXTURE),
            "database": str(database),
            "guard": str(guard),
            "settings_namespace": "moriginal43",
            "pool_max_size": 4,
            "before_constructor": before,
            "after_constructor": after_constructor,
            "after_measurement": after_measurement,
            "constructor_noop_verified": True,
            "byte_unchanged": True,
            "manual_checkpoint_or_repair": False,
        },
        python_hash_seed=os.environ["PYTHONHASHSEED"],
        models_called=0,
    )


if __name__ == "__main__":
    main()
