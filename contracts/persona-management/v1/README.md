# Persona readonly v1 release preparation

Status: release_candidate; production is not enabled until coordinator records actual joint acceptance and publishes the final manifest. This is a separate package; both historical candidates remain unchanged.

Wire operations are get, history_page, revision and compare at POST /internal/v1/persona/manage. Existing Companion is the authority. Platform must authenticate its browser Cookie/CSRF session, require persona.read, bind the fixed subject allowlist before and after awaits, validate request/response association, scope, version and limits, and project redacted results. Credentials remain server-side. No writes, global directory enumeration or browser-supplied authority are granted by this read contract.

Schemas preserve candidate-v1-r2 wire semantics, including imported=null or positive integer; changes are schema identifiers and titles only. The 29 historical samples remain synthetic evidence from producer 0a775631, not new samples from the current producer. Target producer for new joint verification is 31677983798ba27b24d57925feab4774c2eec30f. Do not mark those samples as current HTTPS observations.

History pages are version bound; scope withdrawal and version conflicts require a fresh first page. Validate whole-content comparison and four-field comparison separately. Bound history to 256 KiB and other upstream responses to 1 MiB, cursor to 2048 characters, history limit to 1..100, default 20. Text is data, never HTML or instructions. Missing authorization is not empty data.

Release requires real local HTTPS producer plus Platform plus Chromium testing on fixed revisions: directory/history/revision/compare, changed version, withdrawal during late response, malformed/oversized peer response, browser logout and service authentication. Isolated synthetic execution is not NAS acceptance. The coordinator alone changes release state after independent review. NAS deployment then uses the exact reviewed contract bytes and explicit server-side persona settings, preserving the installed account and provider configuration.

Hashes cover exact UTF-8 LF bytes; manifest does not hash itself. Consumers pin the final manifest digest, cannot accept arbitrary locally edited status or skip file validation. Legacy rehearsal candidate behavior remains distinct and unchanged.
