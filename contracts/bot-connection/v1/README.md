# Bot connection HTTP v1 · 发布候选

状态：**本轮 QQ 实现配置已发布，通用协议未全面冻结**。源候选文档提交 f79280b125027f89981bc9079fd699d4b2d2a8e1；消费者复核发现出站长度差异，原 AstrBot 910ce8c 不通过完整消费者确认。固定实现：Platform `c5f4d3acc3a906376c5dfe36279ef26b91769b25`；字段依据为 平台产品交接 `projects/tianshu-platform/docs/handoffs/BOT-HTTP-DRAFT.md` 与该提交的 `services/platform/bots.py`、`server.py`、`web_console.py`。此包只归档 HTTP 形状与语义，不更改产品行为。本轮确认限于本文件末尾的 QQ 配置范围；不宣称 TG 或任意长度消息已获消费者支持。

`schemas/bot-connection.json` 是 Draft 2020-12；`examples/documents.json` 全为合成值。`conversation/send` 的请求和回执、`conversation/reply-status` 的请求直接 `$ref` 已发布 `text-dialogue/v1` 的 `conversation#send_request`、`conversation#send_receipt`，不复制或重定义。`common#id/error` 也复用已发布定义。`.invalid` schema ID 仅作本地注册解析，不访问网络。清单按 UTF-8 字节的 CRLF→LF 规范化结果计算 SHA-256。

## 部署边界和空槽位

最小独立 service principal 为 `kind=service`、`service=platform`，最小 actions 集合是 `source.register`、`source.dispatch`、`mapping.prepare`；推荐只授这三项，其 `token_env` 只存在平台服务端。这个 principal 不能拿插件凭据、网页管理员凭据或 Core companion 凭据替代。网页 operator principal 另需 `bot.manage` 才能解锁写操作；Core 的 companion principal 用 `dialogue.send`。以下只是**不含真实目标**的设置片段，需合并到现有设置：

```json
{
  "principals": {
    "bot-gateway": {
      "kind": "service",
      "service": "platform",
      "token_env": "TIANSHU_BOT_GATEWAY_PRIVATE_TOKEN",
      "actions": ["source.register", "source.dispatch", "mapping.prepare"]
    }
  },
  "bot_connections": { "principal": "bot-gateway", "slots": {} }
}
```

`slots: {}` 可通过预检并创建自己的 `<database_path>.bots.sqlite` sidecar。已登录且具有 `bot.manage` 的网页 operator 调用 `POST /api/web/bots/view` 时得到 `available:true, slots:[], connections:[], management:{code:"management_required",unlocked:false}`；设置页显示“尚无可用槽位”的部署说明，可以输入密码解锁，但没有“创建连接”按钮。没有 `bot_connections` 时 view 返回 `available:false` 与 `management_disabled`，页面显示未登记步骤。空槽位不会生成连接 token、启用插件或引入真实账号/群。`/internal/v1/bot/*` 与 Core `reply-status` 仅在 `bot_connections` 存在时注册路由；空槽位下没有可鉴权的插件连接或回复 owner。真实槽位必须由部署方明确指定 `adapter/platform_id/self_id/input_entry_ids/label`，每槽位最多创建一个连接，不能从示例或历史数据臆造。

## 端点与鉴权

所有请求为 JSON `POST`。服务模式需 HTTPS；`local_rehearsal` 仅允许 loopback。内部服务 RPC 需要**恰好一个** `Authorization: Bearer ...`，拒绝 `Origin` 和 `Cookie`。插件 Bearer 是网页创建连接时一次展示的每连接 token，数据库仅存摘要；Core 使用独立 companion service Bearer。浏览器 `/api/web/bots/*` 使用现有同源 Cookie、CSRF、Origin 会话；`unlock` 校验管理员密码并给当前 session 900 秒管理窗口，创建/启停/轮换均需窗口有效且 operator 有 `bot.manage`。插件 token、平台 source service token、Core token 和网页 Cookie 互不通用。

