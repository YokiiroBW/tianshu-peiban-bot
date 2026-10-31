import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from tianshu.types import ArtifactRef,MediaRef,OriginKind,TrustedContext
from tianshu.runtime.api import error_middleware
from tianshu.runtime.errors import AppError
from tianshu.capabilities.api import register_routes

class MediaHTTP(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        ctx=TrustedContext("p","a","person","c",("scope",),("person",),1,OriginKind.HUMAN,frozenset({"*"}))
        @web.middleware
        async def identity(request,handler):
            request["trusted_context"]=ctx
            return await handler(request)
        media=MediaRef("artifact:id","sha256:hash","video/mp4",10,"hash")
        async def chunks(ref,*,start=0,length=None):yield b"0123456789"[start:start+length if length is not None else None]
        self.authority=SimpleNamespace(check=AsyncMock())
        self.service=SimpleNamespace(authority=self.authority,artifacts=SimpleNamespace(reference=AsyncMock(return_value=ArtifactRef("id",media))),runtime=SimpleNamespace(files=SimpleNamespace(iter_async=chunks)))
        app=web.Application(middlewares=[error_middleware,identity]);register_routes(app,self.service)
        self.client=TestClient(TestServer(app));await self.client.start_server()
    async def asyncTearDown(self):await self.client.close()
    async def test_single_range_and_head_have_actual_lengths(self):
        response=await self.client.get("/api/v1/capabilities/media/artifact:id",headers={"Range":"bytes=2-5"})
        self.assertEqual(response.status,206);self.assertEqual(response.headers["Content-Range"],"bytes 2-5/10")
        self.assertEqual(await response.read(),b"2345")
        head=await self.client.head("/api/v1/capabilities/media/artifact:id")
        self.assertEqual(head.headers["Content-Length"],"10");self.assertEqual(await head.read(),b"")
    async def test_revocation_is_checked_before_conditional_response(self):
        self.authority.check.side_effect=AppError("forbidden",status=403)
        response=await self.client.get("/api/v1/capabilities/media/artifact:id",headers={"If-None-Match":'"hash"'})
        self.assertEqual(response.status,403)
    async def test_unsatisfiable_range_reports_real_size(self):
        response=await self.client.get("/api/v1/capabilities/media/artifact:id",headers={"Range":"bytes=100-200"})
        self.assertEqual(response.status,416)
