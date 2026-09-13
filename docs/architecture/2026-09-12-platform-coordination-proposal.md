# 天枢中枢、记忆与陪伴 BOT：跨项目职责与接口统筹初审

状态：讨论稿，未修改现有架构权威文件、API、数据库、部署或模块锁。读取日期：2026-09-12。范围是功能与架构边界的首轮核对，不是全仓安全扫描、全量代码审计或线上运行验收。

## 1. 结论

建议将三个项目作为同一产品体系统筹需求、身份映射、接口语义、运维和验收，保留独立领域后端、数据库与发布周期。当前最应先完成的是跨项目的领域分工与接口合同，而不是先合并代码或建设通用执行平台。

这与中枢现有冻结方向一致：统一 Console、少量独立产品仓库、独立事实源、正式 API 和薄 Platform Workspace 已经存在。新增工作是把独立陪伴核心及 NoneBot 接入纳入体系，并将天枢记忆 V2 的新要求与旧平台合同对齐。[平台冻结决策](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/docs/frozen-decisions.md)、[仓库边界 ADR](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/docs/adr/0001-repository-boundaries.md)

建议划分为：Control 管理基础设施与受控运维；Memory 管理长期上下文与世界资料；Companion 管理人物生活、对话与领域行动；NoneBot 管理平台接入和消息投递。Gateway 作为既有规划中的模型服务领域参与接口设计，但不能当成已落地依赖。

## 2. 实际读取的项目与基线

| 项目 | 本地位置 | 本次观察 |
| --- | --- | --- |
| 中枢元工作区 | C:/YOKI/Codex/Tianshu | 包含交接资料、主仓库和历史/并行副本；主线以以下三个目录为准 |
| Control | C:/YOKI/Codex/Tianshu/tianshu-control | HEAD 05d503a118d3ba187f3ebd444df019bf73bb90f0；读取时工作树干净；当前 OpenAPI 文件版本 0.11.0 |
| Console | C:/YOKI/Codex/Tianshu/tianshu-console | HEAD 79104702f6d502fdfcf0ec9b2e2466bae1689416；存在 UI 等未提交改动；package.json 为 0.6.0 |
| Platform Workspace | C:/YOKI/Codex/Tianshu/tianshu-platform-workspace | HEAD 67638937ab900f81e7e816563d3113001fdb78f4；存在新的并行工作记录 |
| 天枢记忆/上下文 | C:/YOKI/Codex/Tianshu-AI-Core | HEAD 67cd9ae3d9b88ddcc314cfea940fac1938ac0f2d；V2 与研究/审阅等为现有本地变更；当前 API 继续有效 |
| 陪伴 BOT | C:/YOKI/Codex/tianshu-peiban-bot | 当前主要是研究、架构讨论及图示，尚无本项目陪伴运行核心与 NoneBot 接入实现 |

Module Lock 仍将 Control 固定在 e23549c / OpenAPI 0.10.0，Console 固定在 329703f；AI Core 固定在 dc9fc42 且 integration_enabled=false。这不等于工作树开发有错，但说明“当前开发内容”“锁定集成基线”“已部署版本”必须分开，不能把三者混成一个版本。[模块锁](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/module-lock.yaml)

本次未核验 NAS 实际容器、证书、模型网关或生产流量；上述状态只描述读取到的本地主线及合同。历史测试结果仅作为历史记录，未在本轮重跑。

## 3. 当前能力、目标设计和缺口

| 领域 | 当前能在源码/合同中确认 | 目标或尚缺的部分 |
| --- | --- | --- |
| Control | Owner/会话、节点注册、Agent、Docker/Compose 观测、遥测、R0 self-test、Operation、SSE 与审计等 | 完整告警/事故/Runbook、Memory/BOT 联邦接入及更高风险动作不能据此视为已交付 |
| Console | 构建时 Feature Module、当前 Control 页面与类型化 API 客户端 | Memory 和 Companion 业务模块、跨域登录与权限、统一通知聚合仍需接入 |
| Memory | 事实型 Memory、Knowledge、Project Archive、独立管理身份、令牌及范围约束、既有客户端接口 | 多人事件、PersonalThread、可移植人物认识和 Worldbook 属于 V2 设计；NoneBot 合同尚未发布 |
| Companion | 已确认独立核心、薄 NoneBot 插件、多角色共享世界和虚构生活方向 | 运行服务、角色状态、消息/任务账本、工具与记忆接入尚需实现 |
| Gateway | 平台冻结方案选择独立 Gateway 域、逻辑模型能力与虚拟 Key | 本次主线模块锁和运行模块中未见已启用的 Gateway 集成，不判断其他独立部署是否存在 |

