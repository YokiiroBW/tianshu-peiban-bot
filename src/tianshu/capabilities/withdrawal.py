"""Atomic source withdrawal from capability derivatives, without deleting shared bytes."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4
from tianshu.runtime.serialization import wire
from .contracts import canonical
from .serialization import receipt_from


async def validate_sources(connection, context, refs, sources):
    if sources is None:
        from .contracts import Fault

        raise Fault("source_persistence_port_unavailable", 503)
    return await sources.validate_persistence(connection, context, tuple(refs))


async def depend(connection, kind, identifier, refs):
    if not refs:
        return
    grouped = {}
    for ref in refs:
        grouped.setdefault((ref.source_id, ref.revision), []).append(wire(ref))
    payload = [
        {"source_id": key[0], "revision": key[1], "refs": values} for key, values in grouped.items()
    ]
    await connection.execute(
        "INSERT INTO capabilities.source_dependencies(owner_kind,owner_id,source_id,source_revision,refs) SELECT %s,%s,item->>'source_id',(item->>'revision')::bigint,item->'refs' FROM jsonb_array_elements(%s::jsonb) item ON CONFLICT(owner_kind,owner_id,source_id,source_revision) DO UPDATE SET refs=(SELECT jsonb_agg(DISTINCT value) FROM jsonb_array_elements(capabilities.source_dependencies.refs||excluded.refs))",
        (kind, identifier, canonical(payload)),
    )


_PROGRESS_FIELDS = frozenset({
    "action_id", "attempt_id", "expression_id", "call_id", "message_id", "message_ids",
    "api_name", "state", "status", "execution", "effect", "delivery", "dispatch",
    "submitted", "provider_handle", "retcode", "error_code", "index", "part_index",
    "segment_index", "started_at", "finished_at", "submitted_at", "sent_at", "updated_at",
    "observed_at", "accepted_at", "confirmed_at", "delivered_at", "attempted_at",
    "timestamp", "count", "total", "sent_count", "confirmed_count", "failed_count",
    "unknown_count", "pending_count", "parts", "segments", "calls", "delivery_progress",
})


def _progress(value):
    """Keep receipt identities and progress, never message bodies or attachments."""
    if isinstance(value, dict):
        return {key: _progress(item) for key, item in value.items() if key in _PROGRESS_FIELDS}
    if isinstance(value, (tuple, list)):
        return [_progress(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return None


def redacted(receipt):
    retained = {
        key: receipt.result[key]
        for key in ("usage", "billing_source", "model", "provider_handle", "generated_at")
        if key in receipt.result
    }
    for key in ("expression_id", "message_id", "message_ids", "calls", "parts", "segments",
                "delivery_progress", "submitted", "dispatch"):
        if key in receipt.result:
            retained[key] = _progress(receipt.result[key])
    retained.update(
        source_revoked=True,
        source_refs=[],
        sharing={"decision": "archive", "reason": "source_revoked"},
    )
    return replace(
        receipt, artifacts=(), coverage=None, result=retained, error_code="source_revoked"
    )


async def selected_dependencies(connection, refs, reason):
    refs = tuple(refs)
    unique = sorted({(ref.source_id, ref.revision) for ref in refs})
    identifiers = [key[0] for key in unique]
    revisions = [key[1] for key in unique]
    if not identifiers:
        return {}
    from tianshu.sources.dependencies import changes_for, intersects

    changes, _ = changes_for(refs)
    dependencies = await (
        await connection.execute(
            "SELECT owner_kind,owner_id,refs FROM capabilities.source_dependencies WHERE (source_id,source_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (identifiers, revisions),
        )
    ).fetchall()
    selected = {}
    for dependency in dependencies:
        if intersects(dependency["refs"], changes, reason):
            selected.setdefault(dependency["owner_kind"], set()).add(dependency["owner_id"])
    return selected


async def dependency_actors(connection, selected):
    """Resolve each derivative to its owner; orphan/shared caches use None."""
    result = {(kind, identifier): None for kind, ids in selected.items() for identifier in ids}
    direct = {
        "action": "SELECT id AS identity,action->>'actor_id' AS actor_id FROM capabilities.actions WHERE id=ANY(%s)",
        "artifact": "SELECT id AS identity,actor_id FROM capabilities.artifacts WHERE id=ANY(%s)",
        "catalog_source": "SELECT id AS identity,body->>'actor_id' AS actor_id FROM capabilities.configuration WHERE kind='catalog_source' AND id=ANY(%s)",
        "plugin_consumption": "SELECT id AS identity,context->>'actor_id' AS actor_id FROM capabilities.plugin_consumptions WHERE id=ANY(%s)",
        "project_continuation": "SELECT pack_id AS identity,actor_id FROM capability_projects.continuations WHERE pack_id=ANY(%s)",
        "project_import": "SELECT i.action_id AS identity,p.actor_id FROM capability_projects.imports i JOIN capability_projects.projects p USING(project_id) WHERE i.action_id=ANY(%s)",
    }
    for kind, query in direct.items():
        identifiers = sorted(selected.get(kind, ()))
        if not identifiers:
            continue
        rows = await (await connection.execute(query, (identifiers,))).fetchall()
        for row in rows:
            result[(kind, row["identity"])] = row["actor_id"]

    versioned = {
        "project_document": ("documents", "document_id"),
        "project_note": ("notes", "note_id"),
        "project_lesson": ("lessons", "lesson_id"),
    }
    for kind, (table, column) in versioned.items():
        owners = selected.get(kind, ())
        identifiers = sorted({item.rsplit(":", 1)[0] for item in owners})
        if not identifiers:
            continue
        rows = await (await connection.execute(
            f"SELECT e.{column} AS identity,p.actor_id FROM capability_projects.{table} e "
            f"JOIN capability_projects.projects p USING(project_id) WHERE e.{column}=ANY(%s)",
            (identifiers,))).fetchall()
        by_id = {row["identity"]: row["actor_id"] for row in rows}
        for item in owners:
            result[(kind, item)] = by_id.get(item.rsplit(":", 1)[0])

    for kind, table, column in (("project_state", "projects", "project_id"),
                                ("project_experience", "experiences", "experience_id")):
        owners = selected.get(kind, ())
        identifiers = sorted({item.rsplit(":", 1)[0] for item in owners})
        if not identifiers:
            continue
        rows = await (await connection.execute(
            f"SELECT {column} AS identity,actor_id FROM capability_projects.{table} WHERE {column}=ANY(%s)",
            (identifiers,))).fetchall()
        by_id = {row["identity"]: row["actor_id"] for row in rows}
        for item in owners:
            result[(kind, item)] = by_id.get(item.rsplit(":", 1)[0])
    return result


async def actors_for_source(connection, refs, reason):
    selected = await selected_dependencies(connection, refs, reason)
    owners = await dependency_actors(connection, selected)
    return tuple(sorted(set(owners.values()), key=lambda actor: (actor is not None, actor or "")))


async def withdraw(owner, connection, refs, reason="source_revoked", *, selected=None):
    if selected is None:
        selected = await selected_dependencies(connection, refs, reason)
    if not selected:
        return {"actions": 0, "artifacts": 0}
    rows = await (
        await connection.execute(
            "SELECT id,media_id FROM capabilities.artifacts WHERE id=ANY(%s) ORDER BY id FOR UPDATE",
            (sorted(selected.get("artifact", ())),),
        )
    ).fetchall()
    media_ids = [row["media_id"] for row in rows]
    artifact_ids = [row["id"] for row in rows]
    await connection.execute(
        "DELETE FROM capabilities.reaction_assets WHERE body->'media'->>'media_id'=ANY(%s)",
        (media_ids,),
    )
    await connection.execute(
        "UPDATE capabilities.configuration SET body=(body-'entries'-'source_refs')||jsonb_build_object('state','source_revoked','error_code',%s::text,'revision',revision+1),revision=revision+1 "
        "WHERE kind='catalog_source' AND id=ANY(%s) AND body->>'state' IS DISTINCT FROM 'source_revoked'",
        (reason, sorted(selected.get("catalog_source", ()))),
    )
    await connection.execute(
        "DELETE FROM capabilities.speech_cache WHERE key=ANY(%s)",
        (sorted(selected.get("speech_cache", ())),),
    )
    await connection.execute("DELETE FROM capabilities.artifacts WHERE id=ANY(%s)", (artifact_ids,))
    actions = await (
        await connection.execute(
            "SELECT id,action,attempt,receipt,document,result_revision FROM capabilities.actions WHERE id=ANY(%s) ORDER BY id FOR UPDATE",
            (sorted(selected.get("action", ())),),
        )
    ).fetchall()
    processed = 0
    for row in actions:
        if json.loads(row["document"]).get("source_revoked"):
            continue
        processed += 1
        receipt = redacted(receipt_from(row["receipt"]))
        if receipt.execution == "pending":
            receipt = replace(receipt, execution="cancelled", effect="none")
        receipt = replace(receipt, receipt_id=str(uuid4()))
        document = {**json.loads(row["document"]), "source_revoked": True, "cancel_requested": True}
        action = {**row["action"], "arguments": {}}
        observed = datetime.now(UTC)
        await connection.execute(
            "UPDATE capabilities.actions SET action=%s::jsonb,receipt=%s::jsonb,document=%s,result_revision=result_revision+1,result_observed_at=%s WHERE id=%s",
            (canonical(action), canonical(wire(receipt)), canonical(document), observed, row["id"]),
        )
        await connection.execute(
            "UPDATE capabilities.attempts SET receipt=jsonb_set(jsonb_set(receipt,'{artifacts}','[]'::jsonb),'{result}',%s::jsonb) WHERE action_id=%s",
            (canonical(receipt.result), row["id"]),
        )
        # Retain the original handle and output-node IDs for safe control queries;
        # neither prompt nor scene/reference content survives withdrawal.
        await connection.execute(
            "UPDATE capabilities.image_work SET body=jsonb_build_object('engine',body->'engine','compiled',jsonb_build_object('outputs',body->'compiled'->'outputs'),'source_revoked',true) WHERE action_id=%s",
            (row["id"],),
        )
        await owner.runtime.jobs.cancel(connection, row["id"])
        if owner.actions.events:
            await owner.actions.events.publish(
                connection,
                {
                    "type": "capability.result",
                    "version": 1,
                    "event_id": receipt.receipt_id,
                    "action_id": row["id"],
                    "result_revision": row["result_revision"] + 1,
                    "result_observed_at": observed.isoformat(),
                    "context": action["context"],
                    "source_refs": [],
                    "receipt": wire(receipt),
                },
            )
    await connection.execute(
        "DELETE FROM capabilities.consumptions WHERE action_id=ANY(%s)",
        ([row["id"] for row in actions],),
    )
    await connection.execute(
        "DELETE FROM capabilities.plugin_consumptions WHERE id=ANY(%s)",
        (sorted(selected.get("plugin_consumption", ())),),
    )
    return {"actions": processed, "artifacts": len(rows)}
