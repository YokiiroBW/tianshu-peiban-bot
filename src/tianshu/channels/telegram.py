"""Telegram Bot API messages, actual media and webhook/long-poll acknowledgement."""

import asyncio
from datetime import UTC, datetime
import hashlib
import json
import aiohttp
from tianshu.identity.types import IncomingEvent
from tianshu.conversation.types import InputPart
from .contracts import ChannelError, TransportResult
from .transports import bounded_json

BOT_API_METHODS = {
    "sendMessage",
    "sendPhoto",
    "sendAnimation",
    "sendAudio",
    "sendVoice",
    "sendVideo",
    "sendVideoNote",
    "sendDocument",
    "sendMediaGroup",
    "sendSticker",
    "sendChatAction",
    "getMe",
    "getFile",
    "getUpdates",
    "getWebhookInfo",
    "setWebhook",
    "deleteWebhook",
    "editMessageText",
    "editMessageCaption",
    "deleteMessage",
    "answerCallbackQuery",
    "getChat",
    "getChatMember",
    "getChatAdministrators",
    "getChatMemberCount",
    "leaveChat",
    "setMessageReaction",
}


def normalize_update(connection, update, *, reply_owner="tianshu"):
    if not isinstance(update.get("update_id"), int):
        raise ChannelError("telegram_update_id", 400)
    message = (
        update.get("message")
        or update.get("edited_message")
        or update.get("channel_post")
        or update.get("edited_channel_post")
    )
    producer = connection["namespace"] + ":" + connection["id"]
    if message is None:
        return None, ()
    chat = message["chat"]
    chat_id = str(chat["id"])
    kind = "private" if chat["type"] == "private" else "group"
    thread = (
        str(message["message_thread_id"]) if message.get("message_thread_id") is not None else None
    )
    target = (
        ("private:" if kind == "private" else "group:")
        + chat_id
        + (":thread:" + thread if thread else "")
    )
    author = message.get("from") or message.get("sender_chat")
    if not author:
        raise ChannelError("telegram_sender_unavailable", 400)
    parts = []
    text = message.get("text", message.get("caption", ""))
    # Telegram entity offsets are UTF-16 code units, not Python character indices.
    utf16 = text.encode("utf-16-le")
    mentions = []
    for entity in message.get("entities", message.get("caption_entities", [])):
        if entity["type"] == "text_mention" and entity.get("user"):
            mentions.append(str(entity["user"]["id"]))
        elif entity["type"] == "mention":
            value = utf16[entity["offset"] * 2 : (entity["offset"] + entity["length"]) * 2].decode(
                "utf-16-le"
            )
            if value.lstrip("@").casefold() == str(connection.get("username", "")).casefold():
                mentions.append(connection["self_external_id"])
    reply = message.get("reply_to_message")
    if reply:
        parts.append(
            InputPart(
                "reply",
                metadata={
                    "channel": "telegram",
                    "message_id": str(reply["message_id"]),
                    "original_author_external_id": str(
                        (reply.get("from") or reply.get("sender_chat") or {}).get("id", "")
                    ),
                },
            )
        )
    if text:
        parts.append(InputPart("text", text))
    for field, kind_name in (
        ("photo", "image"),
        ("animation", "video"),
        ("video", "video"),
        ("video_note", "video"),
        ("audio", "audio"),
        ("voice", "audio"),
        ("document", "file"),
        ("sticker", "reaction"),
    ):
        if field in message:
            media = message[field][-1] if field == "photo" else message[field]
            parts.append(
                InputPart(
                    kind_name,
                    metadata={
                        "channel": "telegram",
                        "file_id": media["file_id"],
                        "file_unique_id": media.get("file_unique_id"),
                        "mime_type": media.get("mime_type"),
                        "filename": media.get("file_name"),
                        "duration": media.get("duration"),
                        "telegram_field": field,
                        "animated": field in {"animation", "sticker"},
                        "original_pending": True,
                    },
                )
            )
    origin = message.get("forward_origin", {})
    if origin:
        parts.append(
            InputPart(
                "forward",
                metadata={"channel": "telegram", "origin": origin, "attribution_only": True},
            )
        )
    event_id = "message:" + chat_id + ":" + str(message["message_id"])
    # Revisions of the same Telegram message amend the same source/episode.
    revision = update["update_id"] + 1
    command = text.split()[0] if text.startswith("/") and text.strip() else None
    event = IncomingEvent(
        producer,
        event_id,
        revision,
        datetime.fromtimestamp(message.get("edit_date", message["date"]), UTC),
        target,
        kind,
        str(author["id"]),
        tuple(
            {"kind": part.kind, "text": part.text, "metadata": dict(part.metadata)}
            for part in parts
        ),
        tuple(mentions),
        reply_to_event_id="message:" + chat_id + ":" + str(reply["message_id"]) if reply else None,
        original_author_external_id=str(
            (origin.get("sender_user") or origin.get("chat") or author)["id"]
        )
        if (origin.get("sender_user") or origin.get("chat") or author).get("id")
        else None,
        command=command,
        reply_owner=reply_owner,
        sender_display_name=" ".join(
            str(author.get(key) or "") for key in ("first_name", "last_name")
        ).strip()[:256]
        or author.get("username"),
        archived=str(author["id"]) == connection["self_external_id"],
    )
    return event, tuple(parts)


