"""Preserve typed resource waits until the native job can resume them."""

from tianshu.runtime.database import classify_database_failure
from tianshu.runtime.recovery_policy import RecoveryWait, recovery_wait_retryable


def is_resource_wait(error):
    if isinstance(error, RecoveryWait):
        return True
    failure = classify_database_failure(error)
    return bool(failure and (failure.retryable or failure.receipt_required))


def can_retry_resource_wait(error):
    if isinstance(error, RecoveryWait):
        return recovery_wait_retryable(error)
    failure = classify_database_failure(error)
    return bool(failure and failure.retryable)


def raise_resource_wait(error):
    if is_resource_wait(error):
        raise error
