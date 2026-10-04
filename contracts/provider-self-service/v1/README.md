# Provider self service v1 — candidate wire contract

This contract is for the local integration branch. It is frozen for the web client at
`v1`; implementation conformance and production activation are separate acceptance gates.
The platform owns the catalog, encryption key, default pointer, publications and grants.
The gateway owns upstream execution. The companion chooses a version at new-turn admission.
No product opens another product's database.

## Browser to platform

All operations are `POST /api/web/providers/<operation>` with JSON objects. They use the
existing `/api/web/session` cookie, exact configured `Origin`, `X-CSRF-Token`, `Content-Type:
application/json`, same-host check and 16 KiB body bound. A live administrator login is
required. `/api/web/models/unlock` with `{ "password": "..." }` must first establish the
existing server-side management lease; `/api/web/models/lock` ends it. No request or response
puts a credential in URL, headers, browser storage, diagnostics, or a public projection.
`models/view` and the ordinary session state remain compatible. The browser uses these
endpoints regardless of whether the static template feature is configured.

| Operation | Exact request fields | Successful response |
| --- | --- | --- |
| `view` | `{}` | `{ "providers": [Provider], "default": Default }` |
| `save` | `{ "client_id": UUID, "provider_id"?: string, "expected_revision"?: integer, "name": string, "protocol": "openai-chat-completions", "base_url": HTTPS_URL, "model_id": string, "enabled": boolean, "api_key"?: string }` | `Provider` |
| `clear-key` | `{ "client_id": UUID, "provider_id": string, "expected_revision": integer }` | `Provider` |
| `delete` | `{ "client_id": UUID, "provider_id": string, "expected_revision": integer }` | `{ "provider_id": string, "deleted": true }` |
| `models` | `{ "provider_id": string, "expected_revision": integer }` | `{ "provider_id": string, "revision": integer, "models": [string] }` |
| `test` | `{ "client_id": UUID, "provider_id": string, "expected_revision": integer }` | `{ "provider_id": string, "revision": integer, "outcome": TestOutcome, "tested_at": number }` |
| `default` | `{ "client_id": UUID, "provider_id": string, "expected_revision": integer, "expected_default_revision": integer }` | `{ "provider_id": string, "provider_revision": integer, "revision": integer }` |

`Provider` is exactly `{ provider_id, name, protocol, base_url, model_id, enabled,
revision, has_key, test }`. `test` is `null` or `{ revision, outcome, tested_at }`.
Failed-test views may also include `test.error_code`, a fixed error from the settled
receipt for the same provider revision. It distinguishes an explicit service refusal
from transport loss while `outcome` retains the execution semantics. Upstream text,
HTTP details, and provider-specific reasons stay in the private receipt.
`Default` is exactly `{ provider_id: string|null, revision, provider_revision:
integer|null, configured: boolean }`. `configured` is a catalog state and does not by
itself claim that gateway/companion is ready. No response contains `api_key`, ciphertext,
credential reference, private network target, or upstream text. A saved URL is visible only
to the unlocked administrator; it must never be echoed into errors or diagnostics.

`save` creates when both optional identity fields are absent. Edit requires both; an
omitted or empty `api_key` retains the existing key. `clear-key` is the only key removal.
An empty `model_id` may be saved so model enumeration/manual entry can follow. Save never
sends a paid generation request. `models` is an explicit upstream GET; `test` is one
explicit short completion with fixed test text and at most 16 output tokens. Enumeration
does not mark test success. An unsupported enumeration is a typed error; manual `model_id`
remains available. `default` requires a successful test for that exact revision.

Every mutation uses a fresh UUID `client_id`; replay of that ID and identical fields
returns the original receipt without another write or paid request, including after restart.
For `test`, a process lost after claiming the UUID but before the verdict transaction
commits returns `409 result_unknown` on replay: the upstream effect cannot be inferred and
the same UUID never submits it again.
Reuse with different fields is `409 idempotency_conflict`. Edit/clear/delete/test/default
CAS on the exact provider revision; `default` also CASes the pointer revision. A changed
provider revision invalidates its test and default. Failed or cancelled tests never record
success. A client cancellation has unknown upstream execution state; the UI must not retry
automatically. Test verdict and replay receipt commit in one SQLite transaction. Timeout,
connection loss, invalid upstream response or an interrupted call records `test.outcome` as
`unknown`; a fixed error code remains available for the UI. The server checks the live login,
current operator authority and management lease again before queued work and before reply.

