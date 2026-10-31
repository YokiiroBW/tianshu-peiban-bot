"""Durable daily extension maintenance; external effects are reconciled before retry."""

import asyncio
from collections import Counter
from contextlib import AsyncExitStack
import hashlib
import os
from pathlib import Path
import re
import uuid
import zipfile

from ...contracts import Fault, require
from .extension_stores import STORES, fetch, read_store, select_packages

DAY = 86400
PENDING = {"queued", "applying", "verifying", "restoring", "busy"}


class Extensions:
    def __init__(self, comics):
        self.comics, self.media, self.repo = comics, comics.media, comics.repo
        self.engine = comics.engine
        self.lock = asyncio.Lock()
        self.installed_root = Path(
            os.environ.get("TIANSHU_COMIC_EXTENSIONS_DIRECTORY", "/media/SuwayomiExtensions")
        )

    async def policy(self):
        identity = await self.comics.sources.engine_id()
        row = await self.media.local(self.repo.get, "comic_extension_policy", identity)
        return row or {
            "engine_id": identity,
            "enabled": False,
            "revision": 0,
            "state": "idle",
            "next_check_at": 0,
            "interval_seconds": DAY,
            "stores": [],
        }

    def save_policy(self, identity, changes, db=None):
        if db is None:
            with self.repo.transaction() as transaction:
                return self.save_policy(identity, changes, transaction)
        previous = self.repo._document(db, "comic_extension_policy", identity) or {}
        if previous and "enabled" not in changes:
            return self.repo.fact(
                "comic_extension_policy", identity, changes, previous["revision"], db=db
            )
        return self.repo.put(
            "comic_extension_policy",
            identity,
            {**previous, **changes, "engine_id": identity, "interval_seconds": DAY},
            db=db,
        )

    def control(self, body, identity, db):
        action = body.get("action")
        require(action in {"enable", "pause", "run"}, "invalid_input", 400)
        current = self.repo._document(db, "comic_extension_policy", identity) or {}
        require(
            body.get("expected_revision", current.get("revision", 0)) == current.get("revision", 0),
            "version_conflict",
            409,
        )
        changes = {"enabled": action != "pause"}
        if action != "pause" and current.get("state") != "running":
            changes.update(next_check_at=0, state="queued")
        return {"policy": self.save_policy(identity, changes, db)}

    async def rows(self, identity):
        return [
            r
            for r in await self.media.local(self.repo.list, "comic_extension")
            if r["engine_id"] == identity
        ]

    async def view(self, body):
        policy = await self.policy()
        rows = await self.rows(policy["engine_id"])
        counts = dict(Counter(r["state"] for r in rows))
        kind, query, offset = (
            body.get("library_kind", "general"),
            body.get("query", ""),
            body.get("offset", 0),
        )
        require(
            kind in {"general", "private", "all"}
            and isinstance(query, str)
            and len(query) <= 200
            and type(offset) is int
            and 0 <= offset <= 20000,
            "invalid_input",
            400,
        )
        selected = [
            r
            for r in rows
            if (kind == "all" or (r["content_warning"] == "SAFE") == (kind == "general"))
            and query.casefold()
            in (r["name"] + " " + r["package"] + " " + " ".join(r["languages"])).casefold()
        ]
        selected.sort(key=lambda r: (r["state"] == "installed", r["name"].casefold()))
        fields = {
            "package",
            "name",
            "store_id",
            "version",
            "installed_version",
            "installed",
            "languages",
            "content_warning",
            "state",
            "code",
            "duplicates",
            "checked_at",
            "restored",
        }
        return {
            "policy": policy,
            "stores": [
                {**s, **next((r for r in policy.get("stores", []) if r["id"] == s["id"]), {})}
                for s in STORES
            ],
            "counts": counts,
            "catalog_total": len(rows),
            "installed_count": sum(r.get("installed", False) for r in rows),
            "plugins": [
                {k: v for k, v in r.items() if k in fields} for r in selected[offset : offset + 100]
            ],
            "total": len(selected),
            "next_offset": offset + 100 if offset + 100 < len(selected) else None,
        }

    async def put(self, row, **changes):
        return await self.media.local(
            self.repo.put,
            "comic_extension",
            row["engine_id"] + ":" + row["package"],
            {**row, **changes, "checked_at": self.repo.clock()},
        )

    async def prepare(self, policy):
        # Capture original bindings before Suwayomi refreshes its highest-version catalogue.
        installed = {e["pkgName"]: e for e in await self.engine.extensions() if e["isInstalled"]}
        prior = {r["package"]: r for r in await self.rows(policy["engine_id"])}
        pinned = {p: r["store_url"] for p, r in prior.items() if p in installed}
        known = await self.engine.extension_stores()
        candidates, stores = [], []
        old_stores = {r["id"]: r for r in policy.get("stores", [])}
        for store in STORES:
            status = {"id": store["id"], "state": "ready", "checked_at": self.repo.clock()}
            try:
                rows, key = await read_store(await self.engine.client(), store)
                old_key = old_stores.get(store["id"], {}).get("signing_key")
                require(not old_key or old_key == key, "comic_extension_store_changed", 409)
                actual_url = old_stores.get(store["id"], {}).get("actual_url", store["url"])
                if not any(s["indexUrl"] == actual_url for s in known):
                    registered = await self.engine.add_extension_store(store["url"])
                    actual_url = registered["indexUrl"]
                for row in rows:
                    row["store_url"] = actual_url
                candidates.extend(rows)
                status.update(count=len(rows), signing_key=key, actual_url=actual_url)
            except Fault as error:
                status.update(state="failed", code=error.code)
            stores.append(status)
        await self.engine.refresh_extensions()
        run_id = uuid.uuid4().hex
        selected = select_packages(candidates, installed, pinned)
        # A store outage cannot erase the installed package or its original origin.
        found = {r["package"] for r in selected}
        for pkg, current in installed.items():
            if pkg not in found:
                old = prior.get(pkg, {})
                selected.append(
                    {
                        **old,
                        "package": pkg,
                        "name": current["name"],
                        "store_id": old.get("store_id", "external"),
                        "store_url": old.get("store_url", current.get("storeIndexUrl")),
                        "version": current["versionName"],
                        "version_code": int(current["versionCodeLong"]),
                        "source_ids": [
                            str(s["id"]) for s in current.get("source", {}).get("nodes", [])
                        ],
                        "languages": old.get("languages", []),
                        "content_warning": current["contentWarning"],
                        "installed": True,
                        "installed_version": current["versionName"],
                        "state": "unlisted",
                        "code": "comic_extension_unlisted",
                    }
                )
        for row in selected:
            await self.put(
                {**row, "engine_id": policy["engine_id"], "run_id": run_id, "retry_at": 0}
            )
        return await self.media.local(
            self.save_policy,
            policy["engine_id"],
            {
                "state": "running",
                "run_id": run_id,
                "started_at": self.repo.clock(),
                "last_check_at": self.repo.clock(),
                "stores": stores,
                "current_package": None,
                "code": "comic_extensions_running",
            },
        )

    def stage(self, row, content, filename):
        require(
            re.fullmatch(r"[A-Za-z0-9_.+-]{1,240}", filename), "comic_extension_file_invalid", 409
        )
        directory = self.comics.data_root / "extensions" / row["package"]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / filename
        temporary = path.with_suffix(path.suffix + ".partial")
        with temporary.open("wb") as out:
            out.write(content)
            out.flush()
            os.fsync(out.fileno())
        try:
            with zipfile.ZipFile(temporary) as package:
                require(package.testzip() is None, "comic_extension_file_invalid", 409)
            temporary.replace(path)
        except zipfile.BadZipFile:
            raise Fault("comic_extension_file_invalid", 409) from None
        finally:
            temporary.unlink(missing_ok=True)
        return {"path": str(path), "sha256": hashlib.sha256(content).hexdigest()}

    def backup(self, row, current):
        filename = str(current.get("apkName") or "").removesuffix(".apk") + ".jar"
        require(
            re.fullmatch(r"[A-Za-z0-9_.+-]{1,240}\.jar", filename),
            "comic_extension_backup_unavailable",
            409,
        )
        path = self.installed_root / filename
        require(
            not path.is_symlink() and path.is_file() and path.stat().st_size <= 32 * 1024**2,
            "comic_extension_backup_unavailable",
            409,
        )
        return {
            **self.stage(row, path.read_bytes(), filename),
            "version": current["versionName"],
            "version_code": int(current["versionCodeLong"]),
            "source_ids": [str(s["id"]) for s in current["source"]["nodes"]],
        }

    @staticmethod
    def matches(current, version, source_ids):
        return bool(
            current
            and current["isInstalled"]
            and int(current["versionCodeLong"]) == version
            and set(source_ids) <= {str(s["id"]) for s in current["source"]["nodes"]}
        )

    async def restore(self, row):
        backup = row.get("backup")
        if not backup:
            return await self.put(
                row, state="failed", installed=False, code="comic_extension_install_failed"
            )
        current = await self.engine.extension(row["package"])
        if not self.matches(current, backup["version_code"], backup["source_ids"]):
            path = Path(backup["path"])
            require(
                path.is_relative_to(self.comics.data_root / "extensions")
                and hashlib.sha256(await self.media.local(path.read_bytes)).hexdigest()
                == backup["sha256"],
                "comic_extension_backup_invalid",
                409,
            )
            row = await self.put(row, state="restoring", retry_at=self.repo.clock() + 180)
            await self.engine.install_extension_file(path)
            current = await self.engine.extension(row["package"])
        require(
            self.matches(current, backup["version_code"], backup["source_ids"]),
            "comic_extension_restore_unverified",
            503,
        )
        return await self.put(
            row,
            state="failed",
            installed=True,
            installed_version=current["versionName"],
            restored=True,
            code="comic_extension_update_restored",
        )

    async def apply(self, row):
        current = await self.engine.extension(row["package"])
        wanted_ids = list(
            set(row["source_ids"]) | set((row.get("backup") or {}).get("source_ids", []))
        )
        if self.matches(current, row["version_code"], wanted_ids):
            return await self.put(
                row,
                state="installed",
                installed=True,
                installed_version=current["versionName"],
                code="comic_extension_verified",
            )
        locks = [
            self.engine.source_locks.setdefault(s, asyncio.Lock())
            for s in sorted(
                set(wanted_ids)
                | {str(s["id"]) for s in (current or {}).get("source", {}).get("nodes", [])}
            )
        ]
        if any(lock.locked() for lock in locks):
            return await self.put(
                row,
                state=row["state"]
                if row["state"] in {"applying", "verifying", "restoring"}
                else "busy",
                retry_at=self.repo.clock() + 30,
                code="comic_extension_source_busy",
            )
        async with AsyncExitStack() as stack:
            for lock in locks:
                await stack.enter_async_context(lock)
            if row["state"] in {"applying", "verifying", "restoring"}:
                return await self.restore(row)
            backup = (
                await self.media.local(self.backup, row, current)
                if current and current["isInstalled"]
                else None
            )
            content = await fetch(await self.engine.client(), row["artifact_url"])
            suffix = ".jar" if row["artifact_url"].endswith(".jar") else ".apk"
            staged = await self.media.local(
                self.stage, row, content, row["package"] + "-" + str(row["version_code"]) + suffix
            )
            row = await self.put(
                row,
                state="applying",
                backup=backup,
                artifact=staged,
                retry_at=self.repo.clock() + 180,
            )
            try:
                result = await self.engine.install_extension_file(staged["path"])
                require(result["pkgName"] == row["package"], "comic_extension_package_changed", 409)
                current = await self.engine.extension(row["package"])
                wanted_ids = list(
                    set(row["source_ids"]) | set((backup or {}).get("source_ids", []))
                )
                if not self.matches(current, row["version_code"], wanted_ids):
                    return await self.restore(row)
                return await self.put(
                    row,
                    state="installed",
                    installed=True,
                    installed_version=current["versionName"],
                    code="comic_extension_verified",
                )
            except Fault as error:
                # The server may still be applying after a timeout; observe before restoring.
                return await self.put(
                    row, state="verifying", code=error.code, retry_at=self.repo.clock() + 180
                )

    async def run_once(self):
        if self.lock.locked():
            return
        async with self.lock:
            policy = await self.policy()
            if (
                not policy["enabled"]
                or policy.get("retry_after", 0) > self.repo.clock()
                or (
                    policy.get("state") != "running"
                    and policy.get("next_check_at", 0) > self.repo.clock()
                )
            ):
                return
            key, owner = "comic-extensions:" + policy["engine_id"], uuid.uuid4().hex
            generation = await self.media.local(self.repo.scan_claim, key, owner)
            running = asyncio.current_task()

            async def renew():
                while True:
                    await asyncio.sleep(5)
                    try:
                        await self.media.local(self.repo.scan_heartbeat, key, owner, generation)
                    except Fault:
                        running.cancel()
                        return

            pulse = asyncio.create_task(renew())
            try:
                if policy.get("state") != "running":
                    policy = await self.prepare(policy)
                completed = 0
                for row in await self.rows(policy["engine_id"]):
                    if (
                        row.get("run_id") != policy["run_id"]
                        or row["state"] not in PENDING
                        or row.get("retry_at", 0) > self.repo.clock()
                    ):
                        continue
                    if not (await self.policy())["enabled"]:
                        return
                    await self.media.local(
                        self.save_policy, policy["engine_id"], {"current_package": row["name"]}
                    )
                    try:
                        await self.apply(row)
                    except (Fault, OSError) as error:
                        latest = await self.media.local(
                            self.repo.get,
                            "comic_extension",
                            policy["engine_id"] + ":" + row["package"],
                        )
                        uncertain = latest["state"] in {"applying", "verifying", "restoring"}
                        await self.put(
                            latest,
                            state=latest["state"] if uncertain else "failed",
                            retry_at=self.repo.clock() + 180,
                            code=error.code
                            if isinstance(error, Fault)
                            else "comic_extension_backup_unavailable",
                        )
                    completed += 1
                    if completed % 25 == 0:
                        await self.comics.sources.refresh()
                    await asyncio.sleep(0.15)
                await self.comics.sources.refresh()
                rows = await self.rows(policy["engine_id"])
                pending = any(
                    r.get("run_id") == policy["run_id"] and r["state"] in PENDING for r in rows
                )
                changes = {"current_package": None, "retry_after": 0}
                if not pending:
                    failed = any(
                        r.get("run_id") == policy["run_id"]
                        and r["state"] in {"failed", "origin_conflict", "source_conflict"}
                        for r in rows
                    ) or any(s["state"] == "failed" for s in policy.get("stores", []))
                    changes.update(
                        state="partial" if failed else "ready",
                        code="comic_extensions_partial" if failed else "comic_extensions_ready",
                        completed_at=self.repo.clock(),
                        next_check_at=self.repo.clock() + DAY,
                    )
                await self.media.local(self.save_policy, policy["engine_id"], changes)
            except Fault as error:
                current = await self.policy()
                await self.media.local(
                    self.save_policy,
                    policy["engine_id"],
                    {
                        "code": error.code,
                        "state": "running" if current.get("state") == "running" else "failed",
                        "retry_after": self.repo.clock() + 300,
                        "next_check_at": self.repo.clock() + 3600,
                    },
                )
            finally:
                pulse.cancel()
                await asyncio.gather(pulse, return_exceptions=True)
                await self.media.local(self.repo.scan_release, key, owner, generation)

    async def pump(self):
        try:
            await self.run_once()
        except Fault:
            pass
