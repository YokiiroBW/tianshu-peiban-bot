"""Synthetic before/after music ledgers, with no external calls or credentials."""

import copy
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from deploy.infrastructure import music_readback


class MusicReadbackTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.before, self.after = self.base / "before.sqlite", self.base / "after.sqlite"
        self.documents = {
            "music_account": {"account": {"account_id": "account", "provider": "synthetic", "secret": "SECRET_ACCOUNT_CANARY"}},
            "music_playlist": {"playlist": {"playlist_id": "playlist", "provider": "synthetic", "account_id": "account", "source_id": "remote-playlist", "state": "active", "interval_seconds": 43200, "auto_download": True, "quality": {"level": "best", "allow_fallback": True}, "entries": ["track"], "next_scan_at": 1}},
            "music_navidrome_playlist": {"link": {"link_id": "link", "playlist_id": "playlist", "connection_id": "connection", "remote_id": "remote-id", "auto_sync": True, "public": False, "status": "queued", "members": []}},
            "music_maintenance": {"settings": {"enabled": True, "interval_seconds": 604800, "playlist_interval_seconds": 43200}},
            "music_settings": {"library": {"directory": "/synthetic/music", "layout": "artist_album", "filename": "artist_title"}},
            "music_navidrome": {"server": {"connection_id": "connection", "enabled": True, "music_folder_id": "folder", "music_directory": "/music", "local_directory": "/synthetic/music", "username": "PRIVATE_NAME_CANARY", "secret": "SECRET_NAVIDROME_CANARY"}},
            "music_lyrics": {"recording": {"state": "queued", "next_check_at": 1, "lyrics": "PRIVATE_LYRIC_CANARY"}},
        }
        self.documents["music_playlist"]["playlist"]["quality"]["secret"] = "SECRET_NESTED_CANARY"
        for index in range(1, 43):
            playlist = copy.deepcopy(self.documents["music_playlist"]["playlist"])
            playlist["playlist_id"] = "playlist-" + str(index)
            playlist["source_id"] = "source-" + str(index)
            self.documents["music_playlist"][playlist["playlist_id"]] = playlist
            link = copy.deepcopy(self.documents["music_navidrome_playlist"]["link"])
            link.update(link_id="link-" + str(index), playlist_id=playlist["playlist_id"], remote_id="remote-" + str(index))
            self.documents["music_navidrome_playlist"][link["link_id"]] = link
        self.save(self.before, self.documents)
        self.save(self.after, self.documents)

    def save(self, path, documents):
        with closing(sqlite3.connect(path)) as db:
            db.execute("CREATE TABLE IF NOT EXISTS documents(kind TEXT,id TEXT,revision INTEGER,body TEXT,PRIMARY KEY(kind,id))")
            db.execute("DELETE FROM documents")
            for kind, rows in documents.items():
                db.executemany("INSERT INTO documents VALUES(?,?,?,?)", [(kind, identity, 1, json.dumps(body)) for identity, body in rows.items()])
            db.commit()

    def test_progress_members_lyrics_and_revisions_can_advance_without_configuration_drift(self):
        changed = copy.deepcopy(self.documents)
        changed["music_playlist"]["playlist"].update(entries=["new-track"], next_scan_at=500, synced_at=400, revision=99)
        changed["music_navidrome_playlist"]["link"].update(status="synced", members=[{"song_id": "new-song"}], next_sync_at=500)
        changed["music_lyrics"]["recording"].update(state="complete", next_check_at=99999)
        self.save(self.after, changed)
        report = music_readback.compare(self.before, self.after, expected_playlists=43, expected_links=43)
        self.assertEqual(report["status"], "preserved")
        self.assertEqual(report["after"]["playlist_intervals_seconds"], [43200])
        self.assertEqual(report["after"]["maintenance"], [{"enabled": 1, "interval_seconds": 604800, "playlist_interval_seconds": 43200}])
        self.assertEqual(report["after"]["navidrome_remote_ids_present"], 43)

    def test_stable_settings_and_remote_identity_changes_are_reported_without_values(self):
        for kind, identity, field, value in (
            ("music_playlist", "playlist", "interval_seconds", 3600),
            ("music_playlist", "playlist", "auto_download", False),
            ("music_maintenance", "settings", "enabled", False),
            ("music_navidrome_playlist", "link-42", "remote_id", "CHANGED_PRIVATE_REMOTE_ID"),
        ):
            changed = copy.deepcopy(self.documents)
            changed[kind][identity][field] = value
            self.save(self.after, changed)
            with self.subTest(field=field):
                report = music_readback.compare(self.before, self.after)
                self.assertEqual(report["status"], "changed")
                self.assertIn(field, report["differences"][kind]["fields"])
                self.assertNotIn("CHANGED_PRIVATE_REMOTE_ID", json.dumps(report))

    def test_projection_never_returns_secrets_or_reads_keys_and_does_not_modify_databases(self):
        before_bytes, after_bytes = self.before.read_bytes(), self.after.read_bytes()
        with patch.object(Path, "read_bytes", side_effect=AssertionError("key or full file read forbidden")):
            projection = music_readback.project(self.before)
            report = music_readback.compare(self.before, self.after)
        encoded = json.dumps({"projection": projection, "report": report})
        for canary in ("SECRET_ACCOUNT_CANARY", "SECRET_NAVIDROME_CANARY", "SECRET_NESTED_CANARY", "PRIVATE_NAME_CANARY", "PRIVATE_LYRIC_CANARY"):
            self.assertNotIn(canary, encoded)
        self.assertEqual(self.before.read_bytes(), before_bytes)
        self.assertEqual(self.after.read_bytes(), after_bytes)

    def test_empty_or_wrong_count_is_incomplete_and_missing_database_is_not_created(self):
        self.save(self.after, {})
        self.assertEqual(music_readback.compare(self.before, self.after)["status"], "incomplete")
        self.assertEqual(music_readback.compare(self.before, self.before, expected_playlists=44)["status"], "incomplete")
        missing_field = copy.deepcopy(self.documents)
        missing_field["music_maintenance"]["settings"].pop("enabled")
        self.save(self.after, missing_field)
        self.assertEqual(music_readback.compare(self.before, self.after)["status"], "incomplete")
        missing = self.base / "missing.sqlite"
        result = subprocess.run([sys.executable, "-I", "-B", str(Path(music_readback.__file__)), "--before", str(missing), "--after", str(self.after)], capture_output=True, check=False, timeout=10)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)["status"], "unavailable")
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
