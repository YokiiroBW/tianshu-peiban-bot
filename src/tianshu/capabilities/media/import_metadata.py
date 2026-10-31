"""Resumable operator preparation for read-only historical media, without jobs."""

import asyncio
import re
import uuid
from pathlib import Path

from ..contracts import Fault, digest, require
from .import_metadata_files import discover, entry_id, export_plan, read_nfo, save_candidate
from .import_metadata_sources import SourceBatch
from .import_nfo import created_nfo, parse, render
from .import_records import imported_path, observation, same_identity
from .organizer import library_lock
from .probe_details import media_details


class ImportMetadata:
    def __init__(self, media, directory):
        self.media, self.directory = media, Path(directory)

    async def measured(self, root, records, local):
        result = []
        for record in records:
            path = await local(imported_path, root["directory"], record["relative_path"])
            before = await local(observation, path)
            require(
                same_identity(before, record.get("observation")), "import_observation_changed", 409
            )
            observed = record.get("media") or {}
            if record.get("observation") == before and "rawffprobe_streams" in observed:
                actual = media_details(
                    {
                        "streams": observed["rawffprobe_streams"],
                        "format": {"duration": observed.get("duration_seconds")},
                    }
                )
            else:
                info = await self.media.engine.probe(path)
                # A pre-existing duration mismatch remains an inventory fact, not a new rejection.
                self.media.engine.validate_probe(info)
                actual = media_details(info)
            require(await local(observation, path) == before, "import_observation_changed", 409)
            quality_known = (
                record["state"] == "verified"
                and record.get("actual_quality")
                and record.get("sha256")
                and record.get("observation") == before
            )
            result.append(
                {
                    **record,
                    "observation": before,
                    "media": actual,
                    "actual_quality": record.get("actual_quality") if quality_known else None,
                    "sha256": record.get("sha256") if quality_known else None,
                }
            )
        return result

    async def prepare(
        self,
        root_id,
        *,
        plan_id=None,
        output_directory=None,
        resolved_by_bvid=None,
        source_errors=None,
        bvids=None,
        request_interval=1.0,
    ):
        root = self.media.imports.root(root_id)
        plan_id = plan_id or uuid.uuid4().hex
        require(
            isinstance(plan_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", plan_id),
            "invalid_input",
            400,
        )
        require(
            type(request_interval) in {int, float} and 0 <= request_interval <= 60,
            "invalid_input",
            400,
        )
        supplied, errors = resolved_by_bvid or {}, source_errors or {}
        require(isinstance(supplied, dict) and isinstance(errors, dict), "invalid_input", 400)
        selected = set(bvids) if bvids is not None else None
        previous = await self.media.local(self.media.repo.get, "import_metadata_plan", plan_id)
        directory = Path(
            output_directory or (previous or {}).get("output_directory") or self.directory / plan_id
        ).absolute()
        require(
            all(
                not directory.is_relative_to(Path(item["directory"]).absolute())
                for item in self.media.config.get("import_roots", [])
            ),
            "import_output_overlaps_source",
            409,
        )
        if previous:
            require(
                previous["root_id"] == root_id
                and previous["root_directory"] == root["directory"]
                and previous["output_directory"] == str(directory),
                "import_scope_changed",
                409,
            )
        lock = library_lock(self.directory / ".plans" / plan_id)
        tasks = set()

        async def local(function, *args):
            pending = asyncio.create_task(self.media.local(function, *args))
            tasks.add(pending)
            pending.add_done_callback(tasks.discard)
            return await asyncio.shield(pending)

        acquisition = asyncio.create_task(self.media.local(lock.__enter__))
        try:
            await asyncio.shield(acquisition)
        except asyncio.CancelledError:

            def acquired(task):
                try:
                    task.result()
                except BaseException:
                    return
                lock.__exit__(None, None, None)

            acquisition.add_done_callback(acquired)
            raise
        # State belongs to this call. A single directory lock covers the durable plan.
        run = SourceBatch(self.media, plan_id, local)
        plan = {
            "schema_version": 1,
            "plan_id": plan_id,
            "root_id": root_id,
            "root_directory": root["directory"],
            "output_directory": str(directory),
            "state": "running",
            "created_at": (previous or {}).get("created_at", self.media.repo.clock()),
            "updated_at": self.media.repo.clock(),
            "discovery_failures": [],
        }
        entries, videos = [], dict((previous or {}).get("videos", {}))
        saved_plan = False
        try:
            previous = await local(self.media.repo.get, "import_metadata_plan", plan_id)
            if previous:
                require(
                    previous["root_id"] == root_id
                    and previous["root_directory"] == root["directory"]
                    and previous["output_directory"] == str(directory),
                    "import_scope_changed",
                    409,
                )
                videos = dict(previous.get("videos", {}))
                plan["created_at"] = previous["created_at"]
            records = [
                record
                for record in await local(self.media.repo.list, "media_import")
                if record["root_id"] == root_id and record["directory"] == root["directory"]
            ]
            require(records, "import_inventory_missing", 409)
            require(
                selected is None or selected <= {record.get("bvid") for record in records},
                "invalid_input",
                400,
            )
            groups, failures, creations = await local(discover, Path(root["directory"]), records)
            plan["discovery_failures"] = failures
            entries = [
                entry
                for entry in await local(self.media.repo.list, "import_metadata_entry")
                if entry["plan_id"] == plan_id
            ]
            old_entries = {entry["nfo_relative_path"]: entry for entry in entries}
            entries = []
            all_by_bv = {}
            for record in records:
                if record.get("bvid") and record.get("cid") and record.get("identity_evidence"):
                    all_by_bv.setdefault(record["bvid"], []).append(record)
            sources, avatars, measured_by_path = {}, {}, {}
            await local(self.media.repo.put, "import_metadata_plan", plan_id, plan)
            saved_plan = True
            for relative, parts in sorted(
                groups.items(), key=lambda item: (item[1][0].get("bvid") or "", item[0])
            ):
                require(self.media.imports.root(root_id) == root, "import_scope_changed", 409)
                bvid = parts[0].get("bvid")
                entry = {
                    "plan_id": plan_id,
                    "entry_id": entry_id(plan_id, relative),
                    "nfo_relative_path": relative,
                    "type": None,
                    "bvid": bvid,
                    "cid": parts[0].get("cid") if len(parts) == 1 else None,
                    "cids": [part.get("cid") for part in parts],
                    "video_relative_paths": [part["relative_path"] for part in parts],
                    "action": "create" if relative in creations else "update",
                    "state": "pending",
                    "code": None,
                    "warnings": [],
                    "original_sha256": None,
                    "after_sha256": None,
                    "candidate_file": None,
                }
                old = old_entries.get(relative)
                if selected is not None and bvid not in selected:
                    entries.append(
                        old
                        or await local(
                            self.media.repo.put, "import_metadata_entry", entry["entry_id"], entry
                        )
                    )
                    continue
                try:
                    require(
                        bvid
                        and all(
                            part.get("bvid") == bvid
                            and part.get("cid")
                            and part.get("identity_evidence")
                            for part in parts
                        ),
                        "import_metadata_identity_conflict",
                        409,
                    )
                    creation = creations.get(relative)
                    if creation:
                        path = await local(imported_path, root["directory"], relative)
                        require(not await local(path.exists), "import_nfo_changed", 409)
                        raw, before_sha = None, None
                        entry["type"] = creation["type"]
                    else:
                        raw, before_sha = await local(read_nfo, root["directory"], relative)
                        entry.update(type=parse(raw).tag, original_sha256=before_sha)
                    if entry["type"] == "tvshow":
                        entry["cid"] = None
                    for part in parts:
                        if part["relative_path"] not in measured_by_path:
                            measured_by_path[part["relative_path"]] = (
                                await self.measured(root, [part], local)
                            )[0]
                    measured = [measured_by_path[part["relative_path"]] for part in parts]
                    for record in measured:
                        videos[record["relative_path"]] = {
                            "import_id": digest(
                                {"root_id": root_id, "relative_path": record["relative_path"]}
                            ),
                            "relative_path": record["relative_path"],
                            "observation": record["observation"],
                            "import_state": record["state"],
                            "import_code": record.get("code"),
                            "actual_quality": record.get("actual_quality"),
                            "expected_duration_seconds": (
                                record.get("identity_evidence") or {}
                            ).get("expected_duration_seconds"),
                            "actual_duration_seconds": record["media"].get("duration_seconds"),
                        }
                    if bvid not in sources:
                        sources[bvid] = await run.obtain(
                            bvid, all_by_bv[bvid], supplied, errors, request_interval
                        )
                        resolved = sources[bvid].get("resolved")
                        avatars[bvid] = (
                            await self.media.creator_cache.prepare(resolved) if resolved else []
                        )
                    source = sources[bvid]
                    resolved = source.get("resolved")
                    entry["creator_avatars"] = avatars[bvid]
                    entry["snapshot"] = resolved["snapshot"] if resolved else None
                    entry["metadata_source"] = (
                        {
                            "kind": resolved.get("source_kind", "bilibili"),
                            "digest": resolved.get("source_digest"),
                            "warning": resolved.get("source_warning"),
                        }
                        if resolved
                        else None
                    )
                    entry["warnings"] = sorted(
                        {
                            record["code"]
                            for record in measured
                            if record.get("code") and record["state"] != "verified"
                        }
                        | {avatar["code"] for avatar in avatars[bvid] if avatar["state"] != "ready"}
                    )
                    if not resolved:
                        entry["warnings"].append(source["code"])
                    elif resolved.get("source_warning"):
                        entry["warnings"].append(resolved["source_warning"])
                    # A replay after application preserves the original before digest and candidate.
                    if (
                        old
                        and before_sha == old.get("after_sha256")
                        and old.get("snapshot") == entry["snapshot"]
                        and old.get("metadata_source") == entry["metadata_source"]
                        and old.get("video_observations")
                        == {record["relative_path"]: record["observation"] for record in measured}
                    ):
                        entry = {
                            **old,
                            "state": "already_applied",
                            "creator_avatars": entry["creator_avatars"],
                            "warnings": entry["warnings"],
                        }
                    else:
                        if old and (old.get("original_sha256") or old.get("action") == "create"):
                            require(
                                before_sha == old.get("original_sha256"), "import_nfo_changed", 409
                            )
                        if creation:
                            template = None
                            if creation.get("template_nfo_relative_path"):
                                template, template_sha = await local(
                                    read_nfo,
                                    root["directory"],
                                    creation["template_nfo_relative_path"],
                                )
                                entry.update(
                                    template_nfo_relative_path=creation[
                                        "template_nfo_relative_path"
                                    ],
                                    template_sha256=template_sha,
                                )
                            raw = created_nfo(creation, measured, resolved, template)
                        candidate = render(raw, bvid, measured, resolved)
                        if creation:
                            require(not await local(path.exists), "import_nfo_changed", 409)
                        else:
                            require(
                                (await local(read_nfo, root["directory"], relative))[1]
                                == before_sha,
                                "import_nfo_changed",
                                409,
                            )
                        for record in measured:
                            path = await local(
                                imported_path, root["directory"], record["relative_path"]
                            )
                            require(
                                await local(observation, path) == record["observation"],
                                "import_observation_changed",
                                409,
                            )
                        entry.update(await local(save_candidate, directory, candidate))
                        entry["video_observations"] = {
                            record["relative_path"]: record["observation"] for record in measured
                        }
                        entry["state"] = "ready" if resolved else "partial"
                        if entry["after_sha256"] == before_sha:
                            entry["state"] = "unchanged"
                        entry["code"] = None if resolved else source["code"]
                    if old and all(
                        old.get(key) == value for key, value in entry.items() if key != "revision"
                    ):
                        entry = old
                    else:
                        entry = await local(
                            self.media.repo.put, "import_metadata_entry", entry["entry_id"], entry
                        )
                except (Fault, OSError, ValueError) as error:
                    entry.update(
                        state="failed",
                        code=error.code
                        if isinstance(error, Fault)
                        else "import_source_unavailable",
                        candidate_file=None,
                        after_sha256=None,
                    )
                    if old and old.get("original_sha256"):
                        entry["original_sha256"] = old["original_sha256"]
                    elif old and old.get("action") == "create":
                        entry.update(action="create", original_sha256=None)
                    entry = await local(
                        self.media.repo.put, "import_metadata_entry", entry["entry_id"], entry
                    )
                entries.append(entry)
            plan["state"] = (
                "partial"
                if failures
                or any(entry["state"] in {"failed", "partial", "pending"} for entry in entries)
                else "completed"
            )
            plan["updated_at"] = self.media.repo.clock()
            plan["videos"] = videos
            plan["entry_ids"] = [entry["entry_id"] for entry in entries]
            await local(self.media.repo.put, "import_metadata_plan", plan_id, plan)
            return await local(export_plan, plan, entries, videos)
        except asyncio.CancelledError:
            if not saved_plan:
                raise
            plan.update(
                state="interrupted",
                videos=videos,
                entry_ids=[entry["entry_id"] for entry in entries],
                updated_at=self.media.repo.clock(),
            )
            await local(self.media.repo.put, "import_metadata_plan", plan_id, plan)
            await local(export_plan, plan, entries, videos)
            raise
        except (Fault, OSError):
            if not saved_plan:
                raise
            plan.update(
                state="interrupted",
                videos=videos,
                entry_ids=[entry["entry_id"] for entry in entries],
                updated_at=self.media.repo.clock(),
            )
            await local(self.media.repo.put, "import_metadata_plan", plan_id, plan)
            await local(export_plan, plan, entries, videos)
            raise
        finally:
            remaining = set(tasks)
            if remaining:

                def finished(task):
                    try:
                        task.result()
                    except BaseException:
                        pass
                    remaining.discard(task)
                    if not remaining:
                        lock.__exit__(None, None, None)

                for task in tuple(remaining):
                    task.add_done_callback(finished)
            else:
                lock.__exit__(None, None, None)

    async def plan(self, plan_id):
        plan = await self.media.local(self.media.repo.get, "import_metadata_plan", plan_id)
        require(plan is not None, "import_plan_not_found", 404)
        root = self.media.imports.root(plan["root_id"])
        require(root["directory"] == plan["root_directory"], "import_scope_changed", 409)
        entries = [
            entry
            for entry in await self.media.local(self.media.repo.list, "import_metadata_entry")
            if entry["plan_id"] == plan_id and entry["entry_id"] in plan.get("entry_ids", [])
        ]
        return await self.media.local(export_plan, plan, entries, plan.get("videos", {}))

    async def verify(self, plan_id, *, server_id, library_id, server_root, refresh=True):
        from .import_metadata_verify import verify_plan

        return await verify_plan(
            self.media, await self.plan(plan_id), server_id, library_id, server_root, refresh
        )
