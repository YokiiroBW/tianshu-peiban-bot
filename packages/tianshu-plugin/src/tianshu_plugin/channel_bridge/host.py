"""External SDK execution and durable delivery identity; no core compatibility API."""

import asyncio
import hashlib
import hmac
import json
import re
import aiohttp
from .journal import Journal

PROTOCOL = "tianshu.channel-bridge/2"


class SDKFailure(Exception):
    def __init__(self, code="sdk_failed", *, certain=False, retcode=None):
        self.code, self.certain, self.retcode = code, certain, retcode


class Host:
    def __init__(
        self,
        *,
        journal_path,
        instance_id,
        self_id,
        token,
        core_url,
        connection_id,
        sdk_call,
        framework,
        version,
    ):
        if not token or not instance_id or not connection_id:
            raise ValueError("bridge_configuration_required")
        self.journal = Journal(journal_path)
        self.instance_id, self.self_id, self.token = instance_id, str(self_id), token
        self.core_url, self.connection_id, self.sdk_call = (
            core_url.rstrip("/"),
            connection_id,
            sdk_call,
        )
        self.framework, self.version = framework, version
        self.tasks = {}
        self.pump_task = None
        self.session = None

    def authorized(self, header):
        return hmac.compare_digest(header or "", "Bearer " + self.token)

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=30), connector=aiohttp.TCPConnector(limit=4)
        )
        self.pump_task = asyncio.create_task(self.pump(), name="tianshu-bridge-events")

    async def stop(self):
        if self.pump_task:
            self.pump_task.cancel()
            await asyncio.gather(self.pump_task, return_exceptions=True)
        for task in tuple(self.tasks.values()):
            task.cancel()
        await asyncio.gather(*tuple(self.tasks.values()), return_exceptions=True)
        if self.session:
            await self.session.close()
        await asyncio.to_thread(self.journal.close)

    async def call(self, body):
        if (
            body.get("protocol") != PROTOCOL
            or body.get("host_instance_id") != self.instance_id
            or str(body.get("self_id")) != self.self_id
        ):
            raise ValueError("bridge_identity_mismatch")
        identifier = body["call_id"]
        name = body["api_name"]
        if (
            not isinstance(identifier, str)
            or len(identifier) > 128
            or not re.fullmatch(r"[a-zA-Z0-9_.]+", name)
        ):
            raise ValueError("bridge_call_invalid")
        row = await asyncio.to_thread(self.journal.begin, identifier, body)
        if row["fresh"]:
            self.tasks[identifier] = asyncio.create_task(
                self.execute(identifier, name, body.get("params", {}))
            )
        task = self.tasks.get(identifier)
        if task:
            try:
                await asyncio.wait_for(asyncio.shield(task), 25)
            except asyncio.TimeoutError:
                pass
        return await self.status(identifier)

    async def execute(self, identifier, name, params):
        state = "unknown"
        response = None
        try:
            data = await self.sdk_call(name, params)
            state = "confirmed"
            response = {"status": "ok", "retcode": 0, "data": data}
        except SDKFailure as error:
            state = "rejected" if error.certain else "unknown"
            response = {
                "status": "failed" if error.certain else "unknown",
                "retcode": error.retcode if error.retcode is not None else -1,
                "error_code": error.code,
            }
        except asyncio.CancelledError:
            response = {"status": "unknown", "retcode": -1, "error_code": "sdk_interrupted"}
            raise
        except Exception:
            response = {"status": "unknown", "retcode": -1, "error_code": "sdk_interrupted"}
        finally:
            await asyncio.shield(
                asyncio.to_thread(
                    self.journal.finish,
                    identifier,
                    state,
                    response or {"status": "unknown", "retcode": -1},
                )
            )
            self.tasks.pop(identifier, None)

    async def status(self, identifier):
        row = await asyncio.to_thread(self.journal.get, identifier)
        if row is None:
            return {
                "protocol": PROTOCOL,
                "call_id": identifier,
                "state": "not_found",
                "response": None,
            }
        return {
            "protocol": PROTOCOL,
            "call_id": identifier,
            "state": row["state"],
            "response": row.get("response"),
            "framework": self.framework,
            "version": self.version,
        }

    async def acknowledge(self, identifier):
        await asyncio.to_thread(self.journal.acknowledge, identifier)

    async def cancel(self, identifier):
        task = self.tasks.get(identifier)
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self.acknowledge(identifier)
        return await self.status(identifier)

    async def publish(self, event, ownership):
        if str(event.get("self_id")) != self.self_id:
            raise ValueError("bridge_account_mismatch")
        if not ownership.get("dispatch_completed") and ownership.get("reply_owner") != "none":
            raise ValueError("bridge_dispatch_incomplete")
        body = {
            "protocol": PROTOCOL,
            "host_instance_id": self.instance_id,
            "event": event,
            "ownership": {**ownership, "framework": self.framework, "version": self.version},
        }
        identifier = hashlib.sha256(
            json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        await asyncio.to_thread(self.journal.event, identifier, body)

    async def pump(self):
        while True:
            pending = await asyncio.to_thread(self.journal.pending)
            for identifier, body in pending:
                try:
                    async with self.session.post(
                        self.core_url + "/channels/bridge/" + self.connection_id + "/events",
                        json=body,
                        headers={"Authorization": "Bearer " + self.token},
                        allow_redirects=False,
                    ) as response:
                        if response.status == 200:
                            raw = await response.json()
                            if raw.get("accepted") is True:
                                await asyncio.to_thread(self.journal.event_ack, identifier)
                except asyncio.CancelledError:
                    raise
                except (aiohttp.ClientError, OSError, ValueError):
                    break
            receipts = await asyncio.to_thread(self.journal.pending_receipts)
            for receipt in receipts:
                try:
                    body = {"protocol": PROTOCOL, "host_instance_id": self.instance_id, **receipt}
                    async with self.session.post(
                        self.core_url + "/channels/bridge/" + self.connection_id + "/receipts",
                        json=body,
                        headers={"Authorization": "Bearer " + self.token},
                        allow_redirects=False,
                    ) as response:
                        if response.status == 200:
                            await asyncio.to_thread(self.journal.receipt_ack, receipt["call_id"])
                except asyncio.CancelledError:
                    raise
                except (aiohttp.ClientError, OSError, ValueError):
                    break
            await asyncio.sleep(1 if pending else 3)