Errors are JSON `{ "schema_version": 1, "request_id": string, "code": string,
"execution_state": "not_started"|"unknown", "retryable": boolean }` with no
upstream body, header, URL or key. Common status/code: 400 `invalid_input`, 401
`unauthorized`/`session_expired`, 403 `forbidden`/`management_required`, 404
`provider_not_found`, 409 `revision_conflict`/`default_revision_conflict`/
`idempotency_conflict`/`provider_not_tested`/`provider_unavailable`/`result_unknown`, 413
`budget_exceeded`, 429 `too_many_requests`, 503 `dependency_unavailable`.
Upstream operation errors are fixed codes `authentication_failed`, `endpoint_failed`,
`model_not_found`, `enumeration_unsupported`, `connection_failed`, `timed_out`,
`upstream_invalid`, `upstream_rejected`; their status is 502, 503 or 504 as appropriate.
The UI may show a retry button only for an explicit new user action. On timeout/cancel
or an uncertain test response, `execution_state` is `unknown`; replay with the same
`client_id` never submits another paid call.

## Internal service binding

Internal routes are not browser routes. All accept a dedicated service Bearer credential,
enforce a fixed service identity and workload, reject arbitrary provider/version input
from the browser, and return bounded JSON with fixed errors. Platform service identity
tokens and gateway identity tokens are distinct. The platform may be HTTP on the existing
private LAN; then service credentials and upstream API keys cross that LAN in cleartext.
Deployment must isolate that network or add TLS without imposing HTTPS on the browser.

* Companion `POST /internal/v1/provider-self-service/select` on platform:
  `{turn_id, actor_id, person_id, audience, conversation_id,
  caller_service:"companion", workload:"companion.text"}`. The platform verifies the
  Bearer service identity and scope, selects the current tested default, atomically
  ensures its publication and exact-version grant, and returns
  `{config_version, expires_at, revoked:false, caller_service:"companion",
  workload:"companion.text"}`. `expires_at` is epoch seconds. The companion persists
  this version with the first accepted input's collection before acknowledging that input,
  then transfers it to the queued turn before Memory/generation. Later default changes do
  not repin it. If selection fails, the input is not accepted.
* Gateway `POST /internal/v1/provider-self-service/runtime` on platform:
  `{config_version, caller_service, workload, turn_id}`. The platform
  authenticates the gateway, checks the exact published version, bound provider revision,
  turn ID and service/workload grant, live lease and revocation, then returns the private
  `{config_version, provider_id, provider_revision, protocol, base_url, model_id,
  api_key, usable_until}`. This response is never logged or cached beyond the request.
  The gateway reauthorizes after queue wait and immediately before upstream submission.
* Platform `POST /internal/v1/provider-self-service/models|test` on gateway:
  `{provider_id, revision, protocol, base_url, model_id, api_key}`; gateway authenticates
  only the platform service and applies public-HTTPS target policy, DNS pinning, TLS
  verification, no redirect, bounded response, timeout and cancellation. `models`
  returns `{models:[string]}`. `test` returns `{outcome:"succeeded"}` only after a real
  valid nonempty completion, otherwise a fixed error code. The platform alone writes
  `record_test` with CAS after receiving this trusted result.

The platform records immutable `(config_version, provider_id, provider_revision)`
publications and `(turn_id, scope_digest, config_version, caller_service, workload)`
grants. Repeating a turn selection returns its original live grant or fails if that
grant expired; it never repins the turn to a changed default. Replacing a key or URL never silently substitutes
new credentials into a pinned old turn. Old versions may finish only while the bound
credential revision and grant remain live; edit/disable/delete makes affected versions
unusable through the revision check. New defaults get bounded one-hour leases renewed on
demand before expiry; users do not republish daily. A renewal makes
a new immutable version for future turns. Revocation is checked again after queueing.
An expired or revoked pinned turn fails rather than selecting the new default.

Static deployments keep their existing `config_versions`, secret references and routes.
Dynamic grants are exact versions and cannot be a wildcard or all-version fallback.
Gateway execution continues through the existing scheduler, diagnostics, SSE and
receipt path; an adapter-only `complete` call is not that path.
