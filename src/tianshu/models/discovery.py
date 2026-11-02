"""Bounded native provider catalogues, using the sole Broker for real admission."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

import aiohttp

from tianshu.runtime.api import CommandResult
from tianshu.runtime.errors import AppError
from tianshu.types import Coverage, RequestBudget, ServiceClass
from .credentials import provider_reference
from .ledger import context_fingerprint, digest, receipt
from .types import ExternalRequest
from .transport import provider_headers


OPERATION = "models.providers.models"
SUPPORTED = {
    "openai.chat",
    "openai.responses",
    "openai.embedding",
    "anthropic.messages",
    "gemini.generate",
}


def attempt_id(context, provider_id, client_request_id):
    return str(
        uuid5(
            NAMESPACE_URL, digest((OPERATION, context.principal_id, provider_id, client_request_id))
        )
    )


def native_request(provider, secret, cursor=None, *, context=None, intent_id=None):
    base = provider["base_url"].rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username
        or parsed.query
        or parsed.fragment
    ):
        raise AppError("invalid_provider_base_url")
    protocol = provider["protocol"]
    if protocol not in SUPPORTED:
        raise AppError("model_enumeration_unsupported", status=422)
    if cursor is not None and (not isinstance(cursor, str) or not 1 <= len(cursor) <= 4096):
        raise AppError("invalid_model_cursor")
    headers = {"Accept": "application/json", "Accept-Encoding": "identity"}
    headers.update(provider_headers(provider["base_url"], context, intent_id))
    params = {}
    if protocol == "gemini.generate":
        headers["x-goog-api-key"] = secret
        params["pageSize"] = 1000
        if cursor:
            params["pageToken"] = cursor
    elif protocol == "anthropic.messages":
        headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
        params["limit"] = 1000
        if cursor:
            params["after_id"] = cursor
    else:
        headers["Authorization"] = "Bearer " + secret
        if cursor:
            raise AppError("model_cursor_unsupported", status=422)
    return base + "/models", headers, params


def parse_catalog(protocol, payload):
    if not isinstance(payload, dict):
        raise AppError("provider_model_catalog_invalid", status=502)
    rows = payload.get("models" if protocol == "gemini.generate" else "data")
    if not isinstance(rows, list) or len(rows) > 4096:
        raise AppError("provider_model_catalog_invalid", status=502)
    ids = []
    for row in rows:
        value = (
            row.get("name" if protocol == "gemini.generate" else "id")
            if isinstance(row, dict)
            else None
        )
        if protocol == "gemini.generate" and isinstance(value, str):
            value = value.removeprefix("models/")
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 512
            or any(ord(c) < 33 or ord(c) > 126 for c in value)
        ):
            raise AppError("provider_model_catalog_invalid", status=502)
        ids.append(value)
    next_cursor = payload.get("nextPageToken") if protocol == "gemini.generate" else None
    if protocol == "anthropic.messages" and payload.get("has_more") is True:
        next_cursor = payload.get("last_id")
        if not next_cursor:
            raise AppError("provider_model_catalog_invalid", status=502)
    if next_cursor is not None and (
        not isinstance(next_cursor, str) or not 1 <= len(next_cursor) <= 4096
    ):
        raise AppError("provider_model_catalog_invalid", status=502)
    return {
        "models": list(dict.fromkeys(ids)),
        "next_cursor": next_cursor,
        "coverage": {
            "complete": next_cursor is None,
            "missing": ["more_pages"] if next_cursor else [],
        },
    }


async def observed(
    services, context, provider_id, client_request_id, *, expected_hash=None, connection=None
):
    async def read(conn):
        row = await (
            await conn.execute(
                "SELECT * FROM models.attempts WHERE attempt_id=%s",
                (attempt_id(context, provider_id, client_request_id),),
            )
        ).fetchone()
        if row is None:
            return None
        if (
            row["principal_id"] != context.principal_id
            or row["context_fingerprint"] != context_fingerprint(context)
            or row["function"] != "models.discover"
            or row["provider_id"] != provider_id
        ):
            raise AppError("model_result_context_conflict", status=403)
        if expected_hash is not None and row["intent_id"] != expected_hash:
            raise AppError("client_request_id_conflict", status=409)
        result = (row["result"] or {}).get("discovery") if row["status"] == "succeeded" else None
        value = {
            "provider_id": provider_id,
            "revision": None,
            "models": None,
            "next_cursor": None,
            "coverage": Coverage(False, missing=("enumeration_not_completed",)),
            "state": row["status"],
            "model_receipt": receipt(row),
        }
        if result is not None:
            value.update(result)
        return CommandResult(
            client_request_id, data=value, attempt_id=row["attempt_id"], revision=value["revision"]
        )

    if connection is not None:
        return await read(connection)
    async with services.database.transaction(control=True, read_only=True) as conn:
        return await read(conn)


async def enumerate_models(services, context, provider_id, body):
    expected = body.get("expected_revision")
    if type(expected) is not int or expected < 1:
        raise AppError("expected_revision_required")
    request_hash = digest(
        {**body, "provider_id": provider_id, "context": context_fingerprint(context)}
    )
    client_id = body["client_request_id"]

    async def replay(conn, original):
        current = await observed(
            services, context, provider_id, client_id, expected_hash=request_hash, connection=conn
        )
        return replace(original, data=current.data if current else None)

    cached = await services.commands.replay(
        context, OPERATION, client_id, request_hash, on_replay=replay
    )
    if cached:
        return cached
    previous = await observed(services, context, provider_id, client_id, expected_hash=request_hash)
    if previous is None:
        async with services.database.transaction(read_only=True) as conn:
            provider = await (
                await conn.execute(
                    "SELECT * FROM models.providers WHERE provider_id=%s", (provider_id,)
                )
            ).fetchone()
            quota = await (
                await conn.execute(
                    "SELECT 1 FROM models.quota_groups WHERE quota_group=%s",
                    (body.get("quota_group"),),
                )
            ).fetchone()
        if not provider or provider["revision"] != expected:
            raise AppError("revision_conflict", status=409)
        if not provider["enabled"] or not quota:
            raise AppError("provider_or_quota_unavailable", status=409)
        reference = provider_reference(
            provider_id,
            provider["credential_ref"],
            services.settings.get("broker", {}).get("provider_credential_refs", {}),
        )
        secret = await services.credentials.resolve(reference)
        request = ExternalRequest(
            request_hash,
            attempt_id(context, provider_id, client_id),
            context,
            RequestBudget(datetime.now(UTC) + timedelta(seconds=30)),
            body["quota_group"],
            "models.discover",
            ServiceClass.INTERACTIVE,
            provider_id,
            estimated_units={"requests": 1},
            execution_timeout_seconds=30,
        )
        url, headers, params = native_request(
            provider,
            secret,
            body.get("cursor"),
            context=request.context,
            intent_id=request.intent_id,
        )
        try:
            async with services.models.resource(request) as permit:
                await permit.submitted()
                async with aiohttp.ClientSession(
                    timeout=aiohttp.ClientTimeout(total=permit.request.budget.remaining(), sock_connect=30)
                ) as session:
                    async with session.get(
                        url, headers=headers, params=params, allow_redirects=False
                    ) as response:
                        if response.status >= 300:
                            await permit.rejected(
                                "model_catalog_http_" + str(response.status),
                                usage={"units": {"requests": 1}},
                            )
                        else:
                            raw = bytearray()
                            async for chunk in response.content.iter_chunked(65536):
                                raw.extend(chunk)
                                if len(raw) > 2 * 1024 * 1024:
                                    await permit.rejected(
                                        "model_catalog_too_large", usage={"units": {"requests": 1}}
                                    )
                                    raise AppError("provider_model_catalog_too_large", status=502)
                            try:
                                parsed = parse_catalog(provider["protocol"], json.loads(raw))
                            except (ValueError, AppError):
                                await permit.rejected(
                                    "model_catalog_invalid", usage={"units": {"requests": 1}}
                                )
                                raise AppError(
                                    "provider_model_catalog_invalid", status=502
                                ) from None
                            await permit.finish(
                                usage={"units": {"requests": 1}},
                                result={
                                    "discovery": {
                                        "provider_id": provider_id,
                                        "revision": expected,
                                        **parsed,
                                    }
                                },
                            )
        except AppError as exc:
            if exc.code != "attempt_already_exists":
                raise
        previous = await observed(
            services, context, provider_id, client_id, expected_hash=request_hash
        )
    if previous is None:
        raise AppError("model_enumeration_observation_unavailable", status=503)

    async def record(conn):
        return previous

    return await services.commands.execute(
        context, OPERATION, client_id, request_hash, record, on_replay=replay
    )
