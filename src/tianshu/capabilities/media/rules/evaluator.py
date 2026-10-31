"""Asynchronous, bounded evaluation with priority, short-circuiting and a stable explanation.

The evaluated logic is frozen by the task card and the architecture:

    accessible AND NOT quality_satisfied AND NOT blacklist_match AND (whitelist_empty OR whitelist_match)

so this module never decides *what* a rule means; it decides *how* the frozen meaning is executed
without losing evidence:

* the whole policy is shape- and syntax-checked before anything is judged, so an invalid expression
  in a rule a current item never reaches is still reported instead of lurking;
* the priority order (inaccessible → quality_satisfied → blacklist → whitelist → eligible) is a
  short-circuit: a skipped rule is ``not_evaluated`` with a fixed reason, never a fabricated
  ``not_matched``;
* a group is AND-ed with short-circuiting in input order, groups are OR-ed, and a matching blacklist
  group stops the evaluation before the whitelist is looked at — which is what makes the explanation
  stable for the same input;
* a runtime failure (regex budget, dead worker, transport limit, busy instance) returns
  ``decision=rule_error``. "We could not decide" is never laundered into "the blacklist did not
  match", because that would silently download an item a rule was supposed to exclude.

One evaluator instance runs at most two requests at a time; the third gets ``busy`` immediately and
there is no hidden queue. A slot of that budget covers a request *and* the resources that request
still owns, so a cancelled request whose child could not be reaped keeps its slot until the child
really ends — capacity is spent by a live process, not by a finished coroutine. Every request owns
its own regex process, so no text, compiled pattern or handle is shared between two requests, and
external cancellation propagates: the child is killed with a synchronous kill before the
``CancelledError`` leaves this module.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from ..types import MediaMetadata
from ..rule_context import RuleContext, advanced_field_value
from . import regex_process as process_layer
from .comparisons import typed_compare
from .matching import any_value_matches, field_values, projection_within_budget
from .policy import parse_policy, regex_rules
from .regex_process import (
    IPC_MAX_REQUEST_BYTES,
    RegexWorker,
    own_cancellation,
    remaining,
)
from .types import (
    DECISION_DOWNLOAD,
    DECISION_RULE_ERROR,
    DECISION_SKIP,
    ERROR_CLOSED,
    ERROR_INVALID_ACCESSIBLE,
    ERROR_INVALID_METADATA,
    ERROR_INVALID_QUALITY_SATISFIED,
    ERROR_INVALID_REGEX,
    ERROR_INVALID_SNAPSHOT_REVISION,
    LIST_BLACKLIST,
    LIST_WHITELIST,
    OP_REGEX,
    REASON_BLACKLIST_MATCH,
    REASON_BUSY,
    REASON_ELIGIBLE,
    REASON_EVALUATION_TIMEOUT,
    REASON_INACCESSIBLE,
    REASON_INPUT_TOO_LARGE,
    REASON_QUALITY_SATISFIED,
    REASON_REGEX_WORKER_FAILED,
    REASON_WHITELIST_NO_MATCH,
    RESULT_ERROR,
    RESULT_MATCHED,
    RESULT_NOT_EVALUATED,
    RESULT_NOT_MATCHED,
    Rule,
    RuleDecision,
    RuleEvaluationError,
    RulePolicy,
    RuleTrace,
    RuleValidationError,
    SNAPSHOT_REVISION_PATTERN,
    TRACE_BLACKLIST_MATCHED,
    TRACE_ERROR_SHORT_CIRCUIT,
    TRACE_PRIORITY_SHORT_CIRCUIT,
    TRACE_RULE_SHORT_CIRCUIT,
    TRACE_WHITELIST_MATCHED,
    TRACE_FIELD_MISSING,
    TEXT_FIELDS,
    TIME_FIELDS,
    Group,
    GroupTrace,
)

MAX_CONCURRENT_REQUESTS = 2

#: Failure codes that are *not* an item outcome. A closed evaluator is a caller bug — it reused an
#: instance it closed, or two callers disagreed about ownership — and reporting it as an item's
#: ``rule_error`` would hide that behind a decision the caller is expected to store.
_FATAL_CODES = frozenset({ERROR_CLOSED})

_SNAPSHOT_REVISION = re.compile(SNAPSHOT_REVISION_PATTERN + r"\Z")


@dataclass(frozen=True)
class _Failure:
    """Why an evaluation could not be completed, and which rule position it belongs to.

    ``group_position``/``rule_position`` are ``None`` when the failure belongs to no single rule (the
    worker could not start at all); the renderer then marks every rule ``not_evaluated`` with the
    failure code rather than inventing a culprit.
    """

    code: str
    group_position: int | None = None
    rule_position: int | None = None


class _GroupRecord:
    """Mutable bookkeeping for one group, rendered into an immutable trace at the end."""

    def __init__(self, group: Group, list_kind: str, index: int) -> None:
        self.group_id = group.id
        self.list_kind = list_kind
        self.index = index
        self.rule_ids = tuple(rule.id for rule in group.rules)
        self.fields = tuple(rule.field for rule in group.rules)
        self.ops = tuple(rule.op for rule in group.rules)
        self.negates = tuple(rule.negate for rule in group.rules)
        self.results: list[str] = [RESULT_NOT_EVALUATED] * len(group.rules)
        self.reasons: list[str] = [TRACE_ERROR_SHORT_CIRCUIT] * len(group.rules)
        self.entered = False

    def record(self, position: int, result: str, reason: str) -> None:
        self.results[position] = result
        self.reasons[position] = reason

    def fill_unrecorded(self, reason: str) -> None:
        """Give every rule that no decision reached the group's own short-circuit reason."""

        for position, result in enumerate(self.results):
            if (
                result == RESULT_NOT_EVALUATED
                and self.reasons[position] == TRACE_ERROR_SHORT_CIRCUIT
            ):
                self.reasons[position] = reason

    def result(self, failure: _Failure | None) -> tuple[str, str]:
        if failure is not None and failure.group_position == self.index:
            return RESULT_ERROR, failure.code
        if not self.entered:
            if failure is not None:
                return RESULT_NOT_EVALUATED, failure.code
            return RESULT_NOT_EVALUATED, self.reasons[0]
        if all(result == RESULT_MATCHED for result in self.results):
            return RESULT_MATCHED, RESULT_MATCHED
        if any(result == RESULT_NOT_MATCHED for result in self.results):
            return RESULT_NOT_MATCHED, RESULT_NOT_MATCHED
        return RESULT_NOT_EVALUATED, self.reasons[0]

    def trace(self, result: str, reason: str) -> GroupTrace:
        return GroupTrace(
            group_id=self.group_id,
            list_kind=self.list_kind,
            result=result,
            reason=reason,
            rules=tuple(
                RuleTrace(
                    rule_id=self.rule_ids[position],
                    field=self.fields[position],
                    op=self.ops[position],
                    result=self.results[position],
                    reason=self.reasons[position],
                    negate=self.negates[position],
                )
                for position in range(len(self.rule_ids))
            ),
        )


