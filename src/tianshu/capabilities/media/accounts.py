"""Media-owned sessions sealed by the runtime's one external master key."""

import base64
import re

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
    def __init__(self, repository, credentials):
        self.repo, self.credentials = repository, credentials
        # Construction also serves unconfigured installations. Secrets are only
        # resolved for the selected account/connection, never scanned at startup.

    def seal(self, identity, value):
        sealed = self.credentials.seal_bytes(
            value.encode(), domain="capabilities.media", reference=identity
        )
        return base64.b64encode(sealed).decode("ascii")

    def _decrypt(self, account):
        return self.unseal(account["account_id"], account["secret"])

    def unseal(self, identity, secret):
        try:
            raw = base64.b64decode(secret, validate=True)
            return self.credentials.unseal_bytes(
                raw, domain="capabilities.media", reference=identity
            ).decode()
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
