"""A real network query through the host, without receiving plaintext credentials."""

from tianshu_plugin import Plugin, PluginError, action


class QueryPlugin(Plugin):
    @action(
        id="query",
        title="查询接口",
        required_capabilities=(("http.client", 1),),
        input_schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "path": {"type": "string", "title": "接口路径", "default": "/", "maxLength": 1024}
            },
        },
        output_schema={
            "type": "object",
            "required": ["status", "body"],
            "properties": {"status": {"type": "integer"}, "body": {}},
            "additionalProperties": False,
        },
    )
    async def query(self, call, args):
        path = args.get("path", "/")
        if not path.startswith("/") or path.startswith("//"):
            raise PluginError("invalid_input", "Use a service-relative path", effect="none")
        request = {"method": "GET", "url": call.ctx.config["base_url"].rstrip("/") + path}
        if call.ctx.secret_configured.get("api_key"):
            request["auth"] = {"secret_name": "api_key", "scheme": "bearer"}
        response = await call.ctx.capability("http.client").call("request", request)
        return {"status": response["status"], "body": response["body"]}
