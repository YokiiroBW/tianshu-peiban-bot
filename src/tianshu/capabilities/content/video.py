"""Temporary Bilibili original acquisition for viewing, without library publication."""
from pathlib import Path
import sys
from tianshu.types import OriginKind
from ..contracts import Fault,digest
from ..media.bilibili import Bilibili
from ..media.engine import Engine

class VideoOriginals:
    def __init__(self,service):self.service=service
    async def acquire(self,action):
        args=action.arguments;service=self.service
        configured=(service.settings.get("perception") or {}).get("video_engine",{})
        engine=service.media.engine if service.media.config else Engine({"python":configured.get("python",sys.executable),"ffmpeg":configured.get("ffmpeg","ffmpeg"),"ffprobe":configured.get("ffprobe","ffprobe"),"timeout_seconds":configured.get("timeout_seconds",600),"max_bytes":configured.get("max_bytes",512*1024*1024)})
        connector=service.media.connector if service.media.config else Bilibili()
        cookie=await service.local(service.media.accounts.cookie,args["account_id"]) if args.get("account_id") else None
        try:
            resolved=await connector.resolve(args["url"],cookie,with_formats=False)
            parts=resolved["video"]["parts"];part=next((x for x in parts if str(x["cid"])==str(args.get("cid"))),None) if args.get("cid") else parts[0] if parts else None
            if part is None:raise Fault("video_part_required",409)
            directory=service.state_directory/"viewing"/digest(action.action_id)
            result=await engine.download({"bvid":resolved["video"]["bvid"],"quality":args.get("quality",{"mode":"best","quality_id":None,"allow_fallback":False})},part,cookie,directory)
            path=Path(result["file"])
            def store():
                with path.open("rb") as source:return service.runtime.files.put_stream(source,media_type="video/mp4" if path.suffix==".mp4" else "video/x-matroska",max_bytes=engine.config["max_bytes"])
            media=await service.local(store)
            source=await service.sources.register_media(action.context,media,locator={"kind":"bilibili_video","url":args["url"],"bvid":resolved["video"]["bvid"],"cid":part["cid"],"part_index":part["index"],"title":resolved["video"].get("title"),"actual_quality":result["actual_quality"],"action_id":action.action_id,"evidence_unit":f"bilibili:{resolved['video']['bvid']}:{part['cid']}:{media.sha256}"},origin_kind=OriginKind.TOOL_RESULT)
            artifact=await service.artifacts.record(action.context,media,source_refs=(source,),metadata={"kind":"video_original","action_id":action.action_id,"duration_seconds":part.get("duration_seconds"),"bvid":resolved["video"]["bvid"],"cid":part["cid"],"parts":parts,"part_index":part["index"],"url":args["url"]})
            await service.local(path.unlink,missing_ok=True)
            return artifact,source
        finally:
            if not service.media.config:await connector.close()
