"""Match an archived file, never just a title, to Navidrome's indexed song."""

from pathlib import Path, PurePosixPath

from ...destination import safe_path
from ..catalog import normalized, same_recording
from ..audio import tag_identity
from ....contracts import digest


def mapped_members(tracks, songs, connection):
    by_path, by_size = {}, {}
    for song in songs:
        if not isinstance(song.get("size"), int) or not isinstance(song.get("suffix"), str):
            continue
        path = str(song.get("real_path", song.get("path", ""))).replace("\\", "/")
        by_path.setdefault(path, []).append(song)
        by_size.setdefault((song.get("size"), song.get("suffix", "").lower()), []).append(song)
    members, cached = [], {}
    for track in tracks:
        artifact = track.get("artifact")
        audio = next((f for f in (artifact or {}).get("files", []) if f["kind"] == "audio"), None)
        member = {k: track[k] for k in ("position", "recording_id", "title")}
        member.update(source_id=track.get("source_id"), provider=track.get("provider"))
        member.update(song_id=None, code="not_downloaded", file=None)
        if audio:
            member["file"] = audio["path"]
            member["archive_key"] = digest({"directory": artifact["directory"], "audio": audio})
            key = (artifact["recording_id"], audio["path"], audio["sha256"])
            if key not in cached:
                cached[key] = match_file(track, artifact, audio, by_path, by_size, connection)
            member.update(cached[key])
        members.append(member)
    return members


def retain_verified(members, previous, target, pending):
    """An inconclusive read cannot remove an unchanged, previously verified member."""
    remote_ids = {row["id"] for row in target.get("entry", [])}
    by_file = {}
    for row in previous:
        if row.get("song_id") in remote_ids and row["song_id"] in pending:
            by_file.setdefault(row.get("file"), []).append(row)
    for member in members:
        if member["code"] not in {"ambiguous", "waiting_index"}:
            continue
        prior = [
            row
            for row in by_file.get(member.get("file"), [])
            if (
                row.get("archive_key") == member.get("archive_key")
                if row.get("archive_key")
                else row.get("recording_id") == member.get("recording_id")
                and row["song_id"] in member.get("candidate_ids", [])
            )
        ]
        identities = {row["song_id"] for row in prior}
        if len(identities) == 1:
            member.update(
                song_id=identities.pop(), code="verification_pending", method="previous_verified"
            )
    return members


def match_file(track, artifact, audio, by_path, by_size, connection):
    path = safe_path(Path(artifact["directory"]), audio["path"])
    try:
        present = path.is_file() and path.stat().st_size == audio["size_bytes"]
    except OSError:
        present = False
    if not present:
        return {"code": "local_file_missing"}
    remote = str(PurePosixPath(connection["music_directory"]) / audio["path"])
    exact = [r for r in by_path.get(remote, []) if r.get("size") == audio["size_bytes"]]
    if len(exact) == 1:
        return {"song_id": exact[0]["id"], "code": "matched", "method": "path_size"}
    if exact:
        return {"code": "ambiguous", "candidate_ids": [r["id"] for r in exact]}
    # Navidrome normally reports a synthetic Artist/Album path. Require unique,
    # corroborated metadata AND the exact archived byte size/format in that mode.
    metadata = {
        **track,
        **(tag_identity(track) if artifact.get("managed") else {}),
        "duration_ms": artifact.get("quality", {}).get("duration_seconds", 0) * 1000,
    }
    candidates = []
    for song in by_size.get((audio["size_bytes"], path.suffix[1:].lower()), []):
        if not isinstance(song.get("duration"), (int, float)):
            continue
        if not all(isinstance(song.get(k), str) for k in ("title", "album")):
            continue
        reported = str(song.get("real_path", song.get("path", ""))).replace("\\", "/")
        if reported.startswith("/"):
            continue  # A real path pointing elsewhere is not our archived file.
        raw_artists = song.get("artists") or []
        names = (
            [a["name"] for a in raw_artists if isinstance(a, dict) and a.get("name")]
            if isinstance(raw_artists, list)
            else []
        )
        if not names:
            names = [song.get("artist", "")]
        other = {
            "title": song.get("title"),
            "artists": names,
            "album": song.get("album"),
            "duration_ms": song.get("duration", 0) * 1000,
        }
        # Integer Subsonic duration truncates the measured duration by < 1 second.
        if abs(metadata["duration_ms"] - other["duration_ms"]) < 1000:
            other["duration_ms"] = metadata["duration_ms"]
        if same_recording(metadata, other) and normalized(metadata.get("album")):
            candidates.append(song)
    if len(candidates) == 1:
        return {"song_id": candidates[0]["id"], "code": "matched", "method": "metadata_size"}
    return {
        "code": "ambiguous" if candidates else "waiting_index",
        # A recording verified by PCM may be reused across different album
        # releases. Its actual archive path, rather than the playlist's album
        # label, remains authoritative for the optional native lookup.
        "candidate_ids": [
            r["id"]
            for r in candidates or by_size.get((audio["size_bytes"], path.suffix[1:].lower()), [])
        ],
    }
