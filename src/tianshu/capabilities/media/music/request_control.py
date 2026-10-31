"""Shared, durable music request pacing. All existing workers use the Media ledger."""

import asyncio
import math
import uuid
from contextlib import asynccontextmanager
from datetime import timezone
from email.utils import parsedate_to_datetime

from ...contracts import Fault

KIND = "music_request_control"
MIN_INTERVAL = 3
HOURLY_LIMIT = 600
DAILY_LIMIT = 3000


class Deferred(Fault):
    def __init__(self, until, reason="music_request_deferred"):
        super().__init__(reason, 429)
        self.until = until


class Limited(Fault):
    def __init__(self, seconds=0):
        super().__init__("music_rate_limited", 429)
        self.retry_after = seconds


def retry_after(value, now):
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            seconds = date.timestamp() - now
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, seconds) if math.isfinite(seconds) else 0


def bucket(row, now):
    row = dict(row or {})
    for name, duration in (("hour", 3600), ("day", 86400)):
        if name + "_start" not in row or now >= row[name + "_start"] + duration:
            row[name + "_start"], row[name + "_used"] = now, 0
    return row


class RequestControl:
    def __init__(
        self,
        media,
        *,
        interval=MIN_INTERVAL,
        hourly=HOURLY_LIMIT,
        daily=DAILY_LIMIT,
        contention_timeout=30,
    ):
        self.repo, self.local = media.repo, media.local
        self.interval, self.hourly, self.daily = interval, hourly, daily
        self.contention_timeout = contention_timeout

    def reserve(self, provider, account_id, maximum, owner, account_revision=None):
        now = self.repo.clock()
        keys = (provider + ":all", account_id or provider + ":login")
        with self.repo.transaction() as db:
            account = self.repo._document(db, "music_account", account_id) if account_id else None
            if account_revision is not None and (
                not account
                or account["revision"] != account_revision
                or account["state"] != "ready"
            ):
                raise Fault("account_changed", 409)
            rows = [bucket(self.repo._document(db, KIND, key), now) for key in keys]
            for row in rows:
                if row.get("auth_required"):
                    raise Fault("music_auth_required", 409)
                if row.get("cooldown_until", 0) > now:
                    return {"wait": row["cooldown_until"], "reason": "music_account_cooling"}
                if row.get("owner") and row.get("lease_until", 0) > now:
                    return {"wait": now + 10, "reason": "music_request_deferred"}
                for name, limit, duration in (
                    ("hour", self.hourly, 3600),
                    ("day", self.daily, 86400),
                ):
                    if row.get(name + "_used", 0) + maximum > limit:
                        return {
                            "wait": row[name + "_start"] + duration,
                            "reason": "music_request_budget",
                        }
            next_at = max(row.get("next_at", 0) for row in rows)
            if next_at > now:
                return {"wait": next_at, "reason": "music_request_deferred"}
            for key, row in zip(keys, rows):
                self.repo.put(
                    KIND,
                    key,
                    {
                        **row,
                        "provider": provider,
                        "scope": "provider" if key == keys[0] else "account",
                        "account_id": account_id if key != keys[0] else None,
                        "owner": owner,
                        "lease_until": now + 60,
                        "next_at": now + self.interval,
                        "hour_used": row.get("hour_used", 0) + maximum,
                        "day_used": row.get("day_used", 0) + maximum,
                    },
                    db=db,
                )
        return {"keys": keys, "account_revision": account.get("revision") if account else None}

    def renew(self, ticket):
        with self.repo.transaction() as db:
            for key in ticket["keys"]:
                row = self.repo._document(db, KIND, key)
                if (
                    not row
                    or row.get("owner") != ticket["owner"]
                    or row.get("lease_until", 0) <= self.repo.clock()
                ):
                    raise Fault("lease_conflict", 409)
                self.repo.put(KIND, key, {**row, "lease_until": self.repo.clock() + 60}, db=db)

    def release(self, ticket, error):
        now = self.repo.clock()
        used = ticket.get("used", ticket["maximum"])
        used = used if type(used) is int and 0 <= used <= ticket["maximum"] else ticket["maximum"]
        with self.repo.transaction() as db:
            for key in ticket["keys"]:
                row = self.repo._document(db, KIND, key)
                if not row or row.get("owner") != ticket["owner"]:
                    continue
                row.update(
                    owner=None,
                    lease_until=0,
                    next_at=max(row.get("next_at", 0), now + self.interval),
                )
                for name in ("hour", "day"):
                    row[name + "_used"] = max(
                        0, row.get(name + "_used", 0) - ticket["maximum"] + used
                    )
                code = error.code if isinstance(error, Fault) else None
                if code == "music_rate_limited":
                    strikes = min(6, row.get("strikes", 0) + 1)
                    row.update(
                        strikes=strikes,
                        code=code,
                        cooldown_until=max(
                            row.get("cooldown_until", 0),
                            now
                            + max(
                                getattr(error, "retry_after", 0),
                                min(86400, 1800 * 2 ** (strikes - 1)),
                            ),
                        ),
                    )
                elif code == "music_auth_required" and row.get("account_id"):
                    account = self.repo._document(db, "music_account", row["account_id"])
                    same_revision = not account or account["revision"] == ticket.get(
                        "account_revision"
                    )
                    if same_revision:
                        row.update(auth_required=True, code=code)
                    if same_revision and account and account["state"] == "ready":
                        self.repo.put(
                            "music_account",
                            row["account_id"],
                            {**account, "state": "auth_required", "checked_at": now},
                            db=db,
                        )
                elif code in {"music_upstream_unavailable", "music_download_unavailable"}:
                    row["failures"] = row.get("failures", 0) + 1
                    if row["failures"] >= 3:
                        row.update(
                            cooldown_until=max(row.get("cooldown_until", 0), now + 300), code=code
                        )
                elif error is None:
                    row["failures"] = 0
                self.repo.put(KIND, key, row, db=db)

    @asynccontextmanager
    async def access(self, provider, account_id=None, maximum=1, account_revision=None):
        owner = uuid.uuid4().hex
        deadline = asyncio.get_running_loop().time() + self.contention_timeout
        while True:
            result = await self.local(
                self.reserve, provider, account_id, maximum, owner, account_revision
            )
            if "wait" not in result:
                break
            wait = result["wait"] - self.repo.clock()
            remaining = deadline - asyncio.get_running_loop().time()
            if result["reason"] != "music_request_deferred" or remaining <= 0:
                raise Deferred(result["wait"], result["reason"])
            # Brief peer contention should not discard an in-flight all-channel
            # comparison and repeat its completed entitlement requests. Poll only
            # the local lease; budgets and upstream cooldown still defer at once.
            await asyncio.sleep(min(max(0.05, wait), remaining, 1))
        ticket = {**result, "owner": owner, "maximum": maximum}
        caller = asyncio.current_task()

        async def pulse():
            while True:
                await asyncio.sleep(15)
                try:
                    await self.local(self.renew, ticket)
                except Fault:
                    caller.cancel()
                    return

        heartbeat = asyncio.create_task(pulse())
        error = None
        try:
            yield ticket
        except BaseException as cause:
            error = cause
            if isinstance(cause, Fault) and cause.code == "music_rate_limited":
                raise Deferred(
                    self.repo.clock() + max(getattr(cause, "retry_after", 0), 1800),
                    "music_account_cooling",
                ) from None
            raise
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)
            # Complete the durable release even if the request was cancelled.
            release = asyncio.create_task(self.local(self.release, ticket, error))
            try:
                await asyncio.shield(release)
            except asyncio.CancelledError:
                await release
                raise

    def view(self):
        now = self.repo.clock()
        rows = [
            bucket(row, now)
            for row in self.repo.list(KIND)
            if row.get("scope") == "provider" or row.get("account_id")
        ]
        return {
            "interval_seconds": self.interval,
            "hourly_limit": self.hourly,
            "daily_limit": self.daily,
            "scopes": [
                {
                    key: row.get(key)
                    for key in (
                        "provider",
                        "account_id",
                        "hour_used",
                        "day_used",
                        "cooldown_until",
                        "auth_required",
                        "code",
                    )
                }
                for row in rows
            ],
        }


@asynccontextmanager
async def request_slot(control, provider, account_id=None, maximum=1, account_revision=None):
    if control is None:
        yield {}
    else:
        async with control.access(provider, account_id, maximum, account_revision) as ticket:
            yield ticket
