"""Compact evidence for a remote/local rollback that was not confirmed."""
import json

from tianshu.runtime.serialization import wire


UNRESOLVED = "memory_remote_wait_unresolved"


def wait_marker(wait):
    evidence = {"reason": wait.reason, "retry_at": wire(wait.retry_at), "dependency": wait.dependency,
        "remote_rollback_confirmed": getattr(wait,"remote_rollback_confirmed",None),
        "transaction_rollback_confirmed": getattr(wait,"transaction_rollback_confirmed",None)}
    return UNRESOLVED + ":" + json.dumps(evidence,ensure_ascii=False,separators=(",",":"))


def is_unresolved_wait(value):
    return isinstance(value,str) and (value == UNRESOLVED or value.startswith(UNRESOLVED + ":"))
