"""Same-source-transaction clearing of project derivatives at exact versions/ranges."""

from tianshu.sources.dependencies import changes_for, intersects
from ..contracts import canonical


async def withdraw(owner, conn, source_refs, reason):
    changes, _ = changes_for(source_refs)
    versions = sorted(changes)
    if not versions:
        return
    rows = await (
        await conn.execute(
            "SELECT owner_kind,owner_id,refs FROM capabilities.source_dependencies WHERE owner_kind=ANY(%s) AND (source_id,source_revision) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (
                [
                    "project_document",
                    "project_note",
                    "project_lesson",
                    "project_experience",
                    "project_state",
                    "project_continuation",
                    "project_import",
                ],
                [value[0] for value in versions],
                [value[1] for value in versions],
            ),
        )
    ).fetchall()
    selected = {}
    for row in rows:
        if intersects(row["refs"], changes, reason):
            selected.setdefault(row["owner_kind"], set()).add(row["owner_id"])
    project_ids = set()
    for kind, table, key in (
        ("document", "documents", "document_id"),
        ("note", "notes", "note_id"),
        ("lesson", "lessons", "lesson_id"),
    ):
        ids = [value.rsplit(":", 1)[0] for value in selected.get("project_" + kind, ())]
        if ids:
            projects = await (
                await conn.execute(
                    f"SELECT DISTINCT project_id FROM capability_projects.{table} WHERE {key}=ANY(%s)",
                    (ids,),
                )
            ).fetchall()
            project_ids.update(value["project_id"] for value in projects)
    project_ids.update(value.rsplit(":", 1)[0] for value in selected.get("project_state", ()))
    if project_ids:
        await conn.execute(
            "SELECT project_id FROM capability_projects.projects WHERE project_id=ANY(%s) ORDER BY project_id FOR UPDATE",
            (sorted(project_ids),),
        )
    # Source -> sorted project -> entity, shared with every project writer.
    for kind, table, key, version_key, head in (
        ("document", "document_versions", "document_id", "version", "documents"),
        ("note", "note_versions", "note_id", "version", "notes"),
        ("lesson", "lesson_versions", "lesson_id", "version", "lessons"),
    ):
        identifiers = selected.get("project_" + kind, ())
        if not identifiers:
            continue
        pairs = [value.rsplit(":", 1) for value in sorted(identifiers)]
        ids = [value[0] for value in pairs]
        revisions = [int(value[1]) for value in pairs]
        assignment = (
            "original_ref='{}',text_ref=NULL,metadata='{}',units='[]'"
            if kind == "document"
            else "payload='{}',citations='[]'"
            if kind == "note"
            else "payload='{}',evidence='[]'"
        )
        await conn.execute(
            f"UPDATE capability_projects.{table} SET state='source_revoked',source_refs='[]', {assignment} WHERE ({key},{version_key}) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (ids, revisions),
        )
        await conn.execute(
            f"UPDATE capability_projects.{head} SET state='source_revoked' WHERE ({key},current_version) IN (SELECT * FROM unnest(%s::text[],%s::bigint[]))",
            (ids, revisions),
        )
    if project_ids:
        await conn.execute(
            "UPDATE capability_projects.projects SET content_revision=content_revision+1 WHERE project_id=ANY(%s)",
            (sorted(project_ids),),
        )
    for identifier in sorted(selected.get("project_experience", ())):
        entry, revision = identifier.rsplit(":", 1)
        await conn.execute(
            "UPDATE capability_projects.experience_versions SET state='source_revoked',payload='{}',evidence='[]',source_refs='[]',disclosure_ref=NULL WHERE experience_id=%s AND revision=%s",
            (entry, int(revision)),
        )
        await conn.execute(
            "UPDATE capability_projects.experiences SET state='source_revoked',payload='{}',evidence='[]',source_refs='[]' WHERE experience_id=%s AND revision=%s",
            (entry, int(revision)),
        )
    for identifier in sorted(selected.get("project_state", ())):
        project, revision = identifier.rsplit(":", 1)
        await conn.execute(
            "UPDATE capability_projects.declared_states SET state='source_revoked',payload='{}',source_refs='[]',evidence='[]' WHERE project_id=%s AND revision=%s",
            (project, int(revision)),
        )
    await conn.execute(
        "DELETE FROM capability_projects.continuations WHERE pack_id=ANY(%s)",
        (sorted(selected.get("project_continuation", ())),),
    )
    await conn.execute(
        "UPDATE capability_projects.imports SET state='source_revoked',items='[]',result='{}' WHERE action_id=ANY(%s)",
        (sorted(selected.get("project_import", ())),),
    )
