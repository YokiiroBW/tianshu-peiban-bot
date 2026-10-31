"""Explicit Edge and OpenAI-compatible speech transports; no fallback route."""
import aiohttp
from ..external import ProviderError
from ..contracts import require
MAX_AUDIO=64*1024*1024
class EdgeSpeech:
    async def tts(self,body,model,**options):
        profile={"voice":body["reference_id"],"speed":body.get("prosody",{}).get("speed",1),"pitch":body.get("pitch",0)}
        text=body["text"]
        import edge_tts

        stream = edge_tts.Communicate(
            text,
            profile["voice"],
            rate=f"{round((profile['speed'] - 1) * 100):+d}%",
            pitch=f"{profile['pitch']:+d}Hz",
            boundary="SentenceBoundary",
            connect_timeout=15,
            receive_timeout=90,
        )
        audio, boundaries = bytearray(), []
        async for event in stream.stream():
            if event["type"] == "audio":
                audio.extend(event["data"])
                require(len(audio) <= MAX_AUDIO, "book_audio_response_invalid", 502)
            elif event["type"] in {"SentenceBoundary", "WordBoundary"}:
                start = event["offset"] / 10000000
                boundaries.append(
                    {
                        "start": start,
                        "end": start + event["duration"] / 10000000,
                        "text": event["text"],
                    }
                )
        return bytes(audio), boundaries


class CompatibleSpeech:
    def __init__(self,endpoint,token):self.endpoint,self.token=endpoint.rstrip("/"),token
    async def tts(self,body,model,**options):
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600,connect=15)) as session:
            async with session.post(self.endpoint+"/audio/speech",headers={"Authorization":"Bearer "+self.token} if self.token else {},json={"model":model,"input":body["text"],"voice":body["reference_id"],"speed":body["prosody"]["speed"],"response_format":"mp3"},allow_redirects=False) as response:
                if response.status in {400,401,402,403,404,413,422,429}:raise ProviderError("speech_rejected",409)
                if response.status!=200:raise ProviderError("speech_remote_unknown",502,effect="unknown")
                raw=bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    raw.extend(chunk)
                    if len(raw)>MAX_AUDIO:raise ProviderError("speech_artifact_too_large",413,effect="unknown")
                if not raw:raise ProviderError("speech_empty_result",502,effect="unknown")
                return bytes(raw),[]
