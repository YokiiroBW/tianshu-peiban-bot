# SnowLuma 内部接口

应用模块和插件共用 Channels 的原生能力目录及执行链。当前目录来自 SnowLuma
`f2ea3354f8bbe8672cbb4ae6a2b4e1fbeff44faf` 的公开动作契约：222 个动作、6 个别名。
其中 213 个动作提供执行路径，9 个官方占位/未实现/不支持的动作保留契约并明确拒绝执行。
`unlike_qzone` 仍带有官方待真机核实的说明。以上是框架覆盖，不代表连接实例已逐项实测。

## 应用模块调用

组合根提供 `product.channels.native`，无需自建 HTTP 客户端或读取 OneBot token。
`context` 必须来自现有 Identity 的 `TrustedContext`；不要自行拼接权限或使用全局管理员替代调用者。

| 方法 | 参数 | 返回 |
| --- | --- | --- |
| `describe` | `context, connection_id, *, api_name=None, category=None` | 连接版本、提供者版本和 `apis` 契约目录 |
| `invoke` | `context, connection_id, api_name, params, *, request_id, source_refs=(), origin_kind=None, behavior_refs=(), behavior_purpose=None` | 现有 `Receipt` |
| `get` | `context, action_id` | 原 Action、attempt 和回执视图；只接受 `channels.api` |

```python
api = product.channels.native

# 也可按 category 筛选，省略筛选时返回完整目录。
definition = await api.describe(
    context, connection_id, api_name="get_group_admin_permissions"
)

receipt = await api.invoke(
    context,
    connection_id,
    "get_group_admin_permissions",
    {"group_id": 123456, "user_id": 234567},
    request_id="group-admin-permissions:123456:234567:refresh-1",
    source_refs=source_refs,
)

# 对 pending/unknown 查原 action，保留相同请求身份，不另发一遍。
current = await api.get(context, receipt.action_id)
```

接口返回现有 `Receipt`，包含 action/attempt、execution/effect/delivery、结果和制品。
原生读写的数据位于 `receipt.result["data"]`；聊天投递返回原表达和 `message_ids`；
流式下载产生可授权读取的 `ArtifactRef`，同时放在结果和 `receipt.artifacts` 中。
调用成功、提供者接受和消息实际投递沿用现有回执语义，不把 HTTP 200 当作成功。

`request_id` 是调用方保存的逻辑请求键。相同请求沿用原键；不同上传分块分别使用稳定的键，
例如 `upload:<stream_id>:chunk:<index>`，完成请求使用 `upload:<stream_id>:complete`。
上传分块回执中的 `chunk_received` 只确认该次分块，`file_complete` 才代表整文件合并完成。

连接选择 `provider="snowluma"` 后，已知动作直接可调用，无需逐项填写 `extension_apis`。
已有 `api_support=false` 继续生效。其他 OneBot 提供者继续沿用原有目录/扩展声明规则。

## 查找动作与处理结果

`describe` 的每个动作包含 `name`、`aliases`、`category`、`summary`、`input_schema`、
`output_schema`、`invariants`、`effect`、`stream`、`delivery`、`sensitive`、`implemented`、
`provider_supported`、`verified_at` 和 `reason`。别名调用归入同一个规范动作；
`provider_supported=null` 和 `verified_at=null` 表示当前连接尚无相应事实。
OneBot 内部 quick operation 也在目录中，以 `public=false` 区分，不计入上游 222 个公开动作。

