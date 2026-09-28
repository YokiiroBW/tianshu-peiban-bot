# BOT-P repair follow-up review (2026-09-28)

## Decision

The two Platform findings in [`BOT-R-2026-09-28.md`](BOT-R-2026-09-28.md) are
resolved in fixed P commit `c5f4d3acc3a906376c5dfe36279ef26b91769b25`, parent
`0a2e6d9242308eb3cb785815aae8ca37b190ec62`. Within the requested narrow scope,
I found no remaining blocker for those paths. This review covers P only; A and N
were not re-reviewed.

The fixed P worktree was clean at the reviewed commit. No NAS, production
credential, live chat account, or real message was used.

## P1 — Physical reply owner uniqueness

`services/platform/bots.py:144-165` now derives an owner key from
`(namespace, self_id, channel_conversation_id, thread_id)` and rejects overlapping
actor IDs for an already enabled connection at that physical destination. The
key excludes Core `binding_id`, adapter type, and runtime `platform_id`; the
regression fixture uses the same actual bot ID across different bindings and
different runtime IDs. A different actual `self_id` remains eligible as a
separate owner.

The rule is applied when enabling (`bots.py:371-383`). An enabled connection is
rechecked by `_connection()` during event admission and claim authentication
(`bots.py:167-172`, `198-210`, `521-529`, `773-785`). Reply enqueue also calls the
same guard before persisting a reply (`bots.py:658-672`). This closes the legacy
case where an old database already contains two enabled connections: both
connections fail closed for new events and claims, and a selected connection
cannot enqueue another reply while its physical-owner conflict remains.
Settlement authentication for an existing attempt remains separate, allowing
late ACK/status reconciliation without admitting new work.

The fixed `test_binding_id_cannot_create_second_owner_for_same_physical_bot`
passes: the second connection cannot be enabled and cannot dispatch an event,
while only one Core dispatch occurs. The separate
`test_distinct_real_bot_ids_can_be_explicit_separate_owners` also passes.

I additionally seeded an isolated sidecar with the old conflicting enabled-row
state and verified that a new event, reply enqueue, and claim each raise `Fault`.
The event and reply tables remained empty. This directly checks the requested
legacy-database behavior across all three admission paths.

## P2 — SQLite work no longer blocks the event loop

`Bots.event()` now runs `_admit_event()` and `_finish_event()` through bounded
`LocalWork` (`bots.py:463-464`, `521-576`, `578-594`). The synchronous admission
transaction commits before the method awaits Core source registration/dispatch;
the asynchronous dispatch remains between the two worker calls. The server route
can continue awaiting the async handler without running the SQLite lock wait on
the aiohttp loop.

The fixed `test_event_database_lock_does_not_block_unrelated_loop_timer` holds a
SQLite write lock for 450 ms and verifies a timer scheduled for 50 ms remains
under the 200 ms lateness threshold. It passed in the full focused bot suite.

## Unknown event concurrency

The admission worker inserts and commits the event row as `unknown` before
dispatch (`bots.py:550-569`). A concurrent duplicate with the same event key
finds that row and returns the stored state rather than dispatching again
(`bots.py:550-563`). The original request later records its final/unknown result
in `_finish_event()`.

I ran an isolated concurrency check with the first dispatch paused, submitted a
duplicate while it was in flight, then released the first dispatch to fail with
a synthetic ambiguous `OSError`. Both calls returned `unknown`; a later retry
also returned `unknown`; the mocked Core dispatch count remained exactly one.
This confirms concurrent unknown admission is not replayed.

## Verification

- `tests/backend/test_bots.py`: **12/12 passed** using the fixed P commit and the
  published `text-dialogue/v1` contract directory.
- Concurrent duplicate / unknown one-off reproduction: **1 passed**.
- Legacy enabled-conflict event/send/claim one-off reproduction: **1 passed**.
- The coordinator reports the separate fixed A/N-to-new-P six-file joint run as
  **21/21 passed, zero skipped**. I did not repeat that run as part of this
  P-only follow-up.

Conclusion: the P1 physical-owner bypass and P2 event-loop blocking findings from
the original report are closed at `c5f4d3a`. This follow-up does not make claims
about live adapter hosts, real accounts, NAS deployment, or production traffic.