| 端点 | 请求 schema `$defs` | 200 响应 schema `$defs` | 身份 |
| --- | --- | --- | --- |
| `/internal/v1/bot/events` | `event_request` | `event_response` | 启用的插件连接 |
| `/internal/v1/bot/events/status` | `event_status_request` | `event_status_response` | 启用的插件连接 |
| `/internal/v1/bot/heartbeat` | `heartbeat_request` | `heartbeat_response` | 启用的插件连接 |
| `/internal/v1/bot/replies/claim` | `claim_request` | `claim_response` | 启用的插件连接 |
| `/internal/v1/bot/replies/status` | `claim_status_request` | `claim_status_response` | 当前 token；停用后可核对既有 attempt |
| `/internal/v1/bot/replies/ack` | `ack_request` | `ack_response` | 当前 token；停用后可结算既有 attempt |
| `/internal/v1/conversation/send` | `send_request`（已发布 ref） | `send_receipt`（已发布 ref） | Core companion `dialogue.send` |
| `/internal/v1/conversation/reply-status` | `reply_status_request`（**原完整** `send_request` ref） | `reply_status_response` | Core companion `dialogue.send` |
| `/api/web/bots/view` | `web_view_request` | `web_view_response` | 登录网页 session |
| `/api/web/bots/unlock` | `web_unlock_request` | `web_unlock_response` | 有 `bot.manage` 的网页 session + 管理员密码 |
| `/api/web/bots/create` | `web_create_request` | `web_create_response` | 已解锁网页 session |
| `/api/web/bots/enable`, `/disable`, `/rotate` | `web_change_request` | 各自的 `web_enable_response` / `web_disable_response` / `web_rotate_response` | 已解锁网页 session |

插件 event JSON 的 `conversation_id` 是外部 `group:<id>` / `private:<id>`，claim delivery 的 `conversation_id` 同样是外部目标；Core `send_request.conversation_id` 是 Core 自己的 opaque ID。插件必须提交稳定 SDK `event_id`，`revision` 首版固定 1，只接文本。作者账号、bot `self_id`、宿主 `platform_id`、namespace、外部会话及线程必须匹配部署预登记 Sources entry。正文 1–8000 字且不能全为空白；events HTTP body 最多 65536 字节，其他 bot RPC 最多 4096 字节。

同一 `(namespace, 实际 self_id, 外部会话, thread_id, actor_id)` 仅一个启用的回复 owner；Core `binding_id`、插件类型和宿主实例 ID 不参与物理判重。不同实际 `self_id` 可作为独立机器人显式共存，部署方需确认同群同角色双机器人回复确属预期。旧台账若已有冲突启用连接，新事件、claim 和 Core send 均拒绝，直到停用冲突连接。

## 结果、恢复与错误

