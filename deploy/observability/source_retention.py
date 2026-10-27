"""Retire native closed source segments only after durable whole-segment proof.

The producers own append-only segments. A higher native segment proves rotation;
a complete live host registry additionally identifies ended UUID instances. No
checkpoint or mtime alone is an acknowledgement. Unconfirmed/invalid bytes stay.
"""

import hashlib
import json
import os
import re
import time
import uuid
from pathlib import Path

from alerts import MONITOR_STALE_SECONDS
from policy import digest_bytes, event_time, segments
from retention import days

MODULES = {"platform", "gateway", "memory", "knowledge", "companion"}
UUID = r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}"


def pid_alive(pid):
    # os.kill(pid, 0) terminates a process on Windows. Native tail reclamation
    # uses Linux process liveness; other hosts keep their unproved tail.
    if os.name != "posix":
        return False
    os.kill(pid, 0)
    return True


def active_instances(path, now):
    """Unknown/partial/stale host state never proves that a tail is closed."""
    if path is None:
        return {}
    try:
        state = json.loads(Path(path).read_bytes())
        diagnostic = state["diagnostics"]
        instances = diagnostic["instances"]
        age = now - state["updated_at"]
        if (
            state["version"] != 1
            or state["status"] != "ready"
            or diagnostic["complete"] is not True
            or set(instances) != MODULES
            or type(state["pid"]) is not int
            or state["pid"] <= 0
            or not 0 <= age <= MONITOR_STALE_SECONDS
        ):
            return {}
        for instance in instances.values():
            if str(uuid.UUID(instance)) != instance:
                return {}
        if not pid_alive(state["pid"]):
            return {}
        return instances
    except (OSError, ValueError, KeyError, TypeError, OverflowError):
        return {}


def native_segment(source, name):
    """Native production filenames only, not every arbitrary JSONL file."""
    if source == "companion":
        match = re.fullmatch(r"companion\.jsonl(?:\.(\d+))?", name)
        return ("companion", int(match[1] or 0)) if match else None
    pattern = (
        rf"({UUID})\.(\d{{6,}})\.jsonl"
        if source in {"memory", "knowledge"}
        else rf"{re.escape(source)}-({UUID})-(\d{{4,}})\.jsonl"
    )
    match = re.fullmatch(pattern, name)
    return (match[1], int(match[2])) if match else None


