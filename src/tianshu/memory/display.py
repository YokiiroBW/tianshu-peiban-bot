"""Current Identity labels attached only to complete, readable evidence.

Labels describe the selected subject/speaker IDs. They neither assert new facts
nor supply historical identity evidence, and never expand the selected people.
"""

from collections.abc import Mapping
from dataclasses import replace

from . import codec
from .candidate_sources import SourceProofGroups, check_group_batches_on


def _get(value, name, default=None):
    return value.get(name, default) if isinstance(value, Mapping) else getattr(value, name, default)


def subject_ids(value):
    ids = {_get(value, "subject_id"), _get(value, "speaker_id")}
    # Relation snapshots have their own authors/subjects. Their sources already
    # belong to the enclosing indivisible evidence unit; do not follow new links.
    for link in _get(value, "relations", ()):
        for side in ("from_evidence", "to_evidence"):
            endpoint = _get(link, side)
            if endpoint:
                ids.update((_get(endpoint, "subject_id"), _get(endpoint, "speaker_id")))
    return tuple(sorted(item for item in ids if isinstance(item, str) and item))


async def resolve_display_on(identity, conn, context, values):
    selected = tuple(subject_ids(value) for value in values)
    requested = sorted({identifier for ids in selected for identifier in ids})
    people = {}
    for start in range(0, len(requested), 128):
        batch = requested[start:start + 128]
        rows = await identity.resolve_memory_subjects_on(conn, context, batch,
            include_group=any(identifier.startswith("group:") for identifier in batch))
        for row in rows:
            identifier = row["subject_id"]
            if identifier not in batch:
                continue  # Identity may also return the current group; it is not an extra candidate.
            name = row.get("display_name")
            internal = {identifier}
            if row["kind"] == "actor":
                internal.add(context.actor_id)
            elif row["kind"] == "group":
                internal.add(context.conversation_id)
            if not isinstance(name, str) or not name.strip() or name in internal:
                name = None  # An internal ID is not a known nickname.
            people[identifier] = {"subject_id": identifier, "display_name": name,
                "aliases": tuple(row.get("aliases") or ()), "kind": row["kind"]}
    return tuple(tuple(people[identifier] for identifier in ids if identifier in people) for ids in selected)


async def display_evidence_on(memory, conn, context, values):
    """Return aligned evidence or None, checking sources before resolving names.

    Both MemoryEvidenceUnit and the Sources raw-read mapping use this path.
    Display data remains inside its unit so withdrawal and whole-unit budgets
    cannot leave a detached name list behind.
    """
    values = tuple(values)
    if not values:
        return ()
    grouped, proofs = SourceProofGroups(), []
    for value in values:
        raw = _get(value, "source_refs")
        if raw is None:
            source = _get(value, "source_ref")
            raw = () if source is None else (source,)
        refs = codec.refs(raw)
        proofs.append(grouped.add(refs))
    checks = await check_group_batches_on(memory.sources, conn, context, grouped.values)
    allowed = [index for index, (value, proof) in enumerate(zip(values, proofs, strict=True))
        if all(checks[item] is True for item in proof) and set(_get(value, "scope_ids", ())) <= set(context.scope_ids)]
    labels = await resolve_display_on(memory.identity, conn, context, tuple(values[index] for index in allowed))
    result = [None] * len(values)
    for index, subjects in zip(allowed, labels, strict=True):
        value = values[index]
        result[index] = ({**value, "display_subjects": subjects} if isinstance(value, Mapping)
                         else replace(value, display_subjects=subjects))
    return tuple(result)
