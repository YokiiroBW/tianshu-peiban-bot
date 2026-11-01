# MC-A Memory domain implementation

Status: implementation in progress; no tests, typecheck, build, model or live acceptance run.

## Frozen MC-A / MC-B interface

`ClaimService.revisions.apply_operations_on(conn, context, *, batch_id, frozen, operations)`
uses the caller's existing transaction and returns a JSON-compatible mapping with
`local_refs`, `changed`, `affected_subject_ids` and `semantic_link_ids`.

`frozen` is a mapping with `subjects`, `materials`, `records`, `read_set`.
Subjects are a server-verified ID map. Material IDs map to full `source_refs`,
`speaker_id`, `origin_kind`, `occurred_at` and role. Record IDs map to
`{kind,id,revision,subject_id,scope_ids,...}`. `reads` are frozen record aliases;
support groups and context refs are frozen material aliases. Link endpoints may
be frozen record aliases or proposal-local creates. IDs are never interpreted
using refreshed array order. The full read_set is checked, including objects
the model did not name in its operations.

MC-B locks and rechecks all frozen source dependencies, then partition/batch,
and calls this domain service. It locks existing domain objects in stable
kind/ID order and checks expected revisions. It does not commit or advance
cursors. Every created ID is deterministic from actor/batch/local identity.
MC-B commits coverage, cursor, commit refs and batch applied in that transaction.

`ClaimService.revisions.source_changed_on(conn, sources, kind)` cleans domain
history, relations and their projections; MC-B calls it from its source callback.

Named migration: `0021_memory_consolidation`; four tables and all fields follow
blueprint sections 5.2–5.5. Batch rows also include created_at/updated_at.
Legacy recorded_at remains NULL. Old contradiction arrays remain audit data
and are not migrated to semantic links.
