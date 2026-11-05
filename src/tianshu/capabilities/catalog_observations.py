"""Time-limited planning observations, never an execution authority."""

import asyncio
from collections import OrderedDict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import logging
import time

from .catalog import Availability
from .contracts import digest
from tianshu.runtime.serialization import wire


logger = logging.getLogger(__name__)

# Changes in a configuration family invalidate only its dependent projections.
DEPENDENCIES = (
    ("images.", ("image_engine", "image_profile", "image_workflow")),
    ("speech.", ("speech_connection", "voice_profile", "dictionary")),
    ("content.discover", ("content_provider",)),
    ("devices.", ("device_connection",)),
    ("device.", ("device_connection",)),
    ("game.", ("game_connection",)),
    ("weather.", ("weather_connection",)),
    ("resources.assets.", ("asset_connection",)),
    ("resources.archive.", ("archive_connection",)),
)


def version_for(identifier, revisions):
    kinds = {kind for prefix, keys in DEPENDENCIES if identifier.startswith(prefix) for kind in keys}
    return tuple((kind, revisions.get(kind, ())) for kind in sorted(kinds))


def semantic_availability(availability):
    """Exclude probe time while retaining eligibility and provider constraints."""
    return replace(availability, verified_at=None)


class CatalogObservations:
    def __init__(self, catalog, *, ttl=60, timeout=5, maximum=4096):
        self.catalog, self.ttl, self.timeout, self.maximum = catalog, ttl, timeout, maximum
        self.entries = OrderedDict()
        self.pending = {}
        self.deferred = {}
        self.closing = False
        self.slots = asyncio.Semaphore(8)

    def semantic_digest(self, context):
        """Snapshot known raw observations without I/O or starting any probes.

        Actor policy and declarations are projected separately by Catalog.
        This digest is only a change watermark, never execution authority.
        """
        values = []
        for (owner, identifier), entry in self.entries.items():
            last = entry.get("last_known")
            if (owner == context and last
                    and self.catalog.handlers.get(identifier) is entry["handler"]):
                values.append((identifier, wire(semantic_availability(last["availability"]))))
        return digest(sorted(values, key=lambda value: value[0]))

    def rows(self, context, handlers, revisions):
        now = time.monotonic()
        rows, missing = [], []
        for handler in handlers:
            definition = handler.definition
            key = (context, definition.id)
            version = version_for(definition.id, revisions)
            entry = self.entries.get(key)
            last_known = (entry.get("last_known") if entry and entry["handler"] is handler else None)
            current = bool(entry and entry["handler"] is handler and entry["version"] == version
                           and entry["expires"] > now)
            if current:
                self.entries.move_to_end(key)
                available = entry["availability"]
                freshness = "current" if entry["known"] else "unknown"
            else:
                available = Availability(True, False, reason="availability_unknown")
                freshness = "expired" if entry else "unknown"
                missing.append((handler, version))
            rows.append({
                "definition": definition,
                "availability": available,
                "can_execute": available.can_execute if current and entry["known"] else False,
                "semantic_availability": semantic_availability(last_known["availability"]) if last_known else None,
                "freshness": freshness,
                "observed_at": last_known["observed_at"] if last_known else None,
                "expires_at": last_known["expires_at"] if last_known else None,
                "observation_error": entry["availability"].reason if current and not entry["known"] else None,
            })
        if missing and context in self.pending:
            deferred = self.deferred.setdefault(context, {})
            deferred.update({handler.definition.id: (handler, version) for handler, version in missing})
        elif missing and len(self.pending) < 128 and not self.closing:
            self._start(context, missing)
        return rows

    def _start(self, context, missing):
        task = asyncio.create_task(self._refresh(context, missing), name="capability-observations")
        self.pending[context] = task
        task.add_done_callback(lambda done: self._finished(context, done))

    def _finished(self, context, task):
        if self.pending.get(context) is task:
            self.pending.pop(context, None)
        if not task.cancelled():
            error = task.exception()
            if error:
                logger.warning("capability observation batch failed: %s", type(error).__name__)
        deferred = self.deferred.pop(context, {})
        if deferred and not self.closing:
            now = time.monotonic()
            missing = []
            for handler, version in deferred.values():
                entry = self.entries.get((context, handler.definition.id))
                if not (entry and entry["handler"] is handler and entry["version"] == version
                        and entry["expires"] > now):
                    missing.append((handler, version))
            if missing:
                self._start(context, missing)

    async def _one(self, context, handler, version):
        async with self.slots:
            try:
                available = await asyncio.wait_for(handler.availability(context), self.timeout)
                if not isinstance(available, Availability):
                    raise TypeError("availability_required")
                known = True
            except Exception as error:
                code = getattr(error, "code", "availability_observation_failed")
                available = Availability(True, False, reason=code)
                known = False
            # A replaced handler is a changed declaration, not the same result.
            identifier = handler.definition.id
            if self.catalog.handlers.get(identifier) is not handler:
                return None
            key = (context, identifier)
            old = self.entries.get(key)
            last_known = (old.get("last_known") if old and old["handler"] is handler else None)
            observed = datetime.now(UTC)
            ttl = self.ttl if known else min(self.ttl, 5)
            entry = {"handler": handler, "version": version, "availability": available,
                     "known": known, "expires": time.monotonic() + ttl,
                     "observed_at": observed.isoformat(),
                     "expires_at": (observed + timedelta(seconds=ttl)).isoformat()}
            if known:
                entry["last_known"] = {"availability": available, "observed_at": entry["observed_at"],
                                       "expires_at": entry["expires_at"]}
            else:
                entry["last_known"] = last_known
            self.entries[key] = entry
            self.entries.move_to_end(key)
            while len(self.entries) > self.maximum:
                self.entries.popitem(last=False)
            if known and (last_known is None or semantic_availability(last_known["availability"])
                          != semantic_availability(available)):
                return "change", identifier
            if known:
                return "ready", identifier
            return None

    async def _refresh(self, context, missing):
        tasks = [asyncio.create_task(self._one(context, handler, version)) for handler, version in missing]
        notifications = []
        buffered = {"change": set(), "ready": set()}
        scheduled = set()

        async def notify(kind):
            # Coalesce probes that settle together without waiting for a slow
            # probe or serializing all subsequent wakeups behind one callback.
            await asyncio.sleep(0.01)
            identifiers = tuple(sorted(buffered[kind]))
            buffered[kind].clear()
            scheduled.discard(kind)
            callback = self.catalog.on_change if kind == "change" else self.catalog.on_ready
            if callback is not None and identifiers:
                try:
                    await callback(context, identifiers)
                except Exception as error:
                    logger.warning("capability observation notification failed: %s", type(error).__name__)

        try:
            for completed in asyncio.as_completed(tasks):
                result = await completed
                if result is None:
                    continue
                # Every completed observation can wake its consumer; neither a
                # slow probe nor a slow notification holds unrelated results.
                kind, identifier = result
                buffered[kind].add(identifier)
                if kind not in scheduled:
                    scheduled.add(kind)
                    notifications.append(asyncio.create_task(notify(kind)))
            if notifications:
                await asyncio.gather(*notifications)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            for task in notifications:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.gather(*notifications, return_exceptions=True)

    async def close(self):
        self.closing = True
        tasks = tuple(self.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.deferred.clear()
        self.entries.clear()
