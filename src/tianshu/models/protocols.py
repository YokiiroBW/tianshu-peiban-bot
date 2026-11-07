"""Native protocol compilation and observation. No lossy cross-protocol relay."""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote, urlsplit

from tianshu.runtime.errors import AppError

from .budgets import effective_output_tokens, merged_parameters, with_output_tokens
from .image_preparation import prepare_inline_images
from .transport import provider_headers, validate_provider_session
from .types import ModelPart, ModelUsage


@dataclass(frozen=True, slots=True)
class NativeCall:
    url: str
    headers: dict[str, str]
    body: dict[str, Any]
    image_preparation: dict[str, Any] | None = None


def chat_video_part(uri):
    """OpenAI-compatible video_url extension, also used by modality probes."""
    return {"type": "video_url", "video_url": {"url": uri}}


def requested_capabilities(request, stream=False):
    required = set(request.required_capabilities)
    if stream:
        required.add("stream")
    if request.tools:
        required.add("tools")
    for message in request.messages:
        for part in message.parts:
            capability = {"image": "vision", "audio": "audio", "video": "video",
                          "tool_call": "tools", "tool_result": "tools"}.get(part.kind)
            if capability:
                required.add(capability)
    native = request.native_payload or {}
    inputs = native.get("input", [])
    for item in inputs if isinstance(inputs, list) else []:
        if not isinstance(item, dict):
            continue
        if item.get("type") in {"function_call", "function_call_output", "computer_call", "web_search_call"}:
            required.add("tools")
        content = item.get("content", [])
        for part in content if isinstance(content, list) else []:
            capability = {"input_image": "vision", "input_audio": "audio", "input_video": "video"}.get(part.get("type"))
            if capability:
                required.add(capability)
    if native.get("tools"):
        required.add("tools")
    # Only native content positions are inspected, never arbitrary tool argument JSON.
    for message in native.get("contents", []):
        for part in message.get("parts", []):
            media = part.get("inlineData", part.get("fileData", {}))
            mime = media.get("mimeType", "")
            if mime.startswith("image/"):
                required.add("vision")
            elif mime.startswith("audio/"):
                required.add("audio")
            elif mime.startswith("video/"):
                required.add("video")
            if "functionCall" in part or "functionResponse" in part:
                required.add("tools")
    for message in native.get("messages", []):
        content = message.get("content", [])
        for part in content if isinstance(content, list) else []:
            if part.get("type") in {"image", "image_url"}:
                required.add("vision")
            if part.get("type") == "input_audio":
                required.add("audio")
            if part.get("type") == "video_url":
                required.add("video")
        if message.get("tool_calls") or message.get("role") == "tool":
            required.add("tools")
    return frozenset(required)