class _TraceBuilder:
    """Accumulates one evaluation and renders the immutable, ordered trace.

    Order is frozen: blacklist groups first, then whitelist groups, each with its rules in input
    order, so the same input always produces the same explanation and a diff between two decisions
    is meaningful.
    """

    def __init__(self, policy: RulePolicy) -> None:
        self.records: list[_GroupRecord] = []
        for group in policy.blacklist:
            self.records.append(_GroupRecord(group, LIST_BLACKLIST, len(self.records)))
        for group in policy.whitelist:
            self.records.append(_GroupRecord(group, LIST_WHITELIST, len(self.records)))
        self.failure: _Failure | None = None

    def record(self, position: int, rule_position: int, result: str, reason: str) -> None:
        self.records[position].entered = True
        self.records[position].record(rule_position, result, reason)

    def short_circuit(self, position: int, reason: str) -> None:
        self.records[position].fill_unrecorded(reason)

    def set_failure(self, failure: _Failure) -> None:
        self.failure = failure
        for record in self.records:
            if failure.group_position is None:
                record.fill_unrecorded(failure.code)
            elif record.index > failure.group_position:
                record.fill_unrecorded(TRACE_ERROR_SHORT_CIRCUIT)
            elif record.index == failure.group_position and failure.rule_position is not None:
                record.record(failure.rule_position, RESULT_ERROR, failure.code)
                record.fill_unrecorded(TRACE_ERROR_SHORT_CIRCUIT)

    def close(self, terminal_reason: str) -> None:
        for record in self.records:
            record.fill_unrecorded(terminal_reason)

    def traces(self) -> tuple[GroupTrace, ...]:
        rendered: list[GroupTrace] = []
        for record in self.records:
            result, reason = record.result(self.failure)
            rendered.append(record.trace(result, reason))
        return tuple(rendered)


@dataclass
class _Handles:
    """Per-request regex state: at most one child process and the handles it issued."""

    process: RegexWorker | None = None
    handles: dict[tuple[int, int], int] = field(default_factory=dict)
    #: The *fixed* conclusion of that worker's one cleanup, recorded when the request's own retirement
    #: ran. It is not the live resource state: a request whose child really exited but whose cleanup
    #: failed in its own right must still not deliver a verdict, and ``settled`` would say the
    #: opposite. Only consulted when ``process`` is set, which is also when this was written.
    retired: bool = False

    def record(self, position: tuple[int, int], handle: int) -> None:
        self.handles[position] = handle

    def lookup(self, position: tuple[int, int]) -> int | None:
        return self.handles.get(position)


