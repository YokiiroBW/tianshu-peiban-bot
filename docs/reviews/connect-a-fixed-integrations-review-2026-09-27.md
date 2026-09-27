# CONNECT-A fixed integration review (2026-09-27)

## Decision

The reviewed code and isolated process tests support conditional acceptance of the
fixed Platform, Memory, Companion, and web-client changes as local integration
candidates. I found no additional blocking code defect in the reviewed paths. This
does not establish a production deployment: the formal persona package is still a
release candidate, and the non-persona U browser specs in this snapshot use mocked
same-origin APIs.

The principal fixed snapshots reviewed were:

| Product | Commit |
| --- | --- |
| Platform B | `9551871796f57d3369396023caf2b013a239c3a0` |
| Memory M browser/API base | `02df5ba8c041a7a6eb8b705c5f264e10543032ec` |
| Memory M knowledge HTTP additions | `bd0123bc7e242dc5a767347602f23957af9b2c33` |
| Companion persona producer | `31677983798ba27b24d57925feab4774c2eec30f` |
| Web client U | `faa5c6ba530b8a529e4c1e6e3e3d3ed42e414a8a` |

All process fixtures used synthetic records, credentials, and isolated local stores.
No NAS, production token, live account, real diary, asset service, or HA device was
used.

## Memory browser

The earlier B `347a15d`/M `0dd542a` pair had a concrete overview mismatch: B
required `subject_count`, while M returned the bounded `memory_group_count` and
`counts_truncated` fields. B commit `bc45342` fixes the consumer, and the final
`9551871` archive retains that change. Its `_read(overview)` validation now matches
the producer response.

The final B/M `tests/backend/test_memory_joint.py` passed (**1 passed**). It ran the
real Platform HTTP application with login/session, a real Memory HTTPS process, and
a synthetic Platform issuer; it read a nonempty overview and scoped subjects and
records. This closes the API mismatch. It is not a Chromium or NAS test. The earlier
review details and post-barrier source-authority checks are recorded in
[`connect-a-memory-browser-review-2026-09-27.md`](connect-a-memory-browser-review-2026-09-27.md).

## Project knowledge

M `knowledge_http.py` adds only four HTTP operations—lesson query, experience query,
continuation recover, and continuation check—and separates the HTTP opt-in
`http_read_operations` from the domain operation permissions. Missing/empty HTTP
grants fail closed; experience search still needs its independent `review`
permission. B fixes the Platform project and checkout sets server-side, forwards
only the registered operation arguments, and keeps continuation packages in a
bounded session/project handle. The browser sees a handle and a redacted summary,
not the original seal, filesystem path, or package. These checks were reviewed in
M `knowledge_http.py`, B `web_readers.py`, and their fixed handoffs.

M's exact `tests/test_knowledge_http.py` and `tests/test_knowledge_http_extra.py`
passed (**45 passed**), including allowlist separation, evidence scoping, checkout
alias validation, and suppression of a result if the HTTP grant is revoked while
the operation is in flight.

B's `tests/backend/test_knowledge_joint.py` passed (**4 passed**) against the real
Memory `knowledge_cli serve` HTTPS process and real Platform web routes, with
synthetic project documents, research notes, lesson/experience data, and checkout.
It covers the lesson and experience searches, document list/read, note query,
continuation recover/check, and a disabled-operation response. U's fixed browser
tests cover the page and scope transitions, but their API responses are routed
through Playwright mocks. No actual U browser → B → M Knowledge chain is established
by those specs.

## Companion life and diaries

B binds one HTTPS Companion endpoint to an independent reader credential and CA.
The Companion reader is separately narrowed by its reader/actor allowlist and
diary authorization. Platform exposes actors, persisted snapshot, diary list, and
published revision reads; it does not advance life state or write diaries.

