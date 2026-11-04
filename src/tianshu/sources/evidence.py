"""Canonical evidence sets, with proof evaluated against current source authority."""

from collections.abc import Mapping

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef
from .types import EvidenceSetRef, EvidenceSpec


def _refs(values):
    selected = {}
    for item in values:
        ref = SourceRef(**item) if isinstance(item, dict) else item
        if (not isinstance(ref, SourceRef) or not isinstance(ref.source_id, str)
            or type(ref.revision) is not int or ref.revision < 1
            or not isinstance(ref.locator, Mapping)):
            raise AppError("invalid_source_reference")
        selected[body_hash(ref)] = ref
    return tuple(selected[key] for key in sorted(selected))


def normalize(spec):
    if not isinstance(spec, EvidenceSpec):
        raise AppError("invalid_evidence_set")
    required = _refs(spec.required)
    generation = _refs(spec.generation_dependencies)
    groups = {}
    for group in spec.alternative_supports:
        members = _refs(group)
        if not members:
            raise AppError("invalid_evidence_set")
        groups[body_hash(members)] = members
    alternatives = tuple(groups[key] for key in sorted(groups))
    if not required and not generation and not alternatives:
        raise AppError("invalid_evidence_set")
    if sum(map(len, alternatives)) + len(required) + len(generation) > 2048:
        raise AppError("source_batch_too_large")
    return EvidenceSpec(required, alternatives, generation)


async def register_on(service, conn, context, spec):
    spec = normalize(spec)
    slots = len(spec.required) + len(spec.generation_dependencies) + sum(
        len(group) for group in spec.alternative_supports
    )
    all_refs = _refs((*spec.required, *spec.generation_dependencies,
                      *(ref for group in spec.alternative_supports for ref in group)))
    if not await service.validate_persistence(conn, context, all_refs):
        raise AppError("source_unavailable", status=403)
    if slots == 1:
        return all_refs[0]
    manifest = wire(spec)
    evidence_id = "evidence_" + body_hash(manifest)[:48]
    await conn.execute(
        "INSERT INTO sources.evidence_sets(evidence_id,manifest) VALUES(%s,%s) "
        "ON CONFLICT DO NOTHING", (evidence_id, Jsonb(manifest)),
    )
    row = await (await conn.execute(
        "SELECT manifest FROM sources.evidence_sets WHERE evidence_id=%s", (evidence_id,)
    )).fetchone()
    if row["manifest"] != manifest:
        raise AppError("evidence_identity_conflict", status=409)
    groups = [("required", 0, spec.required), ("generation", 0, spec.generation_dependencies)]
    groups.extend(("alternative", index, group) for index, group in enumerate(spec.alternative_supports))
    for role, index, refs in groups:
        for ref in refs:
            await conn.execute(
                "INSERT INTO sources.evidence_members(evidence_id,role,group_index,source_id,revision,locator,ref_key) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (evidence_id, role, index, ref.source_id, ref.revision,
                 Jsonb(wire(ref.locator)), body_hash(ref)),
            )
    return EvidenceSetRef(evidence_id)


async def prove_many_on(service, conn, context, references, *, lock=False):
    references = tuple(references)
    if len(references) > 1024:
        raise AppError("source_batch_too_large")
    if any(not isinstance(ref, (SourceRef, EvidenceSetRef)) for ref in references):
        raise AppError("invalid_evidence_reference")
    ids = sorted({ref.evidence_id for ref in references if isinstance(ref, EvidenceSetRef)})
    rows = await (await conn.execute(
        "SELECT evidence_id,manifest FROM sources.evidence_sets WHERE evidence_id=ANY(%s)", (ids,)
    )).fetchall() if ids else ()
    manifests = {row["evidence_id"]: row["manifest"] for row in rows}
    selections = []
    plans = []
    for ref in references:
        if isinstance(ref, SourceRef):
            plans.append((len(selections), 0))
            selections.append((ref,))
            continue
        manifest = manifests.get(ref.evidence_id)
        if manifest is None:
            plans.append(None)
            continue
        spec = normalize(EvidenceSpec(**manifest))
        plans.append((len(selections), len(spec.alternative_supports)))
        selections.append((*spec.required, *spec.generation_dependencies))
        selections.extend(spec.alternative_supports)
    if len(selections) > 1024:
        raise AppError("source_batch_too_large")
    if lock:
        await service.identity.lock_context_on(conn, context)
        await service._lock_selected(conn, _refs(ref for group in selections for ref in group))
    allowed = await service.check_many_on(conn, context, tuple(selections))
    return tuple(
        False if plan is None else
        bool(allowed[plan[0]] and (plan[1] == 0 or any(
            allowed[plan[0] + offset + 1] for offset in range(plan[1])
        )))
        for plan in plans
    )
