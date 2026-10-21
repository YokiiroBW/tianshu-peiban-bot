"""Read-only executor evidence from an already measured UUID fixture.

No extension, global setting, ANALYZE/VACUUM or production relation is touched.
EXPLAIN ANALYZE is restricted to fixed SELECTs over synthetic benchmark tables.
Plans omit conditions and values. These timings exclude client/network work and
must never be substituted for the separately measured native application calls.
"""

from contextlib import closing

from common import arguments, emit, settings
from tianshu_memory.store import Store
from psycopg import sql


PLAN_FIELDS = {
    "Node Type",
    "Parent Relationship",
    "Relation Name",
    "Index Name",
    "Actual Rows",
    "Actual Loops",
    "Plan Rows",
    "Plan Width",
    "Shared Hit Blocks",
    "Shared Read Blocks",
    "Shared Dirtied Blocks",
    "Shared Written Blocks",
    "Local Hit Blocks",
    "Local Read Blocks",
    "Temp Read Blocks",
    "Temp Written Blocks",
    "WAL Records",
    "WAL Bytes",
    "WAL FPI",
    "Plans",
}


def safe_plan(value):
    if isinstance(value, list):
        return [safe_plan(item) for item in value]
    if isinstance(value, dict):
        return {
            key: safe_plan(item) for key, item in value.items() if key in PLAN_FIELDS
        }
    return value


def main():
    args, private, _ = arguments()
    args.profile_only = True
    assert args.backend == "postgresql", "executor diagnostic requires explicit PG"
    directory = (
        args.config.parent
        / "runs"
        / (f"memory-{args.variant}-{args.trial}-{args.backend}")
    )
    assert directory.is_dir(), "run the matching native memory_sql fixture first"
    store = Store.from_config(
        dict(
            database_path=str(directory / "memory.sqlite"),
            **settings(private, f"m{args.namespace}", args.backend),
        )
    )
    try:
        with closing(store.readonly()) as db:
            schema = db.native("SELECT current_schema()").fetchone()[0]
            assert schema == store.database.schema
            scope = db.native(
                "SELECT scope FROM groups ORDER BY rowid LIMIT 1"
            ).fetchone()[0]
            active = db.native(
                "SELECT id,field_key FROM groups WHERE scope=? AND state='active' "
                "AND category='evidence' ORDER BY rowid LIMIT 1",
                (scope,),
            ).fetchone()
            assert active is not None
            identity, field = tuple(active)
            binding = (
                sql.SQL(
                    "SELECT installation_id,logical_name,state FROM {}."
                    "__tianshu_storage_binding WHERE singleton=1"
                )
                .format(sql.Identifier(schema))
                .as_string(db.raw)
            )
            requests = (
                ("binding", binding, ()),
                ("metadata", "SELECT key,value FROM metadata", ()),
                (
                    "field_candidates",
                    "SELECT * FROM groups WHERE scope=? AND state='active' "
                    "AND category IN (?) AND field_key=?",
                    (scope, "evidence", field),
                ),
                ("records", "SELECT * FROM records WHERE group_id=?", (identity,)),
                (
                    "lineage",
                    "SELECT l.group_id,l.revision,l.epoch,s.key,s.state,s.revision,s.epoch "
                    "FROM lineage l JOIN sources s ON s.key=l.source_key WHERE l.group_id=?",
                    (identity,),
                ),
            )
            plans = {}
            for name, statement, parameters in requests:
                report = db.native(
                    "EXPLAIN (ANALYZE,BUFFERS,WAL,SETTINGS,SERIALIZE TEXT,TIMING OFF,FORMAT JSON) "
                    + statement,
                    parameters,
                ).fetchone()[0][0]
                plans[name] = dict(
                    planning_ms=report["Planning Time"],
                    execution_ms=report["Execution Time"],
                    serialization=report.get("Serialization"),
                    plan=safe_plan(report["Plan"]),
                )
            assert plans["field_candidates"]["plan"]["Actual Rows"] >= 1
            server = {
                name: db.native("SHOW " + name).fetchone()[0]
                for name in (
                    "server_version",
                    "fsync",
                    "synchronous_commit",
                    "full_page_writes",
                    "jit",
                    "shared_buffers",
                    "work_mem",
                    "effective_cache_size",
                    "max_parallel_workers_per_gather",
                )
            }
            stats = [
                dict(row)
                for row in db.native(
                    "SELECT relname,n_live_tup,n_dead_tup,n_tup_ins,n_tup_upd,n_tup_hot_upd,"
                    "n_tup_del,analyze_count,autoanalyze_count,autovacuum_count "
                    "FROM pg_stat_user_tables WHERE schemaname=? ORDER BY relname",
                    (schema,),
                )
            ]
            assert (
                server["fsync"]
                == server["synchronous_commit"]
                == server["full_page_writes"]
                == "on"
            )
    finally:
        store.close()
    emit(
        args,
        "executor_profile",
        [],
        diagnostic_only=True,
        read_only=True,
        extension_or_global_settings_changed=False,
        plans=plans,
        server=server,
        estimated_table_stats=stats,
        client_network_time_excluded=True,
        models_called=0,
    )


if __name__ == "__main__":
    main()
