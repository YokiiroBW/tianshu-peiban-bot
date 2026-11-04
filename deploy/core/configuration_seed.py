"""Export authored configuration only and restore it into an empty installation.

This tool never deletes a database or files. The seed contains encrypted secrets
and must remain private with the original master key; stdout contains counts only.
"""

from __future__ import annotations

import argparse
import base64
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


TABLES = (
    "runtime.credentials", "runtime.access_settings",
    "identity.installation", "identity.people", "identity.principals",
    "identity.person_aliases", "identity.actors", "identity.actor_grants",
    "identity.scopes", "identity.scope_grants", "identity.scope_inclusions",
    "identity.conversations", "identity.connections", "identity.accounts",
    "identity.conversation_links", "companion.persona_profiles",
    "companion.persona_revisions", "companion.actors",
    "models.quota_groups", "models.providers", "models.routes", "models.bindings",
    "models.embedding_profile", "channels.connections", "capabilities.configuration",
    "capability_connections.connections", "capability_media.documents",
    "capability_plugins.plugin_packages", "capability_plugins.plugin_instances",
    "capability_plugins.plugin_secrets", "life.actor_state", "life.contact_preferences", "life.routines",
)
MEDIA_CONFIGURATION = frozenset({
    "account", "media_target", "book_settings", "book_source", "comic_settings",
    "music_settings", "music_account", "music_navidrome",
})
BUSINESS_SENTINELS = (
    "conversation.turns", "memory.episodes", "models.attempts", "life.activities",
    "capabilities.actions", "capability_media.jobs", "runtime.commands",
)


def relation(name):
    return sql.Identifier(*name.split("."))


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def encode(value):
    if isinstance(value, (bytes, memoryview)):
        return {"base64": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError(type(value).__name__)


def export_seed(connection, output):
    connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
    # Automatically observed group members are business data. Keep only people
    # referenced by authored identities, permissions, configured scenes/preferences.
    people = {row["person_id"] for row in connection.execute(
        "SELECT person_id FROM identity.principals WHERE person_id IS NOT NULL UNION "
        "SELECT person_id FROM identity.scopes WHERE person_id IS NOT NULL UNION "
        "SELECT person_id FROM identity.conversations WHERE person_id IS NOT NULL UNION "
        "SELECT person_id FROM life.contact_preferences WHERE person_id<>''"
    )}
    tables = {}
    for name in TABLES:
        if name in {"identity.people", "identity.person_aliases", "identity.accounts"}:
            query = sql.SQL("SELECT * FROM {} WHERE person_id=ANY(%s)").format(relation(name))
            rows = connection.execute(query, (sorted(people),)).fetchall()
        elif name == "capability_media.documents":
            rows = connection.execute(
                sql.SQL("SELECT * FROM {} WHERE kind=ANY(%s)").format(relation(name)),
                (sorted(MEDIA_CONFIGURATION),),
            ).fetchall()
        else:
            rows = connection.execute(sql.SQL("SELECT * FROM {}").format(relation(name))).fetchall()
        tables[name] = rows
    now = datetime.now(UTC).isoformat()
    for row in tables["life.routines"]:
        row.update(next_due_at=now, last_tick_at=None)
    for row in tables["life.actor_state"]:
        # World, room controls, timezone and weather location are authored settings.
        # The retired activity and its wardrobe asset are business data.
        row.update(activity_id=None, outfit_id=None, revision=1, updated_at=now)
    for row in tables["life.contact_preferences"]:
        row.update(
            responsiveness=0.5, availability="unknown", available_until=None,
            availability_event_id=None, availability_occurred_at=None, availability_source_refs=[],
        )
    value = json.loads(json.dumps({
        "format": "tianshu-configuration-seed.v1", "created_at": now,
        "source_database": connection.info.dbname, "tables": tables,
    }, default=encode))
    value["sha256"] = hashlib.sha256(canonical(value).encode()).hexdigest()
    path = Path(output).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    return {"path": str(path), "sha256": value["sha256"],
            "counts": {name: len(rows) for name, rows in tables.items()}}


def restore_seed(connection, seed_file):
    value = json.loads(Path(seed_file).read_text(encoding="utf-8"))
    expected = value.pop("sha256", None)
    if (value.get("format") != "tianshu-configuration-seed.v1"
            or set(value.get("tables", {})) != set(TABLES)
            or hashlib.sha256(canonical(value).encode()).hexdigest() != expected):
        raise ValueError("invalid_configuration_seed")
    connection.execute("SELECT pg_advisory_xact_lock(733104310)")
    generation = connection.execute(
        "SELECT id FROM runtime.installation_generation WHERE singleton FOR SHARE"
    ).fetchone()
    if generation is None:
        raise ValueError("fresh_installation_generation_required")
    for name in (*TABLES, *BUSINESS_SENTINELS):
        if connection.execute(sql.SQL("SELECT 1 FROM {} LIMIT 1").format(relation(name))).fetchone():
            raise ValueError("configuration_restore_requires_empty_installation:" + name)
    counts = {}
    for name in TABLES:
        schema, table = name.split(".")
        columns = {row["column_name"]: row["udt_name"] for row in connection.execute(
            "SELECT column_name,udt_name FROM information_schema.columns "
            "WHERE table_schema=%s AND table_name=%s", (schema, table),
        )}
        for record in value["tables"][name]:
            selected = {key: item for key, item in record.items() if key in columns}
            data = []
            for key, item in selected.items():
                if item is not None and columns[key] in {"json", "jsonb"}:
                    item = Jsonb(item)
                elif item is not None and columns[key] == "bytea":
                    item = base64.b64decode(item["base64"], validate=True)
                data.append(item)
            connection.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                relation(name), sql.SQL(",").join(map(sql.Identifier, selected)),
                sql.SQL(",").join(sql.Placeholder() for _ in selected),
            ), data)
        counts[name] = len(value["tables"][name])
    # The retired installation's content, jobs, sessions, results and data UUID
    # never enter this seed; only author/config identities keep their stable IDs.
    return {"generation": generation["id"], "counts": counts, "seed_sha256": expected}