@dataclass
class _Slot:
    """One unit of the instance's concurrency budget, held until its resources are settled.

    A slot is not "one request": it covers the request *and* whatever that request still owns. A
    request whose child has not been reaped — a creation that never handed its process over, a child
    that could not be waited for — keeps its slot after the request itself has ended, because the
    capacity is still spent. Releasing it early is how a cancelled request used to let two more
    children be created on top of two that were still alive.
    """

    finished: bool = False
    worker: RegexWorker | None = None


class RuleEvaluator:
    """Async context manager that validates and evaluates subscription policies.

    ``worker_script`` exists so a test can point the process layer at a controlled script and
    reproduce a dead, silent, oversized or hanging worker. It is not a production setting: the
    default points at the bundled worker inside this package, and no environment variable, settings
    document or request field can change it.

    ``evaluate`` returns a decision for a *runtime* failure, because a caller must still be able to
    record why an item was not enqueued. Structural and syntax errors, by contrast, raise
    ``RuleValidationError``: a malformed policy is a configuration bug, not an item outcome.
    """

    def __init__(self, *, worker_script: str | None = None) -> None:
        self._worker_script = worker_script
        self._slots: list[_Slot] = []
        self._closed = False
        self._workers: set[RegexWorker] = set()
        #: This instance's one close: created by the first caller, joined by every later one. It is a
        #: task the instance owns rather than state a caller has to return through, so a caller that is
        #: cancelled while waiting cannot lose the conclusion the close reached.
        self._closing: asyncio.Task[bool] | None = None
        #: The fixed conclusion of this instance's own close. A close that could not evidence the
        #: retirement of every worker it owned keeps that answer: once the workers really exit, a later
        #: close releases them and returns their capacity, but it does not become a success merely
        #: because the set it walks is empty now. One bounded flag — the error is remembered here, not
        #: by retaining dead process objects.
        self._close_failed = False

    @property
    def _active(self) -> int:
        """How much of the concurrency budget is spent: requests plus unreclaimed resources."""

        return len(self._slots)

    def _admit(self) -> _Slot | None:
        """Take one of the two capacity slots, or ``None`` when both are spent.

        Nothing is queued: a caller that arrives when the budget is gone is told ``busy`` immediately.
        A slot whose request has ended but whose child has not been reaped is still spent, so an
        unsettled resource cannot be hidden by finishing its request.
        """

        self._sweep_slots()
        if len(self._slots) >= MAX_CONCURRENT_REQUESTS:
            return None
        slot = _Slot()
        self._slots.append(slot)
        return slot

    def _sweep_slots(self) -> None:
        """Release the slots of finished requests whose resources really are settled."""

        for slot in tuple(self._slots):
            if slot.finished and (slot.worker is None or slot.worker.settled):
                self._release_slot(slot)
                self._drop_settled_worker(slot)

    def _hold_slot(self, slot: _Slot, worker: RegexWorker) -> None:
        """Tie a slot to the worker it pays for; the slot comes back when that worker is settled."""

        slot.worker = worker
        worker.on_settled(lambda: self._release_after_settlement(slot))

    def _release_after_settlement(self, slot: _Slot) -> None:
        """The worker settled: give back its slot and let go of the worker itself, once each."""

        if slot.finished:
            self._release_slot(slot)
            self._drop_settled_worker(slot)

    def _finish_slot(self, slot: _Slot) -> None:
        """The request is over: release the slot now, or keep holding it for an unsettled resource."""

        slot.finished = True
        if slot.worker is None or slot.worker.settled:
            self._release_slot(slot)
            self._drop_settled_worker(slot)

    def _release_slot(self, slot: _Slot) -> None:
        """Release a slot exactly once, whatever asks for it first."""

        if slot in self._slots:
            self._slots.remove(slot)

    def _drop_settled_worker(self, slot: _Slot) -> None:
        """Let go of a worker that has really settled, once its request has ended.

        This is reference ownership, not the public conclusion: a worker whose close already reported a
        failure is still released here the moment its child really exits, because keeping a dead process
        object alive in order to remember an error is how an instance that stays open accumulates
        workers. The error itself survives as one bounded flag. The guard is what makes the three
        callers — a late settlement, the request's own ``finally`` and the sweep — idempotent, and it is
        also what keeps a worker that is still serving a request owned, since that is what lets
        ``aclose`` retire it.
        """

        worker = slot.worker
        if worker is not None and slot.finished and worker.settled:
            self._workers.discard(worker)

    async def __aenter__(self) -> "RuleEvaluator":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Refuse new calls and retire every process this instance still owns.

        Closing has exactly two outcomes, and its own answer is fixed by the first one. It returns
        normally only when every worker this instance owns was retired with evidence — child reaped,
        creation unable to produce another — and otherwise it raises the existing
        ``regex_worker_failed``: a worker whose creation never handed its child over cannot be reclaimed
        by waiting, and reporting success for it would be a claim this instance cannot evidence.

        The answer belongs to the close, not to the workers' current state. A worker that exits after
        that deadline is released — its slot comes back and the reference goes — but a later close still
        reports the failure the first one reported, instead of turning into a success because the set it
        walks happens to be empty now. Nothing is released by the failure itself: while the resource is
        unsettled the worker stays owned and so does the capacity it occupies.

        The answer also belongs to the *instance*, not to whichever caller happened to wait: the first
        close establishes one shared close that the instance owns, every later caller joins that same
        one instead of deciding again from the current set, and its outcome is recorded from the close's
        own result — so a caller that is cancelled while waiting still leaves with its
        ``CancelledError`` first, without the already-determined failure being lost. That is the gap the
        sixth review measured: a cancelled first close left the aggregate unrecorded, and later closes
        then reported success for a close that had failed.

        The order is what makes the successful return a barrier: the closed mark is set *before* any
        process work, and a worker can only ever be registered while that mark is still false. So when
        this coroutine returns, no request can be about to create a child behind its back, and every
        worker that was registered has been killed and reaped. A child that appears at the
        create/cleanup boundary of a request that is being cancelled right now is retired by the worker
        that owns it, which is why the worker is retired *before* it leaves the owned set.
        """

        self._closed = True
        task = self._ensure_close()
        cancelled = await self._await_close(task)
        if cancelled is not None:
            # The caller's own cancellation leaves first and is never replaced by a verdict: a request
            # that was cancelled must not be turned into an ordinary outcome. The shared close is not
            # cancelled with it, so the workers are still retired and the conclusion is still recorded.
            raise cancelled
        if not self._conclude_close(task):
            # The close's own exception, when it had one, stays the cause: the published outcome is the
            # named failure, but an internal fault is still diagnosable instead of being flattened.
            cause = None if task.cancelled() else task.exception()
            raise RuleEvaluationError(REASON_REGEX_WORKER_FAILED, "regex_worker") from cause

    def _ensure_close(self) -> asyncio.Task[bool]:
        """Create this instance's one close, or join the one that already exists.

        Exactly one close exists per instance, created by the first caller: the closed mark is already
        set, so no worker can be registered afterwards and the aggregate answer cannot be invalidated
        by a later arrival. Later callers join *this* task — they do not sweep the current set and
        decide again, which is what let an empty set look like a success after a failure.
        """

        if self._closing is None:
            task = asyncio.ensure_future(self._retire_owned_workers())
            self._closing = task
            task.add_done_callback(self._close_finished)
        return self._closing

    def _close_finished(self, task: asyncio.Task[bool]) -> None:
        """Record the shared close's outcome the moment it ends, whoever is waiting — or nobody."""

        self._conclude_close(task)

    def _conclude_close(self, task: asyncio.Task[bool]) -> bool:
        """Fix this instance's aggregate close conclusion from the close's own result, once.

        Written from the close's result rather than from a caller's normal return, because a waiter may
        be cancelled: the conclusion must survive a caller that never gets an answer, and the record is
        idempotent — once a close has concluded a failure, every later caller reads that same failure.
        The error is kept as one bounded flag; the workers themselves are still released on their real
        settlement, so remembering a failure never means holding a dead process object.
        """

        if task.cancelled():
            self._close_failed = True
        else:
            error = task.exception()
            if error is not None or not task.result():
                self._close_failed = True
        return not self._close_failed

    async def _await_close(self, task: asyncio.Task[bool]) -> asyncio.CancelledError | None:
        """Wait for the shared close, holding this caller's cancellation until the close is finished.

        The shared close is never cancelled by a waiter: it is the instance retiring its own workers,
        and dropping it would leave live children behind *and* lose the conclusion. The cancellation is
        held and re-raised once the close is done, so it still takes priority for this caller over the
        verdict the close reached. Waiting is by joining that one task, so two waiters — including one
        that is cancelled and one that is not — cost one cleanup budget, not two.
        """

        cancelled: asyncio.CancelledError | None = None
        while True:
            try:
                # ``asyncio.wait`` observes the task without cancelling it and without re-raising its
                # outcome: the close's failures belong to the close, and are read from its result by
                # ``_conclude_close`` — including by the done callback, which runs even when this
                # waiter never returns normally.
                await asyncio.wait({task})
                return cancelled
            except asyncio.CancelledError as error:
                cancelled = cancelled or own_cancellation(error)
                if task.done():
                    return cancelled

    async def _retire_owned_workers(self) -> bool:
        """Retire the owned workers until every one of them is settled, and report the conclusion.

        This is the body of the instance's one close. Two different questions are answered here, and
        conflating them is what the fifth review measured. *Ownership* follows the live state: a worker
        whose child really exited is released from the set even when its own close already reported a
        failure, so a long-lived instance does not accumulate dead workers. The *return value* follows
        each worker's fixed conclusion: one cleanup that could not evidence its reclamation makes this
        close a failure, whatever the set looks like afterwards.

        The set can only shrink once ``_closed`` is set, so this loop terminates after at most the
        number of requests that were in flight; the bound is there to make a silent exit impossible
        rather than to be reached. Each round closes every worker it still owns, concurrently: closing
        is idempotent and shares one cleanup deadline per worker, so a repeated round costs nothing for
        a worker that was already reaped, and two unsettled workers cost one budget rather than two.
        """

        concluded = True
        for _ in range(MAX_CONCURRENT_REQUESTS + 1):
            if not self._workers:
                return concluded
            workers = tuple(self._workers)
            outcomes = await asyncio.gather(*(worker.close_quietly() for worker in workers))
            for worker, retired in zip(workers, outcomes):
                if worker.settled:
                    # The resource really is gone, so the reference goes with it. That is ownership,
                    # not the public answer below.
                    self._workers.discard(worker)
                if not retired:
                    concluded = False
            if not self._workers:
                return concluded
        # Unreachable for a worker that can be settled at all, but a silent exit would be worse than a
        # loud one: whatever is left has its kill issued synchronously rather than abandoned, and stays
        # owned — dropping it here is exactly the loss this loop exists to prevent.
        for worker in tuple(self._workers):
            worker.force_kill()
            if worker.settled:
                self._workers.discard(worker)
        if self._workers:
            concluded = False
        return concluded

    def _spawn_worker(self, slot: _Slot) -> RegexWorker:
        """Register a worker for one in-flight request, or refuse because this instance is closed.

        Registering happens here and only here, synchronously: a request therefore either registers
        its worker before ``aclose`` can observe the set — in which case ``aclose`` retires it — or it
        is refused outright. There is no third case in which a child is created after the set was
        already swept.

        The slot is tied to the worker here as well, so the capacity this request spends comes back at
        the moment the worker is settled — not at the moment the request ends.
        """

        if self._closed:
            raise RuleEvaluationError(ERROR_CLOSED, "evaluator")
        worker = RegexWorker(self._worker_script)
        self._workers.add(worker)
        self._hold_slot(slot, worker)
        return worker

    async def validate_policy(self, document: object) -> RulePolicy:
        """Shape-validate a document, then prove every expression really compiles.

        A plain-text policy never starts a child process. A policy with regular expressions compiles
        every expression once, up front, so "valid" means valid for the whole policy rather than for
        the rules an item happened to reach today. The whole call is bounded by the request budget:
        process creation, the handshake and every compile share one deadline.

        "Valid" also means the worker behind the compilation was retired with evidence. A policy whose
        cleanup could not evidence its reclamation is not a policy this call may bless, so the same
        named failure the process layer raises is raised here rather than returning a policy that
        leaves a live child behind. The question asked is the *fixed* conclusion of that one cleanup,
        not the child's current state: a cleanup that failed — including one that reaped the child and
        then failed in its own right — is a failure even though the process really exited.
        """

        if self._closed:
            raise RuleEvaluationError(ERROR_CLOSED, "evaluator")
        deadline = time.monotonic() + process_layer.REQUEST_TIMEOUT_SECONDS
        policy = parse_policy(document)
        slot = self._admit()
        if slot is None:
            raise RuleEvaluationError(REASON_BUSY, "evaluator")
        try:
            if remaining(deadline) <= 0:
                raise RuleEvaluationError(REASON_EVALUATION_TIMEOUT, "request")
            if policy.has_regex:
                handles = _Handles()
                try:
                    async with self._pattern(policy, handles, slot, deadline):
                        pass
                except _EvaluationFailed as failed:
                    raise RuleEvaluationError(
                        failed.failure.code, _failure_field(policy, failed.failure)
                    ) from None
                if handles.process is not None and not handles.retired:
                    raise RuleEvaluationError(REASON_REGEX_WORKER_FAILED, "regex_worker")
            return policy
        finally:
            self._finish_slot(slot)

    async def evaluate(
        self,
        metadata: MediaMetadata,
        document: object,
        *,
        accessible: bool,
        quality_satisfied: bool,
        snapshot_revision: str,
        context: RuleContext | None = None,
    ) -> RuleDecision:
        """Judge one normalized item against one policy and explain the outcome.

        Three kinds of outcome are possible and never confused with one another:

        * a normal decision (``download``/``skip``) with an explicit reason;
        * ``rule_error`` for a bounded-resource failure, carrying ``requires_rule_attention`` so a
          future scan can pause this policy instead of enqueueing on a guess;
        * ``RuleValidationError`` for a malformed document or an expression that does not compile —
          a configuration bug is raised, not recorded as an item outcome.

        The whole call is bounded by the request budget: process creation, the handshake, every
        compile and every search share one deadline, and cleanup gets its own separate budget. A
        request that runs out of time reports ``rule_error``; it never turns into a normal ``skip``.

        The same holds for a request whose worker could not be retired: a ``download``/``skip`` verdict
        is only delivered for a request whose retirement concluded successfully, and one whose cleanup
        failed — or ran out of its deadline — becomes the fixed ``rule_error``/``regex_worker_failed``
        instead: enqueue forbidden, rule attention required. What decides that is the fixed conclusion
        of that one cleanup, not the child's current state, so a cleanup that failed *after* the child
        had already been reaped is still a failure and still withholds the verdict. A failure that
        already has its own name keeps it; it is never rewritten into a success.
        """

        if self._closed:
            raise RuleEvaluationError(ERROR_CLOSED, "evaluator")
        deadline = time.monotonic() + process_layer.REQUEST_TIMEOUT_SECONDS
        policy = parse_policy(document)
        _validate_caller_facts(metadata, accessible, quality_satisfied, snapshot_revision)
        slot = self._admit()
        if slot is None:
            return _busy_decision(metadata.item_key, snapshot_revision, policy)
        handles = _Handles()
        builder = _TraceBuilder(policy)
        decision: RuleDecision | None = None
        try:
            if remaining(deadline) <= 0:
                failure = _Failure(REASON_EVALUATION_TIMEOUT)
                builder.set_failure(failure)
                decision = _rule_error_decision(
                    metadata, snapshot_revision, policy, failure, builder
                )
            else:
                async with self._pattern(policy, handles, slot, deadline):
                    decision = await self._judge(
                        metadata,
                        policy,
                        handles,
                        builder,
                        accessible,
                        quality_satisfied,
                        snapshot_revision,
                        deadline,
                        context,
                    )
        except _EvaluationFailed as failed:
            if failed.failure.code in _FATAL_CODES:
                # A closed evaluator is a configuration/ownership bug in the caller, not an item
                # outcome: it is raised so the caller cannot record it as "we could not decide".
                raise RuleEvaluationError(failed.failure.code, "evaluator") from None
            builder.set_failure(failed.failure)
            decision = _rule_error_decision(
                metadata, snapshot_revision, policy, failed.failure, builder
            )
        finally:
            self._finish_slot(slot)
        if (
            decision.decision != DECISION_RULE_ERROR
            and handles.process is not None
            and not handles.retired
        ):
            # The verdict was computed, but the worker's one cleanup did not conclude a successful
            # retirement — it may have failed in its own right, or run out of its deadline while the
            # child was still unreclaimed. Delivering the verdict would report a clean outcome for a
            # request whose reclamation this instance cannot evidence, even if the child has exited
            # since: the fixed conclusion is what this answer follows, not the current process state.
            failure = _Failure(REASON_REGEX_WORKER_FAILED)
            builder.set_failure(failure)
            return _rule_error_decision(metadata, snapshot_revision, policy, failure, builder)
        return decision

    async def _judge(
        self,
        metadata: MediaMetadata,
        policy: RulePolicy,
        handles: _Handles,
        builder: _TraceBuilder,
        accessible: bool,
        quality_satisfied: bool,
        snapshot_revision: str,
        deadline: float,
        context: RuleContext | None,
    ) -> RuleDecision:
        if not accessible:
            builder.close(TRACE_PRIORITY_SHORT_CIRCUIT)
            return _render(
                metadata, snapshot_revision, policy, DECISION_SKIP, REASON_INACCESSIBLE, builder
            )
        if quality_satisfied:
            builder.close(TRACE_PRIORITY_SHORT_CIRCUIT)
            return _render(
                metadata,
                snapshot_revision,
                policy,
                DECISION_SKIP,
                REASON_QUALITY_SATISFIED,
                builder,
            )

        if await self._run_groups(
            metadata, policy.blacklist, 0, builder, handles, deadline, context
        ):
            builder.close(TRACE_BLACKLIST_MATCHED)
            return _render(
                metadata,
                snapshot_revision,
                policy,
                DECISION_SKIP,
                REASON_BLACKLIST_MATCH,
                builder,
            )
        if not policy.whitelist:
            builder.close(TRACE_PRIORITY_SHORT_CIRCUIT)
            return _render(
                metadata, snapshot_revision, policy, DECISION_DOWNLOAD, REASON_ELIGIBLE, builder
            )
        if await self._run_groups(
            metadata, policy.whitelist, len(policy.blacklist), builder, handles, deadline, context
        ):
            builder.close(TRACE_WHITELIST_MATCHED)
            return _render(
                metadata, snapshot_revision, policy, DECISION_DOWNLOAD, REASON_ELIGIBLE, builder
            )
        builder.close(TRACE_PRIORITY_SHORT_CIRCUIT)
        return _render(
            metadata, snapshot_revision, policy, DECISION_SKIP, REASON_WHITELIST_NO_MATCH, builder
        )

    async def _run_groups(
        self,
        metadata: MediaMetadata,
        groups: tuple[Group, ...],
        start: int,
        builder: _TraceBuilder,
        handles: _Handles,
        deadline: float,
        context: RuleContext | None,
    ) -> bool:
        """Evaluate OR-ed groups. A group matches only when every rule of it matched.

        Short-circuiting is input-order and per group: the first rule that does not match ends its
        group and leaves the remaining rules of that group ``not_evaluated``, so the explanation says
        exactly how far the evaluation got instead of guessing about rules it never ran.
        """

        for offset, group in enumerate(groups):
            position = start + offset
            matched = True
            for index, rule in enumerate(group.rules):
                try:
                    outcome = await self._match(
                        metadata, rule, (position, index), handles, deadline, context
                    )
                except RuleEvaluationError as error:
                    raise _EvaluationFailed(_Failure(error.code, position, index)) from None
                if outcome:
                    builder.record(position, index, RESULT_MATCHED, RESULT_MATCHED)
                    continue
                builder.record(
                    position,
                    index,
                    RESULT_NOT_MATCHED,
                    TRACE_FIELD_MISSING if outcome is None else RESULT_NOT_MATCHED,
                )
                builder.short_circuit(position, TRACE_RULE_SHORT_CIRCUIT)
                matched = False
                break
            if matched:
                return True
        return False

    async def _match(
        self,
        metadata: MediaMetadata,
        rule: Rule,
        position: tuple[int, int],
        handles: _Handles,
        deadline: float,
        context: RuleContext | None,
    ) -> bool | None:
        if rule.field not in TEXT_FIELDS:
            actual = advanced_field_value(metadata, rule.field, context)
            if actual is None:
                return None
            matched = typed_compare(
                actual, rule.value, rule.op, timestamp=rule.field in TIME_FIELDS
            )
        else:
            matched = await self._match_text(metadata, rule, position, handles, deadline)
        # Unknown is never turned into a match by NOT, including text fields and regular expressions.
        return None if matched is None else not matched if rule.negate else matched

    async def _match_text(self, metadata, rule, position, handles, deadline) -> bool | None:
        values = field_values(metadata, rule.field)
        if not values:
            # A field with no value matches nothing at all: absence is not an empty string, and
            # ``regex=.*`` must not quietly turn a missing title into a match.
            return None
        if not projection_within_budget(values):
            raise RuleEvaluationError(REASON_INPUT_TOO_LARGE, f"field:{rule.field}")
        if rule.op != OP_REGEX:
            return any_value_matches(
                values, rule.value, rule.op, case_sensitive=rule.case_sensitive
            )
        handle = handles.lookup(position)
        process = handles.process
        if handle is None or process is None:
            raise RuleEvaluationError(REASON_REGEX_WORKER_FAILED, "regex_worker")
        for value in values:
            if len(value.encode("utf-8")) > IPC_MAX_REQUEST_BYTES:
                raise RuleEvaluationError(REASON_INPUT_TOO_LARGE, f"field:{rule.field}")
            # Every search gets ``min(50 ms, what is left of the request)``, so a long list of
            # individually-legal searches cannot outlive the request budget.
            if await process.search(handle, value, timeout=remaining(deadline)):
                return True
        return False

    @contextlib.asynccontextmanager
    async def _pattern(
        self, policy: RulePolicy, handles: _Handles, slot: _Slot, deadline: float
    ) -> AsyncIterator[_Handles]:
        """Compile every expression of the policy for this request, then always retire the child.

        A plain-text policy never starts a process. A policy with expressions starts exactly one
        child for this request, reuses it for every expression, and always retires it: on the normal
        path, on a failure, and on cancellation — the kill is synchronous, so a cancelled request
        cannot leave a backtracking match running.

        Compilation happens for the **whole** policy before anything is judged. A syntax error is
        therefore reported as ``RuleValidationError(invalid_regex, <list>[i].rules[j].value)`` even
        when the rule would never have been reached by today's item.
        """

        rules = regex_rules(policy)
        if not rules:
            yield handles
            return
        # Registered before the first await, through the closed barrier: from here on this worker is
        # visible to ``aclose``, which kills and reaps it whatever happens to this request.
        handles.process = self._spawn_worker(slot)
        try:
            budget = min(process_layer.STARTUP_TIMEOUT_SECONDS, remaining(deadline))
            try:
                await handles.process.start(timeout=budget)
            except RuleEvaluationError as error:
                raise _EvaluationFailed(_Failure(error.code)) from None
            seen: dict[tuple[str, bool], int] = {}
            for group_position, rule_position, rule in rules:
                key = (rule.value, rule.case_sensitive)
                handle = seen.get(key)
                if handle is None:
                    try:
                        handle = await handles.process.compile(
                            rule.value,
                            _regex_flags(rule),
                            timeout=remaining(deadline),
                        )
                    except RuleEvaluationError as error:
                        if error.code == ERROR_INVALID_REGEX:
                            raise RuleValidationError(
                                ERROR_INVALID_REGEX,
                                _rule_path(policy, group_position, rule_position),
                            ) from None
                        raise _EvaluationFailed(
                            _Failure(error.code, group_position, rule_position)
                        ) from None
                    seen[key] = handle
                handles.record((group_position, rule_position), handle)
            yield handles
        finally:
            # Cleanup has its own budget, separate from the (possibly exhausted) request deadline:
            # the child is always retired, and always before the worker leaves the owned set. The value
            # recorded here is that one cleanup's *fixed* conclusion — what the caller above must ask
            # about — while the reference is released on the *live* state: a worker that is not settled
            # yet stays owned, so the object that still has a child to answer for is not dropped by this
            # request, and one whose child has really exited is released even if its close reported a
            # failure. Nothing else is released here: the slot this request pays for comes back when the
            # worker is settled.
            handles.retired = await handles.process.close_quietly(
                process_layer.CLEANUP_TIMEOUT_SECONDS
            )
            if handles.process.settled:
                self._workers.discard(handles.process)


