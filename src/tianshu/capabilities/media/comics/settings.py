"""Comic service connections and target mappings on the existing encrypted ledger."""

import json
import ssl
from pathlib import Path

from ...contracts import require
from .suwayomi import service_url

CONNECTIONS = ("suwayomi", "komga", "assetlibrary")


class Settings:
    def __init__(self, media, *, file_defaults=None):
        self.media, self.repo = media, media.repo
        self.file_defaults = dict(file_defaults or {})

    def read(self, db=None):
        value = (
            self.repo._document(db, "comic_settings", "library")
            if db is not None
            else self.repo.get("comic_settings", "library")
        )
        if value is not None:
            return value
        return {
            "revision": 0,
            "interval_seconds": 21600,
            "import_directory": "",
            "connections": {},
            "targets": {},
            **self.file_defaults,
        }

    def public(self, value=None):
        value = value or self.read()
        return {
            "revision": value["revision"],
            "interval_seconds": value["interval_seconds"],
            "import_directory": value["import_directory"],
            "publisher_socket": value.get("publisher_socket", ""),
            "import_socket": value.get("import_socket", ""),
            "connections": {
                name: {
                    "url": row.get("url", ""),
                    "username": row.get("username", ""),
                    "ca_pem": row.get("ca_pem", ""),
                    "has_credentials": bool(row.get("secret")),
                }
                for name, row in value["connections"].items()
            },
            "targets": value["targets"],
        }

    def connection(self, name):
        row = self.read()["connections"].get(name, {})
        secret = (
            json.loads(self.media.accounts.unseal("comic-" + name, row["secret"]))
            if row.get("secret")
            else {}
        )
        return {**row, **secret}

    async def remote_connection(self, name):
        return await self.media.local(self.connection, name)

    def save(self, body, db):
        old = self.read(db)
        interval = body.get("interval_seconds", old["interval_seconds"])
        require(type(interval) is int and 3600 <= interval <= 2592000, "invalid_input", 400)
        directory = body.get("import_directory", old["import_directory"])
        require(
            isinstance(directory, str)
            and len(directory) <= 4096
            and (not directory or Path(directory).is_absolute()),
            "comic_directory_absolute",
            400,
        )
        sockets = {}
        for name in ("publisher_socket", "import_socket"):
            value = body.get(name, old.get(name, ""))
            require(
                isinstance(value, str)
                and len(value) <= 4096
                and (not value or Path(value).is_absolute())
                and not any(ord(c) < 32 for c in value),
                "invalid_input",
                400,
            )
            sockets[name] = value
        incoming = body.get("connections", {})
        require(
            isinstance(incoming, dict) and set(incoming) <= set(CONNECTIONS), "invalid_input", 400
        )
        connections = dict(old["connections"])
        for name, values in incoming.items():
            require(isinstance(values, dict), "invalid_input", 400)
            url = values.get("url", "").strip()
            if url:
                url = service_url(url)
            username = values.get("username", "")
            require(isinstance(username, str) and len(username) <= 256, "invalid_input", 400)
            previous = connections.get(name, {})
            secret = (
                previous.get("secret")
                if previous.get("url") == url and previous.get("username", "") == username
                else None
            )
            credentials = {key: values.get(key, "") for key in ("password", "token")}
            require(
                all(
                    isinstance(v, str) and len(v) <= 8192 and "\r" not in v and "\n" not in v
                    for v in credentials.values()
                ),
                "invalid_input",
                400,
            )
            if any(credentials.values()):
                secret = self.media.accounts.seal("comic-" + name, json.dumps(credentials))
            ca_pem = values.get("ca_pem", previous.get("ca_pem", ""))
            require(isinstance(ca_pem, str) and len(ca_pem) <= 32768, "invalid_input", 400)
            if ca_pem:
                try:
                    ssl.create_default_context(cadata=ca_pem)
                except (ssl.SSLError, ValueError):
                    require(False, "comic_connection_invalid", 400)
            connections[name] = {
                "url": url,
                "username": username,
                "secret": secret,
                "ca_pem": ca_pem,
            }
        targets = body.get("targets", old["targets"])
        require(
            isinstance(targets, dict) and set(targets) <= {"general", "private"},
            "invalid_input",
            400,
        )
        checked = {}
        for kind, target in targets.items():
            require(isinstance(target, dict), "invalid_input", 400)
            checked[kind] = {}
            for key in (
                "directory",
                "asset_target_id",
                "asset_library_id",
                "komga_library_id",
                "komga_root",
            ):
                value = target.get(key, "")
                require(
                    isinstance(value, str)
                    and len(value) <= 4096
                    and not any(ord(c) < 32 for c in value),
                    "invalid_input",
                    400,
                )
                checked[kind][key] = value.rstrip("/") if key.endswith(("path", "root")) else value
            require(
                not checked[kind]["directory"] or Path(checked[kind]["directory"]).is_absolute(),
                "comic_directory_absolute",
                400,
            )
        current = {
            "interval_seconds": interval,
            "import_directory": directory,
            **sockets,
            "connections": connections,
            "targets": checked,
        }
        # Existing jobs retain the precise target snapshot they were created for.
        saved = self.repo.put(
            "comic_settings", "library", current, body.get("expected_revision"), db=db
        )
        return {"settings": self.public(saved)}

    def target(self, kind, db=None):
        row = self.read(db)
        target = row["targets"].get(kind)
        require(
            target
            and all(
                target.get(k)
                for k in (
                    "directory",
                    "asset_target_id",
                    "asset_library_id",
                    "komga_library_id",
                    "komga_root",
                )
            ),
            "comic_target_not_configured",
            409,
        )
        require(
            all(row["connections"].get(name, {}).get("url") for name in ("assetlibrary", "komga")),
            "comic_target_not_configured",
            409,
        )
        return {
            "kind": kind,
            **target,
            **(
                {"publisher_socket": row["publisher_socket"]} if row.get("publisher_socket") else {}
            ),
            "asset_url": row["connections"]["assetlibrary"]["url"],
            "komga_url": row["connections"]["komga"]["url"],
        }