def stamp(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


def identity(path, stat):
    with path.open("rb") as stream:
        first = stream.readline(4097)
    return f"{stat.st_dev}:{stat.st_ino}:" + hashlib.sha256(first).hexdigest()


def ranges(numbers):
    result = []
    for number in sorted(numbers):
        if result and number == result[-1][1] + 1:
            result[-1][1] = number
        else:
            result.append([number, number])
    return result


def attest(ledger, source, path, length, prior=None):
    """Extend exact acknowledged prefix proof without needing expired old hashes."""
    digest, sequences, batch = hashlib.sha256(), {}, []
    verified_size = 0

    def verified():
        nonlocal verified_size
        expected = {record["event_id"]: record for record, _ in batch}
        placeholders = ",".join("?" for _ in expected)
        actual = {
            row[0]: row[1:]
            for row in ledger.db.execute(
                "SELECT id,service,instance,sequence,hash,verified FROM events WHERE id IN ("
                + placeholders
                + ")",
                tuple(expected),
            )
        }
        for event, raw in batch:
            row = actual.get(event["event_id"])
            if not (
                ledger.clear_before > 0 and event_time(event) <= ledger.clear_before
            ) and (
                row is None
                or row[:4]
                != (
                    event["service"],
                    event["instance_id"],
                    event["sequence"],
                    digest_bytes(event),
                )
                or row[4] <= 0
            ):
                return False
            digest.update(raw)
            verified_size += len(raw)
            sequences.setdefault((event["service"], event["instance_id"]), set()).add(
                event["sequence"]
            )
        batch.clear()
        return True

    previous = []
    with path.open("rb") as stream:
        if prior:
            old_size, old_hash, encoded = prior
            if old_size > length:
                return None
            remaining = old_size
            while remaining:
                raw = stream.read(min(remaining, 1024**2))
                if not raw:
                    return None
                digest.update(raw)
                remaining -= len(raw)
            if digest.digest() != old_hash:
                return None
            previous = json.loads(encoded)
            verified_size = old_size
        while stream.tell() < length:
            raw = stream.readline(4097)
            if not raw or stream.tell() > length:
                return None
            try:
                event = ledger.policy.line(raw)
            except ValueError:
                break
            allowed = (
                {"memory", "memory-knowledge"}
                if source == "memory"
                else {"memory-knowledge"}
                if source == "knowledge"
                else {source}
            )
            if event["service"] not in allowed:
                break
            batch.append((event, raw))
            if len(batch) == 128 and not verified():
                batch.clear()
                break
    if batch:
        verified()
    if not sequences and not previous:
        return None
    combined = {(service, instance): spans for service, instance, spans in previous}
    for key, numbers in sequences.items():
        combined.setdefault(key, []).extend(ranges(numbers))
    return (
        verified_size,
        digest.digest(),
        json.dumps(
            [
                [service, instance, merge_ranges(spans)]
                for (service, instance), spans in sorted(combined.items())
            ],
            separators=(",", ":"),
        ),
    )


def merge_ranges(spans):
    result = []
    for first, last in sorted(spans):
        if result and first <= result[-1][1] + 1:
            result[-1][1] = max(last, result[-1][1])
        else:
            result.append([first, last])
    return result


def closed(source, segment, highest, instances):
    instance, index = segment
    return (
        index < highest[instance]
        or source != "companion"
        and source in instances
        and instances[source] != instance
    )


def _sync_directory(path):
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def prune_sources(
    ledger,
    roots,
    retention_days=30,
    *,
    now=None,
    host_state_path=None,
    byte_budget=64 * 1024**2,
):
    """Bounded source reclamation plus coverage allowed for ledger expiration.

    Persist proof before unlink, so a read-only mount, I/O error or restart cannot
    strand a verified source after its per-event hashes expire. A receipt is for
    an exact inode, complete size and whole-file SHA256, never a path-only permit.
    """
    realtime = now is None
    now = time.time() if realtime else now
    cutoff = now - days(retention_days) * 86400
    used = deleted = deleted_bytes = failed = 0
    instances = active_instances(host_state_path, now)
    through = {
        (service, instance): value
        for service, instance, value in ledger.db.execute(
            "SELECT service,instance,through FROM retired_streams"
        )
    }
    candidates, highest = [], {}
    for source, root in roots.items():
        if source not in MODULES:
            continue
        catalog = [(path, native_segment(source, path.name)) for path in segments(root)]
        catalog = [(path, segment) for path, segment in catalog if segment is not None]
        for _, (instance, index) in catalog:
            key = (source, instance)
            highest[key] = max(highest.get(key, -1), index)
        candidates.extend(
            (source + "/" + path.name, source, path, segment)
            for path, segment in catalog
        )
    candidates.sort()
    cursor = getattr(ledger, "source_retention_cursor", "")
    candidates = [item for item in candidates if item[0] > cursor] + [
        item for item in candidates if item[0] <= cursor
    ]
    for name, source, path, segment in candidates:
        if used >= byte_budget:
            break
        ledger.source_retention_cursor = name
        try:
            stat = path.stat()
            if stat.st_nlink != 1:
                continue
            ident = identity(path, stat)
            checkpoint = ledger.db.execute(
                "SELECT identity,offset FROM files WHERE name=?", (name,)
            ).fetchone()
            if (
                not checkpoint
                or checkpoint[0] != ident
                or not 0 <= checkpoint[1] <= stat.st_size
            ):
                continue
            length = checkpoint[1]
            if length == 0 and stat.st_size > 0:
                continue
            receipt = ledger.db.execute(
                "SELECT size,hash,coverage FROM source_receipts WHERE name=? AND identity=?",
                (name, ident),
            ).fetchone()
            source_highest = {segment[0]: highest[(source, segment[0])]}
            sealed = closed(source, segment, source_highest, instances)
            if receipt is None or receipt[0] < length:
                used += length
                if (
                    length == 0
                    and sealed
                    and (
                        stat.st_mtime < cutoff
                        or 0 < stat.st_mtime <= ledger.clear_before
                    )
                ):
                    proof = (0, hashlib.sha256(b"").digest(), "[]")
                elif (
                    ledger.clear_before > 0
                    and stat.st_mtime <= ledger.clear_before
                    and length == stat.st_size
                ):
                    # Explicit operator destruction differs from automatic
                    # confirmed-only retirement, including malformed old bytes.
                    with path.open("rb") as stream:
                        proof = (
                            length,
                            hashlib.file_digest(stream, "sha256").digest(),
                            "[]",
                        )
                else:
                    proof = attest(ledger, source, path, length, receipt)
                if proof is None:
                    continue
                length, file_hash, coverage = proof
            else:
                size, file_hash, coverage = receipt
                if size != length:
                    continue
                if not sealed or length != stat.st_size:
                    continue
                used += length
                with path.open("rb") as stream:
                    if hashlib.file_digest(stream, "sha256").digest() != file_hash:
                        failed += 1
                        continue
            after = path.stat()
            if (
                after.st_dev != stat.st_dev
                or after.st_ino != stat.st_ino
                or after.st_size < length
            ):
                continue
            if receipt is None or receipt[0] < length:
                with ledger.db:
                    ledger.db.execute(
                        "INSERT INTO source_receipts VALUES (?,?,?,?,?) ON CONFLICT(name,identity) "
                        "DO UPDATE SET size=excluded.size,hash=excluded.hash,coverage=excluded.coverage",
                        (name, ident, length, file_hash, coverage),
                    )
            all_retired = all(
                end <= through.get((service, instance), 0)
                for service, instance, spans in json.loads(coverage)
                for _, end in spans
            )
            if (
                not sealed
                or length != stat.st_size
                or not (stat.st_mtime < cutoff or all_retired)
            ):
                continue
            # Never cache a previous healthy host to authorize a later tail.
            if not closed(
                source,
                segment,
                source_highest,
                active_instances(
                    host_state_path,
                    time.time() if realtime else now,
                ),
            ):
                continue
            if stamp(path.stat()) != stamp(stat):
                continue
            path.unlink()
            _sync_directory(path.parent)
            deleted += 1
            deleted_bytes += stat.st_size
            with ledger.db:
                ledger.db.execute(
                    "DELETE FROM files WHERE name=? AND identity=?", (name, ident)
                )
        except (OSError, ValueError):
            failed += 1
    through = {
        (service, instance): value
        for service, instance, value in ledger.db.execute(
            "SELECT service,instance,through FROM retired_streams"
        )
    }
    combined = {}
    for name, ident, encoded in ledger.db.execute(
        "SELECT name,identity,coverage FROM source_receipts"
    ).fetchall():
        coverage = json.loads(encoded)
        removable = True
        for service, instance, spans in coverage:
            combined.setdefault((service, instance), []).extend(spans)
            if any(end > through.get((service, instance), 0) for _, end in spans):
                removable = False
        source, filename = name.split("/", 1)
        if (
            removable
            and source in roots
            and not (Path(roots[source]) / filename).exists()
        ):
            with ledger.db:
                ledger.db.execute(
                    "DELETE FROM source_receipts WHERE name=? AND identity=?",
                    (name, ident),
                )
                ledger.db.execute(
                    "DELETE FROM files WHERE name=? AND identity=?", (name, ident)
                )
    limits = dict(through)
    for key, spans in combined.items():
        end = through.get(key, 0)
        for first, last in sorted(spans):
            if first > end + 1:
                break
            end = max(end, last)
        limits[key] = end
    return {
        "source_retention_deleted_segments": deleted,
        "source_retention_deleted_bytes": deleted_bytes,
        "source_retention_failures": failed,
    }, limits
