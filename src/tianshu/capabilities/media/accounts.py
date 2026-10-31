"""Media sessions encrypted at rest; projections never contain session material."""

import base64
import os
import re
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..contracts import Fault, require

COOKIE_NAMES = frozenset(
    {"SESSDATA", "bili_jct", "DedeUserID", "DedeUserID__ckMd5", "buvid3", "buvid4", "sid"}
)


def normalize_cookie(value):
    require(isinstance(value, str) and 0 < len(value) <= 16384, "invalid_cookie", 400)
    require(not any(ord(c) < 32 or ord(c) > 126 for c in value), "invalid_cookie", 400)
    cookies = {}
    for part in value.split(";"):
        name, separator, item = part.strip().partition("=")
        require(separator and re.fullmatch(r"[A-Za-z0-9_]+", name), "invalid_cookie", 400)
        if name in COOKIE_NAMES:
            cookies[name] = item
    require(bool(cookies.get("SESSDATA")), "invalid_cookie", 400)
    return "; ".join(f"{key}={cookies[key]}" for key in sorted(cookies))


def public_account(value):
    return {
        key: value[key]
        for key in ("account_id", "label", "state", "revision", "checked_at", "code")
    }


class Accounts:
    def __init__(self, repository):
        self.repo = repository
        key = Path(str(repository.database) + ".key")
        if not key.exists():
            require(
                not repository.list("account")
                and not repository.list("media_target")
                and not repository.list("music_account")
                and not repository.list("music_navidrome")
                and not repository.list("book_source")
                and not any(row.get("opds_secret") for row in repository.list("book_settings"))
                and not any(
                    c.get("secret")
                    for row in repository.list("comic_settings")
                    for c in row.get("connections", {}).values()
                ),
                "media_credentials_unavailable",
                503,
            )
            try:
                with key.open("xb") as handle:
                    os.chmod(key, 0o600)
                    handle.write(AESGCM.generate_key(bit_length=256))
                    handle.flush()
                    os.fsync(handle.fileno())
            except FileExistsError:
                pass
        require(key.is_file() and not key.is_symlink(), "media_credentials_unavailable", 503)
        try:
            self.cipher = AESGCM(key.read_bytes())
            for account in repository.list("account") + repository.list("music_account"):
                if account.get("secret"):
                    self._decrypt(account)
            for connection in repository.list("music_navidrome"):
                self.unseal(connection["connection_id"], connection["secret"])
            for source in repository.list("book_source"):
                self.unseal(source["source_id"], source["secret"])
            for settings in repository.list("book_settings"):
                if settings.get("opds_secret"):
                    self.unseal("book-opds", settings["opds_secret"])
            for settings in repository.list("comic_settings"):
                for name, connection in settings.get("connections", {}).items():
                    if connection.get("secret"):
                        self.unseal("comic-" + name, connection["secret"])
        except (ValueError, OSError):
            raise Fault("media_credentials_unavailable", 503) from None

    def seal(self, identity, value):
        nonce = os.urandom(12)
        return base64.b64encode(
            nonce + self.cipher.encrypt(nonce, value.encode(), identity.encode())
        ).decode()

    def _decrypt(self, account):
        return self.unseal(account["account_id"], account["secret"])

    def unseal(self, identity, secret):
        try:
            raw = base64.b64decode(secret, validate=True)
            return self.cipher.decrypt(raw[:12], raw[12:], identity.encode()).decode()
        except Exception:
            raise Fault("media_credentials_unavailable", 503) from None

    def cookie(self, identity, allow_auth_required=False):
        if identity is None:
            return None
        account = self.repo.get("account", identity)
        require(account is not None and account["state"] != "revoked", "account_unavailable", 409)
        require(account["state"] == "ready" or allow_auth_required, "auth_required", 409)
        return self._decrypt(account)

    def save(self, label, cookie, status, *, db=None):
        identity = "bilibili-" + str(status["mid"])
        body = {
            "account_id": identity,
            "label": label or status.get("uname") or identity,
            "state": "ready",
            "checked_at": self.repo.clock(),
            "code": "ready",
            "secret": self.seal(identity, cookie),
        }
        return self.repo.put("account", identity, body, db=db)

    def mark(self, identity, state, code):
        account = self.repo.get("account", identity)
        if account is not None:
            return self.repo.put(
                "account",
                identity,
                {**account, "state": state, "code": code, "checked_at": self.repo.clock()},
                account["revision"],
            )
        raise Fault("not_found", 404)

    def revoke(self, identity, expected, *, db):
        account = self.repo._document(db, "account", identity)
        require(account is not None, "not_found", 404)
        return self.repo.put(
            "account",
            identity,
            {**account, "state": "revoked", "secret": None, "code": "revoked"},
            expected,
            db=db,
        )
