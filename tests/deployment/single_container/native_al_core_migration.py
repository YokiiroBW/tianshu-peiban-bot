"""Run before/after wheel installation against the released and candidate Stores.

The before process must run in the unchanged R15 image. Rows are explicitly
synthetic structural migration fixtures, not source-authorized real experiences.
The after process uses the actual installed candidate migration and guard. Both
run as uid 10001, with a new isolated namespace or SQLite path, never production.
"""

import argparse
import hashlib
import json
from contextlib import closing
from pathlib import Path
from uuid import uuid4


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def rows(store):
    from tianshu_memory.actor_memory.store import TABLES, checkpoint

    with store.transaction() as db:
        result = {}
        for table in TABLES:
            cursor = db.execute("SELECT * FROM " + table)
            result[table] = sorted([list(row) for row in cursor], key=lambda row: str(row))
        return result, checkpoint(db)


def prepare(options):
    from tianshu_memory.actor_memory.store import Store

    output = options.output
    output.mkdir()
    config = dict(
        database_path=str(output / "user-source-never-created.sqlite"),
        actor_memory=dict(database_path=str(output / "actor.sqlite")),
        storage_backends=dict(namespace="alcoremigration" + uuid4().hex),
    )
    if options.postgres_url:
        config["storage_backends"]["postgresql_url"] = options.postgres_url
    write(output / "config.private.json", config)
    installed = Store.initialize(config, output / "initialization.json")
    with closing(Store(config)) as store:
        with store.transaction(write=True) as db:
            for n, state in enumerate(["active", "withdrawn"]):
                eid = "episode:structural-" + str(n)
                source = dict(producer="companion", object_id="structural-fixture:" + str(n),
                              revision="1", content_hash=str(n + 1) * 64)
                fact = dict(source=source, actor_id="actor:migration", relation="participant",
                            provenance="digital_action", occurred_at="2026-10-27T00:00:00Z",
                            learned_at="2026-10-27T00:00:00Z", outcome="completed",
                            summary="CORE_MIGRATION_STRUCTURAL_" + state,
                            source_generation="explicit-structural-fixture", source_sequence=n + 1,
                            access_version="structural-v1", source_constraints=dict(kind="actor_only"),
                            content_reads=[])
                db.execute("INSERT INTO actor_episodes VALUES (?,?,?,?,?,?,?)",
                           (eid, "actor:migration", 1, state, hashlib.sha256(eid.encode()).hexdigest(),
                            json.dumps(fact), "2026-10-27T00:00:00Z"))
                did = "derived:structural-" + str(n)
                payload = dict(kind="preference", text="CORE_MIGRATION_OPINION_" + state,
                               episode_refs=[dict(episode_id=eid, expected_revision=1)],
                               replaces=[], importance="normal", retention="long")
                db.execute("INSERT INTO actor_derived VALUES (?,?,?,?,?,?,?,?)",
                           (did, "actor:migration", 1, state, hashlib.sha256(did.encode()).hexdigest(),
                            json.dumps(payload), "2026-10-27T00:00:00Z", "2027-01-01T00:00:00Z"))
                db.execute("INSERT INTO actor_derived_basis VALUES (?,?,?,?)",
                           (did, "actor:migration", eid, 1))
        before, guard = rows(store)
    assert guard["schema"] == 2, "before phase must use the actual released R15 actor Store"
    write(output / "before.json", dict(tables=before, guard=guard, initialized=installed,
                                       fixture_kind="explicit_structural_rows_not_authorized_episodes"))
    assert not Path(config["database_path"]).exists()
    print(json.dumps(dict(ok=True, phase="released_schema2", backend=installed["backend"])))


def verify(options):
    from tianshu_memory.actor_memory.store import Store
    from tianshu_memory.domain import Fault

    output = options.output
    config = json.loads((output / "config.private.json").read_bytes())
    before = json.loads((output / "before.json").read_bytes())
    try:
        Store(config).close()
    except Fault:
        pass
    else:
        raise AssertionError("candidate must refuse unmigrated schema2 at startup")
    backup = output / "schema2-before-migration.private.json"
    migrated = Store.initialize(config, backup)
    snapshot = json.loads(backup.read_bytes())
    assert snapshot["guard"] == before["guard"]
    assert {name: sorted(value["rows"], key=lambda row: str(row))
            for name, value in snapshot["tables"].items()} == before["tables"]
    with closing(Store(config)) as store:
        after, guard = rows(store)
        assert guard["schema"] == 3 and guard["revision"] > before["guard"]["revision"]
        assert guard["instance"] == before["guard"]["instance"]
        with store.transaction() as db:
            original_episode = next(row for row in before["tables"]["actor_episodes"] if row[0] == "episode:structural-0")
            original_derived = next(row for row in before["tables"]["actor_derived"] if row[0] == "derived:structural-0")
            assert db.execute("SELECT fact FROM actor_episodes WHERE id='episode:structural-0'").fetchone()[0] == original_episode[5]
            assert db.execute("SELECT payload FROM actor_derived WHERE id='derived:structural-0'").fetchone()[0] == original_derived[5]
            assert db.execute("SELECT fact FROM actor_episodes WHERE id='episode:structural-1'").fetchone()[0] == "{}"
            assert db.execute("SELECT payload FROM actor_derived WHERE id='derived:structural-1'").fetchone()[0] == "{}"
            terms = db.execute("SELECT term FROM actor_search_terms").fetchall()
            assert terms and all(len(row[0]) == 64 for row in terms)
        backend = store.database.backend
    assert not Path(config["database_path"]).exists()
    result = dict(ok=True, backend=backend, actual_released_schema=2, candidate_schema=3,
                  explicit_backup_seven_tables=len(snapshot["tables"]),
                  snapshot_exact=True, old_negative_bodies_erased=True,
                  active_bodies_preserved=True, rebuilt_index_terms=len(terms),
                  actor_instance_preserved=True,
                  sqlite_user_source_path_never_created=True,
                  postgres_user_source_creation_checked=False if options.postgres_url else None,
                  backup_bytes=backup.stat().st_size,
                  backup_sha256=hashlib.sha256(backup.read_bytes()).hexdigest(),
                  limitations="Structural synthetic rows; this does not certify their source authority or restore later suppressions.")
    write(output / "native-al-core-migration.json", result)
    print(json.dumps(result))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["prepare", "verify"])
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--postgres-url")
    options = parser.parse_args()
    (prepare if options.phase == "prepare" else verify)(options)
