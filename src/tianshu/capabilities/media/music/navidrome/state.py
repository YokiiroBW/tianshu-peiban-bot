"""Navidrome connection and playlist links in the existing media ledger."""

import hashlib
import json
import uuid
from pathlib import PurePosixPath

from ....contracts import digest, require
from ...config import text
from ...publication import validate_connection

CONNECTION = "music_navidrome"
LINK = "music_navidrome_playlist"


def public_connection(value):
    return {k: v for k, v in value.items() if k != "secret"} if value else None


def public_link(value):
    result = {k: v for k, v in value.items() if k not in {"members", "create_sent", "marker"}}
    problems = [
        m
        for m in value.get("members", [])
        if not m.get("song_id") or m["code"] == "verification_pending"
    ]
    return {**result, "problems": problems[:20], "problem_count": len(problems)}


def mark_dirty(repo, db, playlist_id=None):
    connection = repo._document(db, CONNECTION, "server")
    if not connection or not connection.get("enabled"):
        return
    for raw in db.execute('SELECT body FROM documents WHERE kind=%s', (LINK,)).fetchall():
        row = json.loads(raw[0])
        if (
            row["connection_id"] == connection["connection_id"]
            and row["auto_sync"]
            and row["status"] not in {"deleting", "deleted"}
            and (playlist_id is None or row["playlist_id"] == playlist_id)
        ):
            row["next_sync_at"] = 0
            db.execute(
                'UPDATE documents SET body=%s WHERE kind=%s AND id=%s',
                (json.dumps(row, ensure_ascii=False), LINK, row["link_id"]),
            )


def pause_playlist(repo, db, playlist_id):
    """Stop future publication without deleting the already published remote playlist."""
    for raw in db.execute('SELECT body FROM documents WHERE kind=%s', (LINK,)).fetchall():
        row = json.loads(raw[0])
        if row["playlist_id"] == playlist_id and row["status"] not in {"deleting", "deleted"}:
            repo.put(
                LINK,
                row["link_id"],
                {**row, "auto_sync": False, "status": "paused", "code": "paused"},
                row["revision"],
                db=db,
            )


