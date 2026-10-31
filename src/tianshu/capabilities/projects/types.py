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
        return cls(tuple(value.get("allowed_roots", ())), value.get("git_executable"))
