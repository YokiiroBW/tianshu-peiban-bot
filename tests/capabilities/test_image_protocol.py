import unittest
from unittest.mock import AsyncMock
from tianshu.capabilities.images.comfy import ComfyUI
from tianshu.capabilities.images.provider import ImageResult
from tianshu.capabilities.images.workflow import Workflow
from tianshu.capabilities.images.engines import Engines
from tianshu.capabilities.external import ProviderError

class ImageProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_server_generated_handle_is_preserved(self):
        adapter=object.__new__(ComfyUI)
        adapter.json=AsyncMock(return_value={"prompt_id":"actual-provider-id"})
        result=await adapter.submit("requested-id",{"graph":{}})
        self.assertEqual(result.handle,"actual-provider-id")

    async def test_absent_after_cancel_is_still_unknown(self):
        adapter=object.__new__(ComfyUI)
        adapter.json=AsyncMock(return_value={"cancelled":True})
        adapter.poll=AsyncMock(side_effect=[ImageResult("queued"),ImageResult("unknown")])
        result=await adapter.cancel_pending("actual-provider-id",{})
        self.assertEqual(result.state,"unknown")

    def test_reference_identity_is_not_ignored_by_text_only_workflow(self):
        workflow=Workflow.standard("fixture.safetensors")
        intent={"character":"A","outfit":"B","pose":"C","scene":"D","camera":"E","references":[{"media_id":"artifact:fixture","mime_type":"image/png"}]}
        with self.assertRaises(ProviderError):Engines.compile({"engine":"comfyui"},intent,{"graph":workflow.graph,"bindings":workflow.bindings,"outputs":workflow.outputs})
