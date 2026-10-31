import unittest
from unittest.mock import AsyncMock,patch
from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace
from tianshu.types import Action,Attempt,MediaRef,OriginKind,SourceRef,TrustedContext
from tianshu.capabilities.service import Capabilities
from tianshu.capabilities.contracts import Fault

class MediaAuthority(unittest.IsolatedAsyncioTestCase):
    async def test_upload_uses_authoritative_source_projection(self):
        context=TrustedContext("operator","actor","person","conversation",("private",),("person",),1,OriginKind.HUMAN,frozenset({"*"}))
        source=SourceRef("original",1)
        media=MediaRef("source:original:1","sha256:actual","image/png",3,"actual")
        from tianshu.runtime.serialization import wire
        artifact=SimpleNamespace()
        owner=SimpleNamespace(initialized=True,authority=SimpleNamespace(check=AsyncMock()),sources=SimpleNamespace(read=AsyncMock(return_value={"media":wire(media)})),artifacts=SimpleNamespace(record=AsyncMock(return_value=artifact)))
        owner.ready=lambda:None
        action=Action("action","intent","capabilities.upload","actor",context,{"source":wire(source),"filename":"image.png","media":{"sha256":"attacker-claimed"}},OriginKind.HUMAN)
        with patch("tianshu.capabilities.service.wire",return_value={}):
            await Capabilities._operate(owner,"capabilities","upload",action.arguments,context,action,Attempt("attempt","action"))
        self.assertEqual(owner.artifacts.record.await_args.args[1],media)
        self.assertEqual(owner.artifacts.record.await_args.kwargs["source_refs"],(source,))
