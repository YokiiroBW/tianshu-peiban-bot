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
    for ref in refs:
        await connection.execute(
            "INSERT INTO capabilities.source_dependencies(owner_kind,owner_id,source_id,source_revision) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
            (kind, identifier, ref.source_id, ref.revision),
        )


def redacted(receipt):
    retained = {
        key: receipt.result[key]
        for key in ("usage", "billing_source", "model", "provider_handle", "generated_at")
        if key in receipt.result
    }
    retained.update(
        source_revoked=True,
        source_refs=[],
        sharing={"decision": "archive", "reason": "source_revoked"},
    )
    return replace(
        receipt, artifacts=(), coverage=None, result=retained, error_code="source_revoked"
    )


async def withdraw(owner, connection, refs):
    refs = tuple(refs)
    identifiers = list({ref.source_id for ref in refs})
    if not identifiers:
        return {"actions": 0, "artifacts": 0}
    # Match explicit SourceRefs only; arbitrary JSON identifiers are never sources.
    rows = await (
        await connection.execute(
            "SELECT id,media_id FROM capabilities.artifacts WHERE id IN (SELECT owner_id FROM capabilities.source_dependencies WHERE owner_kind='artifact' AND source_id=ANY(%s)) FOR UPDATE",
            (identifiers,),
        )
    ).fetchall()
    media_ids = [row["media_id"] for row in rows]
    artifact_ids = [row["id"] for row in rows]
    await connection.execute(
        "DELETE FROM capabilities.reaction_assets WHERE body->'media'->>'media_id'=ANY(%s)",
        (media_ids,),
    )
    await connection.execute(
        "DELETE FROM capabilities.speech_cache WHERE key IN (SELECT owner_id FROM capabilities.source_dependencies WHERE owner_kind='speech_cache' AND source_id=ANY(%s))",
        (identifiers,),
    )
    await connection.execute("DELETE FROM capabilities.artifacts WHERE id=ANY(%s)", (artifact_ids,))
    actions = await (
        await connection.execute(
            "SELECT id,action,attempt,receipt,document,result_revision FROM capabilities.actions WHERE id IN (SELECT owner_id FROM capabilities.source_dependencies WHERE owner_kind='action' AND source_id=ANY(%s)) FOR UPDATE",
            (identifiers,),
        )
    ).fetchall()
    for row in actions:
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
        "DELETE FROM capabilities.consumptions WHERE source_id=ANY(%s)", (identifiers,)
    )
    await connection.execute(
        "DELETE FROM capabilities.plugin_consumptions WHERE EXISTS (SELECT 1 FROM jsonb_array_elements(coalesce(event->'source_refs','[]'::jsonb)) ref WHERE ref->>'source_id'=ANY(%s))",
        (identifiers,),
    )
    return {"actions": len(actions), "artifacts": len(rows)}