依据：[Control README](C:/YOKI/Codex/Tianshu/tianshu-control/README.md)、[实际 OpenAPI](C:/YOKI/Codex/Tianshu/tianshu-control/contracts/openapi/v1/openapi.yaml)、[Console README](C:/YOKI/Codex/Tianshu/tianshu-console/README.md)、[Memory API](C:/YOKI/Codex/Tianshu-AI-Core/docs/api.md)、[Memory V2](C:/YOKI/Codex/Tianshu-AI-Core/docs/architecture-v2.md)、[BOT 讨论稿](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/2026-09-11-humanlike-companion-architecture.md)。

## 4. 功能归属建议

原则：每种事实有一个最终负责方；其他项目通过正式接口读取、提交意图或维护可重建投影。统一界面和通用字段不改变领域授权。

| 功能 | 最终负责方 | 其他项目如何参与 |
| --- | --- | --- |
| 登录主体、设备与执行身份 | Control 身份域 | 域服务建立明确的主体映射，继续执行自己的资源授权 |
| 服务安装、运行实例、节点状态 | Control | BOT/Memory 报告健康与能力；不自行覆盖设备事实 |
| 平台账号、入站事件和外发消息 | NoneBot 接入 + Companion 消息域 | Control 观察运行状态；Memory 接收获准的会话事件 |
| 被描述人物、外部人物身份与房间 | Memory V2 | 接入端提供可信来源标识；跨平台合并需明确映射 |
| BotActor、长期人格与共享世界正典 | Memory/Worldbook 目标域 | Companion 使用并提出更新；过渡存储必须有唯一写方及迁移方案 |
| 当前活动、情绪、发言选择和角色协调 | Companion | Memory 提供经历和关系证据；Control 提供运行治理，不代替角色决策 |
| 用户愿望、计划、承诺的语义状态 | Memory PersonalThread | Companion 决定是否跟进、何时说；反馈实际使用与完成依据 |
| 生活日程实例、创作、生图和消息任务 | Companion | 工具执行器返回结果；领域账本保留重试与投递事实 |
| 基础设施观测、检查与配置操作 | Control 的已发布能力 | BOT 可作为受权入口，不能直接操作宿主或借用 Owner 凭据 |
| 原始事件、长期经历、画像与纠正遗忘 | Memory | Companion 的缓存、摘要和日记素材接收失效或修订信息 |
| NAS 当前观测值和配置事实 | Control / 对应外部权威系统 | Knowledge 保存带来源、时间与版本的资料，不能把旧记忆当实时监控 |
| 运行经验、手册与来源知识 | Memory Knowledge | Control 发布受治理的复盘/摘要；读取原资源时仍检查来源权限 |
| 项目需求、Decision、Task、Evidence | Memory Project Archive | 中枢项目 Runner 执行受控任务并回写证据，不能把执行账本替代项目语义 |
| 上游模型凭据、路由、配额与用量 | Gateway 目标域；Control 管理其配置 | BOT/Memory 选择逻辑能力及预算，不重复保管所有上游密钥 |
| 系统告警和运维通知 | Control 的通知领域 | Companion 可承接授权消息投递；告警状态不由角色语气决定 |
| 角色主动分享与生活提醒 | Companion | 查询 Memory 当前事项、接收相关事件，独立于运维告警生命周期 |
| 管理 UI | Tianshu Console 的域 Feature Module | 分别消费领域 API；角色管理与设备运维使用不同工作流 |
| 审计与追踪 | 每域保存自身权威记录，Console/Control 聚合允许的元数据 | 通过关联 ID 跳转；不集中复制全部聊天、记忆正文或模型提示词 |
| 备份与恢复 | 每个领域各自负责，Workspace 编排跨域验证 | 保留身份、版本和引用关系；不让旧备份复活已撤回内容 |

## 5. 最需要先解决的六组差异

### 5.1 一次登录不等于现有身份已经打通

