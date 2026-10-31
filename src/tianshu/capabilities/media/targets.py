"""External media-server connections and directory mappings on the media ledger."""

import hashlib
import json
import re
import uuid
from pathlib import Path

from ..contracts import require
from .config import text
from .publication import validate_connection


class Targets:
    def __init__(self, media):
        self.media = media
        self.configured = dict(media.config["targets"])
        self.reload()

    def token(self, identity):
        value = self.media.repo.get("media_target", identity)
        require(value is not None, "target_not_configured", 409)
        return self.media.accounts.unseal(identity, value["secret"])

    def reload(self):
        values = dict(self.configured)
        for item in self.media.repo.list("media_target"):
            if item.get("removed"):
                continue
            identity = item["target_id"]
            self.media.accounts.unseal(identity, item["secret"])
            values[identity] = {
                "target_id": identity,
                "label": item["label"],
                "kind": "media_server",
                "directory": item["directory"],
                "library_id": str(
                    uuid.uuid5(uuid.NAMESPACE_URL, "tianshu-media-library:" + identity)
                ),
                "revision": item["revision"],
                "servers": [{**item["server"], "token_ref": identity}],
            }
        self.media.config["targets"] = values

    def connection(self, body):
        require(
            body.get("target_id") is None or isinstance(body["target_id"], str),
            "invalid_input",
            400,
        )
        kind = body.get("kind")
        require(kind in {"emby", "jellyfin", "plex"}, "invalid_input", 400)
        connection = {"kind": kind, "base_url": text(body.get("base_url"), 2048).rstrip("/")}
        token = body.get("token", "")
        require(isinstance(token, str) and len(token) <= 4096, "invalid_input", 400)
        if not token:
            saved = self.media.repo.get("media_target", body.get("target_id"))
            require(
                saved
                and not saved.get("removed")
                and all(saved["server"][key] == connection[key] for key in connection),
                "media_peer_not_configured",
                409,
            )
            token = self.media.accounts.unseal(saved["target_id"], saved["secret"])
        require(token.strip() and all(32 <= ord(c) < 127 for c in token), "invalid_input", 400)
        connection["token"] = token
        validate_connection(connection)
        return connection

    def save(self, body, connection, db):
        identity = body.get("target_id") or str(uuid.uuid4())
        require(
            isinstance(identity, str) and re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identity),
            "invalid_input",
            400,
        )
        require(identity not in self.configured, "target_managed_by_config", 409)
        revision = body.get("expected_revision")
        require(type(revision) is int and revision >= 0, "invalid_input", 400)
        directory = Path(text(body.get("directory"), 4096))
        require(directory.is_absolute(), "invalid_input", 400)
        directory = directory.absolute()
        require(
            not any(p.is_symlink() or p.is_junction() for p in (directory, *directory.parents)),
            "unsafe_library_path",
            409,
        )
        directory = directory.resolve()
        stored = [
            json.loads(row[0])
            for row in db.execute("SELECT body FROM documents WHERE kind='media_target'")
        ]
        stored = [t for t in stored if not t.get("removed") and t["target_id"] != identity]
        require(len(stored) + len(self.configured) < 32, "invalid_input", 400)
        for other in [Path(self.media.config["staging_directory"]).resolve()] + [
            Path(t["directory"]).resolve()
            for t in [*self.configured.values(), *stored]
            if t.get("directory")
        ]:
            require(
                directory != other
                and directory not in other.parents
                and other not in directory.parents,
                "directory_overlap",
                409,
            )
        library_id = text(body.get("library_id"), 128)
        require(re.fullmatch(r"[a-zA-Z0-9_-]+", library_id), "invalid_input", 400)
        media_root = text(body.get("media_root"), 4096).replace("\\", "/").rstrip("/")
        require(
            media_root.startswith("/") or re.match(r"^[A-Za-z]:/", media_root), "invalid_input", 400
        )
        server = {key: connection[key] for key in ("kind", "base_url")}
        server.update(server_id=identity, library_id=library_id, media_root=media_root)
        current = self.media.repo._document(db, "media_target", identity)
        if (
            current
            and db.execute(
                "SELECT 1 FROM jobs WHERE (body::jsonb #>> '{target_id}')=%s LIMIT 1", (identity,)
            ).fetchone()
        ):
            require(
                current["directory"] == str(directory)
                and all(
                    current["server"][key] == server[key]
                    for key in ("kind", "library_id", "media_root")
                ),
                "target_mapping_in_use",
                409,
            )
        saved = self.media.repo.put(
            "media_target",
            identity,
            {
                "target_id": identity,
                "label": text(body.get("label"), 128),
                "directory": str(directory),
                "server": server,
                "secret": self.media.accounts.seal(identity, connection["token"]),
            },
            revision,
            db=db,
        )
        return {"target_id": identity, "revision": saved["revision"]}

    def remove(self, body, db):
        identity = body.get("target_id")
        require(identity not in self.configured, "target_managed_by_config", 409)
        current = self.media.repo._document(db, "media_target", identity)
        require(current is not None, "target_not_configured", 404)
        # Historical jobs may still need metadata refresh or repair using this mapping.
        used = db.execute(
            "SELECT 1 FROM jobs WHERE (body::jsonb #>> '{target_id}')=%s LIMIT 1", (identity,)
        ).fetchone()
        used = (
            used
            or db.execute(
                "SELECT 1 FROM documents WHERE kind='subscription' AND (body::jsonb #>> '{target_id}')=%s LIMIT 1",
                (identity,),
            ).fetchone()
        )
        require(not used, "target_mapping_in_use", 409)
        self.media.repo.put(
            "media_target",
            identity,
            {**current, "removed": True},
            body.get("expected_revision"),
            db=db,
        )
        return {"target_id": identity, "removed": True}

    @staticmethod
    def safe_command(body, connection):
        return {
            **{k: v for k, v in body.items() if k != "token"},
            "token_digest": hashlib.sha256(connection["token"].encode()).hexdigest(),
        }
