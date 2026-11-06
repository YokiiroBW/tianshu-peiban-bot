"""Canonical evidence sets, with proof evaluated against current source authority."""

from collections.abc import Mapping
from itertools import islice

from psycopg.types.json import Jsonb

from tianshu.runtime.commands import body_hash
from tianshu.runtime.errors import AppError
from tianshu.runtime.serialization import wire
from tianshu.types import SourceRef
from .types import EvidenceSetRef, EvidenceSpec
from .rbj_budget import SourceBudget
from .rbj_locking import evidence_manifests_on


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


def _charge_spec(spec, budget):
    if not isinstance(spec, EvidenceSpec):
        raise AppError("invalid_evidence_set")
    for group in (spec.required, spec.generation_dependencies):
        budget.charge(inputs=1 + len(group), work=1 + len(group))
    for group in spec.alternative_supports:
        budget.charge(inputs=1 + len(group), work=1 + len(group))


async def register_on(service, conn, context, spec):
    budget = SourceBudget()
    _charge_spec(spec, budget)
    spec = normalize(spec)
    slots = len(spec.required) + len(spec.generation_dependencies) + sum(
        len(group) for group in spec.alternative_supports
    )
    all_refs = _refs((*spec.required, *spec.generation_dependencies,
                      *(ref for group in spec.alternative_supports for ref in group)))
    if not await service._validate_persistence(conn, context, all_refs, budget):
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
    references = tuple(islice(references, 1025))
    if len(references) > 1024:
        raise AppError("source_batch_too_large")
    if any(not isinstance(ref, (SourceRef, EvidenceSetRef)) for ref in references):
        raise AppError("invalid_evidence_reference")
    budget = SourceBudget()
    budget.charge(inputs=len(references), work=len(references))
    ids = sorted({ref.evidence_id for ref in references if isinstance(ref, EvidenceSetRef)})
    rows = await evidence_manifests_on(conn, ids, budget) if ids else ()
    manifests = {row["evidence_id"]: row["manifest"] for row in rows}
    selections, plans, prepared = [], [], {}
    logical_selections = 0
    for ref in references:
        if isinstance(ref, SourceRef):
            logical_selections += 1
            if logical_selections > 1024:
                raise AppError("source_batch_too_large")
            plans.append((len(selections), 0))
            selections.append((ref,))
            continue
        if ref.evidence_id in prepared:
            plan = prepared[ref.evidence_id]
            if plan is not None:
                logical_selections += 1 + plan[1]
                if logical_selections > 1024:
                    raise AppError("source_batch_too_large")
            plans.append(plan)
            continue
        manifest = manifests.get(ref.evidence_id)
        if manifest is None:
            prepared[ref.evidence_id] = None
            plans.append(None)
            continue
        original = EvidenceSpec(**manifest)
        _charge_spec(original, budget)
        spec = normalize(original)
        logical_selections += 1 + len(spec.alternative_supports)
        if logical_selections > 1024:
            raise AppError("source_batch_too_large")
        plan = (len(selections), len(spec.alternative_supports))
        prepared[ref.evidence_id] = plan
        plans.append(plan)
        selections.append((*spec.required, *spec.generation_dependencies))
        selections.extend(spec.alternative_supports)
    if lock:
        await service.identity.lock_context_on(conn, context)
        await service._lock_selected(conn, _refs(ref for group in selections for ref in group), budget=budget)
    allowed = await service._check_with_budget(conn, context, tuple(selections), budget)
    return tuple(
        False if plan is None else
        bool(allowed[plan[0]] and (plan[1] == 0 or any(
            allowed[plan[0] + offset + 1] for offset in range(plan[1])
        )))
        for plan in plans
    )
