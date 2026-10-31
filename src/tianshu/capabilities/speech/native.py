"""Fish native HTTP, SSE, reference audio, ASR and MessagePack WebSocket."""
import aiohttp,msgpack
from ..external import ProviderError
from .stream import fish_stream
MODELS=frozenset({"s2-pro","s2.1-pro","s2.1-pro-free","drama-3"})
class NativeFish:
    def __init__(self,token,endpoint="https://api.fish.audio"):self.token,self.endpoint=token,endpoint.rstrip("/")
    def headers(self,model=None):return {"Authorization":"Bearer "+self.token,**({"model":model} if model else {})}
    @staticmethod
    def check(response):
        if response.status in {400,401,402,403,404,413,422,429}:raise ProviderError({402:"speech_quota",429:"speech_rate_limited",401:"speech_auth",403:"voice_access_denied"}.get(response.status,"speech_rejected"),409)
        if response.status not in {200,201}:raise ProviderError("speech_remote_unknown",effect="unknown")
    async def tts(self,body,model,*,mode="http",on_chunk=None):
        if model not in MODELS:raise ProviderError("speech_model_invalid",400)
        if mode=="websocket":return await self.websocket(body,model,on_chunk=on_chunk)
        if mode not in {"http","sse"}:raise ProviderError("speech_transport_invalid",400)
        path="/v1/tts/stream/with-timestamp" if mode=="sse" else "/v1/tts"
        binary=bool(body.get("references"))
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600,connect=15)) as session:
            async with session.post(self.endpoint+path,headers={**self.headers(model),**({"Content-Type":"application/msgpack"} if binary else {})},**({"data":msgpack.packb(body,use_bin_type=True)} if binary else {"json":body}),allow_redirects=False) as response:
                self.check(response)
                if mode=="sse":return await fish_stream(response)
                chunks=[];total=0
                async for chunk in response.content.iter_chunked(65536):
                    total+=len(chunk)
                    if total>64*1024*1024:raise ProviderError("speech_artifact_too_large",413,effect="unknown")
                    chunks.append(chunk)
                    if on_chunk:await on_chunk(chunk)
                if not total:raise ProviderError("speech_empty_result",502,effect="unknown")
                return b"".join(chunks),[]
    async def websocket(self,body,model,*,on_chunk=None):
        chunks=[];complete=False;total=0
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=600)) as session:
            async with session.ws_connect(self.endpoint.replace("https://","wss://").replace("http://","ws://")+"/v1/tts/live",headers=self.headers(model),max_msg_size=4*1024*1024) as ws:
                await ws.send_bytes(msgpack.packb({"event":"start","request":{**body,"text":""}},use_bin_type=True))
                await ws.send_bytes(msgpack.packb({"event":"text","text":body["text"]},use_bin_type=True));await ws.send_bytes(msgpack.packb({"event":"stop"},use_bin_type=True))
                async for message in ws:
                    if message.type!=aiohttp.WSMsgType.BINARY:continue
                    event=msgpack.unpackb(message.data,raw=False)
                    if event.get("event")=="audio":
                        chunk=event["audio"];total+=len(chunk)
                        if total>64*1024*1024:raise ProviderError("speech_artifact_too_large",413,effect="unknown")
                        chunks.append(chunk)
                        if on_chunk:await on_chunk(chunk)
                    elif event.get("event")=="finish":complete=event.get("reason")=="stop";break
                if not complete:raise ProviderError("speech_stream_interrupted",effect="unknown")
        return b"".join(chunks),[]
    async def asr(self,raw,*,language=None,diarize=False):
        body={"audio":raw,"ignore_timestamps":False,"diarize":diarize,"tag_audio_events":True}
        if language:body["language"]=language
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=900)) as session:
            async with session.post(self.endpoint+"/v1/asr",headers={**self.headers("transcribe-1-pro"),"Content-Type":"application/msgpack"},data=msgpack.packb(body,use_bin_type=True),allow_redirects=False) as response:self.check(response);return await response.json()
    async def voices(self,query="",*,owned=False,limit=20,offset=0):
        async with aiohttp.ClientSession() as session:
            async with session.get(self.endpoint+"/model",params={"title":query,"self":"true" if owned else "false","page_size":min(limit,100),"page_number":offset//max(1,limit)+1},headers=self.headers(),allow_redirects=False) as response:self.check(response);return await response.json()
    async def clone(self,title,recordings,*,description=""):
        form=aiohttp.FormData()
        for key,value in {"type":"tts","title":title,"train_mode":"fast","visibility":"private","description":description}.items():form.add_field(key,value)
        for index,(raw,mime,text) in enumerate(recordings):
            form.add_field("voices",raw,filename=f"sample-{index}.wav",content_type=mime)
            if text:form.add_field("texts",text)
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=300)) as session:
            async with session.post(self.endpoint+"/model",data=form,headers=self.headers(),allow_redirects=False) as response:self.check(response);return await response.json()
