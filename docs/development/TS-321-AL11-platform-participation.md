# TS-321：AL11 平台普通群参与与可靠出站

用户已授权继续 AL11。协调方 `AL11_namespace_delegation` 明确平台从 TS318 最终提交 **6d8dbaf29979c210c027f36a8333b5a605f7c3e6** 独立新卡串行演进，不写 TS311/TS318、Memory 或 Companion。依赖 TS319 候选包与 TS318；TS320 适配器独立产品并行，双方通过候选和自己的隔离联合夹具协作，不读对方可变数据库。

## 精确范围

- 新 `services/platform/participation/`：候选、语义决定、领取/登记/投递关联领域；复用原 owner，不建平行队列
- `services/platform/bot_observation.py`
- `services/platform/bot_delivery.py`
- `services/platform/bots.py`
- `services/platform/sources.py`：仅原 prepared source/admission 接点，主体权威/权限逻辑保持
- `services/platform/service.py`、`services/platform/server.py`：薄装配/受信内部端口
- 新 `tests/backend/test_participation*.py`
- 必要原 `tests/backend/test_bot_observation.py`、`test_c4_delivery.py`、`test_bots.py`
- 新 `docs/platform/group-participation.md`、`docs/handoffs/TS-321.md`

不改 auth/origins/actor_memory_authority/actor_capabilities、原用户合同/记忆、网页或锁。必要额外路径先报告父级，不擅自绕入口建转发层。

## 候选与决定

现 decision() 已分 observe/reply_permitted/reply_triggered；普通许可群产生参与候选，observe_only 永远静默，明确 @/关键词/命令走原触发路径。保持 observation-source 的观察本身不建 Core turn/send intent。候选沿真实归档输入、作者 person/entry、actor、群/实例/机器人账号、来源 digest/version/epoch、配置代际和期限生成，不虚构人物、会话、message 或更改 mentioned。

首次旁听可能只有 native/account/source 的 observed 锚，没有人物或 entry；engage 后原来源准备才装真实 entry，registration 至少 registered，原 Core/Memory 与 Platform `_confirm` 之后才 resolved。不能因首次未映射永久排除普通成员，也不能候选阶段提前派发 Core。候选正文须在原真实事件批次可用时绑定原来源保存，不能从不含正文的 sources 元数据杜撰。按 TS319 阶段约束，所有新身份只由原 owner 确认，原作者/native/source/actor 不换。

AL12 尚未接入，默认没有 engage 就不领取、不派发、不创建回复。提供受既有 Companion 服务认证的窄候选读取和决定接点，服务身份由原认证器确认，不用字段自称授权；模型只能表达参与意图，不选地址/凭据/原作者/账号。不要给普通聊天新增必经模型步骤、后台任务或第二条观察队列。

按 TS319 精确 pin 核七种记录，时间/跨 DTO/原行/current 权限由真实 owner 验证；schema 不是证明。语义 engage 也不是发送许可。claim 前、登记/派发前、reply 创建和投递前核当前角色/原作者锚点/群回复/来源/代际/期限/宿主原领取。历史 read proof 不授予事后补发权限，配置变化不复活旧候选。

## 原受理与出站

普通参与调用 TS320 的真实宿主唯一 owner/claim，遵循同参 receipt-first、历史事实/current validity 分开、跨 actor 唯一槽、过期/unknown/已处理不重发。不能直接把 _reply_event 改成所有普通事件必回，也不能拿 bots.event 原接口冒充候选。

沿原 _admit_event 真实事件键、message_id、register_input 和 prepared source/fanout/actor assertion 受理，仅选已决定 actor；不扇出其他角色，不换 event_id 绕过已存在的明确回复。同一实际事件已有受理/登记时按原身份恢复或冲突。

Core 派发前两侧持久登记并核对真实 admission/source，失败或找不到唯一原准备不发。跨来源库、宿主库和 Bot 队列不是一个事务；阶段恢复查询原同参准备/回执，不制造 turn/reply。HTTP unknown 沿原幂等恢复。

BotDelivery response 与旧 send 的两条真实 reply 创建路径在 **原 Bot 队列同一事务** 插入 reply/attempt 并关联 registration/binding；不得插入 pending 后异步补绑定。参与来源须从原 actor/source owner 解析回真实准备，不猜 assertion 字符串。参与来源已标记但登记失效时不能降成普通回复。claim 在排序和限量前排除无效关联，不先 claimed 占整条连接；普通回复原顺序保持。仍只用原 BotDelivery/attempt/send/status/ack，unknown 不换键补发，不另建 sender。

保留原身份 owner 行为：首次未映射的 context/send 仍按旧 503/403，不因 AL11 放宽。老成员有真实已解析身份时，本次回复可能早于 Sources._confirm 到达；binding 从创建起固定 registration/reply/attempt，身份仍 resolved，另以 source_confirmation 等待本次 fanout 真实 admission，confirmed 后才 eligible。不能把旧身份映射当本次来源确认，也不能为等待先 claim 占连接。各 owner 保持本地事务，不在网络等待时持库事务或声称跨库原子提交。

## 集中验证

完整块稳定后审查全 diff，再集中覆盖：许可普通候选和 absent AL12 静默、observe_only、旧明确触发优先、不抢其他插件、跨角色竞争、迟到/过期/重复/配置变更、ACK 与参与独立、真实 admission/source 登记恢复、两条 reply 原子绑定、claim 选择前过滤与正常连接不堵、未知发送/断线/重启不重发。用实际 owner 类/HTTP/RPC/临时库，不用只校验 DTO 的模拟器宣称 CAS 或事务。

与 TS320 做真实平台+共享适配器联合，至少跑允许 engage→原受理/原回复/原发送回执、宿主已处理、未知、claim 回包丢失及实际回复绑定中断恢复。Core/模型/渠道用明确隔离夹具，实际产品链与替身分别标注。AL12 语义消费者和真实 QQ 本批不实现/不实测；相关旧观察/出站/原 AL07 行为必要回归，成功未变不重复。

仅新临时 SQLite/loopback资源；既有解释器只读使用，不连共享 PG、真实模型/QQ/账号，不推送部署/生产迁移。精确提交和 handoff，父级独立审查后本地交付；根合同/任务板/集成由父级单写。
