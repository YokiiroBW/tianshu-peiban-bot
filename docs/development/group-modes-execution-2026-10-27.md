# 逐群四态执行约定（2026-10-27）

2026-10-27发布阶段已收口：已推送部署、完整运行验收通过；真实QQ配置与业务验收由用户延期。最新见[发布记录](group-modes-release-2026-10-27.md)。下文保留原本地阶段约定/结果。

用户要求每个群独立选择停用、仅观察、启用回复、主动。沿已有 BotObservation、MemoryLearning、AL11 及 BotDelivery 演进，不另建策略、整理队列或发送器；旧分支/原改动保留。本批只做隔离本地实现，0 真实模型、0 QQ 发送、无生产写入/推送/部署。其他聊天的凭据维护及生产单写边界继续有效。

## 统一行为与数据归属

Platform 是群策略唯一配置 owner。群以真实 instance_id + BOT self_id + QQ group_id 定位，角色绑定必须真实，人物仍由原 SID/账户映射 owner 解析，不能按文本或显示名归属。

| mode | 新观察/整理 | @ 或配置字面关键词回复 | 普通话题主动参与 |
| --- | --- | --- | --- |
| disabled | 否，暂停待办 | 否 | 否 |
| observe | 是 | 否 | 否 |
| reply | 是 | 是 | 否 |
| active | 是 | 是 | 由兴趣/近期话题语义判断，可保持沉默 |

停用不删除既有聊天/记忆、不把历史来源变成被遗忘；停止新采集，暂停尚未完成的自动整理。重启不补采停用期间消息，不补发旧回复。已经提交的不可撤销事实/发送回执照原账本对账，unknown 不改键重试。模式和 actor 变更在实际受理/提交/发送边界 fresh 检查；A 群变更不能停用 B 群。私聊沿原策略结构执行，并修正 observe=false 的入口旁路。独立 Chat Audit 的历史保存不由本开关删除。

用户后续明确效率优先：主要模式门必须放在入口。SDK 在实际捕获前、Platform 在归档/Core/source 准备前先分流，disabled 不启动这些链路；observe 不进入回复/主动判断，reply 的非触发消息不准备回复，只有 active 普通消息才允许有界语义判断。后台待办先批读当前处理状态，再领取可运行 phase，停用不耗证明/模型/恢复次数。模型槽等 await 之后和最终发送处的轻量复核只关闭执行中切换状态的竞态，不是主要模式门。对切换前已发的模型/写入/发送保原结果和回执，下一未发 phase 停止；不能追溯撤销已提交事实或声称能取消网络 unknown。

此入口优先要求同时覆盖群聊和私聊。私聊仍用原策略结构，但 observe=false/账号停用不能被名单许可绕过；关闭的私聊同样不采集、不启动归档/整理/模型/回复。验证用实际入口调用计数证明下游为 0，不能只检查最终未发送。

## 原 group_policy 的版本化演进

`group_policy` v2 为 `{version:2, default:{mode,actor_id,keywords}, groups:{<真实QQ群号>:{mode,actor_id,keywords}}}`。mode 为上表枚举；keywords 为显式配置的非空字面子串，不用正则、不伪装成实际 mentioned。策略/SDK两端使用相同边界和确定迁移，并做实际联合测试。SDK接收全体新结构但 actor 身份和归属仍由原 owner 确认。默认/覆盖最多 256 群，关键词每项最多 64 字、每组最多 32 项、唯一；无额外全局开关与四态形成冲突。

仅为真实已持久策略兼容旧 observe/mode/list/actor_id：observe=false → disabled；observe_only → observe；whitelist → 默认 observe、名单 reply；blacklist → 默认 reply、名单 observe；绝不自动转 active。旧 actor=null 允许读出并明确“未绑定，仅归档”，用户保存具体群为非 disabled 时必须选真实角色。配置 UI 的默认群规则可沿原 intent 保存，不猜多角色。私聊仍原 policy。旧协议能力不支持新版时明确不兼容，不能静默声称已应用。

## 三个完整功能块

1. Platform/UI：在原 BotObservation 的实际观察、回复连接、历史版本、AL11 capture/current 和高级精确范围入口贯通逐群决策。原账号面板显示逐群模式/角色/关键词及实际宿主能力、pending/失败，真实发现群可编辑，允许输入真实群号预配置；切模式不再清空无关群或名单。群画像页从同一真实群/角色归属列出观察群；沿原 WEB reader/auth/origins 和 canonical conversation 登记读取范围，禁止伪造 scope 或扩到私聊。Chat 未连接时仅对应统计/历史部分明确不可用，已有 Memory 话题/画像仍可显示。配置即接通的链路必须有真实 owner 测试；不能只加 UI label。
2. Companion/SDK：共享适配器及 NoneBot/AstrBot canonical/release 副本同一写者。真实采集、精确范围 capture、实际 send fresh guard 均使用有效逐群策略；关键词与真实 @ 分开。AL12 在既有后台调度/模型路由和槽位内读取 AL11 持久候选，使用真实角色信息与有界群上下文做结构化 engage/silent 判断，复用原 AL11 decision/claim/register/Core/发送回执。无新持久队列、无每条前台必经模型、无模型选择地址/账号/权限。单个候选决定/unknown 同参恢复，当前权限复核，支持冷却/合并保持安静；宿主无普通 claim 能力时如实静默并展示原因，不把提醒冒充参与。
3. 自动整理暂停与联合验收：复用原分段/job/output/receipt；从当前群策略派生“允许新整理”，和历史可读权分开。停用未完成阶段暂停、重启可续原已受理材料；若来源/角色真失效仍按原负态处理。集中验证四态、跨群隔离、关键词/@、降级中途/重启/unknown、普通 active/显式触发不争抢、归属与旧历史仍可读；仅隔离固定模型及临时 SQLite/loopback，无共享 PG。