- 入站以 `(connection_id,event_id,account_id)` 去重。平台先持久记录 unknown intent，再向 Sources/Core dispatch；同键同语义返回首次台账，变更正文等语义冲突报 409。`event_response.state=accepted` 只表示 Core admission，**不表示机器人已发送回复**。`not_started` 表示 Core outcomes 没有 accepted/duplicate；`unknown` 表示 dispatch 结果不明。`events/status` 只读，不触发重发；`found:false` 也不能证明外部事件可安全再发。
- Core `send` 校验原 origin/scope、目标渠道、actor、conversation、deadline、当前启用 owner，并持久入队。首次接收的已发布回执固定 `state=unknown`、`retry_safe=false`、空 `channel_message_ids`。同 reply/语义重放返回当前台账回执；冲突 409。插件 `claim` 原子领取最多 20 条，单 attempt 不再自动重领；空列表是正常结果。claim 60 秒超时保守转或投影为 unknown。插件必须先持久记录自身发送意图，再调用原 SDK。
- 仅 SDK 确认且带真实消息 ID 才可 ACK `sent`；`failed/unknown` 的 ID 列表为空。同 attempt 相同终态回执可重放，冲突终态 409。SDK 自报 unknown 冻结；平台因租约或停用推导的 unknown 可由同 attempt 晚到 ACK 补全真实 sent/failed。停用阻新事件与新 claim，当前 token 仍可只读查询和结算已领取 attempt；轮换后旧 token 对结算也立即失效。
- `/internal/v1/conversation/reply-status` 请求必须是**原完整** `conversation#send_request`，不是 `{reply_id}` 或新的命令。平台重验当前 origin/scope 与原语义；未找到或 pending/claimed 返回 `{receipt:null}`，sent/failed/unknown 返回已发布 `conversation#send_receipt`。Core 30 秒 reconciliation 截止仅限制 Core 等待；平台 60 秒 claim 租约和晚到 SDK ACK 是独立事实。已过 Core 截止的真实 ACK 仍留在平台台账，不触发再次发送。
- 内部错误使用已发布 `common#error`；网页错误使用同五字段形状，网页专用 code 如 `management_required` 不写入已发布的 closed code enum。典型拒绝：400 `invalid_input`，401 `unauthorized`（缺/错连接 token），403 `forbidden` 或 `management_required`（停用/越界/未解锁），404 `not_found`，408 `timeout`（Core send deadline），409 `idempotency_conflict` / `scope_changed`，413 `budget_exceeded`，503 `dependency_unavailable`。实际 HTTP status 与 code 由服务边界返回；不要根据通用错误体的 `execution_state:not_started` 推断事件或 SDK 发送可重试，必须读持久 status，结果仍不明则停发。

## 候选验证范围

本地已用 Draft 2020-12 验证器离线注册已发布 `common.json` 和 `conversation.json`，逐条通过 36 份合成文档，并复算 manifest 全部规范化 SHA-256。另用真实 Platform `validate_settings`、`Platform(settings)`、`WebConsole` 的 `web_route(view)` 校验了空 `slots`：预检通过、sidecar 存在、响应与 `web_view_empty_slots` 实例逐字段相同。发布时仍由根合同验证工具及生产者/消费者复核。BOT-P 自身 12 项测试、N/A 前版联合与网页测试的结果见 平台产品交接 `projects/tianshu-platform/docs/handoffs/BOT-P-2026-09-28.md`。本包不是实际账号、SDK 或 NAS 部署验收。

## 本轮 QQ 消费者配置范围

本轮 N/A 适配器只支持 QQ 纯文本及 null thread_id，部署不得登记 TG 槽位；协议中保留 tg 不表示两适配器实现了它。入站最多 8000 字符。Core 实际出站每段最多 32768 UTF-8 字节（不是字符数），NoneBot 已按此预算验证；原 AstrBot 910ce8c 误用入站 8000 字符上限，返修前阻断消费者确认。出站字节预算是本轮可达实现约束，通用 JSON Schema 尚不表达 UTF-8 字节长度；不能仅以 schema-valid 宣称任意长消息均可发送。超界且尚未调用 SDK 的拒绝应明确 failed，已调用 SDK 而结果不明仍 unknown，均不得重发尝试。真实 QQ/NapCat 对大消息的接受能力仍需用户指定目标后实测。

2026-09-28 返修验收：AstrBot 589c99c（产品主线 ad180ca）已按 32768 UTF-8 字节预算处理出站；总控审查完整补丁，相关 12 项通过，首次 HTTP 测试因协调检出目录深度导致 fixture 路径错误，改用同实现 BOT-P 检出后单项通过。真实本地 HTTP 覆盖 8001 字 sent / 32769 字 failed；中文字节边界、重启重领及私有 CA 验证通过。该 QQ 范围的原长度阻断已关闭；真实 SDK 对长度的接受能力仍待实机对象验收。
