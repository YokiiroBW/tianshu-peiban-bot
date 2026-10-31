"""The broker's bounded, fair quota scheduler. There is no caller-side model semaphore."""

from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
import time

from tianshu.runtime.errors import AppError
from tianshu.types import ServiceClass


@dataclass(slots=True)
class Waiter:
    request: object
    units: dict[str, float]
    queued: float
    future: asyncio.Future

    @property
    def tenant(self):
        context = self.request.context
        return context.conversation_id or context.actor_id or context.principal_id


@dataclass(slots=True)
class GroupState:
    waiting: list[Waiter] = field(default_factory=list)
    active: dict[str, tuple[ServiceClass, float | None]] = field(default_factory=dict)
    # Timestamp, attempt identity, reserved/reported units. Unknown usage retains reservation.
    window: deque = field(default_factory=deque)
    served: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    class_streak: int = 0
    rate_classes: dict = field(default_factory=dict)


class QuotaScheduler:
    def __init__(self, policies):
        self.policies = policies
        self.groups = {name: GroupState() for name in policies}
        self.lock = asyncio.Lock()
        self.changed = asyncio.Event()
        self.closed = False
        self.task = None

    def restore(self, rows):
        now = datetime.now(UTC)
        for row in rows:
            state = self.groups.get(row["quota_group"])
            if state is None:
                continue
            if row["started_at"] and (now - row["started_at"]).total_seconds() < 60:
                state.window.append((row["started_at"].timestamp(), row["attempt_id"],
                                     row["charged_units"] or row["estimated_units"]))
                state.rate_classes[row["attempt_id"]] = ServiceClass(row["service_class"])
            if row["status"] == "unknown" and row["hold_until"] and row["hold_until"] > now:
                state.active[row["attempt_id"]] = (ServiceClass(row["service_class"]),
                                                   row["hold_until"].timestamp())

    async def start(self):
        self.task = asyncio.create_task(self._pump(), name="quota-scheduler")

    async def close(self):
        self.closed = True
        self.changed.set()
        if self.task:
            await self.task
        async with self.lock:
            for state in self.groups.values():
                for waiter in state.waiting:
                    if not waiter.future.done():
                        waiter.future.set_exception(AppError("broker_stopping", status=503))
                state.waiting.clear()

    async def acquire(self, request, group, units):
        if any(type(value) not in (int, float) or not math.isfinite(value) or value < 0
               for value in units.values()):
            raise AppError("invalid_resource_units")
        if (type(request.max_queue_wait) not in (int, float)
            or not math.isfinite(request.max_queue_wait) or request.max_queue_wait < 0):
            raise AppError("invalid_queue_wait")
        if group not in self.policies:
            raise AppError("quota_group_not_configured", status=503)
        policy, state = self.policies[group], self.groups[group]
        limits = {**policy.unit_limits}
        if policy.tpm is not None:
            limits["tokens"] = policy.tpm
        for unit, limit in limits.items():
            if units.get(unit, 0) > limit:
                raise AppError("request_exceeds_quota_window", status=429,
                               details={"unit": unit, "limit": limit})
        future = asyncio.get_running_loop().create_future()
        waiter = Waiter(request, dict(units), time.monotonic(), future)
        async with self.lock:
            if self.closed:
                raise AppError("broker_stopping", status=503)
            if len(state.waiting) >= policy.max_queue:
                raise AppError("model_queue_full", status=429)
            state.waiting.append(waiter)
            self.changed.set()
        try:
            timeout = min(request.budget.remaining(), request.max_queue_wait,
                          policy.max_wait_seconds)
            async with asyncio.timeout(timeout):
                await asyncio.shield(future)
        except BaseException:
            async with self.lock:
                if waiter in state.waiting:
                    state.waiting.remove(waiter)
                # A cancellation racing with grant must return that grant once.
                elif future.done() and not future.cancelled() and future.exception() is None:
                    state.active.pop(request.attempt_id, None)
                    state.window = deque(item for item in state.window
                                         if item[1] != request.attempt_id)
                future.cancel()
                self.changed.set()
            raise

    async def release(self, group, attempt_id, *, units=None, hold_until=None, not_started=False):
        async with self.lock:
            state = self.groups[group]
            old = state.active.pop(attempt_id, None)
            if hold_until and old:
                state.active[attempt_id] = (old[0], hold_until.timestamp())
            if units is not None:
                state.window = deque((stamp, key, dict(units) if key == attempt_id else cost)
                                     for stamp, key, cost in state.window)
            if not_started:
                state.window = deque(item for item in state.window if item[1] != attempt_id)
            self.changed.set()

    async def cancel(self, attempt_id):
        async with self.lock:
            for state in self.groups.values():
                for waiter in list(state.waiting):
                    if waiter.request.attempt_id == attempt_id:
                        state.waiting.remove(waiter)
                        if not waiter.future.done():
                            waiter.future.set_exception(AppError("attempt_cancelled_before_start", status=409))
            self.changed.set()

    async def _pump(self):
        while not self.closed:
            async with self.lock:
                for group, state in self.groups.items():
                    policy = self.policies.get(group)
                    if policy is None:
                        continue
                    now = time.time()
                    state.window = deque(item for item in state.window if now-item[0] < 60)
                    window_ids = {item[1] for item in state.window}
                    state.rate_classes = {key: value for key, value in state.rate_classes.items()
                                          if key in window_ids}
                    state.active = {key: val for key, val in state.active.items()
                                    if val[1] is None or val[1] > now}
                    while self._grant(policy, state, now):
                        pass
                self.changed.clear()
            try:
                await asyncio.wait_for(self.changed.wait(), timeout=0.25)
            except TimeoutError:
                pass

    def _grant(self, policy, state, now):
        if len(state.active) >= policy.concurrency:
            return False
        if policy.rpm is not None and len(state.window) >= policy.rpm:
            return False
        totals = defaultdict(float)
        background_totals = defaultdict(float)
        background_requests = 0
        for _stamp, key, units in state.window:
            for unit, count in units.items():
                totals[unit] += count
                if state.rate_classes.get(key) != ServiceClass.INTERACTIVE:
                    background_totals[unit] += count
            if state.rate_classes.get(key) != ServiceClass.INTERACTIVE:
                background_requests += 1
        limits = {**policy.unit_limits}
        if policy.tpm is not None:
            limits["tokens"] = policy.tpm
        background = sum(kind != ServiceClass.INTERACTIVE for kind, _ in state.active.values())
        candidates = []
        for waiter in state.waiting:
            if waiter.future.done():
                continue
            kind = waiter.request.service_class
            if kind != ServiceClass.INTERACTIVE:
                if background >= min(policy.background_max,
                                     policy.concurrency-policy.interactive_reserve):
                    continue
                rpm_max = (policy.rpm-policy.interactive_rpm_reserve
                           if policy.rpm is not None else None)
                if policy.background_rpm_max is not None:
                    rpm_max = min(rpm_max, policy.background_rpm_max) if rpm_max is not None else policy.background_rpm_max
                if rpm_max is not None and background_requests >= rpm_max:
                    continue
                background_limits = {unit: limit-policy.interactive_unit_reserve.get(unit, 0)
                                     for unit, limit in limits.items()}
                for unit, limit in policy.background_unit_max.items():
                    background_limits[unit] = min(background_limits.get(unit, limit), limit)
                if any(background_totals[unit]+waiter.units.get(unit, 0) > limit
                       for unit, limit in background_limits.items()):
                    continue
            if any(totals[unit]+waiter.units.get(unit, 0) > limit
                   for unit, limit in limits.items()):
                continue
            age = time.monotonic()-waiter.queued
            # Give elastic a guaranteed turn after three foreground/due grants, with
            # deadline ordering for due work and tenant rotation within each share.
            priority = (0 if kind == ServiceClass.INTERACTIVE else
                        1 if kind == ServiceClass.DUE else 2)
            if kind == ServiceClass.ELASTIC and (state.class_streak >= 3 or age > 30):
                priority = -1
            deadline = waiter.request.budget.deadline_at.timestamp()
            candidates.append(((priority, -int(age//5), state.served[waiter.tenant],
                                deadline if kind == ServiceClass.DUE else waiter.queued), waiter))
        if not candidates:
            return False
        _key, selected = min(candidates, key=lambda item: item[0])
        state.waiting.remove(selected)
        state.active[selected.request.attempt_id] = (selected.request.service_class, None)
        state.window.append((now, selected.request.attempt_id, selected.units))
        state.rate_classes[selected.request.attempt_id] = selected.request.service_class
        state.served[selected.tenant] += 1
        state.class_streak = (0 if selected.request.service_class == ServiceClass.ELASTIC
                              else state.class_streak+1)
        selected.future.set_result(None)
        return True

    def snapshot(self):
        return {group: {"active": len(state.active), "waiting": len(state.waiting),
                        "unknown_holds": sum(expiry is not None for _, expiry in state.active.values()),
                        "window_requests": len(state.window)}
                for group, state in self.groups.items()}