Control 已有 Principal、Owner 和短期内部 Audience Token，但代码目前只允许 audience=tianshu-control-domain，且令牌签发不是公开 HTTP 接口。Memory 仍有自己的 AdminUser、BrowserSession、AgentConnection 与 ResourceGrant。不能直接把浏览器 Cookie 或同一个 token 复制过去当成 SSO。[Audience 实现](C:/YOKI/Codex/Tianshu/tianshu-control/src/tianshu_control/modules/identity/audience.py)、[Memory 身份模型](C:/YOKI/Codex/Tianshu-AI-Core/backend/src/tianshu_server/models.py)

应先定义 Control 主体到各领域连接/授权主体的映射、独立服务凭据、精确受众、用途、有效期与撤销传播。目标域检查本地 Grant；Control 的登录成功不能自动扩大某人记忆或某个群的读取范围。管理访问和无人值守服务调用使用不同生命周期。

### 5.2 Principal、Person、BotActor 和 Agent 不是同一个概念

Principal 表示谁有执行或访问权限；Person 表示记忆中描述的谁；BotActor 表示持续的角色身份；平台账号和运行实例表示它从哪里出现、在哪个进程运行。中枢节点 Agent 是设备软件，也不是陪伴角色的大模型 Agent。

一个服务进程可承载多个角色，一个角色可对应多个平台账号；这些映射不能让任意消息中的 actor_id 获得权限。角色背景是“技术高手”不会赋予 NAS 操作权限。昵称不能作为跨平台身份合并依据。

### 5.3 同名 Task、Action、Job、Operation 要保留领域语义

当前 Control OperationHandle 固定为 agent.self_test@1，并携带 node_id；不是可直接容纳生图、聊天、世界活动与个人愿望的通用任务模型。[Operation 实现](C:/YOKI/Codex/Tianshu/tianshu-control/src/tianshu_control/modules/actions/operations.py)

建议保留三类：Control Action/Operation 表示基础设施操作；Companion Task/Delivery 表示创作与消息执行；Memory PersonalThread/Project Task 表示事项及项目语义。统一列表可以投影共同字段，如所属域、状态、更新时间和结果引用，不要求共用一张任务表或一个全局 Worker。

平台冻结文本中的“写工具转 Action”需要在新增 BOT 的 ADR 中明确适用范围。建议将普通对话投递、获准记忆整理、外部账号行为和基础设施操作分别分类。该分类是待采纳设计，不能据此绕开当前 Control/Agent/Helper 仅执行 R0 的合同。

### 5.4 通用事件信封目前并非无条件适配三个领域

Control 的事件信封已有 event_id、event_type、producer、subject、occurred_at、correlation_id 和 causation_id，但 subject.resource_id 限定 UUID，字段也禁止任意增加。Memory 的 new_id 使用带前缀字符串，不能强行裁掉前缀或改成另一种身份。[事件信封](C:/YOKI/Codex/Tianshu/tianshu-control/contracts/events/v1/event-envelope.schema.json)、[Memory ID 生成](C:/YOKI/Codex/Tianshu-AI-Core/src/tianshu_core/domain/models.py)

跨域资源引用应保留 domain、resource_type 和原始 opaque id。采用新信封版本或独立的版本化引用合同，保留现有 Control v1 消费者；不能静默修改所有事件类型。事件通知只携带接收方获准看到的最小信息，正文通过领域 API 再授权读取。

### 5.5 Memory 的旧写入合同与 V2 自动整理需明确迁移

现有平台要求 Agent 默认 Candidate；Memory 当前完成回合接口也只授权提案，且群聊不提取人物事实。V2 则已记录来源开通后普通消息自动整理的产品要求。这不是删除一个审批判断就能解决的问题。

应定义已启用来源、普通自述、推断、敏感跨场景使用和世界正典修改的不同政策，发布新版本接口，并保留旧接口行为。已确认的自动整理方向不必反复重新决定，但其身份、授权、修订和异常处理必须在实现前说明。[当前完成回合合同](C:/YOKI/Codex/Tianshu-AI-Core/docs/api.md)、[V2 自动整理](C:/YOKI/Codex/Tianshu-AI-Core/docs/architecture-v2.md)

### 5.6 开发基线、集成基线和部署状态要分别可见

当前开发 HEAD/OpenAPI 已超出 Module Lock 的部分 pin，Memory 仍被标记为未启用集成。应先确定要联调的精确源码和版本，再更新兼容矩阵；本轮不自动更新锁文件或发布。

此外，统一 Console 的构建时 Feature Module 已有合同，Memory/BOT 页面不能直接通过运行时远程脚本或 iframe 拼接绕过它。可先保留原管理页为过渡入口；最终管理导航、身份和业务流程通过明确模块接入。[Feature Module](C:/YOKI/Codex/Tianshu/tianshu-console/src/feature-module.ts)

