import base64,json
import unittest
from types import SimpleNamespace
from tianshu.capabilities.speech.stream import fish_stream
from tianshu.capabilities.speech.mcp import FishMCP
from tianshu.capabilities.external import ProviderError

class SpeechProtocol(unittest.IsolatedAsyncioTestCase):
    async def test_cumulative_alignment_replaces_prior_chunk_snapshot(self):
        events=[{"chunk_seq":0,"audio_base64":base64.b64encode(b"a").decode(),"chunk_audio_offset_sec":2,"alignment":{"segments":[{"text":"你","start":0,"end":0.5}]}},
                {"chunk_seq":0,"audio_base64":base64.b64encode(b"b").decode(),"chunk_audio_offset_sec":2,"alignment":{"segments":[{"text":"你好","start":0,"end":1}]}}]
        async def lines():
            for event in events:
                yield ("data: "+json.dumps(event,ensure_ascii=False)+"\n").encode()
                yield b"\n"
        audio,alignment=await fish_stream(SimpleNamespace(content=lines()))
        self.assertEqual(audio,b"ab")
        self.assertEqual(alignment,[{"text":"你好","start":2.0,"end":3.0}])

    def test_connected_schema_required_parameters_are_not_silently_omitted(self):
        adapter=FishMCP("fixture")
        adapter.tools={"create_voice_clone":{"inputSchema":{"type":"object","properties":{"name":{"type":"string"},"audio_urls":{"type":"array"}},"required":["name","audio_urls"]}}}
        with self.assertRaises(ProviderError):adapter.compile("create_voice_clone",{"name":"voice"})
        arguments={"name":"voice",**adapter.recordings("create_voice_clone",[{"url":"https://fixture.invalid/actual.wav"}])}
        self.assertEqual(adapter.compile("create_voice_clone",arguments)["audio_urls"],["https://fixture.invalid/actual.wav"])
