"""Read the existing Prometheus endpoint with a fixed, bounded query set."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import json
import math

import aiohttp


QUERIES = {
    "observer_memory_bytes": 'sum by (job) (process_resident_memory_bytes{job=~"vector|loki|prometheus"})',
    "observer_cpu_cores": 'sum by (job) (rate(process_cpu_seconds_total{job=~"vector|loki|prometheus"}[5m]))',
}


class PrometheusResources:
    def __init__(self, url=None):
        self.url = url.rstrip("/") if url else None

    async def read(self):
        if not self.url:
            return {"status": "not_configured", "scope": "observability_processes", "metrics": {}}
        async def query(session, name, expression):
            try:
                async with session.get(self.url + "/api/v1/query", params={"query": expression},
                                       allow_redirects=False) as response:
                    if response.status != 200:
                        return name, {"status": "unavailable", "samples": []}
                    raw = await response.content.read(1024 * 1024 + 1)
                    if len(raw) > 1024 * 1024:
                        return name, {"status": "unavailable", "samples": []}
                    data = json.loads(raw)
                if data.get("status") != "success" or data.get("data", {}).get("resultType") != "vector":
                    return name, {"status": "unavailable", "samples": []}
                samples = []
                for item in data["data"].get("result", [])[:16]:
                    value = float(item["value"][1])
                    job = item.get("metric", {}).get("job")
                    if math.isfinite(value) and job in {"vector", "loki", "prometheus"}:
                        samples.append({"job": job, "value": value, "timestamp": item["value"][0]})
                return name, {"status": "available" if samples else "no_samples", "samples": samples}
            except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError, IndexError):
                return name, {"status": "unavailable", "samples": []}
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            values = dict(await asyncio.gather(*(query(session, key, expression) for key, expression in QUERIES.items())))
        return {"status": "available" if any(value["status"] == "available" for value in values.values()) else "unavailable",
                "scope": "observability_processes", "metrics": values, "as_of": datetime.now(UTC)}