class NativeCompiler:
    def __init__(self, files, secrets):
        self.files, self.secrets = files, secrets
        self.session_generation = None

    def _bytes(self, media):
        if not media or not media.sha256:
            raise AppError("inline_media_not_available", status=422)
        return base64.b64encode(self.files.read_bytes(media)).decode("ascii")

    def _data_uri(self, media):
        if media.uri.startswith(("https://", "http://", "data:")):
            return media.uri
        return f"data:{media.mime_type};base64,{self._bytes(media)}"

    def _gemini_part(self, part):
        value = dict(part.native)
        if part.kind == "text":
            value.setdefault("text", part.text or "")
        elif part.kind in {"image", "audio", "video"}:
            if part.media is None:
                raise AppError("media_reference_required")
            if part.media.sha256:
                value.setdefault("inlineData", {"mimeType": part.media.mime_type,
                                                "data": self._bytes(part.media)})
            else:
                value.setdefault("fileData", {"mimeType": part.media.mime_type,
                                              "fileUri": part.media.uri})
        elif part.kind == "tool_call":
            value.setdefault("functionCall", {"name": part.name, "args": dict(part.arguments or {})})
            if part.call_id:
                value["functionCall"].setdefault("id", part.call_id)
        elif part.kind == "tool_result":
            response = part.result if isinstance(part.result, dict) else {"result": part.result}
            value.setdefault("functionResponse", {"name": part.name, "response": response})
            if part.call_id:
                value["functionResponse"].setdefault("id", part.call_id)
        return value

    def compile(self, plan, request, *, stream, api_key=None):
        prepared, files, facts = prepare_inline_images(self.files, request)
        if facts is None:
            return self._compile(plan, request, stream=stream, api_key=api_key)
        compiler = NativeCompiler(files, self.secrets)
        compiler.session_generation = self.session_generation
        return replace(compiler._compile(plan, prepared, stream=stream, api_key=api_key),
                       image_preparation=facts)

    def _compile(self, plan, request, *, stream, api_key=None):
        parsed = urlsplit(plan.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username:
            raise AppError("invalid_provider_base_url")
        secret = api_key if api_key is not None else self.secrets.resolve(plan.credential_ref)
        headers = {"Accept-Encoding": "identity", "Content-Type": "application/json"}
        headers.update(provider_headers(plan.base_url, request.context, request.intent_id,
            session_scope=request.provider_session_scope, function=request.function,
            generation=self.session_generation))
        base = plan.base_url.rstrip("/")
        body = dict(request.native_payload) if request.native_payload is not None else None
        parameters = merged_parameters(plan, request)
        validate_provider_session(request.provider_session_scope,
            parameters=parameters, native_payload=request.native_payload)
        if plan.protocol == "gemini.generate":
            headers["x-goog-api-key"] = secret
            if body is None:
                contents = []
                for message in request.messages:
                    content = {**message.native, "role": "model" if message.role == "assistant"
                               else "user", "parts": [self._gemini_part(p) for p in message.parts]}
                    contents.append(content)
                body = {"contents": contents,
                        "generationConfig": {"maxOutputTokens": request.max_output_tokens,
                                             **parameters.pop("generationConfig", {})}, **parameters}
                if request.system:
                    body["systemInstruction"] = {"parts": [{"text": request.system}]}
                if request.tools:
                    body["tools"] = [{"functionDeclarations": [
                        {"name": tool.name, "description": tool.description,
                         "parameters": tool.parameters} for tool in request.tools]}]
            body.pop("model", None)
            method = "streamGenerateContent?alt=sse" if stream else "generateContent"
            url = base + "/models/" + quote(plan.model, safe="") + ":" + method
        elif plan.protocol == "openai.chat":
            headers["Authorization"] = "Bearer " + secret
            if body is None:
                messages = []
                if request.system:
                    messages.append({"role": "system", "content": request.system})
                for message in request.messages:
                    content, calls = [], []
                    tool_results = []
                    for part in message.parts:
                        if part.native.get("thought"):
                            continue
                        if part.kind == "text":
                            content.append({"type": "text", "text": part.text or ""})
                        elif part.kind == "image":
                            content.append({"type": "image_url", "image_url":
                                            {"url": self._data_uri(part.media)}})
                        elif part.kind == "audio":
                            formats = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/wave": "wav",
                                       "audio/mpeg": "mp3", "audio/mp3": "mp3"}
                            if part.media is None or part.media.mime_type not in formats:
                                raise AppError("native_audio_requires_transcode", status=422)
                            content.append({"type": "input_audio", "input_audio": {
                                "data": self._bytes(part.media),
                                "format": formats[part.media.mime_type]}})
                        elif part.kind == "tool_call":
                            calls.append({"id": part.call_id, "type": "function", "function":
                                          {"name": part.name, "arguments": json.dumps(part.arguments)}})
                        elif part.kind == "video":
                            content.append(chat_video_part(self._data_uri(part.media)))
                        elif part.kind == "tool_result":
                            tool_results.append({"role": "tool", "tool_call_id": part.call_id,
                                                 "content": json.dumps(part.result, ensure_ascii=False)})
                        else:
                            raise AppError("native_modality_unsupported", status=422)
                    if content or calls:
                        item = {**message.native, "role": message.role, "content": content or None}
                        if calls:
                            item["tool_calls"] = calls
                        messages.append(item)
                    messages.extend(tool_results)
                body = {"messages": messages, "max_tokens": request.max_output_tokens, **parameters}
                if request.tools:
                    body["tools"] = [{"type": "function", "function": {"name": t.name,
                        "description": t.description, "parameters": t.parameters}} for t in request.tools]
            body.update(model=plan.model, stream=stream)
            if stream:
                body["stream_options"] = {**body.get("stream_options", {}), "include_usage": True}
            url = base + "/chat/completions"
        elif plan.protocol == "openai.responses":
            headers["Authorization"] = "Bearer " + secret
            if body is None:
                inputs = []
                for message in request.messages:
                    content = []
                    for part in message.parts:
                        if part.kind == "text":
                            content.append({"type": "output_text" if message.role == "assistant"
                                            else "input_text", "text": part.text or ""})
                        elif part.kind == "image":
                            content.append({"type": "input_image", "image_url": self._data_uri(part.media)})
                        elif part.kind == "tool_call":
                            inputs.append({**part.native, "type": "function_call", "call_id": part.call_id,
                                           "name": part.name, "arguments": json.dumps(part.arguments)})
                        elif part.kind == "tool_result":
                            inputs.append({"type": "function_call_output", "call_id": part.call_id,
                                           "output": json.dumps(part.result, ensure_ascii=False)})
                        else:
                            raise AppError("native_modality_unsupported", status=422)
                    if content:
                        inputs.append({**message.native, "role": message.role, "content": content})
                body = {"input": inputs, "instructions": request.system,
                        "max_output_tokens": request.max_output_tokens, **parameters}
                if request.tools:
                    body["tools"] = [{"type": "function", "name": t.name,
                        "description": t.description, "parameters": t.parameters} for t in request.tools]
            body.update(model=plan.model, stream=stream)
            url = base + "/responses"
        elif plan.protocol == "anthropic.messages":
            headers.update({"x-api-key": secret, "anthropic-version": "2023-06-01"})
            if body is None:
                messages = []
                for message in request.messages:
                    parts = []
                    for part in message.parts:
                        if part.kind == "text":
                            parts.append({"type": "text", "text": part.text or ""})
                        elif part.kind == "image":
                            parts.append({"type": "image", "source": {"type": "base64",
                                "media_type": part.media.mime_type, "data": self._bytes(part.media)}})
                        elif part.kind == "tool_call":
                            parts.append({**part.native, "type": "tool_use", "id": part.call_id,
                                          "name": part.name, "input": part.arguments})
                        elif part.kind == "tool_result":
                            parts.append({"type": "tool_result", "tool_use_id": part.call_id,
                                          "content": json.dumps(part.result, ensure_ascii=False)})
                        else:
                            raise AppError("native_modality_unsupported", status=422)
                    messages.append({"role": "assistant" if message.role == "assistant" else "user",
                                     "content": parts})
                body = {"messages": messages, "max_tokens": request.max_output_tokens,
                        "system": request.system, **parameters}
                if request.tools:
                    body["tools"] = [{"name": t.name, "description": t.description,
                                      "input_schema": t.parameters} for t in request.tools]
            body.update(model=plan.model, stream=stream)
            url = base + "/messages"
        elif plan.protocol in {"openai.embedding", "cohere.rerank"}:
            headers["Authorization"] = "Bearer " + secret
            if body is None:
                texts = [part.text for message in request.messages for part in message.parts
                         if part.kind == "text"]
                body = ({"input": texts, "dimensions": 1024} if plan.protocol == "openai.embedding"
                        else {"query": request.system, "documents": texts})
                body.update(parameters)
            body["model"] = plan.model
            if plan.protocol == "openai.embedding":
                if body.get("dimensions", 1024) != 1024:
                    raise AppError("embedding_dimension_must_be_1024")
                body["dimensions"] = 1024
            url = base + ("/embeddings" if plan.protocol == "openai.embedding" else "/rerank")
        else:
            raise AppError("native_protocol_unsupported", status=422)
        if plan.protocol not in {"openai.embedding", "cohere.rerank"}:
            body = with_output_tokens(plan.protocol, body, effective_output_tokens(plan, request))
        return NativeCall(url, headers, body)


