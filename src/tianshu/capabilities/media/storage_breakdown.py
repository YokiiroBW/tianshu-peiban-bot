"""Single-flight background media accounting, scoped snapshots and cooperative shutdown."""

import asyncio
import math
import copy
import os
import threading
import time
import socket

from ..contracts import Fault, digest
from .storage_scan import ScanCancelled, map_location, scan_files, server_path
from .storage import media_storage

CACHE_SECONDS = 300


def initial_breakdown(volume, *, emby_configured, refreshing):
    available = volume["state"] == "available"
    return {
        "state": "scanning" if available else "unavailable",
        "subscription_bytes": None,
        "emby_bytes": None if emby_configured else 0,
        "other_bytes": None,
        "unused_bytes": volume["free_bytes"],
        "measured_at": None,
        "basis": "allocated" if os.name != "nt" else "logical",
        "code": "storage_scanning" if available else "storage_capacity_unavailable",
        "refreshing": available and refreshing,
    }


def project_breakdown(volume, measurement, measured_at, refreshing):
    subscription, emby = measurement["subscription_bytes"], measurement["emby_bytes"]
    other = None
    code = measurement["code"]
    state = "partial"
    if volume["state"] != "available":
        state, code = "unavailable", "storage_capacity_unavailable"
    elif subscription is not None and emby is not None:
        residual = volume["total_bytes"] - volume["free_bytes"] - subscription - emby
        if residual < 0:
            code = "storage_measurement_inconsistent"
        else:
            state, other = "ready", residual
            if measurement["basis"] == "logical":
                code = "storage_logical_estimate"
    return {
        "state": state,
        "subscription_bytes": subscription,
        "emby_bytes": emby,
        "other_bytes": other,
        "unused_bytes": volume["free_bytes"],
        "measured_at": measured_at,
        "basis": measurement["basis"],
        "code": code,
        "refreshing": refreshing,
    }


