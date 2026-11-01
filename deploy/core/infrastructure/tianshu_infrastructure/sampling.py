"""Cross-process slots, per-node sampling lock, and small atomic counter state."""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
import time
from contextlib import contextmanager

from .protocol import Failure, decode_json


def stable_id(prefix: str, *parts) -> str:
    return prefix + "_" + hashlib.sha256(json.dumps(parts, ensure_ascii=True, separators=(",", ":")).encode()).hexdigest()[:24]


class _Lock:
    def __init__(self, path):
        self.stream = open(path, "a+b")
        if os.fstat(self.stream.fileno()).st_size == 0:
            self.stream.write(b"0")
            self.stream.flush()

    def acquire(self):
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def close(self):
        self.stream.close()


@contextmanager
def slots(config, node, deadline, *, sampling=False):
    locks = []
    try:
        config.state_dir.mkdir(parents=True, exist_ok=True)
        candidates = [_Lock(config.state_dir / f"slot-{index}.lock") for index in range(config.max_concurrency)]
        locks.extend(candidates)
        while not any(candidate.acquire() for candidate in candidates):
            deadline.check()
            time.sleep(min(0.02, deadline.remaining))
        if sampling:
            sample_lock = _Lock(config.state_dir / f"node-{node.sampling_key}.lock")
            locks.append(sample_lock)
            while not sample_lock.acquire():
                deadline.check()
                time.sleep(min(0.02, deadline.remaining))
        yield
    except OSError:
        raise Failure("sample_state_unavailable") from None
    finally:
        for lock in locks:
            lock.close()


class SampleStore:
    def __init__(self, config, node):
        self.path = config.state_dir / f"sample-{node.sampling_key}.json"
        self.state = {}
        self.reasons = []
        try:
            with self.path.open("rb") as stream:
                raw = stream.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ValueError("oversized state")
            value = decode_json(raw)
            if not isinstance(value, dict) or value.get("version") != 1 or set(value) - {"version", "host", "docker"}:
                raise ValueError("invalid state")
            host = value.get("host", {})
            docker = value.get("docker", {})
            if not isinstance(host, dict) or not isinstance(docker, dict) or not isinstance(host.get("interfaces", {}), dict):
                raise ValueError("invalid state sections")
            samples = list(docker.values()) + list(host.get("interfaces", {}).values())
            if host.get("cpu") is not None:
                samples.append(host["cpu"])
            for sample in samples:
                if not isinstance(sample, dict) or set(sample) != {"at", "identity", "values"}:
                    raise ValueError("invalid sample")
                if type(sample["at"]) not in (int, float) or not math.isfinite(sample["at"]) or sample["at"] < 0:
                    raise ValueError("invalid sample timestamp")
                if not isinstance(sample["values"], list) or not 1 <= len(sample["values"]) <= 8 or any(type(item) is not int or not 0 <= item < 2**64 for item in sample["values"]):
                    raise ValueError("invalid sample counters")
                if not isinstance(sample["identity"], list) or len(sample["identity"]) > 4 or any(item is not None and type(item) not in (str, int) for item in sample["identity"]):
                    raise ValueError("invalid sample identity")
            self.state = value
        except FileNotFoundError:
            pass
        except (OSError, ValueError, RecursionError, TypeError):
            self.reasons.append("sample_state_invalid")

    def save(self):
        self.state["version"] = 1
        # Bound old/disappeared container samples, retaining the newest samples.
        docker = self.state.get("docker", {})
        if len(docker) > 10000:
            self.state["docker"] = dict(sorted(docker.items(), key=lambda item: item[1].get("at", 0), reverse=True)[:10000])
        raw = json.dumps(self.state, separators=(",", ":"), allow_nan=False).encode()
        if len(raw) > 4 * 1024 * 1024:
            raise Failure("sample_state_too_large")
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.path.parent, prefix="sample-", suffix=".tmp", delete=False) as stream:
                temporary = stream.name
                stream.write(raw)
                stream.flush()
            os.replace(temporary, self.path)
        except OSError:
            raise Failure("sample_state_unavailable") from None
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def sample_condition(previous, current, now, stale_after, *, identity=None):
    if not isinstance(previous, dict) or not previous:
        return "first_sample", None
    if identity is not None and previous.get("identity") != identity:
        return "counter_reset", None
    sampled_at = previous.get("at")
    if type(sampled_at) not in (int, float):
        return "counter_reset", None
    elapsed = now - sampled_at
    old = previous.get("values")
    if not isinstance(old, list) or len(old) != len(current) or any(type(v) is not int or v < 0 for v in old):
        return "counter_reset", None
    if any(value < before for value, before in zip(current, old)):
        return "counter_reset", None
    if elapsed <= 0 or elapsed > stale_after:
        return "stale", None
    return "ok", (elapsed, [value - before for value, before in zip(current, old)])


def save_sample(values, now, identity):
    return {"at": now, "identity": identity, "values": values}
