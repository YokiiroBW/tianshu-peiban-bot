"""Compact evidence for a remote/local rollback that was not confirmed."""
import json

from tianshu.runtime.serialization import wire


UNRESOLVED = "memory_remote_wait_unresolved"


def wait_marker(wait, *, extra=None):
    evidence = {"reason": wait.reason, "retry_at": wire(wait.retry_at), "dependency": wait.dependency,
        "remote_rollback_confirmed": getattr(wait,"remote_rollback_confirmed",None),
        "transaction_rollback_confirmed": getattr(wait,"transaction_rollback_confirmed",None)}
    if extra:
        evidence.update(extra)
    return UNRESOLVED + ":" + json.dumps(evidence,ensure_ascii=False,separators=(",",":"))


def is_unresolved_wait(value):
    return isinstance(value,str) and (value == UNRESOLVED or value.startswith(UNRESOLVED + ":"))


READ_OBSERVATION = "memory_preparation_read_observation"


def read_observation(value):
    prefix = READ_OBSERVATION + ":"
    if not isinstance(value,str) or not value.startswith(prefix):
        return None
    try:
        result = json.loads(value[len(prefix):])
    except (TypeError,ValueError):
        return None
    return result if isinstance(result,dict) else None


def read_observation_marker(wait, previous=None):
    evidence = {"reason":wait.reason, "retry_at":wire(wait.retry_at), "dependency":wait.dependency,
        "preparation_stage":(getattr(wait,"memory_preparation",None) or {}).get("stage"),
        "remote_rollback_confirmed":getattr(wait,"remote_rollback_confirmed",None),
        "transaction_rollback_confirmed":getattr(wait,"transaction_rollback_confirmed",None)}
    record = {"first": previous.get("first",evidence) if previous else evidence,
        "last":evidence,"count":int(previous.get("count",0))+1 if previous else 1}
    return READ_OBSERVATION + ":" + json.dumps(record,ensure_ascii=False,separators=(",",":"))
