# CONNECT-A review: Memory browser candidate `0dd542a` (2026-09-27)

## Decision

The authorization and source-freshness composition in Memory commit
`0dd542a76cb7db36013513b3de28b81c2c81ac05` is acceptable as a **conditional
candidate**, following the coordinator's selection of the owner-only SourceAuthority
barrier plus a separate fresh Platform user authorization. This does not make the
Memory browser endpoint a published cross-product contract or prove the full browser
chain.

The candidate does not impersonate Companion or change frozen `source-sync/v1`.
The API handoff correctly records that the existing `current_access` viewer rule
still accepts Companion only and that this browser path uses a different, explicit
authorization composition.

## Fixed-code review

In `src/tianshu_memory/source_authority.py`, `SourceAuthority.barrier(...,
context=None)` takes the local source revision and coverage, obtains owner facts,
current grants, and a final owner head, then calls `sync`. `sync` commits current
source/admission/grant facts and withdrawals in its own Store transaction before
the browser handler continues. Network calls finish before those Store transactions.

In `src/tianshu_memory/browser.py`, `_authorized_db` then reauthenticates the same
presented Platform bearer, checks the current `browse` operation, resolves the same
origin reference again through the configured HTTPS issuer, rereads
`browser_readers`, and compares the verified account, actor, and full scope. Only
after those checks does it open the result transaction, compare the local source
revision with the barrier result, and call `MemoryService._authorize` for the live
binding and complete scope. A reader/origin rejection therefore cannot roll a
source-negative commit back. No peer network request runs while the result
transaction is open.

`_group` requires current source lineage/projections and returns an explicit field
projection without private source references. Shared-profile reads are limited to
current `profile_shares`, the configured actor/scope, and current shareable
projections; they do not return the target's private records. The overview response
matches the fixed API handoff: `memory_group_count` and `counts_truncated`, with no
invented total subject count.

## Verification

I exported the exact `0dd542a` commit into this task's ignored `.runtime` and ran
the fixed candidate tests against that archive, not the mutable Memory worktree:

- `tests/test_browser_catalog.py`: **11 passed**.
- Reviewer-owned `tests/persona_acceptance/test_memory_postbarrier.py`: **5 passed**.

The additional cases change the registered reader after owner synchronization has
started: disable the reader, change its account, change its actor, or narrow its
scope. Each request is refused without returning `items`. A separate case withdraws
the source grant and disables the reader during the same barrier; the request is
refused and the local group remains inactive, proving the negative state was
committed before authorization failed.

The test commands used the immutable archive at
`.runtime/connect-a-m-0dd542a/checkout`; the Memory virtual environment supplied
the test dependencies, while the bundled Codex Python generated the synthetic TLS
certificate:

```powershell
$env:TIANSHU_TEST_CERT_PYTHON = 'C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe'
$env:TIANSHU_CONTRACT_DIRECTORY = 'C:/YOKI/Codex/tianshu-peiban-bot/contracts/text-dialogue/v1'
& 'C:/YOKI/Codex/tianshu-peiban-bot/worktrees/CONNECT-M/tianshu-memory/.venv/Scripts/python.exe' -B -m pytest -p no:cacheprovider --basetemp '.runtime/connect-a-test' tests/test_browser_catalog.py -q

$env:CONNECT_M_SNAPSHOT = 'C:/Users/Administrator/.codex/worktrees/7208/tianshu-peiban-bot/.runtime/connect-a-m-0dd542a/checkout'
& 'C:/YOKI/Codex/tianshu-peiban-bot/worktrees/CONNECT-M/tianshu-memory/.venv/Scripts/python.exe' -B -m pytest -p no:cacheprovider -c "$env:CONNECT_M_SNAPSHOT/pyproject.toml" --basetemp "$env:CONNECT_M_SNAPSHOT/.runtime/connect-a-reviewer-extra" 'C:/Users/Administrator/.codex/worktrees/7208/tianshu-peiban-bot/tests/persona_acceptance/test_memory_postbarrier.py' -q
```

Tests used synthetic accounts, a synthetic HTTPS owner/issuer, and isolated SQLite
databases under this task's `.runtime`; no NAS, live account, real message, or
production data was used. The Memory process test is a real Memory HTTP process,
but its Platform issuer is synthetic.

## Consumer compatibility and remaining boundary

The earlier B consumer `347a15d87189f0269861d1e0d209b4748380926b` did require
`subject_count` for `overview`, which the fixed Memory response does not publish.
That mismatch is closed in B's fixed `bc45342babac2c7597987d0edb4f8a9f27763023`
and final `9551871796f57d3369396023caf2b013a239c3a0` snapshots:
`services/platform/web_memory.py::_read` now validates `memory_group_count` and
`counts_truncated`, matching the producer's bounded-count contract.

On the final B archive paired with M `02df5ba8c041a7a6eb8b705c5f264e10543032ec`,
`tests/backend/test_memory_joint.py` passed (**1 passed**). It started the real
Platform HTTP application and real Memory HTTPS process, used a real Platform
login/session and a synthetic issuer, and checked a nonempty overview plus scoped
subject and record reads. The database and source/admission facts were synthetic;
this test did not launch Chromium or contact the NAS.

The `subject_count` defect is therefore no longer an open code or API-pair blocker.
The exact test is a backend process integration, not evidence of a browser run or
production deployment. The final report for the overall CONNECT-A integration
review records the separate U browser evidence and its mocked-API limit.