先用 `api_name` 查单个动作的参数，再按 `input_schema` 和 `invariants` 构造 `params`。
完整机器可读目录在 [catalog.json](../../src/tianshu/channels/snowluma/catalog.json)，
上游使用说明见 [SnowLuma API](https://snowluma.github.io/zh/docs/api)。目录版本固定，运行时不依赖外网。

调用方应同时读取 `execution`、`effect`、`delivery` 和 `error_code`，不能只检查是否返回回执。
`pending`/`unknown` 使用 `get` 读取同一个 Action，不能更换 `request_id` 自动重发外部操作。
参数与授权等前置失败可能直接抛出项目 `AppError`；进入执行后的结果沿用现有 Actions 回执。

| 常见错误码 | 含义与处理 |
| --- | --- |
| `channel_api_parameters` | 参数不符合契约；按动作 schema 和约束修正 |
| `provider_api_not_implemented` | 上游占位动作；保留接口，当前不可执行 |
| `provider_api_unsupported` | 连接明确声明不支持该动作 |
| `provider_extension_not_declared` | 非 SnowLuma 连接未声明扩展动作 |
| `channel_request_id_required` | 缺少稳定的逻辑请求键 |

当前不可执行的 9 个动作是 `_get_model_show`、`_set_model_show`、`.get_word_slices`、
`create_collection`、`get_recent_contact`、`nc_get_packet_status`、`set_group_anonymous`、
`set_group_anonymous_ban` 和 `set_restart`。上游更新后，需核对实现再修改分类。

## 插件调用

插件在 manifest 的 required 或 optional capabilities 中声明：

```json
{"id": "channels.native", "major": 1}
```

插件使用现有 SDK capability 机制：

```python
port = call.ctx.capability("channels.native")
definition = await port.call("describe", {
    "connection_id": "my-qq",
    "api_name": "get_group_msg_history",
})
receipt = await port.call("invoke", {
    "connection_id": "my-qq",
    "api_name": "get_group_msg_history",
    "params": {"group_id": 123456, "count": 20},
    "request_id": "read-history-1",
})
current = await port.call("get", {"action_id": receipt["action_id"]})
```

插件调用继承宿主原 invocation 的身份、来源、行为依赖和代际；请求键按父 Action 隔离。
实际执行仍进入 `channels.api`，保留现有 `external.manage` 权限和人类管理操作限制。
接口齐全不自动授予角色管理权限。凭据类结果仅提供受控引用，不把 token/cookies 交给插件。
所有端口操作都需要有效的插件 invocation。

## 执行归属

- 消息、卡片发送、单条/合并转发、文件投递和闪传消息进入现有 Expression → Channels 出口。
- 公告、群权限、相册、空间等原生操作复用同一 Actions/Channels 账本，不另建恢复队列。
- `send_ark_share`、`share_peer` 等实际只获取卡片数据；`upload_forward_msg` 只上传资源。
  它们不会被误记为聊天投递，消息号 0 也不作为可撤回的实际消息号。
- 5 个多帧流式动作支持 HTTP 和 WS；`clean_stream_temp_file` 使用普通请求。
  下载分块直接进入现有 FileStore，只在顺序、字节数及完成帧一致后登记 Artifact。
  文件大小沿用连接 `max_media_bytes`，默认 128 MiB；单个协议帧有界，取消和断线保留未知结果。
- 现有 OneBot 消息、引用、媒体和运维事件入口保持。通知/请求的接收与将其开发成上层业务功能分别处理。

本次没有新增空间运营、群管理面板或自动执行策略；它们可以直接复用上述基础接口。
没有数据库迁移或生产配置改写。

## 更新契约

`src/tianshu/channels/snowluma/catalog.json` 保存输入/输出结构、别名、读写属性、流式标记和来源提交。
它包含公开接口元数据，不包含上游实现。更新时先检出明确的上游提交，再运行：

```powershell
.\.venv\Scripts\python.exe scripts\sync_snowluma_catalog.py C:\path\to\SnowLuma
```

更新目录后同时审阅 `snowluma/catalog.py` 的真实发送、敏感操作、占位状态分类，以及
`parameters.py` 中对应的跨字段约束。运行时不会联网下载目录或自动宣称新版本已验证。

## 本次验证

222 份输入和输出 schema 可解析，223 份官方示例参数通过本地契约检查。
新增定向用例覆盖目录/别名、占位拒绝、参数与权限、统一投递、原请求身份、插件端口、
HTTP/WS 多帧处理、完整文件登记、失败/缺帧和取消。59 个不同定向用例通过；未调用真实 QQ 或模型。
管理页既有 3 项夹具因 execute mock 的参数签名不兼容失败，已在原始代码复现；未修改该夹具。
发布事实见 [上线记录](../handoffs/SNOWLUMA-INTERFACES-20261108.md)。
