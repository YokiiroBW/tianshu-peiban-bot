"""A normal plugin action over the host's explicitly installed discovery provider."""

import json
from pathlib import Path

from tianshu_plugin import Plugin, action

CONTRACT = json.loads((Path(__file__).parent / "contract.json").read_text(encoding="utf-8"))


class DiscoveryPlugin(Plugin):
    @action(
        id="discover",
        title="寻找内容",
        effect="read",
        timeout_seconds=30,
        input_schema=CONTRACT["input"],
        output_schema=CONTRACT["output"],
        required_capabilities=(("content.discovery", 1), ("http.client", 1)),
    )
    async def discover(self, call, args):
        return await call.ctx.capability("content.discovery").call("discover", args)