根协调者单写任务板/约定/联合验证和最终状态。产品工作者只写正式卡列出的范围；完整功能块稳定后集中检查，不重复未改成功检查。不修改 AL11 既有冻结 namespace/pin；任何必要协议扩展先由协调者收敛。

## 当前处理状态窄接点（不变更历史来源证明）

复用 Platform 原受信服务身份，新增 `POST /internal/v2/observation-source/processing`。请求为 `{schema_version:1, request_id, frames:[{instance_id,self_id,conversation_id,actor_id}]}`，1–100 项、无重复，conversation_id 为原观测物理会话 `group:<id>` / `private:<id>`，actor_id 可 null 用于尚未映射材料。不得从 Core conversation_id 或 person_id 猜该值；只能通过原 inbox/材料/分段的持久关系定位。

响应为 `{schema_version:1,request_id,frames:[{frame:<原请求项>,mode,actor_id,processing_enabled,policy_version,code}]}`，保持项序。mode 为 disabled/observe/reply/active（无法定位时 null），actor_id 为当前实际绑定（无则 null），policy_version 为 owner 当前整数 revision（无则 null）。processing_enabled 只有真实唯一账号/物理群或私聊、当前 enabled/read_enabled/已应用策略、角色匹配且允许观察整理时才 true。正常 disabled 的 code 为 null；无法判断/待应用/角色不符等返回该项固定 code，不能把一个群的问题放大为整个批次失败。鉴权/结构错误仍原 HTTP 错误。此响应只允许决定是否开始下一自动 phase，不作为人物或历史来源证明，不写 source tombstone、不授予发送权。Comp 保存暂停提示仅为调度恢复，实际开始工作仍 fresh 检查；保留原已发请求回执。

纯前台已有 QQ 输入只保存原 channel 时，同一 frames 支持严格第二形状 `{channel:<原完整QQchannel四字段>,actor_id}`。Platform 通过自己的真实 reply_connections、catalog、slot/auth.entries 全值匹配还原物理会话，不拆未知 binding/UUID 或猜人物；确证没有受管账号的旧外部连接按原行为保留，code=not_managed。新材料把已证 native frame 随本地 segment 保存，不改变 Memory wire；旧材料无法唯一恢复公开关系时保留暂停并报 mapping_required，不复制 Memory 私有 opaque source_key hash 算法。

## 原 Memory browser 读取新增真实群

Memory 独立小卡仅演进原 `browser._registration`，增加 reader 的显式 `allow_runtime_groups: true`（缺失默认 false，原静态限制不变）。这表示将新增群 CID 的登记权交给已有受信 Platform origin owner，而非浏览器自报：仍核 exact reader account、fresh context.allowed_scope 与请求相等、group audience、scope.person_id 等于 reader 已登记模板中的 operator person；actor 必须原静态 actor 或满足原 allow_runtime_roles + 当前 role_grants。不得顺带扩私聊/人物/角色/跨账号读取。Platform 只为真实观察 catalog+角色+原 Core canonical frame 创建本地 WEB group entry，页面不能传 scope/issuer/账户/上游 URL。所有原 source barrier、当前 origin复验、读事务与删除负态保持，无新授权数据库/registry/合同。

本地联合必须证明设置该明确部署字段后实际 Platform→Memory topics/records 读取正常；未设置保持原拒绝，不能把配置未接称已验收上线。部署配线与 Chat peer/mapping 后续仍由生产协调者在解除维护后统一进行，本批不改真实配置。

## 已收口的薄装配

- 宿主原 observation capability 增加 `group_policy_version:2` 和实际 `ordinary_claim_available`；原 apply 下发同一 v2 policy 及 owner 的 per-group 单调 generation。普通 participation capabilities 可携带真实 group_id，原 AL11 记录 schema/pin 不变。
- AL12 复用原独立 `companion_background` Gateway/selector、ModelSlots 和自动 learning dispatcher phase；function_id=`participation` 沿原功能模型目录继承默认。首位作者尚未解析时，只有该 function + background caller + group selection 允许 person_id=null，route context 是明确 native group key，不冒充 Core conversation/person。
- `POST /internal/v1/bot-bindings/participation-status {}` 仅允许原已鉴权 Platform，返回 `{consumer:"integrated",available:<bool>,reason,last_error}` 的当前纯读状态；无 heartbeat/业务写。Platform 管理页和原 participation status 沿现 Core client 读取，旧 peer 不支持如实 unavailable，不能静态填“已启用”。
- 动态观察群的 Core source channel 不冒充独立 Chat 归档 binding。只有原已登记、通过当前 actor/canonical CID/channel 复核的唯一真实 Chat mapping 才查询原统计/历史，否则对应 part 为 `chat_group_not_registered`，Memory 话题/画像仍可读；不在另一协调者维护 Chat 期间接管其产品或另建统计引擎。
