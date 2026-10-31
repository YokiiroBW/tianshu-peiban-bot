"""AstrBot 4.27.3 aiocqhttp bridge, preserving its native command ownership.

Ordinary messages are observations. The explicit /tianshu handler stops native
reply processing and transfers that one event; native commands keep their owner.
"""

import asyncio
from pathlib import Path
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools
from tianshu_plugin.channel_bridge.host import Host, SDKFailure
from tianshu_plugin.channel_bridge.http import start_listener


class TianshuChannelBridge(Star):
    def __init__(self, context: Context, config: dict):
        super().__init__(context)
        self.context = context
        self.config = config
        self.runner = None
        self.start_task = None
        import astrbot

        if getattr(astrbot, "__version__", None) != "4.27.3":
            raise RuntimeError("unsupported_astrbot_sdk_version")

        async def sdk_call(name, params):
            matches = []
            for platform in context.platform_manager.platform_insts:
                if platform.meta().name != "aiocqhttp":
                    continue
                client = platform.get_client()
                if self.config["self_id"] in {
                    str(key) for key in getattr(client, "_wsr_api_clients", {})
                }:
                    matches.append(client)
            if len(matches) != 1:
                raise SDKFailure("sdk_account_offline", certain=True)
            try:
                return await matches[0].call_action(
                    name, **{**params, "self_id": self.config["self_id"]}
                )
            except Exception as error:
                result = getattr(error, "retcode", None)
                raise SDKFailure(
                    "sdk_action_failed" if result is not None else "sdk_network_unknown",
                    certain=result is not None,
                    retcode=result,
                ) from None

        self.host = Host(
            journal_path=Path(StarTools.get_data_dir("tianshu_channel_bridge"))
            / "receipts.sqlite3",
            instance_id=config["instance_id"],
            self_id=config["self_id"],
            token=config["token"],
            core_url=config["core_url"],
            connection_id=config["connection_id"],
            sdk_call=sdk_call,
            framework="astrbot",
            version="4.27.3",
        )

    async def initialize(self):
        await self.host.start()
        self.runner = await start_listener(
            self.host,
            self.config.get("listen_host", "127.0.0.1"),
            int(self.config.get("listen_port", 18765)),
        )

    async def terminate(self):
        if self.runner:
            await self.runner.cleanup()
        await self.host.stop()

    def raw(self, event):
        message = event.message_obj
        raw = getattr(message, "raw_message", None)
        if not isinstance(raw, dict) or str(raw.get("self_id")) != self.host.self_id:
            return None
        # Keep the actual SDK raw segment array, not a text reconstruction.
        return dict(raw)

    @filter.event_message_type(filter.EventMessageType.ALL, priority=-10000)
    async def observe(self, event: AstrMessageEvent):
        raw = self.raw(event)
        if raw:
            await self.host.publish(
                raw,
                {
                    "dispatch_completed": False,
                    "reply_owner": "none",
                    "reason": "opaque_host_observation",
                },
            )

    @filter.command("tianshu", priority=10000)
    async def tianshu(self, event: AstrMessageEvent):
        raw = self.raw(event)
        if raw is None:
            return
        event.stop_event()
        await self.host.publish(
            raw,
            {"dispatch_completed": True, "reply_owner": "tianshu", "exclusive_command": "tianshu"},
        )

    @filter.after_message_sent()
    async def external_sent(self, event: AstrMessageEvent):
        raw = self.raw(event)
        if raw:
            await self.host.publish(
                raw,
                {
                    "dispatch_completed": True,
                    "reply_owner": "external",
                    "reason": "sdk_after_message_sent",
                },
            )
