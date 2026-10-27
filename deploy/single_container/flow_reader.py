"""Adapt the existing authenticated log query to the platform's bounded graph reader."""

import asyncio
import json
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode

from deploy.observability.query import LokiClient, QueryError
from deploy.observability.transport import Deadline

SELECTOR = (
    '{stack="tianshu",service=~"tianshu_(platform|gateway|memory|memory-knowledge|companion)"}'
    ' | json | correlation_id=~"[a-f0-9]{32}"'
)
CORRELATION = re.compile(r"[a-f0-9]{32}\Z")


class FlowEventReader:
    """Two query workers, four waiting readers, and the original transport deadline.

    This adapter owns no log storage and cannot execute browser-supplied LogQL.
    Product projection validates the frozen event fields and session again.
    """

    def __init__(self, client, *, work=None):
        if work is None:
            from services.platform.local_work import LocalWork

            work = LocalWork(workers=2, capacity=2)
        self.client, self.work = client, work
        self.pending = 0
        self.closed = False

    @classmethod
    def from_settings(cls, spec):
        settings = json.loads(Path(spec["settings"]).read_bytes())
        token = Path(settings["tokens"]["query"]).read_text(encoding="utf-8").strip()
        return cls(LokiClient(spec["url"], settings["ca"], token=token))

    async def __call__(self, *, correlation_id, start_ns, end_ns, limit):
        if (
            correlation_id is not None
            and (
                not isinstance(correlation_id, str)
                or not CORRELATION.fullmatch(correlation_id)
            )
            or type(start_ns) is not int
            or type(end_ns) is not int
            or not 0 < start_ns <= end_ns
            or end_ns - start_ns > 24 * 3600 * 1_000_000_000
            or type(limit) is not int
            or not 1 <= limit <= 2000
        ):
            raise ValueError("flow_query_input_invalid")
        if self.closed or self.pending >= 4:
            raise QueryError("flow_query_busy_or_closed")
        self.pending += 1
        deadline = Deadline(seconds=4)
        try:
            return await self.work.run(
                self._read, correlation_id, start_ns, end_ns, limit, deadline
            )
        except asyncio.CancelledError:
            deadline.cancelled.set()
            raise
        finally:
            self.pending -= 1

    def _read(self, correlation_id, start_ns, end_ns, limit, deadline):
        selector = SELECTOR
        if correlation_id is not None:
            selector += ' | correlation_id="' + correlation_id + '"'
        params = urlencode(
            {
                "query": selector,
                "start": str(start_ns),
                # Loki timestamps are ingestion times. Include late records already
                # received, then select their original event time below.
                "end": str(max(end_ns, time.time_ns())),
                "direction": "backward",
                "limit": limit + 1,
            }
        )
        status, body, _ = self.client.request(
            "/loki/api/v1/query_range?" + params, deadline=deadline
        )
        if status != 200:
            raise QueryError("flow_query_unavailable")
        try:
            document = json.loads(body)
            if (
                document["status"] != "success"
                or document["data"]["resultType"] != "streams"
            ):
                raise ValueError()
            values = [
                (int(stamp), json.loads(line))
                for stream in document["data"]["result"]
                for stamp, line in stream["values"]
            ]
            if len(values) > limit + 1:
                raise ValueError()
            values.sort(key=lambda row: row[0], reverse=True)
            events = []
            for _, event in values:
                stamp = datetime.fromisoformat(
                    event["timestamp"].replace("Z", "+00:00")
                )
                if stamp.utcoffset() is None:
                    raise ValueError()
                original_ns = int(stamp.timestamp() * 1_000_000_000)
                if start_ns <= original_ns <= end_ns:
                    events.append(event)
            return {"events": events[:limit], "complete": len(values) < limit + 1}
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
            raise QueryError("flow_query_invalid_response") from None

    def close(self):
        self.closed = True
        self.work.close()
        self.client.close()

    async def aclose(self):
        self.close()
        await self.work.drain()
