# Persona authoring candidate v1

Status: **candidate**. This package documents the Platform → Companion authoring addition to
`POST /internal/v1/persona/manage`. It does not replace or alter the published
`persona-management/v1` read contract. Production rollout requires coordinator review and
explicit deployment configuration.

Companion owns reusable profiles, metadata, immutable revisions, operation ledger and published
role pointers. A profile has a generated `persona-profile:<uuid>` identity in the same Personas
store. Creating it never registers a conversational role. Platform sends only a fixed operation
document over registered HTTPS with a dedicated Companion management credential. The browser
never receives that credential or a service address.

## Operations

`author_catalog` lists reusable profile summaries; `author_view` returns one profile or
registered role with its current draft if present, otherwise its published content.
`create_profile` writes a reusable draft; `save_profile` and `save_role` write drafts and metadata.
When copying, `create_profile.source` and `source_expected` read the selected revision inside
the same transaction and preserve extension fields that the four-field browser form does not
show. A stale copy source is rejected.
`apply_profile` copies a profile revision into an existing registered target role; `apply_role`
edits that role directly. Both apply operations create a revision, append an explicit approval,
publish the pointer and record one idempotent result inside one Companion SQLite transaction.
There is no sequence of browser approve/publish requests.

Writes require `request_id`, `operator`, `reason` and optimistic version fields. The operation
identity is `(authenticated scope, request_id, operation)` and binds the normalized request.
An exact retry returns its first result without another publication; another request with a stale
version receives `version_conflict`. Metadata is not copied into the persona prompt. Four editor
fields are `persona`, `tone`, `style`, `address`; existing scalar extension fields remain in the
revision when the form updates those four fields. An omitted optional editor field clears that
field on application; it does not inherit the target role's old value. The profile summary records
`last_applied_target_revision`, the exact target publication created by its last application.
The web catalog compares that pointer with the authorized target's current published pointer to
show "current" or "previous" application status. A published revision affects only turns prepared
after publication; previously pinned turns retain their immutable snapshot.

## Web projection and permission

Same-origin `/api/web/personas/{profiles,view,create,save,apply}` uses existing authenticated
Cookie, Origin and CSRF checks. `web_personas.authoring_enabled` is false by default.
`persona.read` is required for every authoring read. Creation, editing and application each need
their own operator action: `persona.create`, `persona.edit`, `persona.apply`; application of edited
content needs both edit and apply. `apply_subjects` is an explicit subset of the old closed
`allowed_subjects`; only those registered roles may be edited or targeted. Creating a profile
does not widen either set. Platform checks live session and scope before and after the upstream
request. A lost upstream response can be retried with the same browser `client_id`.

The existing four read routes and their published contract stay available. The authoring
operations are not valid requests under that read contract. This candidate does not change the
source, model, bot or sending policies, and adds no database table or migration.
