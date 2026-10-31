"""Keep API-owned playlists from being imported a second time from our M3U8 exports."""

import os
from pathlib import Path

from ....contracts import digest
from ...destination import safe_path
from ...organizer import library_lock
from ...video_storage import sync_directory


def ignore_export(directory):
    path = safe_path(directory, ".ndignore")
    existing = path.read_bytes() if path.exists() else b""
    if existing.splitlines() and existing.splitlines()[-1] == b"playlist.m3u8":
        return
    # Add only our exact generated filename; preserve the user's other ignore rules.
    with path.open("ab") as stream:
        stream.write(b"\nplaylist.m3u8\n")
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(directory)


def prepare_exports(repo, connection):
    exports = [
        row
        for row in repo.list("music_export")
        if row["directory"] == connection["local_directory"]
    ]
    if not exports:
        return
    with library_lock(connection["local_directory"]):
        for row in exports:
            expected = "歌单/" + digest(row["playlist_id"])[:20]
            if row["relative"] != expected:
                continue
            directory = safe_path(Path(row["directory"]), expected)
            if directory.is_dir():
                ignore_export(directory)
