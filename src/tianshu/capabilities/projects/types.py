"""Stable project evidence references; source authority remains in Sources."""

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True, slots=True)
class ReadUnitRef:
    document_id: str
    version: int
    unit_id: str
    sha256: str


@dataclass(frozen=True, slots=True)
class NoteVersionRef:
    note_id: str
    version: int
    sha256: str


@dataclass(frozen=True, slots=True)
class ProjectSettings:
    allowed_roots: tuple[str, ...] = ()
    git_executable: str | None = None

    @classmethod
    def from_mapping(cls, value: Mapping):
        from pathlib import Path
        from ..contracts import require

        roots = value.get("allowed_roots", [])
        require(
            isinstance(roots, (list, tuple))
            and len(roots) <= 32
            and all(isinstance(root, str) and Path(root).is_absolute() for root in roots),
            "project_managed_roots_invalid",
            503,
        )
        git = value.get("git_executable")
        require(
            git is None or isinstance(git, str) and Path(git).is_absolute(),
            "project_git_executable_invalid",
            503,
        )
        return cls(tuple(roots), git)
