"""Operator-only in-place media registration and exact, currently authorized reuse."""

import asyncio
import re
from pathlib import Path

from ..contracts import Fault, digest, require
from .engine import file_record
from .probe_details import media_details
from .import_records import imported_path, inventory, manifest_entries, observation, same_identity
from .organizer import library_lock


def item_id(root_id, relative):
    return digest({"root_id": root_id, "relative_path": relative})


def nfo_bvid(path):
    # Only a hint when no operator row exists; this never proves CID or quality.
    for candidate in (path.with_suffix(".nfo"), path.parent / "movie.nfo"):
        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and candidate.stat().st_size <= 256 * 1024
        ):
            matches = set(
                re.findall(
                    r"BV[0-9A-Za-z]{10}", candidate.read_text(encoding="utf-8", errors="replace")
                )
            )
            if len(matches) == 1:
                return next(iter(matches))
    return None


class Imports:
    def __init__(self, media):
        self.media = media

    def root(self, identity):
        root = next(
            (
                root
                for root in self.media.config.get("import_roots", [])
                if root["root_id"] == identity
            ),
            None,
        )
        require(root is not None, "import_root_not_configured", 409)
        return dict(root)

    async def reconcile(self, root_id, *, evidence_manifest):
        root = self.root(root_id)
        entries = manifest_entries(evidence_manifest)
        lock = library_lock(
            Path(self.media.config["staging_directory"]) / ".imports" / digest(root_id)
        )
        in_flight = set()

        async def local(function, *args, **kwargs):
            pending = asyncio.create_task(self.media.local(function, *args, **kwargs))
            in_flight.add(pending)
            pending.add_done_callback(in_flight.discard)
            return await asyncio.shield(pending)

        def release(task=None):
            if task:
                try:
                    task.result()
                except BaseException:
                    pass
            lock.__exit__(None, None, None)

        acquisition = asyncio.create_task(self.media.local(lock.__enter__))
        try:
            await asyncio.shield(acquisition)
        except asyncio.CancelledError:

            def acquired(task):
                try:
                    task.result()
                except BaseException:
                    return
                release()

            acquisition.add_done_callback(acquired)
            raise
        result = {
            "root_id": root_id,
            "state": "running",
            "discovered": 0,
            "registered": 0,
            "cid_confirmed": 0,
            "verified": 0,
            "unknown": 0,
            "invalid": 0,
            "conflicts": 0,
            "cursor": None,
        }
        try:
            files = await local(inventory, root["directory"])
            require(set(entries) <= set(files), "import_manifest_file_missing", 409)
            result["discovered"] = len(files)
            await local(self.media.repo.put, "media_import_run", root_id, result)
            for relative in files:
                require(self.root(root_id) == root, "import_scope_changed", 409)
                identity = item_id(root_id, relative)
                old = await local(self.media.repo.get, "media_import", identity)
                record = {
                    "root_id": root_id,
                    "directory": root["directory"],
                    "relative_path": relative,
                    "bvid": None,
                    "cid": None,
                    "part_index": None,
                    "actual_quality": None,
                    "sha256": None,
                    "identity_confirmed": False,
                    "identity_evidence": None,
                    "quality_evidence": None,
                    "state": "unknown",
                    "code": "import_quality_unknown",
                    "missing_evidence": [],
                    "media": None,
                    "evidence_digest": None,
                }
                entry = entries.get(relative)
                try:
                    path = await local(imported_path, root["directory"], relative)
                    before = await local(observation, path)
                    record["observation"] = before
                    if (
                        old
                        and old["state"] in {"unknown", "verified"}
                        and old["directory"] == root["directory"]
                        and same_identity(old["observation"], before)
                    ):
                        if entry is None and old.get("identity_evidence"):
                            entry = {
                                key: old[key]
                                for key in (
                                    "relative_path",
                                    "bvid",
                                    "cid",
                                    "part_index",
                                    "identity_evidence",
                                    "quality_evidence",
                                )
                            }
                            entry.update(
                                size_bytes=before["size_bytes"], mtime_ns=before["mtime_ns"]
                            )
                        elif (
                            entry
                            and entry["quality_evidence"] is None
                            and all(entry[key] == old[key] for key in ("bvid", "cid", "part_index"))
                        ):
                            entry = {**entry, "quality_evidence": old["quality_evidence"]}
                    if entry:
                        record.update({key: entry[key] for key in ("bvid", "cid", "part_index")})
                        record["identity_evidence"] = entry["identity_evidence"]
                        record["quality_evidence"] = entry["quality_evidence"]
                        record["evidence_digest"] = digest(entry)
                        require(
                            all(before[key] == entry[key] for key in ("size_bytes", "mtime_ns")),
                            "import_observation_changed",
                            409,
                        )
                    else:
                        record["bvid"] = await local(nfo_bvid, path)
                    if (
                        old
                        and old["state"] in {"unknown", "verified"}
                        and old["directory"] == root["directory"]
                        and old["observation"] == before
                        and old["evidence_digest"] == record["evidence_digest"]
                    ):
                        record = {key: value for key, value in old.items() if key != "revision"}
                        saved = await local(
                            self.media.repo.import_commit, identity, record, old["revision"]
                        )
                        result["registered"] += 1
                        result["cid_confirmed"] += int(saved["identity_confirmed"])
                        result[saved["state"]] += 1
                        result["cursor"] = relative
                        await local(self.media.repo.put, "media_import_run", root_id, result)
                        continue
                    info = await self.media.engine.probe(path)
                    record["media"] = media_details(info)
                    duration = (
                        (entry.get("identity_evidence") or {}).get("expected_duration_seconds")
                        if entry
                        else None
                    )
                    self.media.engine.validate_probe(info, duration)
                    record["identity_confirmed"] = bool(entry and entry["identity_evidence"])
                    record["missing_evidence"] = [
                        name
                        for name, present in (
                            ("bvid", record["bvid"]),
                            ("cid", record["cid"]),
                            ("identity_evidence", record["identity_confirmed"]),
                            ("actual_quality", entry and entry["quality_evidence"]),
                        )
                        if not present
                    ]
                    if not record["missing_evidence"]:
                        quality = entry["quality_evidence"]
                        hashed = await local(file_record, path, relative, "video", record["cid"])
                        require(
                            hashed["sha256"] == quality["sha256"]
                            and hashed["size_bytes"] == quality["size_bytes"],
                            "import_evidence_conflict",
                            409,
                        )
                        record.update(
                            actual_quality=str(quality["quality"]),
                            sha256=hashed["sha256"],
                            state="verified",
                            code="import_verified",
                        )
                    require(
                        await local(observation, path) == before, "import_observation_changed", 409
                    )
                except (Fault, OSError, ValueError) as error:
                    record.update(
                        state="invalid",
                        actual_quality=None,
                        sha256=None,
                        code=error.code
                        if isinstance(error, Fault)
                        else "import_source_unavailable",
                    )
                try:
                    saved = await local(
                        self.media.repo.import_commit,
                        identity,
                        record,
                        old.get("revision", 0) if old else 0,
                    )
                    result["registered"] += 1
                    result["cid_confirmed"] += int(saved["identity_confirmed"])
                    result[saved["state"]] += 1
                except Fault as error:
                    if error.code not in {"version_conflict", "import_evidence_conflict"}:
                        raise
                    result["conflicts"] += 1
                result["cursor"] = relative
                await local(self.media.repo.put, "media_import_run", root_id, result)
            result["state"] = "partial" if result["invalid"] or result["conflicts"] else "completed"
            await local(self.media.repo.put, "media_import_run", root_id, result)
            return result
        except asyncio.CancelledError:
            result["state"] = "interrupted"
            await local(self.media.repo.put, "media_import_run", root_id, result)
            raise
        except (Fault, OSError):
            result["state"] = "interrupted"
            await local(self.media.repo.put, "media_import_run", root_id, result)
            raise
        finally:
            remaining = set(in_flight)
            if remaining:

                def finished(task):
                    try:
                        task.result()
                    except BaseException:
                        pass
                    remaining.discard(task)
                    if not remaining:
                        release()

                for task in tuple(remaining):
                    task.add_done_callback(finished)
            else:
                release()

    def source(self, candidate, job=None):
        root = self.root(candidate["root_id"])
        require(root["directory"] == candidate["directory"], "import_scope_changed", 409)
        record = self.media.repo.get("media_import", candidate["import_record_id"])
        require(
            record
            and record["state"] == "verified"
            and record["revision"] == candidate["import_record_revision"],
            "import_source_changed",
            409,
        )
        require(
            all(
                record[key] == candidate[key]
                for key in (
                    "bvid",
                    "cid",
                    "actual_quality",
                    "directory",
                    "relative_path",
                    "sha256",
                    "evidence_digest",
                )
            ),
            "import_source_changed",
            409,
        )
        path = imported_path(root["directory"], record["relative_path"])
        before = observation(path)
        require(same_identity(before, record["observation"]), "import_source_changed", 409)
        actual = file_record(path, record["relative_path"], "video", record["cid"])
        require(
            actual == candidate["entry"] and observation(path) == before,
            "import_source_changed",
            409,
        )
        require(
            self.root(candidate["root_id"]) == root
            and self.media.repo.get("media_import", candidate["import_record_id"])["revision"]
            == record["revision"],
            "import_source_changed",
            409,
        )
        download = {
            "file": str(path),
            "actual_quality": record["actual_quality"],
            "extension": path.suffix[1:],
            "sha256": actual["sha256"],
            "size_bytes": actual["size_bytes"],
            "source": {
                "kind": "import",
                "root_id": root["root_id"],
                "import_record_id": candidate["import_record_id"],
                "file": str(path),
                "sha256": actual["sha256"],
                "evidence_digest": record["evidence_digest"],
            },
        }
        return self.media.retention.pin(job, record["cid"], download) if job else download
