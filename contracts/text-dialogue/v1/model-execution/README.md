# Model execution extension

Coordinator-reviewed implementation baseline. Joint runtime validation is pending. Existing packages retain version 1.0.0 and optional receipt fields; publication, package digest regeneration and consumer rollout belong to the coordinator. `existing-schema.patch` states the exact changes against the current coordination schemas. This is one change group, not a third runtime configuration authority or contract-path setting. Embed the closed `$defs` in the existing schemas as proposed.

## Routes and authority

- `GET /internal/v1/model-capabilities`: existing Chat authentication plus X-Request-ID, X-Tianshu-Config-Version, X-Tianshu-Turn-ID, X-Tianshu-Workload. No query/body. Resolve the exact existing binding; no upstream model call. `capability_response`.
- `GET /internal/v1/native-model-capabilities`: existing native registration plus X-Request-ID, X-Tianshu-Native-Config-Version and internal turn header. Existing native authorization and config version chain only. No query/body. Same response shape; config_version means the selected native_config_version.
- `POST /internal/v1/model-requests/{request_id}/cancel`: existing Chat authenticated caller owns the ID. JSON `{}` without query/encoding. `cancel_request` → `cancel_response`.
- `POST /internal/v1/native-model-requests/{request_id}/cancel`: existing full native identity (contract/principal/caller/credential namespace) owns the ID. Same body/result. Routes stay disabled under the existing native_enabled deployment setting.
- Existing receipt GET routes include optional `execution`; existing normalized/native usage fields are unchanged. No raw provider error or message/tool data in execution.

## Native stream and follow-up

Chat remains POST /v1/chat/completions with stream:true, optional stream_options:{include_usage:true}. SSE data contains native choices[].delta.content/reasoning_content/refusal/tool_calls, finish_reason, optional usage. The provider terminal is data:[DONE]. Tool-call ID/name/arguments may arrive in separate fragments; callers assemble by choice and call index before executing. Follow-up keeps assistant.tool_calls and role:tool/tool_call_id/content in messages.

Chat vision remains native content parts type:text/text and type:image_url/image_url:{url,detail?}. Responses remain POST /v1/responses with input_image/image_url/detail and function/custom native tool input/output and typed SSE events. The gateway never fetches image URLs or executes tools. Responses file_id/state references and unsupported input media remain explicitly rejected. No Chat ↔ Responses conversion or fallback.

## Semantics

native:true means the gateway carries the native protocol shape, not that a selected model supports it. verification_source is explicit. Only provider verified capabilities from a real test source become verified; fixture_only stays unverified. Missing declarations are unverified and may be attempted. Explicit unsupported_capabilities fail locally with capability_unsupported/422 before an upstream attempt. Unsupported and verified declarations must not overlap. Provider runtime source accepts the two optional capability arrays; omission preserves the old response.

Execution counters are bounded observations. received_bytes measures consumed upstream bytes; forwarded_bytes means accepted by the downstream transport, not delivery to an end user. event_count counts completed data-bearing SSE events including the native terminal, not keepalive comments. output_observed means upstream output was recognized. finish_reasons is a bounded fixed-vocabulary projection, never arbitrary provider text. Partial streams keep observed usage and usage_complete:false when completeness is not proven; absent usage is null, never zero.

Cancellation is one local task cancellation, no retry or new provider request. requested means cancellation was requested for an active owned task. Upstream outcome is not_started only when upstream_started:false proves this; otherwise unknown until a real terminal. Terminal receipts remain immutable to cancellation. not_active means a durable attempt exists but no current local task; it never means not executed. Unknown ID is existing not_found/404, including IDs owned by another caller. Cancel before forwarding persists a cancelled execution in the same existing receipt row; request_cancelled/409 has execution_state:not_started. Cancellation after possible forwarding closes the connection and leaves provider outcome/charging unknown unless already observed.

On restart, previous running/queued execution snapshots become unknown with error_code:interrupted; existing counts/usage are preserved and the gateway never resumes/repeats the provider call. No new database table or ledger is needed.

Fixed execution error_code values: upstream_http_error, timeout, stream_incomplete, transport_error, cancelled, interrupted, invalid_response, capability_unsupported, or null. Existing public error envelopes gain capability_unsupported and request_cancelled (the latter only for known cancellation before upstream start); unchanged generic errors remain compatible.

Official references checked: https://developers.openai.com/api/docs/guides/streaming-responses ; https://developers.openai.com/api/docs/guides/function-calling ; https://developers.openai.com/api/docs/guides/images-vision . These establish protocol shape, not arbitrary compatible-provider availability. No real model/API request was executed.
