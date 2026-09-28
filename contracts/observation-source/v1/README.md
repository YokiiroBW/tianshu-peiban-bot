# observation-source/v1 · 1.0.0

合同 1.0.0：已完成双方实现、真实 HTTPS 联合与独立审查，2026-09-29 发布。部署状态以部署验收报告为准。它增加账号级被动观察来源，保持 text-dialogue/v1、source-sync/v1 和 bot-adapter/v1 的原语义。观察事件不得伪造 `conversation.turn_committed`，不得创建角色轮次、候选记忆、模型请求或发送意图。

宿主只从真实在线 SDK 账号捕获新消息，先写持久队列。Platform 管理员显式登记 `(adapter, instance_id, self_id)`，分群聊和私聊设置观察开关及 `observe_only / whitelist / blacklist` 回复模式；默认双开观察、双 `observe_only`。名单只控制回复。旧精确连接保持原范围，绝不自动升级。宿主使用独立观察 RPC 能力协商；没有能力时页面显示不支持。

Platform 只在核对已登记实例、账号、当前观察范围后签发来源。Companion 只接受 Platform 身份，先持久收件，再向 Memory `POST /internal/v2/memory/observations` 转交。Memory 通过独立的 Platform `POST /internal/v2/observation-source/verify` 查询当前来源，核对账号、会话、作者、原生事件、内容摘要、范围版本和观察许可后写入独立 ledger。历史读取关闭后以递增的 `archive_epoch` 撤销旧档案；单纯暂停观察仍允许此前接受的来源完成归档。Memory 返回持久回执后才可标 `archived`；在此之前必须显示 `pending_memory` 或失败原因。Companion 已持久收件即可 ACK 宿主；断线后由 Companion 重试 Memory，异参重放拒绝。

读取由 Platform 管理界面发起，经认证的 `POST /internal/v2/observations/query` 到 Companion，再由配置登记的 Companion caller 调用 Memory `POST /internal/v2/memory/observations/query`。Memory 对来源逐条复核当前 Platform 授权，按同一账号、会话和 `archive_epoch` 分页；Platform 在跨服务调用返回前再次核对历史读取权限。界面不显示未授权正文或把展示名称当同一身份。群成员、会话和消息来源键均包含实例、机器人账号、会话类型及 ID；禁止跨账号或跨群合并。非文本 `content_state=unsupported` 只记元数据，不下载附件，不声称媒体正文已归档。

回复策略的许可与触发独立：仅观察永不回复；白名单空为全禁；黑名单空为全可触发；群聊默认还须直接 @机器人，私聊不需 @。旧观察事件在模式切换后不补发。观察钩子自身不阻断；只有宿主持久入队、当前策略许可、且 Platform 活跃轮询租约有效时，回复接管钩子才阻断其他宿主回复者。接管决策在宿主阻断钩子单独持久记录；轮询先到时暂候决策，超时按未接管处理。已确认接管的原生消息重放仍归天枢，未确认接管的旧消息不得日后补发。租约失效后新事件不认领。宿主无法瞬时获知远端故障，租约有效期内的故障可能使已认领事件暂时无回复。天枢不回复并不保证其他宿主插件不回复。

契约文件：[schema.json](schema.json)、[examples.json](examples.json)、[negative-examples.json](negative-examples.json)、[capabilities.md](capabilities.md)、[manifest.json](manifest.json)。对应实现：Platform 04cbe8a、Companion 6adf480、Memory 10214b2。协调者 Python 3.12.14 联合复验 2/2 通过。运行时采用各端显式验证器，本 schema 为跨产品发布合同。