class _EvaluationFailed(Exception):
    """Carry a rule failure out of the nested compile/match scopes to the decision renderer."""

    def __init__(self, failure: _Failure) -> None:
        super().__init__(failure.code)
        self.failure = failure


def _rule_path(policy: RulePolicy, group_position: int, rule_position: int) -> str:
    """The input path of one rule, derived from the policy's own structure.

    The path is what makes ``invalid_regex`` actionable without echoing the expression: a caller
    learns *which* rule is broken and fixes it, while the pattern itself stays out of the error.
    """

    blacklist = len(policy.blacklist)
    if group_position < blacklist:
        return f"blacklist[{group_position}].rules[{rule_position}].value"
    return f"whitelist[{group_position - blacklist}].rules[{rule_position}].value"


def _failure_field(policy: RulePolicy, failure: _Failure) -> str:
    """Where a rule failure happened, in the caller's input vocabulary."""

    if failure.group_position is None or failure.rule_position is None:
        return "regex_worker"
    return _rule_path(policy, failure.group_position, failure.rule_position)


def _regex_flags(rule: Rule) -> int:
    return 0 if rule.case_sensitive else re.IGNORECASE


def _validate_caller_facts(
    metadata: object, accessible: object, quality_satisfied: object, snapshot_revision: object
) -> None:
    """Refuse a caller whose trust facts are not the frozen shapes.

    ``accessible`` and ``quality_satisfied`` are produced elsewhere (account and quality policy) and
    arrive here as trusted booleans. A truthy string or a ``1`` is refused rather than coerced:
    coercing would let a caller turn "unknown" into "accessible".
    """

    if not isinstance(metadata, MediaMetadata):
        raise RuleValidationError(ERROR_INVALID_METADATA, "metadata")
    if type(accessible) is not bool:
        raise RuleValidationError(ERROR_INVALID_ACCESSIBLE, "accessible")
    if type(quality_satisfied) is not bool:
        raise RuleValidationError(ERROR_INVALID_QUALITY_SATISFIED, "quality_satisfied")
    if (
        not isinstance(snapshot_revision, str)
        or _SNAPSHOT_REVISION.match(snapshot_revision) is None
    ):
        raise RuleValidationError(ERROR_INVALID_SNAPSHOT_REVISION, "snapshot_revision")


