"""Time-limited planning observations, never an execution authority."""

import asyncio
from collections import OrderedDict
from datetime import UTC, datetime, timedelta
import logging
import time

from .catalog import Availability


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


class CatalogObservations:
    def __init__(self, catalog, *, ttl=60, timeout=5, maximum=4096):
        self.catalog, self.ttl, self.timeout, self.maximum = catalog, ttl, timeout, maximum
        self.entries = OrderedDict()
        self.pending = {}
        self.slots = asyncio.Semaphore(8)

    def rows(self, context, handlers, revisions):
        now = time.monotonic()
        rows, missing = [], []
        for handler in handlers:
            definition = handler.definition
            key = (context, definition.id)
            version = version_for(definition.id, revisions)
            entry = self.entries.get(key)
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
                "freshness": freshness,
                "observed_at": entry["observed_at"] if current else None,
                "expires_at": entry["expires_at"] if current else None,
            })
        if missing and context not in self.pending and len(self.pending) < 128:
            task = asyncio.create_task(self._refresh(context, missing), name="capability-observations")
            self.pending[context] = task
            task.add_done_callback(lambda done: self._finished(context, done))
        return rows

    def _finished(self, context, task):
        if self.pending.get(context) is task:
            self.pending.pop(context, None)
        if not task.cancelled():
            error = task.exception()
            if error:
                logger.warning("capability observation batch failed: %s", type(error).__name__)

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
            observed = datetime.now(UTC)
            ttl = self.ttl if known else min(self.ttl, 5)
            entry = {"handler": handler, "version": version, "availability": available,
                     "known": known, "expires": time.monotonic() + ttl,
                     "observed_at": observed.isoformat(),
                     "expires_at": (observed + timedelta(seconds=ttl)).isoformat()}
            self.entries[key] = entry
            self.entries.move_to_end(key)
            while len(self.entries) > self.maximum:
                self.entries.popitem(last=False)
            if known and (old is None or not old["known"] or old["availability"] != available
                          or old["version"] != version or old["expires"] <= time.monotonic()):
                return identifier
            return None

    async def _refresh(self, context, missing):
        tasks = [asyncio.create_task(self._one(context, handler, version)) for handler, version in missing]
        first_sent, remaining = False, []

        async def notify(changed):
            if changed and self.catalog.on_change is not None:
                try:
                    await self.catalog.on_change(context, tuple(changed))
                except Exception as error:
                    logger.warning("capability observation notification failed: %s", type(error).__name__)

        try:
            for completed in asyncio.as_completed(tasks):
                identifier = await completed
                if identifier is None:
                    continue
                if not first_sent:
                    # An unrelated slow probe must not hold the first usable
                    # observation or the planner wake behind the entire batch.
                    await notify((identifier,))
                    first_sent = True
                else:
                    remaining.append(identifier)
            await notify(remaining)
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def close(self):
        tasks = tuple(self.pending.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self.pending.clear()
        self.entries.clear()