class State:
    def __init__(self, music):
        self.music, self.repo = music, music.media.repo

    def saved(self):
        return self.repo.get(CONNECTION, "server")

    def recording_changed(self, identity, db):
        """Rescan tag changes through existing links without creating any playlist."""
        connection = self.repo._document(db, CONNECTION, "server")
        if not connection or not connection.get("enabled"):
            return
        for raw in db.execute('SELECT body FROM documents WHERE kind=%s', (LINK,)).fetchall():
            row = json.loads(raw[0])
            if (
                row["connection_id"] == connection["connection_id"]
                and row["auto_sync"]
                and row["status"] not in {"deleting", "deleted"}
                and any(m.get("recording_id") == identity for m in row.get("members", []))
            ):
                row.update(next_sync_at=0, scan_requested_digest=None)
                db.execute(
                    'UPDATE documents SET body=%s WHERE kind=%s AND id=%s',
                    (json.dumps(row, ensure_ascii=False), LINK, row["link_id"]),
                )

    def connection(self, body=None):
        saved = self.saved()
        if body is None:
            require(saved and saved["enabled"], "navidrome_not_configured", 409)
            return {
                **saved,
                "password": self.music.media.accounts.unseal(
                    saved["connection_id"], saved["secret"]
                ),
            }
        base_url = text(body.get("base_url"), 2048).rstrip("/")
        username = text(body.get("username"), 256)
        validate_connection({"base_url": base_url, "token": ""})
        password = body.get("password", "")
        require(isinstance(password, str) and len(password) <= 4096, "invalid_input", 400)
        if not password:
            require(
                saved and saved["base_url"] == base_url and saved["username"] == username,
                "navidrome_auth_required",
                409,
            )
            password = self.music.media.accounts.unseal(saved["connection_id"], saved["secret"])
        return {"base_url": base_url, "username": username, "password": password}

    def view(self):
        connection = self.saved()
        links = self.repo.list(LINK)
        return {
            "connection": public_connection(connection),
            "playlists": [
                public_link(r)
                for r in links
                if connection
                and r["connection_id"] == connection["connection_id"]
                and r["status"] != "deleted"
            ],
        }

    def safe_command(self, body, connection):
        return {
            **{k: v for k, v in body.items() if k != "password"},
            "credential_digest": hashlib.sha256(connection["password"].encode()).hexdigest(),
        }

    def save(self, body, connection, checked, db):
        folder = text(body.get("music_folder_id"), 256)
        require(folder in {f["id"] for f in checked["folders"]}, "navidrome_folder_missing", 409)
        directory = text(body.get("music_directory"), 4096).replace("\\", "/").rstrip("/") or "/"
        require(
            directory.startswith("/") and ".." not in PurePosixPath(directory).parts,
            "invalid_input",
            400,
        )
        current = self.repo._document(db, CONNECTION, "server")
        settings = self.repo._document(db, "music_settings", "library") or {}
        require(settings.get("directory"), "music_directory_unavailable", 409)
        same = current and all(current[k] == connection[k] for k in ("base_url", "username"))
        identity = (
            current["connection_id"]
            if same
            else "navidrome:" + digest({k: connection[k] for k in ("base_url", "username")})
        )
        value = self.repo.put(
            CONNECTION,
            "server",
            {
                "connection_id": identity,
                "base_url": connection["base_url"],
                "username": connection["username"],
                "secret": self.music.media.accounts.seal(identity, connection["password"]),
                "music_folder_id": folder,
                "music_directory": directory,
                "enabled": True,
                "local_directory": settings["directory"],
                **checked,
                "checked_at": self.repo.clock(),
            },
            body["expected_revision"],
            db=db,
        )
        mark_dirty(self.repo, db)
        return {"connection": public_connection(value)}

    def disable(self, body, db):
        current = self.repo._document(db, CONNECTION, "server")
        require(current is not None, "navidrome_not_configured", 409)
        value = self.repo.put(
            CONNECTION, "server", {**current, "enabled": False}, body["expected_revision"], db=db
        )
        return {"connection": public_connection(value)}

    def link(self, body, db):
        connection = self.repo._document(db, CONNECTION, "server")
        require(connection and connection["enabled"], "navidrome_not_configured", 409)
        playlist = self.repo._document(db, "music_playlist", body["playlist_id"])
        require(playlist and playlist.get("state") != "removed", "not_found", 404)
        require(
            type(body["public"]) is bool and type(body["auto_sync"]) is bool, "invalid_input", 400
        )
        require(isinstance(body["name"], str) and len(body["name"]) <= 256, "invalid_input", 400)
        identity = digest(
            {"connection": connection["connection_id"], "playlist": playlist["playlist_id"]}
        )
        current = self.repo._document(db, LINK, identity)
        value = (
            current
            if current and current["status"] != "deleted"
            else {
                "link_id": identity,
                "playlist_id": playlist["playlist_id"],
                "connection_id": connection["connection_id"],
                "remote_id": None,
                "marker": "TianShu:" + str(uuid.uuid4()),
                "create_sent": False,
                "synced_count": 0,
                "total": playlist["member_count"],
                "members": [],
            }
        )
        value = self.repo.put(
            LINK,
            identity,
            {
                **value,
                "name": body["name"].strip(),
                "public": body["public"],
                "auto_sync": body["auto_sync"],
                "status": "queued",
                "code": "queued",
                "next_sync_at": 0,
            },
            current["revision"]
            if current and current["status"] == "deleted" and body["expected_revision"] == 0
            else body["expected_revision"],
            db=db,
        )
        return {"playlist": public_link(value)}

    def control(self, body, db):
        row = self.repo._document(db, LINK, body["link_id"])
        connection = self.repo._document(db, CONNECTION, "server")
        require(
            row
            and connection
            and row["connection_id"] == connection["connection_id"]
            and row["status"] != "deleted",
            "not_found",
            404,
        )
        action = body["action"]
        require(
            isinstance(action, str) and action in {"sync", "pause", "resume", "delete", "recreate"},
            "invalid_input",
            400,
        )
        changes = {"next_sync_at": 0, "status": "queued", "code": "queued"}
        if action == "pause":
            changes = {"auto_sync": False, "status": "paused", "code": "paused"}
        elif action == "resume":
            changes["auto_sync"] = True
        elif action == "delete":
            changes.update(status="deleting", auto_sync=False)
        elif action == "recreate":
            require(
                row.get("code") == "navidrome_not_found" and row.get("remote_id"),
                "navidrome_recreate_not_allowed",
                409,
            )
            changes.update(remote_id=None, marker="TianShu:" + str(uuid.uuid4()), create_sent=False)
        value = self.repo.put(
            LINK, row["link_id"], {**row, **changes}, body["expected_revision"], db=db
        )
        return {"playlist": public_link(value)}
