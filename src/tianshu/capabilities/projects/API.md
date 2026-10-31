# Projects and knowledge public boundary

Implementation status: in progress; no execution or verification has run.

Construction is owned by `Capabilities`: `Projects(runtime, settings, authority, sources, artifacts, local)`, no independent process or authentication. Deployment config is `capabilities.projects.allowed_roots` (explicit managed absolute roots) and optional `git_executable`. No root scan, watching or repository writes. Migration name `capabilities-projects-0001` loads `projects/schema.sql`.

All routes are `/api/v1/projects/<operation>` POST, with actor/person/conversation selectors in the shared query channel. Reads return projections/Page; writes return the common CommandResult through the one Actions catalog. Config registration requires existing `config.publish`; project data reads/writes require `projects.read`/`projects.write`, explicit shared experience `projects.share`. An actual authorized actor and project scope are required; no project client tokens.

Read operations: `view`, `registry/get`, `documents`, `documents/read`, `documents/original`, `query`, `check`, `notes`, `notes/get`, `lessons`, `lessons/get`, `experiences`, `continuation/recover`, `continuation/check`, `state`. Mutations: `registry/save`, `directories/preview`, `directories/import`, `documents/import`, `documents/remove`, `notes/save`, `notes/withdraw`, `lessons/save`, `lessons/retire`, `experiences/promote`, `experiences/withdraw`, `state/save`. Directory preview persists a bounded immutable plan but imports nothing; import explicitly names that plan and selected files/tombstones. Large imports are durable elastic Actions, never a second task queue.

A ReadUnitRef is `{document_id,version,unit_id,sha256}`. A note citation is `{kind:'unit'|'note',reference:ReadUnitRef|{note_id,version,sha256}}`. Each item also returns complete authoritative SourceRefs; note/lesson transitive citations recheck source permission and current project versions. Bodies are stored once in Sources. Directory entries explicitly report `source_validation:'not_checked'`; real reads check disk/URL freshness and original Source authority before serving content.

CLI/MCP default read only and call these same HTTP operations with the current Identity session and CSRF; no old Core transport or project-token store. Worktree observation runs only fixed bounded read-only Git fact commands, never text-supplied commands.
