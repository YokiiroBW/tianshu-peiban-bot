# 机器人消息结构扩展 v2

现有 `tianshu.bot-adapter/v1` 传输新增 `onebot` 宿主类型。精确会话事件 schema_version=3、观察事件 schema_version=4，增加 `message_context` 并保留昵称与群名片（可为空）。旧 v1/v2 精确事件、v2/v3 观察事件继续读取。

`reply_ids` 是原生消息 ID，限同一连接和房间解析；未知 ID 保持未解析，不能跨房间补查正文。`mentioned_accounts` 是精确 QQ 账号，仅作为消息元数据，不能改变作者、管理员、角色、会话权限。宿主读取 SDK 的原始消息链，避免 SDK 预处理移除引用和 @。多人 @ 与引用不再被当成媒体丢弃。

同一事件仍只由一个责任方回复，持久队列、ACK、发送 unknown 不自动重发的语义保持。OneBot 入口复用 NoneBot SDK；QQ 登录由 OneBot 实现承担。当前入站多媒体仍标记 unsupported，不将部分正文冒充完整消息。