## 6. 通信拓扑：管理路径与运行路径分开

管理路径：Console → 受限 BFF/领域路由 → Control、Memory、Companion，以及未来 Gateway 的管理 API。每个目标域独立授权并返回真实业务状态。

运行路径：QQ/TG → NoneBot 接入插件 → Companion → Memory 上下文 / 模型服务 / 工具执行 → 平台投递 → 结果事件。普通聊天不必先经过 Control 的管理 API 或审批流程。

运维路径：Control 观察服务和资源；受权角色可查询观测或申请已发布动作。执行结果经 Control 权威状态确认后，再作为角色可见信息进入对话。不能把角色生成的“已修好”当成 Control 的完成证据。

将 Control 移出普通聊天的必经调用链，可以降低管理控制面维护对聊天的影响。服务调用仍必须尊重凭据有效期、目标域本地权限及撤销规则；不能承诺 Control 离线时无限期沿用已撤权身份。新授权、未知权限及高影响操作应按约定停止或延后。

第一版使用明确目标的 HTTP 请求、持久任务与有限事件通知即可；长任务返回本领域操作引用。Console 沿用 SSE 失效通知后重读权威资源的模式，不能把 SSE 当消息总账。只有出现真实双向实时需求才增加相应 WebSocket；无需先建设全平台消息总线。[SSE ADR](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/docs/adr/0012-cs09-sse-operation-bff.md)

## 7. 首批接口目录草案

以下是逻辑能力名，不是已经发布的 HTTP 路由或可以直接调用的 API。正式路径、请求 schema 与权限标识应由领域实现切片确认。

| 接口族 | 提供方 / 消费方 | 必须明确的合同 |
| --- | --- | --- |
| Identity / Service Binding | Control 与各域 / Console、可信安装 | 主体映射、受众、授权来源、凭据轮换、撤销期限；用户身份与服务身份分开 |
| Runtime Registration / Capabilities | Companion、Memory / Control | 服务实例、协议版本、已实现能力、依赖与健康；不得虚报未来功能 |
| Ingest Conversation Events | Companion 接入 / Memory | 原平台消息身份、观察账号、发言者、角色、房间、引用/撤回、现实范围、幂等与持久 ACK |
| Prepare Context / Search / Read Source | Memory / Companion、受权客户端 | 当前角色、受众、用途、时间、预算；返回范围、来源、修订、状态和降级信息 |
| Correct / Forget / Invalidate | Memory / Console、受权客户端与投影消费者 | 修订/删除依据、影响范围、任务句柄、可重试性及缓存失效，不泄露被撤回正文 |
| PersonalThread / Follow-up Claim | Memory / Companion | 事项状态、版本、期限、提及预约、冲突与撤销；实际投递后才记录已提及 |
| World / Persona / Scene Commit | Memory Worldbook / Companion、Console | 正典版本、世界内事件、参与/知情范围、期待版本与冲突；虚构不污染真实用户记忆 |
| Companion Runtime / Agenda / Delivery | Companion / Console、NoneBot 适配 | 角色启停、领域配置、任务状态、目标账号、消息 ID、失败/unknown、取消与重放 |
| Model Capability / Inference / Usage | Gateway 目标域 / BOT、Memory | 逻辑能力、模型版本、数据外发策略、预算、超时、缓存与安全重试；不传上游秘密给 UI |
| Resource Observation / Supported Action | Control / Console、受权 BOT | 资源引用、观测时间与质量、权限、动作版本、幂等、审批状态、结果引用；当前只用已发布 R0 能力 |
| Notification Delivery | Control 通知域 / Companion 消息域 | 原事件、目标受众、时效、发送归属、去重、失败与替代通道；通知记录不承载完整私聊 |
| Health / Metrics / Operation Reference | 各域 / Control、Console | 存活、就绪、工作能力、积压和新鲜度分别报告；保留领域状态而不是编造统一完成百分比 |
| Audit / Evidence Reference | 各域 / Console、受权运维工具 | 关联 ID、领域修订、允许展示的摘要和证据读取入口；执行审计与对话正文分离 |

每个接口至少记录：用途与责任方、输入输出、身份与授权、状态转换、幂等键作用域、并发/版本、超时/错误/unknown、数据保留、兼容策略和正反例。先冻结影响边界的语义，不必一次冻结所有内部表结构和算法参数。

