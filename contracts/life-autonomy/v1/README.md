# Life autonomy v1

This additive contract connects Companion's existing daily routine, activity and diary
owners to Platform management. It does not replace `life-runtime/v2` or `life-read/v1`.
The package is a local implementation candidate until joint acceptance and coordinator
integration are recorded separately.

## Transport and access

Companion exposes `POST /internal/v1/life-autonomy/read` and `/manage` to the existing
trusted Platform management service. Read queries retain the registered query origin
and role/story grants. These resources include unpublished drafts and are management
resources, never ordinary published-diary reader resources. Disabled roles remain
manageable; reading or configuring a routine does not enable the role.

Platform exposes same-origin `POST /api/web/life/autonomy/read` and `/manage` through
the existing authenticated session, role management and life read permissions.
Writes retain the existing Origin/CSRF, idempotency and actor scope checks. Browser
requests never contain a service credential or choose an upstream address.

## Routine and execution

`routine` returns one role's effective local-day schedule, generation availability,
diary setting and current phase/activity association. Phase time and plan generation
are separate from activity execution; a completed plan is not evidence of completed
reading, writing or other external work. Nullable current fields represent absence.
Room controls remain the existing fictional room controls, each in [0,1].

`routine.configure` uses the actor version for compare-and-swap and requires 1–24
schedule entries with distinct civil minutes. The server normalizes order and omitted
controls. `diary_enabled: null` inherits the autonomous role default; an explicit
global diary disable still takes precedence. Existing custom schedules are preserved
until deliberately edited. This operation does not grant tools or messaging rights.

Routine transitions do not discard persistent work checkpoints. A suspended activity
and its original tool receipts remain available for recovery. Unknown tool outcomes
are not silently replayed. Error codes are bounded classified values, not exception
messages, provider responses or private material.

## Diary management

`diaries` lists management summaries in stable descending (day,id) order with an opaque
cursor. `diary_revision` reads an immutable revision by `object_id`, subject to the same
management role scope. All response objects echo `query.request_id`.

Automatic generation produces a fictional **draft** only. Existing `life-runtime/v2`
`diary.request`, `diary.revise` and `diary.publish` remain the generation, editing and
explicit publication paths. `diary.retry` uses the diary version and requests a new
safe generation attempt on that diary owner. It cannot duplicate a publication or
replay an activity tool. Exact manage retries return the recorded operation result;
changed payloads under the same request identity conflict.

`material_capture` reports eligible selection, private exclusions and budget omissions.
`selection: bounded` does not claim a complete day's record. `material_version` binds
the immutable captured material; generated prose does not prove real-world execution.
Published readers continue to use the existing publication pointer and access rules.

## Schema and validation

`schemas/autonomy.json` provides `read_request`, `read_response`, `manage_request`,
`manage_response` and the closed projections. Query identity references the unchanged
`text-dialogue/v1` common schema. Package hashes use UTF-8 bytes with CRLF normalized
to LF, as in the existing runtime loader. Examples are synthetic and contain no
production actor, conversation, credential or diary material.