def normalize_usage(protocol, raw, estimated=None):
    if not isinstance(raw, dict):
        return ModelUsage(estimated_input_tokens=estimated)
    def count(value):
        return value if type(value) is int and value >= 0 else None
    if protocol == "gemini.generate":
        input_tokens, output_tokens = count(raw.get("promptTokenCount")), count(raw.get("candidatesTokenCount"))
        if output_tokens is not None and count(raw.get("thoughtsTokenCount")) is not None:
            output_tokens += count(raw["thoughtsTokenCount"])
        cached, written = count(raw.get("cachedContentTokenCount")), None
    elif protocol == "anthropic.messages":
        values = [count(raw.get(key)) for key in ("input_tokens", "cache_read_input_tokens",
                                           "cache_creation_input_tokens")]
        input_tokens = sum(values) if all(value is not None for value in values) else None
        output_tokens = count(raw.get("output_tokens"))
        cached, written = count(raw.get("cache_read_input_tokens")), count(raw.get("cache_creation_input_tokens"))
    else:
        input_tokens = count(raw.get("input_tokens", raw.get("prompt_tokens")))
        output_tokens = count(raw.get("output_tokens", raw.get("completion_tokens")))
        details = raw.get("input_tokens_details", raw.get("prompt_tokens_details")) or {}
        cached, written = count(details.get("cached_tokens")), count(details.get("cache_write_tokens"))
    return ModelUsage(input_tokens, output_tokens, cached, written, estimated, raw, "reported")