class StorageBreakdown:
    def __init__(self, media, *, monotonic=time.monotonic, clock=time.time):
        self.media = media
        self.monotonic, self.clock = monotonic, clock
        self.scope = None
        self.snapshot = None
        self.measured_at = None
        self.completed_at = None
        self.task = None
        self.cancel = None
        self.closed = False
        self.retired = set()
        self.file_work = set()
        self.scan_gate = asyncio.Lock()
        self.shared_cache = getattr(media, "shared_cache", None)
        self.node_key = (
            digest({"node": socket.gethostname(), "anchor": str(media.repo.database)})
            if self.shared_cache is not None
            else None
        )

    def shared_key(self, scope):
        return digest({"node": self.node_key, "scope": scope})

    def configuration(self):
        targets = copy.deepcopy(self.media.config["targets"])
        mappings = copy.deepcopy(self.media.config.get("storage_mappings", []))
        servers, direct = {}, []
        for target in targets.values():
            for server in target.get("servers", []):
                if server.get("kind") != "emby":
                    continue
                identity = server["server_id"]
                servers.setdefault(identity, server)
                if target.get("kind") == "media_server":
                    try:
                        prefix = server_path(server["media_root"])
                    except Fault:
                        continue
                    direct.append(
                        {
                            "server_id": identity,
                            "server_prefix": prefix,
                            "local_prefix": target["directory"],
                        }
                    )
        # Mapping input never creates a new server connection.
        return targets, servers, [*direct, *mappings]

    def view(self, storage):
        targets, servers, mappings = self.configuration()
        scope = digest(
            {
                "targets": targets,
                "mappings": mappings,
                "volumes": [
                    {"storage_id": v["storage_id"], "directories": v["directories"]}
                    for v in storage["volumes"]
                ],
            }
        )
        if scope != self.scope:
            self.invalidate()
            self.scope = scope
        if (
            storage["volumes"]
            and not self.closed
            and (self.snapshot is None or self.monotonic() - self.completed_at >= CACHE_SECONDS)
        ):
            if self.task is None or self.task.done():
                self.cancel = threading.Event()
                self.task = asyncio.create_task(
                    self.measure(
                        scope,
                        copy.deepcopy(storage["volumes"]),
                        targets,
                        servers,
                        mappings,
                        self.cancel,
                    )
                )
        refreshing = self.task is not None and not self.task.done()
        return {
            **storage,
            "volumes": [
                {
                    **volume,
                    "breakdown": project_breakdown(
                        volume, self.snapshot[volume["storage_id"]], self.measured_at, refreshing
                    )
                    if self.snapshot and volume["storage_id"] in self.snapshot
                    else initial_breakdown(
                        volume, emby_configured=bool(servers), refreshing=refreshing
                    ),
                }
                for volume in storage["volumes"]
            ],
        }

    def invalidate(self):
        if self.cancel:
            self.cancel.set()
        if self.task and not self.task.done():
            self.task.cancel()
            self.retired.add(self.task)
            self.task.add_done_callback(self.retired.discard)
        self.task = None
        self.snapshot, self.completed_at, self.measured_at = None, None, None

    async def refresh(self, *, force=False):
        """Observe/wait for one scoped measurement; normal views remain nonblocking."""
        if force and (self.task is None or self.task.done()):
            if self.shared_cache is not None and self.scope is not None:
                await asyncio.to_thread(
                    self.shared_cache.delete, "storage_breakdown", self.shared_key(self.scope)
                )
            self.invalidate()
        storage = await self.media.local(media_storage, dict(self.media.config["targets"]))
        self.view(storage)
        task = self.task
        if task is not None:
            await asyncio.shield(task)
        storage = await self.media.local(media_storage, dict(self.media.config["targets"]))
        return self.view(storage)

    async def locations(self, servers, mappings):
        roots, code = [], None
        for identity, server in servers.items():
            try:
                libraries = await self.media.servers.libraries(server)
                if not isinstance(libraries, list):
                    raise ValueError
                for library in libraries:
                    paths = library.get("paths", [])
                    if not isinstance(paths, list):
                        raise ValueError
                    for location in paths:
                        mapped = map_location(identity, location, mappings)
                        if mapped is None:
                            code = code or "storage_path_unmapped"
                        else:
                            roots.append(str(mapped))
            except (Fault, OSError, ValueError, TypeError, AttributeError):
                code = "storage_emby_unavailable"
        return list(dict.fromkeys(roots)), code

    async def measure(self, scope, volumes, targets, servers, mappings, cancel):
        try:
            if self.shared_cache is not None:
                shared = await asyncio.to_thread(
                    self.shared_cache.get, "storage_breakdown", self.shared_key(scope)
                )
                if (
                    isinstance(shared, dict)
                    and isinstance(shared.get("measurements"), dict)
                    and type(shared.get("measured_at")) in (int, float)
                    and type(shared.get("expires_at")) in (int, float)
                    and math.isfinite(shared["expires_at"])
                    and shared["expires_at"] > time.time()
                    and scope == self.scope
                    and not self.closed
                    and not cancel.is_set()
                ):
                    self.snapshot = shared["measurements"]
                    self.measured_at = shared["measured_at"]
                    self.completed_at = self.monotonic() - (
                        CACHE_SECONDS - (shared["expires_at"] - time.time())
                    )
                    return
            roots, discovery_code = await self.locations(servers, mappings)
            subscription_roots = [
                (volume["storage_id"], target["directory"])
                for volume in volumes
                for identity in volume["target_ids"]
                if (target := targets[identity]).get("kind") == "media_server"
            ]
            await self.scan_gate.acquire()
            work = asyncio.create_task(
                self.media.local(
                    scan_files, volumes, subscription_roots, roots, discovery_code, cancel
                )
            )
            self.file_work.add(work)

            def settled(task):
                self.file_work.discard(task)
                try:
                    task.exception()
                except asyncio.CancelledError:
                    pass
                finally:
                    self.scan_gate.release()

            work.add_done_callback(settled)
            try:
                measurements = await asyncio.shield(work)
            finally:
                if not work.done():
                    cancel.set()
            if not self.closed and scope == self.scope and not cancel.is_set():
                self.snapshot = measurements
                self.measured_at, self.completed_at = self.clock(), self.monotonic()
                if self.shared_cache is not None:
                    fetched = time.time()
                    await asyncio.to_thread(
                        self.shared_cache.set,
                        "storage_breakdown",
                        self.shared_key(scope),
                        {
                            "measurements": measurements,
                            "measured_at": self.measured_at,
                            "fetched_at": fetched,
                            "expires_at": fetched + CACHE_SECONDS,
                        },
                        ttl=CACHE_SECONDS,
                    )
        except (asyncio.CancelledError, ScanCancelled):
            cancel.set()
        except (Fault, OSError):
            if not self.closed and scope == self.scope and not cancel.is_set():
                self.snapshot = {
                    v["storage_id"]: {
                        "subscription_bytes": None,
                        "emby_bytes": None if servers else 0,
                        "basis": "allocated" if os.name != "nt" else "logical",
                        "code": "storage_path_unavailable",
                    }
                    for v in volumes
                }
                self.measured_at, self.completed_at = self.clock(), self.monotonic()

    async def close(self):
        self.closed = True
        self.invalidate()
        if self.retired:
            await asyncio.gather(*tuple(self.retired), return_exceptions=True)
        if self.file_work:
            await asyncio.shield(asyncio.gather(*tuple(self.file_work), return_exceptions=True))
