"""Typed RPC waits preserve rollback evidence across both transaction owners."""

from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace

import pytest

from tianshu.runtime.errors import AppError
from tianshu.runtime.jobs import ResourceRecoveryRetry
from tianshu.runtime.recovery_policy import (
    RecoveryWait, recovery_wait_from_wire, recovery_wait_retryable, recovery_wait_to_wire,
)


def original_wait(*, rollback_confirmed):
    value = RecoveryWait("source_lock_set_changed", datetime.now(UTC) + timedelta(seconds=30),
                         dependency="sources:lock_set")
    if rollback_confirmed is not None:
        value.transaction_rollback_confirmed = rollback_confirmed
    return value


def through_json(wait):
    return recovery_wait_from_wire(json.loads(json.dumps(recovery_wait_to_wire(wait))))


def test_wire_requires_explicit_wait_and_confirmed_remote_rollback():
    confirmed = original_wait(rollback_confirmed=True)
    payload = recovery_wait_to_wire(confirmed)
    assert set(payload) == {"kind", "version", "reason", "retry_at", "dependency", "rollback_confirmed"}
    assert payload["kind"] == "recovery_wait" and payload["version"] == 1
    assert payload["rollback_confirmed"] is True
    decoded = through_json(confirmed)
    assert decoded.reason == confirmed.reason and decoded.retry_at == confirmed.retry_at
    assert decoded.dependency == confirmed.dependency and decoded.remote_rollback_confirmed is True
    assert recovery_wait_retryable(decoded)
    for evidence in (None, False):
        decoded = through_json(original_wait(rollback_confirmed=evidence))
        assert decoded.remote_rollback_confirmed is False and not recovery_wait_retryable(decoded)
    with pytest.raises(TypeError, match="recovery_wait_required"):
        recovery_wait_to_wire(AppError("source_lock_set_changed", status=503))


@pytest.mark.parametrize("updates", [
    {"kind": "app_error"}, {"version": 2}, {"version": True}, {"reason": ""},
    {"reason": "bad\nreason"}, {"reason": "x" * 97}, {"retry_at": "not-a-date"},
    {"retry_at": "2026-11-05T10:00:00"}, {"dependency": "x" * 129},
    {"dependency": "bad\nvalue"}, {"dependency": []}, {"rollback_confirmed": "true"},
    {"extra": "not-permitted"},
])
def test_malformed_or_untyped_envelopes_are_never_restored_as_waits(updates):
    document = {**recovery_wait_to_wire(original_wait(rollback_confirmed=True)), **updates}
    with pytest.raises(ValueError, match="invalid_recovery_wait_envelope"):
        recovery_wait_from_wire(document)


def test_missing_field_and_non_mapping_are_rejected():
    payload = recovery_wait_to_wire(original_wait(rollback_confirmed=True))
    del payload["rollback_confirmed"]
    for invalid in (payload, None, [payload], "recovery_wait"):
        with pytest.raises(ValueError, match="invalid_recovery_wait_envelope"):
            recovery_wait_from_wire(invalid)


@pytest.mark.parametrize("remote_confirmed", [False, True])
async def test_local_rollback_cannot_overwrite_remote_rollback_evidence(database, remote_confirmed):
    original = original_wait(rollback_confirmed=remote_confirmed)
    received = through_json(original)
    with pytest.raises(RecoveryWait) as captured:
        async with database.transaction():
            raise received
    wait = captured.value
    assert wait.transaction_rollback_confirmed is True
    assert wait.remote_rollback_confirmed is remote_confirmed
    assert recovery_wait_retryable(wait) is remote_confirmed
    decision = ResourceRecoveryRetry().get_retry_decision(exception=wait, job=SimpleNamespace(attempts=0))
    assert (decision is not None) is remote_confirmed
    assert recovery_wait_to_wire(wait)["rollback_confirmed"] is remote_confirmed


async def test_nested_local_rollback_is_independent_from_confirmed_remote(database):
    received = through_json(original_wait(rollback_confirmed=True))
    async with database.transaction():
        with pytest.raises(RecoveryWait) as captured:
            async with database.transaction():
                raise received
        assert captured.value.remote_rollback_confirmed is True
        assert captured.value.transaction_rollback_confirmed is False
        assert not recovery_wait_retryable(captured.value)
    received = through_json(original_wait(rollback_confirmed=True))
    with pytest.raises(RecoveryWait) as captured:
        async with database.transaction():
            async with database.transaction():
                raise received
    assert captured.value.remote_rollback_confirmed is True
    assert captured.value.transaction_rollback_confirmed is True
    assert recovery_wait_retryable(captured.value)


def test_repeated_rpc_hops_cannot_launder_negative_remote_evidence():
    wait = through_json(original_wait(rollback_confirmed=False))
    for _ in range(3):
        wait.transaction_rollback_confirmed = True
        assert not recovery_wait_retryable(wait)
        assert recovery_wait_to_wire(wait)["rollback_confirmed"] is False
        wait = through_json(wait)
        assert wait.remote_rollback_confirmed is False


def test_ordinary_retry_cannot_fall_back_for_any_typed_wait():
    ordinary = ResourceRecoveryRetry(retryable=True, resource_recovery=False)
    opted_in = ResourceRecoveryRetry(retryable=True, resource_recovery=True)
    job = SimpleNamespace(attempts=0)
    for confirmed in (True, False):
        wait = through_json(original_wait(rollback_confirmed=confirmed))
        assert ordinary.get_retry_decision(exception=wait, job=job) is None
        assert (opted_in.get_retry_decision(exception=wait, job=job) is not None) is confirmed
    assert ordinary.get_retry_decision(exception=RuntimeError("ordinary"), job=job) is not None
    assert opted_in.get_retry_decision(exception=RuntimeError("ordinary"), job=job) is not None
