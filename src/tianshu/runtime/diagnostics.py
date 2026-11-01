"""Read actual jobs, process resources and Loki; no parallel operational state ledger."""

import json
import ssl
import re

import aiohttp

from .errors import AppError
from .execution import job_observation


class RuntimeDiagnostics:
    def __init__(self, database, models, settings):
        self.database, self.models, self.settings = database, models, settings

    async def tasks(self, *, limit=50):
        limit = max(1, min(int(limit), 100))
        async with self.database.transaction(control=True, read_only=True) as conn:
            jobs = await (
                await conn.execute(
                    "SELECT id,task_name,queue_name,status,scheduled_at,attempts,abort_requested,"
                    "coalesce(scheduled_at>now(),false) AS scheduled "
                    "FROM procrastinate_jobs ORDER BY id DESC LIMIT %s",
                    (limit,),
                )
            ).fetchall()
            counts = await (
                await conn.execute(
                    "SELECT queue_name,status,count(*) AS count "
                    "FROM procrastinate_jobs GROUP BY queue_name,status"
                )
            ).fetchall()
        return {"jobs": [job_observation(row) for row in jobs], "counts": counts,
                "limit": limit, "complete": len(jobs) < limit}

    async def resources(self):
        async with self.database.transaction(control=True, read_only=True) as conn:
            processes = await (
                await conn.execute(
                    "SELECT role,ready,registration,heartbeat_at "
                    "FROM runtime.processes ORDER BY heartbeat_at DESC LIMIT 100"
                )
            ).fetchall()
        try:
            broker = await self.models.snapshot()
        except (AppError, aiohttp.ClientError, TimeoutError):
            broker = {"status": "unavailable", "quota_groups": None}
        return {"processes": processes, "broker": broker}

    async def logs(self, *, start_ns, end_ns, role=None, limit=100):
        config = self.settings.get("observability", {})
        if not config.get("loki_url"):
            raise AppError("log_query_not_configured", status=503)

        def nanoseconds(value):
            if type(value) is int:
                parsed = value
            elif isinstance(value, str) and re.fullmatch(r"[0-9]{1,19}", value):
                parsed = int(value)
            else:
                raise AppError("invalid_log_time_range")
            if not 0 <= parsed <= 2**63 - 1:
                raise AppError("invalid_log_time_range")
            return parsed

        start_ns, end_ns = nanoseconds(start_ns), nanoseconds(end_ns)
        if end_ns <= start_ns or end_ns - start_ns > 24 * 3600 * 1_000_000_000:
            raise AppError("invalid_log_time_range")
        if role not in {None, "interactive", "model-broker", "worker"}:
            raise AppError("invalid_log_role")
        limit = max(1, min(int(limit), 1000))
        selector = '{stack="tianshu",service="tianshu"}'
        if role:
            selector += ' | json | role="' + role + '"'
        headers = {"X-Scope-OrgID": config.get("tenant", "tianshu")}
        if config.get("query_token_ref"):
            headers["Authorization"] = "Bearer " + self.settings.secrets.resolve(
                config["query_token_ref"]
            )
        tls = ssl.create_default_context(cafile=config.get("ca_file"))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(
                config["loki_url"].rstrip("/") + "/loki/api/v1/query_range",
                params={
                    "query": selector,
                    "start": str(start_ns),
                    "end": str(end_ns),
                    "limit": str(limit),
                    "direction": "backward",
                },
                headers=headers,
                ssl=tls,
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise AppError("log_query_unavailable", status=503)
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > 8 * 1024 * 1024:
                        raise AppError("log_query_unavailable", status=503)
                    chunks.append(chunk)
                try:
                    value = json.loads(b"".join(chunks))
                except (ValueError, UnicodeError):
                    raise AppError("log_query_unavailable", status=503) from None
        if value.get("status") != "success":
            raise AppError("log_query_unavailable", status=503)
        streams = value.get("data", {}).get("result", [])
        count = sum(len(stream.get("values", [])) for stream in streams)
        # A saturated bucket is explicitly incomplete; never promise false full coverage.
        next_end = min(
            (int(item[0]) for stream in streams for item in stream.get("values", [])), default=None
        )
        return {
            "streams": streams,
            "coverage": {
                "complete": count < limit,
                "missing": ["query_limit"] if count >= limit else [],
            },
            "next_end_ns": str(next_end) if next_end is not None else None,
        }
