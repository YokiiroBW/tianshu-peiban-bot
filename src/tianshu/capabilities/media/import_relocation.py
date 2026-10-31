"""Trusted local maintenance for an exact import-root path relocation.

The operator creates the destination files first and keeps the originals through commit.
This module never creates, copies, moves, deletes or probes media. Call its synchronous
methods through ``media.local`` when using an asynchronous application.
"""

import copy
import os
import re
from pathlib import Path

from ..contracts import digest, require
from .engine import file_record
from .import_records import imported_path, observation, relative_path, same_identity
from .imports import item_id
from .organizer import library_lock


def request_plan(value):
    require(
        isinstance(value, dict)
        and set(value)
        == {
            "schema_version",
            "migration_id",
            "root_id",
            "old_directory",
            "new_directory",
            "entries",
        }
        and value["schema_version"] == 1,
        "import_relocation_invalid_plan",
        400,
    )
    for field, maximum in (("migration_id", 100), ("root_id", 64)):
        require(
            isinstance(value[field], str)
            and re.fullmatch(r"[A-Za-z0-9_-]{1," + str(maximum) + "}", value[field]),
            "import_relocation_invalid_plan",
            400,
        )
    for field in ("old_directory", "new_directory"):
        directory = value[field]
        require(
            isinstance(directory, str)
            and "\0" not in directory
            and Path(directory).is_absolute()
            and ".." not in Path(directory).parts
            and directory == str(Path(directory).absolute()),
            "import_relocation_invalid_plan",
            400,
        )
    require(value["old_directory"] != value["new_directory"], "import_relocation_invalid_plan", 400)
    require(
        isinstance(value["entries"], list) and value["entries"],
        "import_relocation_invalid_plan",
        400,
    )
    old_paths, new_paths = set(), set()
    for entry in value["entries"]:
        require(
            isinstance(entry, dict)
            and set(entry) == {"old_relative_path", "new_relative_path", "expected_record"},
            "import_relocation_invalid_plan",
            400,
        )
        old, new = (
            relative_path(entry["old_relative_path"]),
            relative_path(entry["new_relative_path"]),
        )
        require(
            Path(old).suffix.lower() in {".mp4", ".mkv"}
            and Path(new).suffix.lower() == Path(old).suffix.lower(),
            "import_relocation_invalid_plan",
            400,
        )
        destination = os.path.normcase(str(Path(value["new_directory"]).joinpath(*new.split("/"))))
        require(
            old not in old_paths and destination not in new_paths,
            "import_relocation_path_conflict",
            409,
        )
        old_paths.add(old)
        new_paths.add(destination)
        record = entry["expected_record"]
        require(
            isinstance(record, dict)
            and type(record.get("revision")) is int
            and record["revision"] > 0
            and record.get("root_id") == value["root_id"]
            and record.get("directory") == value["old_directory"]
            and record.get("relative_path") == old
            and record.get("state") in {"unknown", "invalid", "verified"},
            "import_relocation_invalid_plan",
            400,
        )
    result = copy.deepcopy(value)
    result["entries"].sort(key=lambda entry: entry["old_relative_path"])
    return result


