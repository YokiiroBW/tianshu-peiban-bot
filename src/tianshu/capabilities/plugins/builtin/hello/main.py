"""Minimal installed plugin using the public SDK and host action entry points."""

from tianshu_plugin import Invocation, Plugin, action, command, tool

INPUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 2000}},
}
OUTPUT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["text"],
    "properties": {"text": {"type": "string"}},
}


class HelloPlugin(Plugin):
    @command("/echo", arguments=({"property": "text", "position": 0, "rest": True},))
    @tool("echo", description="原样返回文字，用于检查插件入口是否连通")
    @action(id="echo", title="回声", input_schema=INPUT, output_schema=OUTPUT)
    async def echo(self, call: Invocation, args: dict) -> dict:
        return {"text": args["text"]}
