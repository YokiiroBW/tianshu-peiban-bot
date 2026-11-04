"""External project clients call the same Identity-authorized native HTTP operations."""

import json
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
from .catalog import READ_SCHEMAS, WRITE_SCHEMAS
from ..contracts import Fault, require


class ProjectClient:
    def __init__(self, base_url, session_file, *, actor_id=None, person_id=None, conversation_id=None):
        parsed = urlsplit(base_url)
        require(
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment,
            "project_server_url_invalid",
            400,
        )
        self.base_url = base_url.rstrip("/")
        value = json.loads(Path(session_file).read_text(encoding="utf-8"))
        require(
            isinstance(value, dict)
            and isinstance(value.get("cookie"), str)
            and isinstance(value.get("csrf"), str),
            "identity_session_file_required",
            400,
        )
        self.headers = {"Cookie": value["cookie"], "X-CSRF-Token": value["csrf"]}
        self.target = {
            "actor_id": actor_id,
            "person_id": person_id,
            "conversation_id": conversation_id,
        }

    async def request(self, path, arguments):
        payload = {**arguments, **self.target}
        params = {key: value for key, value in self.target.items() if value}
        async with aiohttp.ClientSession(
            headers=self.headers, timeout=aiohttp.ClientTimeout(total=120)
        ) as session:
            async with session.post(
                self.base_url + "/api/v1/" + path,
                params=params,
                json=payload,
                allow_redirects=False,
            ) as response:
                raw = await response.content.read(2 * 1024 * 1024 + 1)
                require(len(raw) <= 2 * 1024 * 1024, "project_response_budget", 413)
                value = json.loads(raw)
                if response.status >= 300:
                    raise Fault(
                        (value.get("error") or {}).get("code", "project_request_failed"),
                        response.status,
                    )
                return value

    async def read(self, operation, arguments):
        require(operation in READ_SCHEMAS, "project_read_operation_required", 400)
        return await self.request("projects/" + operation, arguments)

    async def write(self, operation, arguments, *, client_request_id, expected_revision):
        require(operation in WRITE_SCHEMAS, "project_write_operation_required", 400)
        return await self.request(
            "projects/" + operation,
            {
                **arguments,
                "client_request_id": client_request_id,
                "expected_revision": expected_revision,
            },
        )

    async def status(self, action_id):
        return await self.request("capabilities/get", {"action_id": action_id})

    async def source_read(self, source_ref, requested):
        return await self.request(
            "sources/read", {"source_ref": source_ref, "requested": requested}
        )
