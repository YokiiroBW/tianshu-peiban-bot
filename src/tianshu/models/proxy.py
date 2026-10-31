"""Expose only native public model routes through the interactive TLS listener."""

import asyncio

import aiohttp
from aiohttp import web

from tianshu.runtime.errors import AppError


NATIVE_PATHS = (
    "/v1/chat/completions",
    "/v1/responses",
    "/v1/messages",
    "/v1/embeddings",
    "/v1beta/models/{model}:generateContent",
    "/v1beta/models/{model}:streamGenerateContent",
)


def register_native_proxy(app, settings):
    session_key = web.AppKey("native_model_proxy", aiohttp.ClientSession)

    async def lifecycle(_app):
        async with aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=32),
            timeout=aiohttp.ClientTimeout(total=920),
            auto_decompress=False,
        ) as session:
            _app[session_key] = session
            yield

    async def forward(request):
        # No Cookie, setup/session credentials or internal Broker key crosses this proxy.
        headers = {
            key: request.headers[key]
            for key in (
                "Authorization",
                "x-api-key",
                "x-goog-api-key",
                "anthropic-version",
                "anthropic-beta",
                "Content-Type",
                "X-Request-Id",
            )
            if key in request.headers
        }
        headers["Accept-Encoding"] = "identity"

        async def body():
            total = 0
            async for chunk in request.content.iter_chunked(65536):
                total += len(chunk)
                if total > 32 * 1024 * 1024:
                    raise AppError("model_request_too_large", status=413)
                yield chunk

        try:
            async with request.app[session_key].request(
                request.method,
                settings.broker_url + str(request.rel_url),
                data=body() if request.method == "POST" else None,
                headers=headers,
                allow_redirects=False,
            ) as upstream:
                response = web.StreamResponse(
                    status=upstream.status,
                    headers={
                        key: upstream.headers[key]
                        for key in ("Content-Type", "Cache-Control", "X-Request-Id")
                        if key in upstream.headers
                    },
                )
                await response.prepare(request)
                try:
                    async for chunk in upstream.content.iter_chunked(32768):
                        await response.write(chunk)
                    await response.write_eof()
                except (aiohttp.ClientError, ConnectionError, TimeoutError):
                    # Headers are committed. Closing this exact stream cancels the original attempt.
                    response.force_close()
                    raise asyncio.CancelledError() from None
                return response
        except aiohttp.ClientError:
            raise AppError("model_broker_unavailable", status=503) from None

    app.cleanup_ctx.append(lifecycle)
    for path in NATIVE_PATHS:
        app.router.add_post(path, forward)
    app.router.add_get("/v1/models", forward)