def rebuild_settings(document):
    """Retain product settings while assigning the new physical role budgets."""
    result = deepcopy(document)
    roles = result["database"].setdefault("roles", {})
    previous = roles.pop("worker", {"max_connections": 4, "control_connections": 2, "sync_connections": 2})
    for name in ("bot-foreground", "bot-background", "independent"):
        roles.setdefault(name, dict(previous))
    roles.setdefault("operations", {"max_connections": 1, "control_connections": 1, "sync_connections": 1})
    result.setdefault("worker", {})["queue_connections"] = 3
    result.setdefault("supervisor", {})["roles"] = [
        "interactive", "model-broker", "bot-foreground", "bot-background", "independent", "operations"
    ]
    lanes = result["worker"].setdefault("lanes", {})
    for name in ("life_control", "capability_control", "operations", "diary"):
        lanes.setdefault(name, 1)
    result.setdefault("memory", {}).setdefault("retrieval", {}).setdefault("vector_sql_seconds", 2.0)
    broker = result["broker"]
    broker.setdefault("upstream_connections", 64)
    broker.setdefault("diagnostic_connections", 4)
    transport = broker.setdefault("transport", {})
    per_role = transport.setdefault("roles", {})
    keys = ("foreground_connections", "due_connections", "background_connections", "control_connections")
    for name, limits in (
        ("interactive", (2, 1, 2, 2)), ("bot-foreground", (8, 2, 2, 2)),
        ("bot-background", (2, 4, 16, 2)), ("independent", (2, 2, 8, 2)),
        ("operations", (1, 1, 4, 2)),
    ):
        per_role.setdefault(name, dict(zip(keys, limits)))
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("export", "restore", "settings"))
    parser.add_argument("--file", required=True)
    parser.add_argument("--dsn-env", default="TIANSHU_DATABASE_URL")
    parser.add_argument("--output")
    args = parser.parse_args()
    if args.operation == "settings":
        if not args.output:
            parser.error("settings requires --output")
        document = json.loads(Path(args.file).read_text(encoding="utf-8"))
        destination = Path(args.output)
        with destination.open("x", encoding="utf-8") as stream:
            json.dump(rebuild_settings(document), stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        print(json.dumps({"settings": str(destination.resolve())}))
        return
    with psycopg.connect(os.environ[args.dsn_env], row_factory=dict_row) as connection:
        report = (export_seed(connection, args.file) if args.operation == "export"
                  else restore_seed(connection, args.file))
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
