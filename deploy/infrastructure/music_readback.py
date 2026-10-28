"""Compare stable music configuration using explicit read-only SQLite projections.

Run against the existing cold-backup media database and the current database.
No full document, credential, key file, member list or audio/lyric file is read.
The single JSON receipt fits the existing final readback; this is not a scheduler.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path


# Field ownership checked against Platform d3f23ee00d7e3dab3c2f5114add98e679de0fc3f:
# music/library.py, quality.py, maintenance.py and navidrome/state.py.
FIELDS = {
    "music_account": ("account_id", "provider"),
    "music_playlist": (
        "playlist_id", "provider", "account_id", "source_id", "state", "interval_seconds",
        "auto_download", "quality.level", "quality.allow_fallback",
    ),
    "music_navidrome_playlist": (
        "link_id", "playlist_id", "connection_id", "remote_id", "auto_sync", "public",
    ),
    "music_maintenance": ("enabled", "interval_seconds", "playlist_interval_seconds"),
    "music_settings": ("directory", "layout", "filename"),
    "music_navidrome": (
        "connection_id", "enabled", "music_folder_id", "music_directory", "local_directory",
    ),
}


def _digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def project(path):
    path = Path(path).resolve(strict=True)
    database = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        database.execute("PRAGMA query_only=ON")
        database.execute("BEGIN")
        result = {}
        for kind, fields in FIELDS.items():
            # JSON extraction happens inside SQLite. Python never receives body,
            # secret/password/cookie fields or complete connection documents.
            pairs = ",".join("?,json_extract(body,?)" for _ in fields)
            parameters = [item for field in fields for item in (field, "$." + field)]
            rows = database.execute(
                "SELECT id,json_object(" + pairs + ") FROM documents WHERE kind=? ORDER BY id",
                (*parameters, kind),
            )
            result[kind] = {identity: json.loads(raw) for identity, raw in rows}
        return result
    finally:
        database.close()


def summarize(projection):
    playlists = list(projection["music_playlist"].values())
    links = list(projection["music_navidrome_playlist"].values())
    return {
        "counts": {kind: len(rows) for kind, rows in projection.items()},
        "sha256": {kind: _digest(rows) for kind, rows in projection.items()},
        "active_playlists": sum(row["state"] == "active" for row in playlists),
        "auto_download_playlists": sum(row["auto_download"] is True or row["auto_download"] == 1 for row in playlists),
        "playlist_intervals_seconds": sorted({row["interval_seconds"] for row in playlists if row["interval_seconds"] is not None}),
        "navidrome_remote_ids_present": sum(bool(row["remote_id"]) for row in links),
        "navidrome_auto_sync_links": sum(row["auto_sync"] is True or row["auto_sync"] == 1 for row in links),
        "maintenance": list(projection["music_maintenance"].values()),
    }


def compare(before, after, *, expected_playlists=None, expected_links=None):
    before, after = project(before), project(after)
    changed = {}
    for kind in FIELDS:
        old, new = before[kind], after[kind]
        common = old.keys() & new.keys()
        fields = sorted({field for identity in common for field in FIELDS[kind] if old[identity][field] != new[identity][field]})
        if old != new:
            changed[kind] = {"added": len(new.keys() - old.keys()), "removed": len(old.keys() - new.keys()),
                             "changed": sum(old[identity] != new[identity] for identity in common), "fields": fields}
    summaries = {"before": summarize(before), "after": summarize(after)}
    incomplete = []
    for label, projection in (("before", before), ("after", after)):
        incomplete.extend(label + ":" + kind for kind, rows in projection.items() if not rows)
        incomplete.extend(label + ":" + kind + ":missing_fields" for kind, rows in projection.items()
                          if any(value[field] is None for value in rows.values() for field in FIELDS[kind]))
        for kind, expected in (("music_playlist", expected_playlists), ("music_navidrome_playlist", expected_links)):
            if expected is not None and len(projection[kind]) != expected:
                incomplete.append(label + ":" + kind + ":unexpected_count")
        if summaries[label]["navidrome_remote_ids_present"] != len(projection["music_navidrome_playlist"]):
            incomplete.append(label + ":navidrome_remote_id_missing")
    return {
        "status": "incomplete" if incomplete else "changed" if changed else "preserved",
        **summaries, "differences": changed, "incomplete": incomplete,
        "credential_values_selected": False, "key_files_read": False,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--before", required=True, type=Path)
    parser.add_argument("--after", required=True, type=Path)
    parser.add_argument("--expect-playlists", type=int)
    parser.add_argument("--expect-links", type=int)
    args = parser.parse_args(argv)
    try:
        result = compare(args.before, args.after, expected_playlists=args.expect_playlists, expected_links=args.expect_links)
    except (OSError, sqlite3.Error, ValueError, TypeError):
        result = {"status": "unavailable", "reason": "music_configuration_readback_unavailable"}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "preserved" else 2


if __name__ == "__main__":
    raise SystemExit(main())