class ImportRelocation:
    def __init__(self, media):
        self.media = media

    def _scope(self, plan, *, commit=False):
        root = self.media.imports.root(plan["root_id"])
        require(
            root["directory"] == plan["new_directory"]
            if commit
            else root["directory"] in {plan["old_directory"], plan["new_directory"]},
            "import_scope_changed",
            409,
        )
        return root

    def _lock(self, root_id):
        return library_lock(
            Path(self.media.config["staging_directory"]) / ".imports" / digest(root_id)
        )

    def _records(self, plan):
        records = [
            record
            for record in self.media.repo.list("media_import")
            if record["root_id"] == plan["root_id"] and record["directory"] == plan["old_directory"]
        ]
        require(
            {record["relative_path"]: record for record in records}
            == {entry["old_relative_path"]: entry["expected_record"] for entry in plan["entries"]},
            "version_conflict",
            409,
        )
        for entry in plan["entries"]:
            new_id = item_id(plan["root_id"], entry["new_relative_path"])
            old_id = item_id(plan["root_id"], entry["old_relative_path"])
            require(
                new_id == old_id
                or (
                    self.media.repo.get("media_import", new_id) is None
                    and self.media.repo.get("media_source", "import:" + new_id) is None
                ),
                "import_relocation_path_conflict",
                409,
            )

    def _proof(self, plan, entry, previous=None):
        old = imported_path(plan["old_directory"], entry["old_relative_path"])
        new = imported_path(plan["new_directory"], entry["new_relative_path"])
        before, after = observation(old), observation(new)
        record = entry["expected_record"]
        require(same_identity(record.get("observation"), before), "import_observation_changed", 409)
        require(
            before["size_bytes"] == after["size_bytes"], "import_relocation_content_changed", 409
        )
        if previous:
            require(
                previous["old_observation"] == before and previous["new_observation"] == after,
                "import_observation_changed",
                409,
            )
        proof = {
            "old_observation": before,
            "new_observation": after,
            "method": "hardlink",
            "sha256": None,
        }
        if not same_identity(before, after):
            original = file_record(old, entry["old_relative_path"], "video", record.get("cid"))
            relocated = file_record(new, entry["new_relative_path"], "video", record.get("cid"))
            require(
                original["sha256"] == relocated["sha256"]
                and original["size_bytes"] == relocated["size_bytes"] == before["size_bytes"]
                and (not record.get("sha256") or original["sha256"] == record["sha256"])
                and (not previous or original["sha256"] == previous["sha256"]),
                "import_relocation_content_changed",
                409,
            )
            proof.update(method="sha256", sha256=original["sha256"])
        require(
            observation(old) == before and observation(new) == after,
            "import_observation_changed",
            409,
        )
        return proof

    def _relocated(self, plan, entry, proof):
        record = copy.deepcopy(entry["expected_record"])
        record.pop("revision")
        record.update(
            directory=plan["new_directory"],
            relative_path=entry["new_relative_path"],
            observation=proof["new_observation"],
        )
        identity = record.get("identity_evidence")
        if identity:
            require(
                identity.get("relative_path") == entry["old_relative_path"],
                "import_evidence_conflict",
                409,
            )
            identity["relative_path"] = entry["new_relative_path"]
        if record.get("evidence_digest") is not None:
            record["evidence_digest"] = digest(
                {
                    key: record.get(key)
                    for key in (
                        "relative_path",
                        "bvid",
                        "cid",
                        "part_index",
                        "identity_evidence",
                        "quality_evidence",
                    )
                }
                | {
                    "size_bytes": proof["new_observation"]["size_bytes"],
                    "mtime_ns": proof["new_observation"]["mtime_ns"],
                }
            )
        record["relocation"] = {
            "migration_id": plan["migration_id"],
            "old_record_id": item_id(plan["root_id"], entry["old_relative_path"]),
            "old_directory": plan["old_directory"],
            "old_relative_path": entry["old_relative_path"],
            "old_evidence_digest": entry["expected_record"].get("evidence_digest"),
            "proof_digest": digest(proof),
        }
        return record

    def _historical_metadata(self, request):
        mapping = {
            entry["old_relative_path"]: entry["new_relative_path"] for entry in request["entries"]
        }
        result = []
        entries = self.media.repo.list("import_metadata_entry")
        for plan in self.media.repo.list("import_metadata_plan"):
            if (
                plan.get("root_id") != request["root_id"]
                or plan.get("root_directory") != request["old_directory"]
            ):
                continue
            result.append(
                {
                    "plan_id": plan["plan_id"],
                    "revision": plan["revision"],
                    "digest": digest(plan),
                    "entries": [
                        {
                            "entry_id": entry["entry_id"],
                            "revision": entry["revision"],
                            "digest": digest(entry),
                            "old_nfo_relative_path": entry["nfo_relative_path"],
                            "video_paths": [
                                {"old_relative_path": path, "new_relative_path": mapping.get(path)}
                                for path in entry.get("video_relative_paths", [])
                            ],
                        }
                        for entry in entries
                        if entry["plan_id"] == plan["plan_id"]
                    ],
                }
            )
        return result

    def prepare(self, plan):
        """Verify already-present files and persist a fixed, replayable maintenance plan."""
        request = request_plan(plan)
        with self._lock(request["root_id"]):
            self._scope(request)
            previous = self.media.repo.get("import_relocation_plan", request["migration_id"])
            fingerprint = digest(request)
            if previous:
                require(previous["fingerprint"] == fingerprint, "idempotency_conflict", 409)
                self._validate(previous)
                return previous
            self._records(request)
            entries = []
            for entry in request["entries"]:
                proof = self._proof(request, entry)
                entries.append(
                    {
                        **entry,
                        "old_record_id": item_id(request["root_id"], entry["old_relative_path"]),
                        "new_record_id": item_id(request["root_id"], entry["new_relative_path"]),
                        "proof": proof,
                        "record": self._relocated(request, entry, proof),
                    }
                )
            self._records(request)
            self._scope(request)
            return self.media.repo.put(
                "import_relocation_plan",
                request["migration_id"],
                {
                    **request,
                    "entries": entries,
                    "fingerprint": fingerprint,
                    "state": "prepared",
                    "created_at": self.media.repo.clock(),
                    "historical_metadata": self._historical_metadata(request),
                },
                0,
            )

    def _load(self, migration_id):
        require(
            isinstance(migration_id, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,100}", migration_id),
            "invalid_input",
            400,
        )
        plan = self.media.repo.get("import_relocation_plan", migration_id)
        require(plan is not None, "import_relocation_plan_not_found", 404)
        return plan

    def _validate(self, plan):
        self._scope(plan)
        receipt = self.media.repo.get("import_relocation", plan["migration_id"])
        if receipt:
            require(receipt["fingerprint"] == plan["fingerprint"], "idempotency_conflict", 409)
            for entry in receipt["entries"]:
                require(
                    self.media.repo.get("media_import", entry["new_record_id"]) == entry["record"],
                    "version_conflict",
                    409,
                )
                require(
                    entry["old_record_id"] == entry["new_record_id"]
                    or self.media.repo.get("media_import", entry["old_record_id"]) is None,
                    "import_relocation_path_conflict",
                    409,
                )
                path = imported_path(plan["new_directory"], entry["new_relative_path"])
                before = observation(path)
                require(
                    same_identity(before, entry["record"]["observation"]),
                    "import_observation_changed",
                    409,
                )
                if entry["proof"]["method"] == "sha256":
                    actual = file_record(
                        path, entry["new_relative_path"], "video", entry["record"].get("cid")
                    )
                    require(
                        actual["sha256"] == entry["proof"]["sha256"],
                        "import_relocation_content_changed",
                        409,
                    )
                    require(observation(path) == before, "import_observation_changed", 409)
                for source in entry["updated_sources"]:
                    require(
                        self.media.repo.get("media_source", source["new_source_id"])
                        == source["record"],
                        "import_source_changed",
                        409,
                    )
            # Originals may be removed by the operator only after independent Emby verification.
            return {**receipt, "replayed": True}
        self._records(plan)
        for entry in plan["entries"]:
            self._proof(plan, entry, entry["proof"])
        self._records(plan)
        self._scope(plan)
        return {
            "migration_id": plan["migration_id"],
            "state": "validated",
            "count": len(plan["entries"]),
            "replayed": False,
        }

    def validate(self, migration_id):
        """Read-only revalidation, including file reads for copied rather than linked videos."""
        plan = self._load(migration_id)
        with self._lock(plan["root_id"]):
            return self._validate(plan)

    def commit(self, migration_id):
        """Revalidate then atomically rebind records; the configured root must be the target."""
        plan = self._load(migration_id)
        with self._lock(plan["root_id"]):
            self._scope(plan, commit=True)
            validated = self._validate(plan)
            if validated["replayed"]:
                return validated
            self._scope(plan, commit=True)
            return self.media.repo.import_relocate(plan)