class ResponseCollector:
    """Observe native results, preserving all original parts for a subsequent tool round."""

    def __init__(self, protocol):
        from .types import ModelCompletion

        self.protocol = protocol
        self.parts = []
        self.raw_usage = None
        self.native_response = None
        self.completed = False
        self.calls = {}
        self.chat_finished = {}
        self.completion = ModelCompletion()

    def _chat_parts(self, value):
        if not isinstance(value, dict) or not isinstance(value.get("choices"), list):
            raise AppError("invalid_native_chat_response", status=502)
        usage = value.get("usage")
        if usage is not None:
            if not isinstance(usage, dict):
                raise AppError("invalid_native_chat_response", status=502)
            self.raw_usage = usage
        added = []
        for choice in value["choices"]:
            if not isinstance(choice, dict):
                raise AppError("invalid_native_chat_response", status=502)
            choice_index = choice.get("index")
            if type(choice_index) is not int or choice_index < 0:
                raise AppError("invalid_native_chat_response", status=502)
            streaming = "message" not in choice
            message = choice.get("delta") if streaming else choice["message"]
            if not isinstance(message, dict):
                raise AppError("invalid_native_chat_response", status=502)
            if not streaming and message.get("role") != "assistant":
                raise AppError("invalid_native_chat_response", status=502)
            content = message.get("content")
            if content is not None:
                if not isinstance(content, str):
                    raise AppError("invalid_native_chat_response", status=502)
                added.append(ModelPart("text", text=content))
            reasoning = message.get("reasoning_content")
            if reasoning is not None:
                if not isinstance(reasoning, str):
                    raise AppError("invalid_native_chat_response", status=502)
                added.append(ModelPart("text", text="", native={"thought": True, "reasoning_content": reasoning}))
            # These fields are explicitly nullable in ChatCompletionMessage and
            # ChoiceDelta. Required choices/message/delta structures are not.
            calls = message.get("tool_calls")
            if calls is not None:
                if not isinstance(calls, list):
                    raise AppError("invalid_native_chat_response", status=502)
                for call in calls:
                    self._chat_call(choice_index, call, streaming=streaming)
            finish = choice.get("finish_reason")
            if finish is not None and (
                not isinstance(finish, str)
                or finish not in {"stop", "length", "tool_calls", "content_filter", "function_call"}
            ):
                raise AppError("invalid_native_chat_response", status=502)
            self.chat_finished[choice_index] = (
                self.chat_finished.get(choice_index, False) or finish is not None
            )
        # A usage-only trailer has choices=[] and must neither fabricate a
        # finish nor erase a finish observed on earlier chunks.
        self.completed = bool(self.chat_finished) and all(self.chat_finished.values())
        return added

    def _chat_call(self, choice_index, call, *, streaming):
        if not isinstance(call, dict):
            raise AppError("invalid_native_chat_response", status=502)
        identity = call.get("id")
        function = call.get("function")
        kind = call.get("type")
        if (kind is not None and kind != "function") or (not streaming and kind is None):
            raise AppError("invalid_native_chat_response", status=502)
        if streaming:
            index = call.get("index")
            if type(index) is not int or index < 0:
                raise AppError("invalid_native_chat_response", status=502)
        else:
            if not isinstance(identity, str) or not identity or not isinstance(function, dict):
                raise AppError("invalid_native_chat_response", status=502)
            index = identity
        current = self.calls.setdefault(
            (choice_index, index), {"id": None, "name": "", "arguments": ""}
        )
        if identity is not None:
            if (
                not isinstance(identity, str)
                or not identity
                or current["id"] not in {None, identity}
            ):
                raise AppError("invalid_native_chat_response", status=502)
            current["id"] = identity
        if function is None and streaming:
            return
        if not isinstance(function, dict):
            raise AppError("invalid_native_chat_response", status=502)
        for field in ("name", "arguments"):
            fragment = function.get(field)
            if fragment is None and streaming:
                continue
            if not isinstance(fragment, str):
                raise AppError("invalid_native_chat_response", status=502)
            current[field] += fragment

    def feed(self, value):
        from .completion import completion_from_native, merge_completion

        self.native_response = value
        added = []
        if self.protocol == "gemini.generate":
            if "usageMetadata" in value:
                self.raw_usage = value["usageMetadata"]
            for candidate in value.get("candidates", []):
                self.completed |= bool(candidate.get("finishReason"))
                for part in candidate.get("content", {}).get("parts", []):
                    if "text" in part:
                        added.append(ModelPart("text", text="" if part.get("thought") else part["text"],
                                               native=part))
                    elif "functionCall" in part:
                        call = part["functionCall"]
                        added.append(ModelPart("tool_call", call_id=call.get("id"),
                            name=call["name"], arguments=call.get("args", {}), native=part))
        elif self.protocol == "openai.chat":
            added.extend(self._chat_parts(value))
        elif self.protocol == "openai.responses":
            if value.get("type") in {"response.completed", "response.failed", "response.incomplete"}:
                response = value.get("response", {})
                self.native_response = response
                self.raw_usage = response.get("usage")
                # Terminal status settles the physical request. Its content
                # may still be incomplete or unusable.
                self.completed = True
            elif value.get("status") in {"completed", "incomplete", "failed"}:
                # Native non-streaming status settles the physical request even
                # when the provider has no output items to include.
                self.completed = True
                self.raw_usage = value.get("usage")
            if value.get("type") == "response.output_text.delta":
                added.append(ModelPart("text", text=value.get("delta", "")))
            if "output" in value:
                self.raw_usage = value.get("usage")
                for item in value["output"]:
                    if item.get("type") == "function_call":
                        self.calls[("responses", item.get("call_id") or item.get("id"))] = {
                            "id": item.get("call_id"), "name": item.get("name"),
                            "arguments": item.get("arguments", "{}"), "native": item}
                    for content in item.get("content", []):
                        if content.get("type") == "output_text":
                            added.append(ModelPart("text", text=content.get("text", ""), native=content))
            if value.get("type") == "response.output_item.done":
                item = value.get("item", {})
                if item.get("type") == "function_call":
                    # A function item can finish before response.incomplete.
                    # Parse only after the response stop is known, just like Chat.
                    self.calls[("responses", item.get("call_id") or item.get("id"))] = {
                        "id": item.get("call_id"), "name": item.get("name"),
                        "arguments": item.get("arguments", "{}"), "native": item}
        elif self.protocol == "anthropic.messages":
            event_type = value.get("type")
            if event_type == "message_start":
                self.raw_usage = dict(value.get("message", {}).get("usage", {}))
            if "usage" in value:
                self.raw_usage = {**(self.raw_usage or {}), **value["usage"]}
            if event_type == "content_block_delta":
                delta = value.get("delta", {})
                if delta.get("type") == "text_delta":
                    added.append(ModelPart("text", text=delta.get("text", "")))
                elif delta.get("type") == "input_json_delta":
                    self.calls.setdefault(value["index"], {"id": None, "name": "", "arguments": ""})[
                        "arguments"] += delta.get("partial_json", "")
            if event_type == "content_block_start":
                block = value.get("content_block", {})
                if block.get("type") == "tool_use":
                    self.calls[value["index"]] = {"id": block["id"], "name": block["name"], "arguments": ""}
            self.completed |= event_type == "message_stop" or value.get("stop_reason") is not None
            for item in value.get("content", []):
                if item.get("type") == "text":
                    added.append(ModelPart("text", text=item["text"], native=item))
                elif item.get("type") == "tool_use":
                    added.append(ModelPart("tool_call", call_id=item["id"], name=item["name"],
                                           arguments=item["input"], native=item))
        else:
            self.raw_usage = value.get("usage", value.get("meta", {}).get("billed_units"))
            self.completed = True
        # Decode/validate the native structure before reading its stop metadata;
        # malformed required fields must retain their protocol-specific error.
        observed = value.get("response", value) if self.protocol == "openai.responses" else value
        self.completion = merge_completion(self.completion, completion_from_native(self.protocol, observed))
        self.parts.extend(added)
        return added

    def tool_parts(self):
        if self.completion.state == "incomplete":
            # Keep partial native output for diagnosis, never fabricate a tool
            # execution from truncated arguments or turn a known stop into unknown.
            return ()
        if self.protocol in {"openai.chat", "openai.responses"}:
            parts = []
            for value in self.calls.values():
                if not self.completed or not value["id"] or not value["name"]:
                    raise AppError("incomplete_native_tool_call", status=502)
                try:
                    arguments = json.loads(value["arguments"])
                except (ValueError, TypeError):
                    raise AppError("invalid_native_tool_call", status=502) from None
                if not isinstance(arguments, dict):
                    raise AppError("invalid_native_tool_call", status=502)
                parts.append(
                    ModelPart(
                        "tool_call", call_id=value["id"], name=value["name"], arguments=arguments,
                        native=value.get("native", {}),
                    )
                )
            return tuple(parts)
        return tuple(
            ModelPart(
                "tool_call",
                call_id=value["id"],
                name=value["name"],
                arguments=json.loads(value["arguments"] or "{}"),
            )
            for value in self.calls.values()
        )
