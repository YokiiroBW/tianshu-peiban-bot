"""Same-source-transaction clearing of project derivatives at exact versions/ranges."""

async def withdraw(owner, conn, source_refs, reason, *, selected=None):
    if selected is None:
        from ..withdrawal import selected_dependencies
        selected = await selected_dependencies(conn, source_refs, reason)
    selected = {kind: set(values) for kind, values in selected.items() if kind.startswith("project_")}
    # Replaying one invalidation does not manufacture another project revision.
    for kind, table, key, version_key in (
        ("project_document", "document_versions", "document_id", "version"),
        ("project_note", "note_versions", "note_id", "version"),
        ("project_lesson", "lesson_versions", "lesson_id", "version"),
        ("project_experience", "experience_versions", "experience_id", "revision"),
        ("project_state", "declared_states", "project_id", "revision"),
    ):
        pairs = [item.rsplit(":", 1) for item in sorted(selected.get(kind, ()))]
        if not pairs:
            continue
        rows = await (await conn.execute(
            f"SELECT {key} AS identity,{version_key} AS version FROM capability_projects.{table} "
            f"WHERE ({key},{version_key}) IN (SELECT * FROM unnest(%s::text[],%s::bigint[])) "
            "AND state IS DISTINCT FROM 'source_revoked'",
            ([item[0] for item in pairs], [int(item[1]) for item in pairs]))).fetchall()
        selected[kind] = {str(row["identity"]) + ":" + str(row["version"]) for row in rows}
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