class TelegramTransport:
    def __init__(self, connection, token, repository, on_event):
        self.connection, self.token, self.repository, self.on_event = (
            connection,
            token,
            repository,
            on_event,
        )
        self.session = None
        self.task = None
        self.stopped = False

    @property
    def base(self):
        return self.connection.get("endpoint", "https://api.telegram.org").rstrip("/")

    async def start(self):
        self.session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=45), connector=aiohttp.TCPConnector(limit=4)
        )

        async def no_submission():
            pass

        identity = await self.call("startup:getMe", "getMe", {}, no_submission)
        if (
            identity.state != "confirmed"
            or not isinstance(identity.data, dict)
            or str(identity.data.get("id")) != self.connection["self_external_id"]
        ):
            raise ChannelError("channel_account_unverified", 503)
        self.connection["username"] = identity.data.get("username", "")
        if self.connection["transport"] == "telegram_poll":
            self.task = asyncio.create_task(
                self.poll(), name="telegram-poll:" + self.connection["id"]
            )

    async def call(self, identifier, name, params, submitted, *, context=None):
        if name not in BOT_API_METHODS:
            raise ChannelError("telegram_api_not_implemented", 400)
        try:
            from contextlib import AsyncExitStack
            from tianshu.types import MediaRef

            async with AsyncExitStack() as stack:
                uploads = params.get("_uploads", [])
                if uploads:
                    if context is None:
                        raise ChannelError("telegram_media_context_required", 403)
                    form = aiohttp.FormData()
                    for key, value in params.items():
                        if key != "_uploads":
                            form.add_field(
                                key, value if isinstance(value, str) else json.dumps(value)
                            )
                    for upload in uploads:
                        reader = await stack.enter_async_context(
                            self.artifacts.open_media(context, MediaRef(**upload["media"]))
                        )

                        async def chunks(_reader=reader):
                            while True:
                                chunk = await asyncio.to_thread(_reader.read, 1024 * 1024)
                                if not chunk:
                                    break
                                yield chunk

                        form.add_field(
                            upload["field"],
                            aiohttp.payload.AsyncIterablePayload(
                                chunks(), content_type=upload["mime_type"]
                            ),
                            filename=upload["filename"],
                        )
                    request = {"data": form}
                else:
                    request = {"json": params}
                await submitted()
                response = await stack.enter_async_context(
                    self.session.post(
                        self.base + "/bot" + self.token + "/" + name,
                        allow_redirects=False,
                        **request,
                    )
                )
                raw = await bounded_json(response)
                if raw.get("ok") is True:
                    data = raw.get("result")
                    messages = data if isinstance(data, list) else [data]
                    ids = tuple(
                        str(x["message_id"])
                        for x in messages
                        if isinstance(x, dict) and "message_id" in x
                    )
                    return TransportResult(
                        "confirmed", data, 0, message_ids=ids, raw={"data": data, "ok": True}
                    )
                if response.status >= 500:
                    return TransportResult("unknown", error_code="telegram_server_error")
                return TransportResult(
                    "rejected",
                    retcode=raw.get("error_code"),
                    error_code="telegram_api_failed",
                    raw={"data": raw.get("parameters"), "error_code": raw.get("error_code")},
                )
        except asyncio.CancelledError:
            raise
        except (aiohttp.ClientError, OSError, ChannelError):
            return TransportResult("unknown", error_code="telegram_transport_interrupted")

    async def poll(self):
        async with self.repository.database.transaction(read_only=True) as conn:
            row = await (
                await conn.execute(
                    "SELECT update_id FROM channels.telegram_offsets WHERE connection_id=%s",
                    (self.connection["id"],),
                )
            ).fetchone()
        offset = row["update_id"] + 1 if row else 0

        async def noop():
            pass

        while not self.stopped:
            result = await self.call(
                "poll",
                "getUpdates",
                {
                    "offset": offset,
                    "timeout": 30,
                    "allowed_updates": [
                        "message",
                        "edited_message",
                        "channel_post",
                        "edited_channel_post",
                        "callback_query",
                        "my_chat_member",
                        "chat_member",
                        "chat_join_request",
                    ],
                },
                noop,
            )
            if result.state != "confirmed":
                await asyncio.sleep(3)
                continue
            try:
                for update in result.data:
                    # Persist + enqueue before acknowledging the provider offset.
                    await self.on_event(self.connection["id"], update)
                    offset = max(offset, update["update_id"] + 1)
                    async with self.repository.database.transaction(control=True) as conn:
                        await conn.execute(
                            "INSERT INTO channels.telegram_offsets(connection_id,update_id) VALUES(%s,%s) ON CONFLICT(connection_id) DO UPDATE SET update_id=greatest(channels.telegram_offsets.update_id,excluded.update_id)",
                            (self.connection["id"], offset - 1),
                        )
            except asyncio.CancelledError:
                raise
            except Exception as error:
                await self.repository.observe(
                    self.connection["id"],
                    "transport",
                    "poll",
                    {
                        "state": "retrying",
                        "error_code": getattr(error, "code", type(error).__name__),
                    },
                )
                await asyncio.sleep(3)

    async def close(self):
        self.stopped = True
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
        if self.session:
            await self.session.close()