## 8. 通用规则应统一到什么程度

可统一：术语、引用格式、关联 ID、时间表达、版本规则、能力声明、错误分类、幂等与 unknown 语义、必要的状态投影、授权与审计最低要求。

继续独立：领域数据库、ORM、内部任务类型、画像/世界算法、调度策略、业务迁移、具体缓存和发布周期。共同使用 PostgreSQL 服务不等于共享数据库用户或允许跨域写表。

建议 Platform Workspace 拥有跨域决策、兼容矩阵、消费测试和发布组合；各领域拥有自己的业务 schema。Control 当前拥有的通用事件与平台协议源按既有 ADR 保持，新增跨域合同的责任与版本演进通过新 ADR 明确。不要让三个仓库各自复制一份同名 DTO，也不要为共享几个字段建立依赖全部后端的“大公共包”。

## 9. 用五条真实业务链检验边界

### A. 多角色群聊与长期记忆

NoneBot 观察同一群消息 → Companion 去重、选择角色 → Memory 按发言者/角色/群返回允许上下文 → 生成并发送 → 实际发送 ID 回写 → Memory 后台整理。重连和两个观察账号不能产生重复经历；未被选中回应不等于消息没有被观察。

### B. 普通事项与重复跟进

用户提出愿望 → Memory 建立本次事项 → Companion 安排候选 → 发送前取得提及预约并重查状态 → 发送 → 用户明确完成 → Memory 关闭。Control 可展示任务健康，但不成为事项状态权威，也不为每次普通聊天重新审批。

### C. 运维信息进入陪伴对话

用户在受权私聊询问设备状态 → BOT 用独立服务授权查询 Control → 获得带时间和质量的观测 → 角色解释。若请求不受支持的基础设施写操作，返回当前能力边界，不能退回任意 Shell 或借用浏览器管理员会话。首先验证现有只读观测或受支持 self-test，R1-R3 留在其正式实施阶段。

### D. 世界生活、ComfyUI 与日记

Companion 安排世界内活动与穿搭 → 校验 Worldbook 当前版本 → 异步生图 → 结果返回后复核计划与受众 → 投递并记录 → 日记读取该角色已知且允许的素材。Control 负责运行资源观测；Memory 负责长期世界/经历；角色生活不会反写真实 NAS 配置。

### E. 纠正、删除、重启与恢复

Memory 纠正人物归因或撤回内容 → Companion 的相关缓存和派生素材失效 → Console 收到允许的失效通知并重读 → 重启与恢复后仍应用当前撤回状态。事件通知丢失时可凭版本/水位补查；旧摘要不能恢复已关闭事项或重新公开私人内容。

## 10. 故障、保留和恢复规则

| 故障或变化 | 预期行为 |
| --- | --- |
| Control 管理面暂时不可用 | 已授权的普通聊天可在有效服务凭据与本地政策范围内继续；新授权和需当前审批的动作暂停 |
| Memory 不可用或整理积压 | Companion 显式降级为近期上下文；可持久排队获准事件，不能宣称已形成长期记忆；依赖最新状态的主动跟进暂缓 |
| 模型/图片服务不可用 | 保留任务真实状态与受限重试；不生成虚假完成或把故障伪装成角色剧情 |
| NoneBot/平台掉线 | 核心任务可继续；恢复后按时效、去重和当前受众判断投递，未知结果不能盲目重发 |
| 撤权、成员离开或账号绑定变化 | 目标域先重新授权，再排序和生成；缓存绑定政策版本，定义撤销传播上限 |
| 临时账本清理 | 不删除仍需长期保存的业务证据；临时 Outbox 不成为唯一完成依据 |
| 单域回滚/恢复 | 保留原身份和历史引用，校验其他域观察的修订；无法一致时暴露待协调状态，不假装全平台原子回滚 |

Memory 的历史工程审阅已记录“临时投递记录成为长期验证依据”等问题。本轮没有重做复现；统筹设计应吸取对应的保留边界，实际修复与验收继续由 Memory 工作流负责。[历史工程审阅](C:/YOKI/Codex/Tianshu-AI-Core/docs/reviews/2026-09-08-project-audit.md)

## 11. 推进顺序与交付物

