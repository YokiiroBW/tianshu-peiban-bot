"""Standard plugin lifecycle over the host's fixed comic-file runtime port."""

import json
from pathlib import Path

from tianshu_plugin import Plugin, action

EMPTY = {"type": "object", "additionalProperties": False}
STATUS = json.loads((Path(__file__).parent / "contract.json").read_text(encoding="utf-8"))["status"]


class ComicFilesPlugin(Plugin):
    async def start(self):
        await self.ctx.capability("comics.files").call("start", {})

    async def stop(self, reason):
        await self.ctx.capability("comics.files").call("stop", {})

    @action(
        id="status",
        title="查看漫画文件辅助状态",
        effect="read",
        input_schema=EMPTY,
        output_schema=STATUS,
        required_capabilities=(("comics.files", 1),),
    )
    async def status(self, call, args):
        return await call.ctx.capability("comics.files").call("status", {})