def _render(
    metadata: MediaMetadata,
    snapshot_revision: str,
    policy: RulePolicy,
    decision: str,
    reason: str,
    builder: _TraceBuilder,
) -> RuleDecision:
    return RuleDecision(
        item_key=metadata.item_key,
        snapshot_revision=snapshot_revision,
        policy_revision=policy.revision,
        policy_digest=policy.policy_digest,
        decision=decision,
        reason=reason,
        automatic_enqueue_allowed=decision == DECISION_DOWNLOAD,
        requires_rule_attention=decision == DECISION_RULE_ERROR,
        trace=builder.traces(),
    )


def _rule_error_decision(
    metadata: MediaMetadata,
    snapshot_revision: str,
    policy: RulePolicy,
    failure: _Failure,
    builder: _TraceBuilder,
) -> RuleDecision:
    return RuleDecision(
        item_key=metadata.item_key,
        snapshot_revision=snapshot_revision,
        policy_revision=policy.revision,
        policy_digest=policy.policy_digest,
        decision=DECISION_RULE_ERROR,
        reason=failure.code,
        automatic_enqueue_allowed=False,
        requires_rule_attention=True,
        trace=builder.traces(),
    )


def _busy_decision(item_key: str, snapshot_revision: str, policy: RulePolicy) -> RuleDecision:
    return RuleDecision(
        item_key=item_key,
        snapshot_revision=snapshot_revision,
        policy_revision=policy.revision,
        policy_digest=policy.policy_digest,
        decision=DECISION_RULE_ERROR,
        reason=REASON_BUSY,
        automatic_enqueue_allowed=False,
        requires_rule_attention=True,
        trace=(),
    )


__all__ = [
    "MAX_CONCURRENT_REQUESTS",
    "RuleEvaluator",
]
