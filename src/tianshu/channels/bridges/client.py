"""Authenticated calls to existing external SDK hosts, with explicit status recovery."""

import asyncio
import aiohttp
from ..contracts import ChannelError, TransportResult
from ..transports import bounded_json, onebot_result


class BridgeTransport:
    def __init__(self, connection, token):
        self.connection, self.token = connection, token
        self.session = None

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=35), connector=aiohttp.TCPConnector(limit=4)
        )

    async def request(self, method, path, body=None):
        async with self.session.request(
            method,
            self.connection["endpoint"].rstrip("/") + "/tianshu/channel-bridge/v2/" + path,
            json=body,
            headers={"Authorization": "Bearer " + self.token},
            allow_redirects=False,
        ) as response:
            if response.status != 200:
                raise ChannelError("bridge_http_failed", 502, effect="unknown")
            value = await bounded_json(response)
            if value.get("protocol") != "tianshu.channel-bridge/2":
                raise ChannelError("bridge_protocol_mismatch", 502, effect="unknown")
            return value

    def result(self, value):
        if value["state"] == "confirmed":
            return onebot_result(value["response"])
        if value["state"] == "rejected":
            return TransportResult(
                "rejected",
                retcode=(value.get("response") or {}).get("retcode"),
                error_code=(value.get("response") or {}).get("error_code", "sdk_rejected"),
                raw=value.get("response") or {},
            )
        return TransportResult(
            "accepted" if value["state"] == "submitted" else "unknown",
            error_code="sdk_" + value["state"],
            raw=value.get("response") or {},
        )

    async def call(self, identifier, name, params, submitted, *, context=None):
        await submitted()
        try:
            value = await self.request(
                "POST",
                "calls",
                {
                    "protocol": "tianshu.channel-bridge/2",
                    "host_instance_id": self.connection["bridge_instance_id"],
                    "self_id": self.connection["self_external_id"],
                    "call_id": identifier,
                    "api_name": name,
                    "params": params,
                },
            )
            return self.result(value)
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, ChannelError):
            return TransportResult("unknown", error_code="bridge_transport_interrupted")

    async def status(self, identifier):
        return self.result(await self.request("GET", "calls/" + identifier))

    async def acknowledge(self, identifier):
        await self.request("POST", "calls/" + identifier + "/ack", {})

    async def cancel(self, identifier):
        return self.result(await self.request("POST", "calls/" + identifier + "/cancel", {}))

    async def close(self):
        if self.session:
            await self.session.close()
