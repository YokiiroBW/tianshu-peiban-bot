"""Synthetic persistence tests: selection and recovery must never fork authority."""

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from tianshu_storage import FileStore, StorageError, StorageOptions, StorageRuntime
from tianshu_storage.postgres import qmark

PG_URL = os.environ.get("TIANSHU_TEST_POSTGRES_URL", "")
REAL_PG = pytest.mark.skipif(
    not PG_URL, reason="explicit isolated PostgreSQL URL required"
)


def options(**overrides):
    return StorageOptions(namespace="storage_test_" + uuid4().hex[:12], **overrides)


def runtime(tmp_path, settings=None):
    return StorageRuntime(
        settings or options(), service="test", state_directory=tmp_path
    )


def prepare_records(connection, source):
    connection.native(
        "CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,body TEXT NOT NULL)"
    )
    if source is not None:
        rows = source.execute("SELECT id,body FROM records ORDER BY id").fetchall()
        connection.executemany(
            "INSERT INTO records VALUES(?,?)", (tuple(row) for row in rows)
        )
        assert connection.native("SELECT count(*) FROM records").fetchone()[0] == len(
            rows
        )


def create_source(path):
    with sqlite3.connect(path) as source:
        source.execute("CREATE TABLE records(id TEXT PRIMARY KEY,body TEXT NOT NULL)")
        source.execute("INSERT INTO records VALUES('old','原有记忆')")


def test_default_sqlite_does_not_create_marker_or_initialize_external_resources(
    tmp_path,
):
    state = tmp_path / "state"
    with runtime(state) as manager:
        database = manager.database("core", ":memory:")
        connection = database.connect()
        with connection:
            connection.execute("CREATE TABLE records(id TEXT)")
            connection.execute("INSERT INTO records VALUES('local')")
        assert connection.execute("SELECT id FROM records").fetchone() == ("local",)
        assert manager.backend == "sqlite"
        assert not manager.cache.enabled
        manager.finalize()
        connection.close()
    assert not state.exists()


def test_configuration_precedence_and_secrets_are_not_repr():
    settings = {
        "storage_backends": {
            "postgresql_url": "postgresql://u:secret@host/db",
            "redis_url": "redis://:redis-secret@host/0",
            "namespace": "configured",
        }
    }
    selected = StorageOptions.from_settings(
        settings,
        {
            "TIANSHU_POSTGRES_URL": "postgresql://u:override@other/db",
            "TIANSHU_STORAGE_NAMESPACE": "environment",
        },
    )
    assert selected.postgresql_url.endswith("@other/db")
    assert selected.namespace == "environment"
    assert "secret" not in repr(selected) and "override" not in repr(selected)


def test_first_unreachable_postgres_stays_sqlite_with_visible_status(tmp_path):
    selected = options(
        postgresql_url="postgresql://u:secret@127.0.0.1:1/no_db", connect_timeout=0.1
    )
    with runtime(tmp_path, selected) as manager:
        assert manager.backend == "sqlite"
        assert manager.status()["postgresql"] == "unavailable"
        assert not manager.state_path.exists()


def test_failed_connection_logs_do_not_expose_libpq_identity_or_password(
    tmp_path, caplog
):
    selected = options(
        postgresql_url="postgresql://private-user:private-password@127.0.0.1:1/private-database",
        connect_timeout=0.1,
    )
    with runtime(tmp_path, selected) as manager:
        assert manager.backend == "sqlite"
    assert "private-user" not in caplog.text
    assert "private-password" not in caplog.text
    assert "private-database" not in caplog.text