1. **建立共同术语与功能矩阵。** 确认 Principal/Person/BotActor、Action/Task/PersonalThread、世界正典/运行状态等归属。记录现有、计划和不在本阶段的能力。
2. **形成一份平台级 ADR。** 将 Companion 与 NoneBot 纳入现有独立领域体系；明确业务动作分类、身份联邦及模型 Gateway 边界。此文是其输入，不自动替代旧 ADR。
3. **发布最小接口合同。** 先做服务绑定、群聊事件、按角色上下文和实际发送回执；用同一批正反例驱动两侧。Memory V2 缺口与 BOT 接入分别负责，不能各自猜测对方 schema。
4. **跑通一条端到端纵切。** 两角色、一个群及私聊，包含重复事件、权限错误、纠正、Memory 暂不可用和平台掉线。接口/状态用确定性测试，角色自然度另做固定模型试点。
5. **再接统一 Console 与运维读取。** 先补来源开通、身份绑定、角色状态和实际拒绝原因，做到管理可操作；不要仅统一视觉样式。
6. **按需求扩展世界、通知和工具。** 使用兼容矩阵检查各域组合，不把全部远期模块完成当成普通开发前提。发布前才更新经验证的精确模块锁。

建议正式统筹资料最终归入既有 tianshu-platform-workspace：一份能力清单、一份责任矩阵、一份跨域接口目录、一份兼容矩阵和少量真实业务场景验收。各产品仓库保留领域实现、领域合同和自己的测试；没有必要新建第四个“统一中枢内核”来接管一切。

## 12. 尚未完成的决定及本轮产出

仍需在相应实现切片明确：联邦身份的签发/消费与撤销方式；普通领域动作和 Control Action 的精确分类；跨域事件 v1/v2 兼容路径；Gateway 的实际部署与能力版本；Worldbook 过渡存储迁移；最小 NoneBot 接入及 Memory V2 版本。

这些是具体设计和接口工作，不要求对已经确认的“独立软件、统一架构、多角色、自动整理”产品方向反复表决。与既有权威边界冲突的条款应记录 ADR 和迁移，并在实际改变行为前完成对应验收。

本轮完成：定位三个项目、读取主线与架构合同、抽查身份/事件/任务/Console 边界、记录当前缺口并形成候选职责和接口目录。未合并软件、未改业务代码、未修改既有锁文件、未启动生产操作，也未宣称现有模块已经互通。

## 本地依据索引

- [中枢交接与产品边界](C:/YOKI/Codex/Tianshu/00-CODEX-HANDOFF.md)
- [平台冻结决策](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/docs/frozen-decisions.md)
- [仓库边界 ADR 0001](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/docs/adr/0001-repository-boundaries.md)
- [契约来源 ADR 0002](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/docs/adr/0002-contract-source-and-versioning.md)
- [Module Lock](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/module-lock.yaml)
- [Control OpenAPI](C:/YOKI/Codex/Tianshu/tianshu-control/contracts/openapi/v1/openapi.yaml)
- [Control Audience Token](C:/YOKI/Codex/Tianshu/tianshu-control/src/tianshu_control/modules/identity/audience.py)
- [Control OperationHandle](C:/YOKI/Codex/Tianshu/tianshu-control/src/tianshu_control/modules/actions/operations.py)
- [Control 事件信封](C:/YOKI/Codex/Tianshu/tianshu-control/contracts/events/v1/event-envelope.schema.json)
- [Console Feature Module](C:/YOKI/Codex/Tianshu/tianshu-console/src/feature-module.ts)
- [Console 当前界面方向记录](C:/YOKI/Codex/Tianshu/tianshu-platform-workspace/docs/parallel/2026-09-11/console-direction.md)
- [Memory 现有架构](C:/YOKI/Codex/Tianshu-AI-Core/docs/architecture.md)
- [Memory 当前 API](C:/YOKI/Codex/Tianshu-AI-Core/docs/api.md)
- [Memory V2 设计](C:/YOKI/Codex/Tianshu-AI-Core/docs/architecture-v2.md)
- [Memory 身份与连接模型](C:/YOKI/Codex/Tianshu-AI-Core/backend/src/tianshu_server/models.py)
- [Memory ID 生成](C:/YOKI/Codex/Tianshu-AI-Core/src/tianshu_core/domain/models.py)
- [BOT 文献与架构讨论](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/2026-09-11-humanlike-companion-architecture.md)

本地链接不会自动附带对应私有仓库。架构结论基于所列主线、合同与本次抽查，没有逐行审计所有并行副本或访问生产部署。
