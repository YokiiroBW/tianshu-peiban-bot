"""NoneBot 2.5.0 plugin using public SDK hooks and original_message preservation."""

from importlib.metadata import version
from pathlib import Path
from fastapi import Request
from fastapi.responses import JSONResponse
from nonebot import get_asgi, get_bots, get_driver, get_plugin_config
from nonebot.adapters import Bot, Event
from nonebot.adapters.onebot.v11 import Bot as OneBotBot, Message, MessageSegment
from nonebot.adapters.onebot.v11.exception import ActionFailed, NetworkError
from nonebot.matcher import Matcher
from nonebot.message import (
    event_preprocessor,
    event_postprocessor,
    run_preprocessor,
    run_postprocessor,
)
from nonebot.typing import T_State
from pydantic import BaseModel
from .host import Host, SDKFailure
from .http import handle

if version("nonebot2") != "2.5.0" or version("nonebot-adapter-onebot") != "2.4.0":
    raise RuntimeError("unsupported_nonebot_sdk_version")


class Config(BaseModel):
    tianshu_bridge_instance_id: str
    tianshu_bridge_connection_id: str
    tianshu_bridge_self_id: str
    tianshu_bridge_token: str
    tianshu_bridge_core_url: str
    tianshu_bridge_data_dir: Path = Path("data/tianshu_channel_bridge")


config = get_plugin_config(Config)
driver = get_driver()
if driver.type != "fastapi":
    raise RuntimeError("tianshu_bridge_requires_fastapi_driver")


async def sdk_call(name, params):
    bot = get_bots().get(config.tianshu_bridge_self_id)
    if not isinstance(bot, OneBotBot):
        raise SDKFailure("sdk_offline", certain=True)
    clean = dict(params)
    for key in ("message", "messages"):
        if key in clean and isinstance(clean[key], list):
            clean[key] = Message(
                [MessageSegment(item["type"], item["data"]) for item in clean[key]]
            )
    try:
        return await bot.call_api(name, **clean)
    except ActionFailed as error:
        raise SDKFailure(
            "sdk_action_failed", certain=True, retcode=error.info.get("retcode")
        ) from None
    except NetworkError:
        raise SDKFailure("sdk_network_unknown") from None


host = Host(
    journal_path=config.tianshu_bridge_data_dir / "receipts.sqlite3",
    instance_id=config.tianshu_bridge_instance_id,
    self_id=config.tianshu_bridge_self_id,
    token=config.tianshu_bridge_token,
    core_url=config.tianshu_bridge_core_url,
    connection_id=config.tianshu_bridge_connection_id,
    sdk_call=sdk_call,
    framework="nonebot2",
    version="2.5.0",
)
driver.on_startup(host.start)
driver.on_shutdown(host.stop)
app = get_asgi()


async def rpc(request: Request, path: str):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 128 * 1024 * 1024:
            return JSONResponse({"error_code": "request_budget"}, status_code=413)
    status, value = await handle(
        host, request.method, path, request.headers.get("Authorization"), bytes(body)
    )
    return JSONResponse(value, status_code=status, headers={"Cache-Control": "no-store"})


app.add_api_route(
    "/tianshu/channel-bridge/v2/{path:path}", rpc, methods=["GET", "POST"], include_in_schema=False
)


@event_preprocessor
async def begin(bot: Bot, event: Event, state: T_State):
    if isinstance(bot, OneBotBot) and str(bot.self_id) == host.self_id:
        state["tianshu_bridge_proof"] = {"started": True, "handlers": [], "failed": False}


@run_preprocessor
async def started(matcher: Matcher, bot: Bot, event: Event, state: T_State):
    proof = state.get("tianshu_bridge_proof")
    if proof is not None:
        proof["handlers"].append(
            {
                "plugin": str(matcher.plugin_name or "external"),
                "priority": matcher.priority,
                "block": matcher.block,
            }
        )


@run_postprocessor
async def finished(
    matcher: Matcher, bot: Bot, event: Event, state: T_State, exception: Exception | None
):
    proof = state.get("tianshu_bridge_proof")
    if proof is not None and exception is not None:
        proof["failed"] = True


@event_postprocessor
async def completed(bot: Bot, event: Event, state: T_State):
    proof = state.get("tianshu_bridge_proof")
    if proof is None or not isinstance(bot, OneBotBot) or str(bot.self_id) != host.self_id:
        return
    raw = event.model_dump(mode="json")
    if str(raw.get("self_id")) != host.self_id:
        return
    original = getattr(event, "original_message", None)
    if original is not None:
        raw["message"] = [{"type": part.type, "data": dict(part.data)} for part in original]
    # NoneBot runs event postprocessors concurrently. An unaccounted callback
    # can send after matcher completion, so it cannot grant TianShu ownership.
    from nonebot import message as dispatch

    other_postprocessors = [
        item for item in dispatch._event_postprocessors if item.call is not completed
    ]
    owner = (
        "external" if proof["handlers"] or proof["failed"] or other_postprocessors else "tianshu"
    )
    await host.publish(
        raw,
        {
            "dispatch_completed": True,
            "reply_owner": owner,
            "handlers": proof["handlers"],
            "failed": proof["failed"],
        },
    )
