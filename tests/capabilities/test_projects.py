"""Important evidence, range and bounded directory behavior; no real account/provider calls."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from tianshu.capabilities.contracts import Fault
from tianshu.capabilities.projects.directories import scan
from tianshu.capabilities.projects.documents import Documents
from tianshu.capabilities.projects.notes import Notes, validate_note
from tianshu.capabilities.projects.types import ProjectSettings


class ProjectEvidence(unittest.IsolatedAsyncioTestCase):
    def test_semantic_dependency_group_keeps_non_ascii_character_ranges(self):
        prepared = {
            "text": "甲😀\n乙\n丙\n",
            "digest": "original",
            "provenance": {"coverage_unit": "characters", "coverage_total": 7},
        }
        units = Documents.units(
            prepared,
            [
                {"start": 1, "end": 1, "depends_on": [2]},
                {"start": 2, "end": 2, "depends_on": []},
                {"start": 3, "end": 3, "depends_on": []},
            ],
        )
        linked = next(item for item in units if len(item["spans"]) == 2)
        self.assertEqual(
            linked["spans"],
            [
                {"unit": "characters", "start": 0, "end": 3},
                {"unit": "characters", "start": 5, "end": 7},
            ],
        )

    def test_paged_directory_preview_never_implies_removal_of_unseen_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "docs").mkdir()
            (root / "docs/a.txt").write_text("first", encoding="utf-8")
            (root / "docs/b.txt").write_text("second", encoding="utf-8")
            project = {"root": str(root), "directories": ["docs"]}
            indexed = [
                {
                    "locator": "docs/b.txt",
                    "document_id": "b",
                    "version": 1,
                    "sha256": "older",
                    "state": "active",
                }
            ]
            result, coverage, cursor = scan(project, "docs", indexed, None, 1)
            self.assertEqual(cursor, "docs/a.txt")
            self.assertFalse(any(item["state"] == "removed" for item in result))
            self.assertFalse(coverage["truncated"])

    async def test_note_version_chain_cannot_hide_cycle_or_cross_project_lookup(self):
        documents = SimpleNamespace(evidence=AsyncMock())
        store = SimpleNamespace(readable=AsyncMock())
        notes = Notes(store, documents)
        notes.row = AsyncMock(
            return_value={
                "note_id": "b",
                "version": 1,
                "current_version": 1,
                "state": "active",
                "version_state": "active",
                "sha256": "bhash",
                "source_refs": [],
                "citations": [
                    {"kind": "note", "reference": {"note_id": "a", "version": 1, "sha256": "ahash"}}
                ],
            }
        )
        with self.assertRaises(Fault) as caught:
            await notes.resolve(
                None,
                {"project_id": "this-project"},
                {"note_id": "b", "version": 1, "sha256": "bhash"},
                visited=("a",),
            )
        self.assertEqual(caught.exception.code, "note_citation_cycle")
        self.assertEqual(notes.row.call_args.args[0]["project_id"], "this-project")

    def test_machine_read_unit_is_not_accepted_as_an_untyped_note_title(self):
        with self.assertRaises(Fault):
            validate_note(
                {
                    "question": "question",
                    "source_statements": [],
                    "inferences": [],
                    "open_questions": [],
                    "decision": None,
                }
            )
        with self.assertRaises(Fault):
            ProjectSettings.from_mapping({"allowed_roots": "not-a-managed-root-list"})