def test_file_references_reopen_verify_and_do_not_retain_partial_files(tmp_path):
    content = b"\x00\xffbinary attachment" * 1000
    files = FileStore(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(
            pool.map(
                lambda _: files.put_bytes(content, media_type="image/png"), range(8)
            )
        )
    assert len({r.sha256 for r in refs}) == 1
    reference = json.loads(json.dumps(refs[0].as_dict()))
    assert FileStore(tmp_path).read_bytes(reference) == content
    assert not list(tmp_path.rglob("*.partial"))
    path = tmp_path / reference["relative_path"]
    path.write_bytes(b"x" * len(content))
    with pytest.raises(StorageError, match="file_content_mismatch"):
        files.read_bytes(reference)


@pytest.mark.parametrize(
    "change",
    [
        {"relative_path": "../../escape"},
        {"sha256": None},
        {"size_bytes": -1},
        {"format": "other"},
    ],
)
def test_untrusted_reference_does_not_read_outside_file_root(tmp_path, change):
    files = FileStore(tmp_path)
    reference = files.put_text("资料原件").as_dict() | change
    with pytest.raises(StorageError, match="file_reference_invalid"):
        files.read_bytes(reference)


def test_question_mark_binding_keeps_literals_comments_and_native_parameters():
    statement = "SELECT '?' AS literal, ? AS value, '90%' AS percentage -- ?\n /* ? */"
    assert (
        qmark(statement, True)
        == "SELECT '?' AS literal, %s AS value, '90%%' AS percentage -- ?\n /* ? */"
    )
    assert qmark("SELECT $$? 100%$$, ?", True) == "SELECT $$? 100%%$$, %s"
    assert qmark("SELECT %s, '90%%'", True) == "SELECT %s, '90%%'"


@REAL_PG
def test_actual_postgres_migrates_and_restart_uses_current_pg_not_old_source(tmp_path):
    selected = options(postgresql_url=PG_URL)
    source = tmp_path / "local.sqlite"
    create_source(source)
    with runtime(tmp_path, selected) as manager:
        database = manager.database("core", source, prepare=prepare_records)
        with database.connect() as connection:
            connection.execute(
                "INSERT INTO records VALUES(?,?)", ("new", "PG 的新事实")
            )
        manager.finalize()
        assert manager.status()["state"] == "active"
        with database.connect(read_only=True) as connection:
            assert (
                connection.execute(
                    "SELECT body FROM records WHERE id=?", ("new",)
                ).fetchone()[0]
                == "PG 的新事实"
            )
        assert "postgresql://" not in manager.state_path.read_text()
    with sqlite3.connect(source) as local:
        assert local.execute("SELECT count(*) FROM records").fetchone()[0] == 1
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    probe = StorageRuntime.inspect(selected, service="test", state_directory=tmp_path)
    assert probe["backend"] == "postgresql" and probe["postgresql"] == "ok"
    assert before == {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    from contextlib import closing

    with closing(
        StorageRuntime.open_existing(
            selected, service="test", state_directory=tmp_path, name="core", path=source
        )
    ) as reader:
        assert (
            reader.execute("SELECT body FROM records WHERE id=?", ("new",)).fetchone()[
                0
            ]
            == "PG 的新事实"
        )
        with pytest.raises(StorageError):
            reader.execute("DELETE FROM records")
    with runtime(tmp_path, selected) as restarted:
        database = restarted.database("core", source, prepare=prepare_records)
        with database.connect() as connection:
            assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 2
        restarted.finalize()


@REAL_PG
def test_active_postgres_missing_or_broken_config_never_falls_back(tmp_path):
    selected = options(postgresql_url=PG_URL)
    with runtime(tmp_path, selected) as manager:
        manager.database("core", tmp_path / "unused.sqlite", prepare=prepare_records)
        manager.finalize()
    for url in ("", "postgresql://u:secret@127.0.0.1:1/no_db"):
        invalid = StorageOptions(
            postgresql_url=url, namespace=selected.namespace, connect_timeout=0.1
        )
        with pytest.raises(StorageError, match="postgresql_bound_"):
            runtime(tmp_path, invalid).start()
    assert not (tmp_path / "unused.sqlite").exists()


@REAL_PG
def test_failed_migration_rolls_back_then_retries_without_duplicate_rows(tmp_path):
    selected = options(postgresql_url=PG_URL)
    source = tmp_path / "local.sqlite"
    create_source(source)

    def fail_after_import(connection, old):
        prepare_records(connection, old)
        raise StorageError("synthetic_migration_interrupted")

    with runtime(tmp_path, selected) as manager:
        with pytest.raises(StorageError, match="synthetic_migration_interrupted"):
            manager.database("core", source, prepare=fail_after_import)
        assert manager.status()["state"] == "pending"
    with runtime(tmp_path, selected) as restarted:
        database = restarted.database("core", source, prepare=prepare_records)
        with database.connect() as connection:
            assert connection.execute("SELECT count(*) FROM records").fetchone()[0] == 1
        restarted.finalize()


@REAL_PG
def test_source_changed_during_import_cannot_become_active(tmp_path):
    selected = options(postgresql_url=PG_URL)
    source = tmp_path / "local.sqlite"
    create_source(source)
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA journal_mode=WAL")

    def concurrent_change(connection, old):
        prepare_records(connection, old)
        with sqlite3.connect(source) as writer:
            writer.execute("INSERT INTO records VALUES('racing','later fact')")

    with runtime(tmp_path, selected) as manager:
        with pytest.raises(StorageError, match="sqlite_source_changed"):
            manager.database("core", source, prepare=concurrent_change)
        assert manager.status()["state"] == "pending"


@REAL_PG
def test_pending_restart_rejects_source_writes_after_logical_import(tmp_path):
    selected = options(postgresql_url=PG_URL)
    source = tmp_path / "local.sqlite"
    create_source(source)
    with runtime(tmp_path, selected) as manager:
        manager.database("core", source, prepare=prepare_records)
        assert manager.status()["state"] == "pending"
    with sqlite3.connect(source) as writer:
        writer.execute("INSERT INTO records VALUES('new','未被接续的新事实')")
    with (
        runtime(tmp_path, selected) as restarted,
        pytest.raises(StorageError, match="sqlite_source_changed"),
    ):
        restarted.database("core", source, prepare=prepare_records)


@REAL_PG
@pytest.mark.parametrize("missing", ["schema", "binding"])
def test_active_missing_schema_or_binding_never_initializes_from_old_sqlite(
    tmp_path, missing
):
    selected = options(postgresql_url=PG_URL)
    source = tmp_path / "local.sqlite"
    create_source(source)
    with runtime(tmp_path, selected) as manager:
        database = manager.database("core", source, prepare=prepare_records)
        manager.finalize()
        schema = database.schema
    with psycopg.connect(PG_URL, autocommit=True) as admin:
        if missing == "schema":
            admin.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )
        else:
            admin.execute(
                sql.SQL("DROP TABLE {}.__tianshu_storage_binding").format(
                    sql.Identifier(schema)
                )
            )
    with (
        runtime(tmp_path, selected) as restarted,
        pytest.raises(StorageError, match="postgresql_bound_schema_missing"),
    ):
        restarted.database("core", source, prepare=prepare_records)


@REAL_PG
def test_expected_constraint_conflict_can_use_savepoint_without_poisoning_transaction(
    tmp_path,
):
    selected = options(postgresql_url=PG_URL)
    with runtime(tmp_path, selected) as manager:
        database = manager.database(
            "core", tmp_path / "new.sqlite", prepare=prepare_records
        )
        manager.finalize()
        with database.connect() as connection:
            connection.execute("INSERT INTO records VALUES('one','first')")
            with pytest.raises(sqlite3.IntegrityError):
                connection.native(
                    "INSERT INTO records VALUES('one','duplicate')", savepoint=True
                )
            assert (
                connection.execute("SELECT body FROM records").fetchone()[0] == "first"
            )
            assert (
                connection.native(
                    "SELECT '?' AS literal, ? AS bound, '90%' AS percent", ("safe",)
                ).fetchone()[1]
                == "safe"
            )


@REAL_PG
def test_another_installation_cannot_adopt_or_overwrite_owned_schema(tmp_path):
    selected = options(postgresql_url=PG_URL)
    with runtime(tmp_path / "owner", selected) as manager:
        database = manager.database(
            "core", tmp_path / "owner.sqlite", prepare=prepare_records
        )
        with database.connect() as connection:
            connection.execute("INSERT INTO records VALUES('one','owner')")
        manager.finalize()
    with (
        runtime(tmp_path / "other", selected) as other,
        pytest.raises(StorageError, match="postgresql_target_not_owned"),
    ):
        other.database("core", tmp_path / "other.sqlite", prepare=prepare_records)