B's `CONNECT-B-LIFE.md` records **1/1 passed** for `test_life_joint.py` against the
fixed Companion producer `31677983798ba27b24d57925feab4774c2eec30f`. The test uses a
real Companion uvicorn HTTPS process and a real Platform HTTP login/route, synthetic
fictional rows, and verifies all four reads, stale-version 409, unauthorized actor
404, revoked Platform permission 403, and unchanged Companion database rows. It is
backend process evidence; U's corresponding UI tests use mock responses. It does
not verify a browser-to-live-Companion path or production reader setup.

## External AssetLink and Home Assistant settings

B's connection editor is limited to the two registered peer types; it is not a
generic URL proxy. The reviewed code validates the peer URL and fixed route,
constrains private addresses to explicit CIDRs, pins resolved addresses for the
outbound request, and rechecks the current address policy. HA reads do not create
control templates. Credentials and CA data use AES-GCM in the private catalog;
save/test receipts are revision-bound and guarded by SQLite compare-and-swap.
Responses omit the secret fields. The external handoff correctly states that
Windows ACLs must be configured separately: the source's `chmod` calls do not
prove effective Windows access control.

B's `tests/backend/test_web_external.py` passed (**4 passed**) against a real local
Platform HTTP app and synthetic AssetLink HTTPS/HA HTTP peers. The cases include
credential and CA rotation, invalid public target/key refusal, tamper detection,
expiry/permission checks, and an in-flight test racing a new configuration
revision. No real external service was contacted. U's `external-connections.spec.ts`
tests a concurrent configuration revision in the UI with mocked responses, not a
browser flow against B.

## Web client and persona joint acceptance

The fixed U build type-checks and builds, with the reported existing large-chunk
warning. Its selected `integration-pages`, `scope-transitions`, and
`external-connections` Playwright specs passed **22 tests** across desktop and
mobile Chromium projects. They verify rendered states, stale-scope suppression,
and the connection revision race, but these specs intercept the same-origin API;
they are UI evidence only.

I added a separate test harness at
[`tests/persona_acceptance/test_published_chromium.py`](../../tests/persona_acceptance/test_published_chromium.py)
and [`persona_browser.mjs`](../../tests/persona_acceptance/persona_browser.mjs).
It passed **1/1** against the exact B `9551871` app, U `faa5c6` build, and current
Companion producer `3167798`. It started real Platform and Companion HTTPS
listeners, seeded synthetic persona history through the producer's operator API,
then used Chromium to log in, read the allowlisted catalog/history/revision, and
render a four-field comparison. A producer-side actor outside the Platform
allowlist stayed absent. The browser called only the same-origin Platform persona
routes; no API route was mocked, and captured API request/response bodies contained
no upstream token, endpoint, CA path, connection id, database name, package path,
or hidden actor. Both listeners used generated loopback certificates; Playwright
ignored the Platform certificate warning, while Platform validated the Companion
certificate against its configured CA.

The reproduction command was:

```powershell
& .runtime/connect-a-b-env/venv/Scripts/python.exe -m unittest discover -s tests/persona_acceptance -p test_published_chromium.py -v
```

For this test only, the harness copied the formal v1 package to `.runtime`, changed
the copy's publication metadata, recomputed that copy's manifest digest, and patched
the pinned digest only in the isolated Python process. The formal manifest was not
edited; its current SHA-256 remains
`4bb6038fc5a6fb43ebb08aa9664171dc2ae81db3893f3afe53de597b9a7f212b` and its fields
remain `status=release_candidate`, `production_publish_authorized=false`, and
`joint_runtime_acceptance=pending`. Therefore this proves the B published-mode
loader and real page path against candidate file bytes under test-only published
metadata. It is not formal publication evidence and must not be recorded as a
production approval.

## Remaining work

- The coordinator still owns any change to the root v1 manifest and must decide
  whether to publish it using the test evidence above.
- B's fixed production manifest pin is intentionally `None`; after formal publication,
  B needs to pin the approved manifest digest and repeat its published-mode check.
- Production reader registrations, credentials, CA files, service ACLs, real project
  data, and real external endpoints remain unverified.
- The U browser evidence for Memory, Knowledge, Life, and external settings is not a
  real-browser backend integration. Use the coordinator's combined integration run
  for that scope; do not infer it from mocked UI specs.
