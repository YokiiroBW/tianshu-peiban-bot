"""Real cookie sessions, anonymous CSRF and a persistent first-install seal."""

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
from pathlib import Path
import re
import secrets

from tianshu.runtime.api import CommandResult, PrincipalView, SessionView
from tianshu.runtime.errors import AppError
from .passwords import password_hash, verify_password
from .service import identifier


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


class SessionService:
    def __init__(self, database, settings):
        self.database, self.settings = database, settings
        self.config = settings.get("identity", {})
        self.cookie = self.config.get("cookie_name", "tianshu_session")
        self.seal = Path(
            self.config.get(
                "installation_seal", str(settings.artifact_directory.parent / "identity.sealed")
            )
        )

    async def initialized(self, conn):
        initialized = bool(
            await (
                await conn.execute("SELECT 1 FROM identity.installation WHERE singleton")
            ).fetchone()
        )
        if not initialized and self.seal.exists():
            raise AppError("installation_state_missing", status=503)
        return initialized

    async def session(self, request):
        token = request.cookies.get(self.cookie, "")
        if len(token) > 256:
            return None
        async with self.database.transaction(read_only=True) as conn:
            return await (
                await conn.execute(
                    "SELECT s.*,p.username,p.display_name,p.permissions,p.enabled,"
                    "p.person_id,p.access_epoch AS current_epoch FROM identity.sessions s JOIN identity.principals p "
                    "USING(principal_id) WHERE token_hash=%s AND s.expires_at>now() AND p.enabled "
                    "AND s.access_epoch=p.auth_epoch",
                    (token_hash(token),),
                )
            ).fetchone()

    async def authenticate(self, request):
        row = await self.session(request)
        if row is None:
            raise AppError("unauthorized", status=401)
        return row

    async def verify_csrf(self, request):
        supplied = request.headers.get("X-CSRF-Token", "")
        row = await self.session(request)
        if row is None:
            async with self.database.transaction(read_only=True) as conn:
                row = await (
                    await conn.execute(
                        "SELECT csrf_token FROM identity.anonymous_sessions WHERE "
                        "token_hash=%s AND expires_at>now()",
                        (token_hash(request.cookies.get(self.cookie, "")),),
                    )
                ).fetchone()
        if (
            not row
            or not supplied.isascii()
            or not hmac.compare_digest(supplied, row["csrf_token"])
        ):
            raise AppError("csrf_invalid", status=403)

    def view(self, principal, initialized=True):
        return SessionView(
            principal is not None,
            initialized,
            PrincipalView(
                principal["principal_id"],
                principal["display_name"],
                tuple(principal["permissions"]),
            )
            if principal
            else None,
            principal["csrf_token"] if principal else None,
            bool(self.config.get("setup_token_ref", "TIANSHU_SETUP_TOKEN")),
        )

    async def inspect(self, request):
        principal = await self.session(request)
        async with self.database.transaction() as conn:
            initialized = await self.initialized(conn)
            if principal:
                return self.view(principal, initialized), None
            token = request.cookies.get(self.cookie, "")
            row = await (
                await conn.execute(
                    "SELECT csrf_token FROM identity.anonymous_sessions WHERE "
                    "token_hash=%s AND expires_at>now()",
                    (token_hash(token),),
                )
            ).fetchone()
            if row is None:
                token, csrf = "anon." + secrets.token_urlsafe(32), secrets.token_urlsafe(32)
                await conn.execute(
                    "DELETE FROM identity.anonymous_sessions WHERE expires_at<=now()"
                )
                await conn.execute(
                    "INSERT INTO identity.anonymous_sessions VALUES(%s,%s,now()+interval '1 hour')",
                    (token_hash(token), csrf),
                )
                row = {"csrf_token": csrf}
            return SessionView(
                False,
                initialized,
                None,
                row["csrf_token"],
                bool(self.config.get("setup_token_ref", "TIANSHU_SETUP_TOKEN")),
            ), token

    async def _new_session(self, conn, principal):
        token, csrf = "session." + secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        await conn.execute("DELETE FROM identity.sessions WHERE expires_at<=now()")
        await conn.execute(
            "INSERT INTO identity.sessions(token_hash,principal_id,csrf_token,access_epoch,expires_at) "
            "VALUES(%s,%s,%s,%s,%s)",
            (
                token_hash(token),
                principal["principal_id"],
                csrf,
                principal["auth_epoch"],
                datetime.now(UTC) + timedelta(days=self.config.get("session_days", 14)),
            ),
        )
        return token, self.view({**principal, "csrf_token": csrf})

    async def setup(self, body):
        reference = self.config.get("setup_token_ref", "TIANSHU_SETUP_TOKEN")
        if reference:
            expected = self.settings.secrets.resolve(reference)
            supplied = body.get("setup_token", "")
            if not isinstance(supplied, str) or not hmac.compare_digest(
                supplied.encode(), expected.encode()
            ):
                raise AppError("setup_token_invalid", status=403)
        if self.seal.exists():
            raise AppError("installation_sealed", status=409)
        username = self.username(body)
        display_name = body.get("display_name", username)
        if not isinstance(display_name, str) or not 1 <= len(display_name) <= 128:
            raise AppError("invalid_display_name")
        try:
            encoded = await asyncio.to_thread(password_hash, body.get("password"))
        except ValueError:
            raise AppError("invalid_password_length") from None
        async with self.database.transaction() as conn:
            await conn.execute("SELECT pg_advisory_xact_lock(733104304)")
            if await self.initialized(conn):
                raise AppError("already_initialized", status=409)
            person_id, principal_id = identifier("person"), identifier("principal")
            await conn.execute(
                "INSERT INTO identity.people(person_id,display_name) VALUES(%s,%s)",
                (person_id, display_name),
            )
            await conn.execute(
                "INSERT INTO identity.principals(principal_id,username,display_name,password_hash,"
                "person_id,permissions) VALUES(%s,%s,%s,%s,%s,%s)",
                (principal_id, username, display_name, encoded, person_id, ["*"]),
            )
            await conn.execute("INSERT INTO identity.installation(singleton) VALUES(true)")
            scope_id = identifier("scope")
            await conn.execute(
                "INSERT INTO identity.scopes(scope_id,person_id,kind,label) VALUES(%s,%s,'system','应用管理')",
                (scope_id, person_id),
            )
            await conn.execute(
                "INSERT INTO identity.scope_grants VALUES(%s,%s,%s)",
                (scope_id, principal_id, ["*"]),
            )
            principal = await (
                await conn.execute(
                    "SELECT * FROM identity.principals WHERE principal_id=%s", (principal_id,)
                )
            ).fetchone()
            token, view = await self._new_session(conn, principal)
        # A surviving seal stops an accidentally emptied database from reopening registration.
        await asyncio.to_thread(self._seal)
        return CommandResult(body["client_request_id"], data=view), token

    def _seal(self):
        self.seal.parent.mkdir(parents=True, exist_ok=True)
        with self.seal.open("a", encoding="ascii") as stream:
            stream.write("initialized\n")
            stream.flush()
            import os

            os.fsync(stream.fileno())
        self.seal.chmod(0o600)

    @staticmethod
    def username(body):
        username = body.get("username")
        if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.@-]{3,96}", username):
            raise AppError("invalid_username")
        return username.casefold()

    async def login(self, body):
        username = self.username(body)
        username_key = token_hash(username)
        async with self.database.transaction() as conn:
            window = await (
                await conn.execute(
                    "INSERT INTO identity.login_windows(username_hash) VALUES(%s) "
                    "ON CONFLICT(username_hash) DO UPDATE SET username_hash=excluded.username_hash RETURNING *",
                    (username_key,),
                )
            ).fetchone()
            if (datetime.now(UTC) - window["window_at"]).total_seconds() < 60 and window[
                "failures"
            ] >= 10:
                raise AppError("login_rate_limited", status=429)
            principal = await (
                await conn.execute(
                    "SELECT * FROM identity.principals WHERE username=%s AND enabled", (username,)
                )
            ).fetchone()
        valid = await asyncio.to_thread(
            verify_password, body.get("password"), principal["password_hash"] if principal else None
        )
        async with self.database.transaction() as conn:
            if not valid:
                await conn.execute(
                    "UPDATE identity.login_windows SET failures=CASE WHEN window_at<now()-interval '1 minute' "
                    "THEN 1 ELSE failures+1 END,window_at=CASE WHEN window_at<now()-interval '1 minute' THEN now() "
                    "ELSE window_at END WHERE username_hash=%s",
                    (username_key,),
                )
            else:
                await conn.execute(
                    "DELETE FROM identity.login_windows WHERE username_hash=%s", (username_key,)
                )
                # Recheck enabled/epoch after expensive password work.
                fresh = await (
                    await conn.execute(
                        "SELECT * FROM identity.principals WHERE principal_id=%s AND "
                        "enabled AND access_epoch=%s",
                        (principal["principal_id"], principal["access_epoch"]),
                    )
                ).fetchone()
                if not fresh:
                    raise AppError("access_changed", status=409)
                token, view = await self._new_session(conn, fresh)
        if not valid:
            raise AppError("invalid_credentials", status=401)
        return CommandResult(body["client_request_id"], data=view), token

    async def logout(self, request, body):
        await self.authenticate(request)
        async with self.database.transaction() as conn:
            await conn.execute(
                "DELETE FROM identity.sessions WHERE token_hash=%s",
                (token_hash(request.cookies.get(self.cookie, "")),),
            )
        return CommandResult(body["client_request_id"], data=self.view(None))

    def set_cookie(self, response, token):
        response.set_cookie(
            self.cookie,
            token,
            httponly=True,
            secure=self.config.get("cookie_secure", True),
            samesite="Strict",
            path="/",
            max_age=14 * 24 * 3600,
        )
