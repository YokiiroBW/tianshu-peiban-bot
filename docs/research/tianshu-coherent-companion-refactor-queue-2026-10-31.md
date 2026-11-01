# 天枢完整改造队列

日期：2026-10-31；状态更新：2026-11-01。**30 块源码及前端已集成，CR19 集中验收通过；CR20 正式新版、两 QQ 接入、作者配置迁移和旧业务退役完成。NAS 原 SSH 公钥已按用户授权恢复、限定来源并完成实际只读采集核验，完整队列已收口。** 这是[完整改造说明](tianshu-coherent-companion-system-2026-10-31.md)的施工队列，覆盖关系与好感、记忆画像、人格情绪、日程陪伴、模型分流、并发、网页论坛 B 站、实际视频理解、角色手记、日记、风格成长、语音听说、图片理解、表情管理、OneBot、自拍多引擎、前端和代码质量。

保留 CR01–CR20 主编号、原六个专项块，并将前端拆为 CR18A/B/C/D 四块，共 **30 个施工块**；CR18 保留为前端最终接线。编号不占用历史 TS/PC 任务号，执行状态记录在本批独立执行清单，不覆盖旧任务板的混合修改。每块交付业务行为及其实际调用方，删除被替代实现；模块写完但没有消费者不算完成。内部可以分提交施工，对外只切换一次完整核心系统。执行边界见[本批执行说明](../development/coherent-refactor-execution-2026-10-31.md)。

最新补充已并入原卡：**自拍包含被动请求、日程约定和角色自主创作/分享；语音优先完整接入 Fish Audio，利用 Plus 的官方 MCP，并实现原生 API 的增强能力。** 两种 Fish 传输共用一个语音领域，照片生成与分享共用既有动作/表达链，不新增平行队列。

免费 API 与定制音色在 CR13B 明确支持。插件扩展作为完整交付的一部分，宿主/SDK、安装配置、命令/工具/日程、页面与启停分别落实到相关后端卡及 CR18D；不再开第二套插件工程。具体边界见[总方案第 13.2 节](tianshu-coherent-companion-system-2026-10-31.md)。

## 1 施工原则和确定选择

1. **以最新有效行为为输入。** CR01 开始时重新读取活动修复窗口和具体源提交，保留 R58–R67 等有效修复。不能拿研究开始时的旧 HEAD 覆盖后续成果。
2. **目标是一套产品代码。** 以当前根仓库作为最终产品入口，后端归入 `src/tianshu/`，前端归入 `web/`，部署归入 `deploy/`。这些目录已在本批隔离集成树建立，完整装配、集中验收和正式发布已完成；原工作区未提交修改保留。旧自有产品树在迁入、调用切换和必要验证后退出交付树；独立 Chat Audit/AssetLibrary 仍是外部产品。
3. **原生 PostgreSQL。** 新建天枢专属 database，采用 pgvector、既有 psycopg 与单一 Procrastinate。最终初装基线不携带旧 SQLite/PG 兼容和旧数据迁移链；未来新版本仍正常演进 schema。
4. **三个进程角色。** 交互、Model Broker、后台 worker；媒体/CPU 子进程按需。按实际共享 quota_group 管理模型并发/RPM/TPM，各进程有数据库和 I/O 预算。资源不可抢占时明确剩余等待，不能宣称换模型名字就完全隔离。
5. **同一业务规则只有一个归属。** 内部使用类型化调用；持久事件用于独立后续处理和重建，不把所有函数改成事件总线。共享装配、数据库演进和发布由一个集成人写入。
6. **清理与功能一起完成。** 每卡列出的旧分支在对应消费者迁入后删除。最终不保留 `legacy`、双读、双写、旧算法回退或“以后再删”的安装入口。保留历史提交与尚未交接的他人工作不等于保留旧运行逻辑。
7. **完整实现后集中验证。** 用户本轮明确要求先完成全队列实现和集成，再由协调者统一执行必要验收、测试及构建；开发期间只做源码/接口/diff检查并编写有价值的用例，不逐块运行测试、typecheck或构建矩阵。CR19 集中执行后，只对发现并修复的问题补受影响验证，成功且未变的结果复用。

## 2 队列总览

下表是依赖拓扑顺序。实现者可先按已发布类型与接口并行编写独立模块，依赖未集成的卡不得标为交付完成；前端可先做页面结构和数据客户端，生产路径不使用 mock 冒充后端。并发受当前三个子代理执行槽及文件独占边界约束。

| 编号 | 完整施工块 | 直接依赖 | 规则归属 |
|---|---|---|---|
| CR01 | 产品装配与能力保留清单 | 无 | composition root |
| CR18A | 前端应用壳 语义路由 与完整页面迁移 | CR01 | web 基础 |
| CR02 | PG 原生存储与持久任务基础 | CR01 | runtime 与各领域 repository |
| CR03 | 模型功能路由 资源 Broker 与取消 | CR02 | models |
| CR04 | 可信接入 事件身份 与对话入口 | CR02 | identity / channels |
| CR05 | 来源 覆盖 修订 与遗忘传播 | CR04 | sources |
| CR06 | 事实 持续事项 与增量抽取 | CR03 CR05 | memory |
| CR07 | 有界检索 与统一上下文组装 | CR06 | memory / conversation.context |
| CR08 | 画像 关系 与分场景认识 | CR06 | memory.people |
| CR09 | 人格 情绪 与角色手记类型 | CR07 CR08 | companion |
| CR10 | 目标 日程 承诺 与联系时机 | CR03 CR06 CR08 CR09 | life |
| CR11 | 能力目录 插件宿主 动作执行 与结果回流 | CR03 CR05 | capabilities / capabilities.plugins |
| CR04A | OneBot 完整消息 事件 API 与传输 | CR04 CR11 | channels.onebot |
| CR12 | 对话编排 与前台完整闭环 | CR07 CR08 CR09 CR10 CR11 CR04A | conversation |
| CR13B | Fish 语音 MCP 原生 API 与稳定音色 | CR03 CR12 | capabilities.speech |
| CR13A | 图片 语音理解 与辅助感知 | CR03 CR05 CR12 CR13B | capabilities.perception |
| CR13C | 表情包管理 与语义选择 | CR07 CR12 CR13A | capabilities.reactions |
| CR13 | 多模态表达 与真实任务进度 | CR12 CR13A CR13B CR13C | companion.expression / channels |
| CR14 | 网页 论坛 与 B 站内容探索 | CR07 CR10 CR11 CR13A | capabilities.content / life |
| CR15 | 实际视频理解 与持续观看 | CR14 | capabilities.perception / memory |
| CR16 | 日记 后台整理 兴趣 与风格成长 | CR10 CR12 CR15 | memory / companion / life |
| CR17A | 主被动自拍 场景快照 与提示词规划 | CR09 CR10 CR11 CR13A | life / capabilities.images |
| CR17B | ComfyUI NAI GPT Image 三引擎 | CR17A CR13 | capabilities.images |
| CR17 | 现有媒体 创作 设备 项目能力接线 | CR11 CR13 CR16 CR17B | 各能力模块 |
| CR18B | 角色 记忆 关系 生活 与成长界面 | CR18A CR08 CR10 CR12 CR16 | web 角色与认识 |
| CR18C | 内容 多模态 阅读 与自拍界面 | CR18A CR13 CR17 | web 内容与媒体 |
| CR18D | 模型 插件 连接 与系统界面 | CR18A CR03 CR04A CR11 CR17 | web 设置与运维 |
| CR18 | 前端完整接线 与交互收口 | CR18B CR18C CR18D | web 集成 |
| CR19 | 必要联合验证 与体验对照 | CR03 CR05 CR07 CR10 CR12 CR15 CR16 CR17 CR18 | 集成 |
| CR20 | 空数据安装 切换 与旧实现退役 | CR19 | 部署集成人 |

CR11 可在 CR05 后与认识层独立推进；CR08 可与 CR07 独立推进。迁移文件、依赖锁、路由入口和根装配保持单写。卡号顺序不意味着每一项都必须等待前面全部卡结束。

## 3 关键接口与唯一写入权

以下是目标业务接口，不要求给每行建一个类或网络服务。正式类型由对应施工块维护一份定义，提示示例和外部 schema 从它派生。

| 输入或结果 | 核心内容 | 写入权与消费者 |
|---|---|---|
| `TrustedContext` | actor/person/conversation/audience、可信 origin、access_epoch | identity 创建；所有读取与动作使用 |
| `InputEvent` | producer/event_id/revision、origin_kind、occurred_at、received_at、原件引用 | channels/sources 登记；去重、抽取、状态更新使用 |
| `EvidenceSpan` | 原件版本、文本/时空范围、受众、必要或替代支持关系 | sources 校验；memory 与动作提交复核 |
| `ClaimRevision` / `MatterView` | 谁说/关于谁、事实或观点、条件与有效期、关联与冲突 | memory；people、context、life 使用 |
| `ContextPack` | 入选语义单元、依赖、材料预算、缺失原因、版本快照 | ContextAssembler；对话/规划/抽取入口使用 |
| `ModelRequest` / `ModelAttempt` | function、service_class、quota_group、模态、deadline、真实路线与 usage | models；所有模型调用走同一 Broker |
| `Action` / `Receipt` | intent/action/attempt、实际状态、外部效果、结果/产物/覆盖 | capabilities；life、表达、memory 订阅结果 |
| `ConsumptionReceipt` | 内容版本、时间/页码、实际模态、已处理段、抽样与遗漏 | content/perception；经历、阅读进度、后续探索使用 |
| `ReflectionProposal` / `AffectUpdate` | 关联经历、主观解释、目标与范围、简短理由、事件修订 | companion 提案与状态；memory 存有来源的手记 |
| `ExpressionPlan` | expression_id、公开片段、格式/媒体意图、真实目标引用 | companion 表达；channels 只投递公开片段 |
| `MessageEnvelope` / `MediaRef` | 原生有序消息段、引用/@、附件原件/MIME/时长/范围 | channels 登记；感知、对话与投递共同使用 |
| `VoiceProfile` / `SpeechIntent` | 稳定音色与本次台词/语气分别配置 | companion 定表达，speech 产音频 |
| `FishConnection` / `AudioArtifact` | MCP/native 连接引用、team/workspace、账单域与能力、实际音频及对齐 | speech 管连接和产物，models 管实际容量，channels 仅发送文件 |
| `ReactionIntent` / `ReactionAsset` | 交流用途、候选素材、图包范围和索引版本 | reactions；表达按实际回应选图 |
| `SceneSnapshot` / `ImageIntent` / `EngineProfile` | 日程/天气/穿搭快照、图像语义、引擎能力与原生参数 | life 提供状态，images 编译执行与产物回流 |
| `PhotoIntent` | 用户/日程/自主来源、创作或分享用途、目标、窗口、去重键与产物引用 | 作为通用 image Action 输入保存；life 定时机，images 生成，expression 统一投递 |

**事项索引不抢执行权。** “用户报告维修完成”由 memory 记录；“天枢执行重启成功”由 capabilities 回执决定；“明天提醒是否取消”由 life 决定。其他模块只读其投影或引用，不能分别维护一套当前状态。

**模型与任务不是同一把锁。** 作业队列记录何时再做、谁持有作业；Broker 管实际模型调用；会话轮次、CPU、媒体、数据库与渠道各有相应容量。模型 permit 在每次模型请求结束后释放，工具等待期间不占模型配额。

## 4 各施工块的完整交付

### CR01 产品装配与能力保留清单

**输入与范围：** [CURRENT](../development/CURRENT.md)、[原功能清单](../product/requirements-v2.json)、[本次源码核对](tianshu-whale-practice-review-2026-10-31.md)、各自有产品当前 manifest 与部署装配。部署基础参考证据索引中已定位的 BusinessHost 提交，不能因协调根缺少某目录误判能力不存在。

**改造：** 确定 `src/tianshu/{identity,sources,memory,companion,conversation,life,capabilities,models,runtime}` 的实际依赖方向，`people` 放在 memory 内。整理单产品启动入口和各工具链锁文件；列出既有能力的入口、真实 handler、实际状态、配置、外部依赖和目标归属。将正在修复的行为与本轮新需求一起纳入，杜绝只复制旧发布快照。

插件清单包含当前原生包/实例、SDK 版本、命令/工具/页面、依赖和实际宿主；已有 content-discovery、comic-files 与 AstrBot/NoneBot 接入均有去向。核心依赖锁合并不抹掉第三方插件按包版本隔离的依赖环境；三个核心进程之外保留 PluginHost 监督的按需插件子进程。

**消费者与退出：** 交互进程、Broker、worker 和 web 使用同一配置/版本清单。旧独立产品安装、内部 HTTP 信封和跨事件循环桥在相应模块迁入时退出；这一卡不提前删尚未接线的实现。

**完成条件：** 空白开发配置可以启动三个进程角色；已实现能力没有无故变成 unavailable；U01–U100 都有处置归属。新目录不是一堆调用旧 Core 的转发层。只验证装配与依赖，不提前跑整套业务测试。

### CR18A 前端应用壳 语义路由 与完整页面迁移

**复用入口：** Platform 的 `apps/web/src`、公共资源、package/lock、Vite 与已有页面；同时核对 R67 实际前端源 `911a1dc`，以本批来源清单的确切 SHA 为准。

**改造：** 迁入根 `web/`，统一会话/首次设置/登录退出、CSRF、API 客户端、错误与未知状态、请求取消、领域类型和角色选择。数字段路由改为语义路由，导航/地址/组件由同一注册表管理。保留已认可主题、材质、移动导航、书架、小屋和有效页面；工作台连接真实近况/事项/约定/资源。既有未挂载或未实现入口明确登记，不冒充已有功能。

**消费者与删除：** CR18B/C/D 共用这些基础。删除重复 session/fetch、旧 Core/跨产品信封类型、`legacy_roles` 拼接和数字段路由分支；迁入的页面不能只是 iframe 包住旧站。前端依赖锁归本卡唯一写者。

**完成条件：** 有效页面/素材都有新入口；统一客户端保留实际错误、revision、游标和缺失语义；写入保留 client_request_id，unknown 不盲重试。开发时保留原有关键用例并写必要新增用例，执行留到 CR19。

### CR02 PG 原生存储与持久任务基础

**复用入口：** 现有存储公共包、[插件调度](../../projects/tianshu-platform/services/platform/plugins/scheduler.py)、各领域 store；队列采用[Procrastinate](https://procrastinate.readthedocs.io/en/stable/)。

**改造：** 建天枢专属 PG database 与各领域 schema，统一事务、连接池、绝对 deadline、错误类别和时间类型；支持业务变化与派工同事务提交。任务按主体/事项合并、保存游标、租约和下次唤醒；通知只提示“有新工作”，恢复靠持久状态。未来提醒不能共用会阻塞全部后继的全角色作业锁。

插件包/实例元数据、受管 KV 和配置修订迁入 PG；插件 action 复用通用执行账本，周期任务转入同一个调度器。保留所需的恢复/检查点语义，不保留 `.plugins.sqlite` 双读和单独周期轮询。包缓存、凭据锚点与产物文件使用各自已有存储归属。

**消费者与删除：** CR03 起所有模块用原生 repository；删除迁入部分的 SQLite DDL、qmark/SQL 翻译、JSON blob 全表模拟与重复自写队列。保留领域意图、action 和回执，不把它们并成队列表。

**完成条件：** 事务回滚不留下孤儿任务；同一变更只派工一次；worker 重启能恢复可重入计算；已取消任务不再执行，unknown 外部动作不被自动重放。用隔离 PG 覆盖这些边界，无需 SQLite 双后端矩阵。

### CR03 模型功能路由 资源 Broker 与取消

**复用入口：** [model_functions.py](../../projects/tianshu-platform/services/platform/model_functions.py)、[model_slots.py](../../projects/tianshu-companion/src/tianshu_companion/model_slots.py)、[Gateway scheduler](../../projects/tianshu-model-gateway/src/tianshu_gateway/scheduler.py) 及原生协议 adapter。

**改造：** 分开 function、control/interactive/due/elastic 时效、真实 quota_group；接通 chat、plan、extract、embedding、rerank、tools、code、search、vision、ASR/TTS 与 video.observe 的实际消费者。Broker 独占供应商容量与 attempt 状态；前台预留、后台最大在途与公平推进同时存在。数据库/HTTP 池及 CPU 容量分预算；请求取消贯穿网络、SQL 与分段任务。

等待队列有上限并按角色/请求公平分配；重启复原在途与未知 attempt，不能因内存计数清零立刻重复占用或重发。Broker 保持唯一活跃容量所有者，监督器负责恢复；control 状态/取消路径不排在耗时模型生成之后。

**消费者与删除：** 对话、抽取、生活、视觉和插件统一调用 Broker。删除重复全局模型队列、槽内做准备/等待工具、隐式借前台专用池、每个调用方猜模型默认值。保留会话两轮次等独立业务限额。模型结果回原角色表达，不变成几个互相抢话的角色。

Gemini 3.8 作为用户默认主理解路线，必须打通原生多模态请求与工具续轮；当前 Companion 固定 Chat Completions 的组装入口随之替换。增加 speech、image.plan 与辅助感知功能绑定；ComfyUI 等图像引擎仍由 images 执行，不通过 chat/completions 伪装调用。实际共享 GPU/供应商额度使用同一资源归属。

Fish 原生 TTS/ASR 按实际账户共享并发，多个 API Key 不拆成多个独立池；Plus MCP 的网页额度与原生 API 余额分开，MCP 容量不套用 API 限额。短语音预留、长任务分片、最终合成文本的 UTF-8 字节计量、取消与未知费用保留进入同一 Broker。订阅额度不足不隐式切换付费 API；MCP 参数按连接时实际 schema 校验。

**完成条件：** 同账户不同模型共享限额正确；后台持续运行时前台有可观测预留；后台不永久饿死；工具等待释放模型 permit；取消停止实际工作或保留原 attempt 未知状态。一个无工具/模态能力的模型不会被选来假装执行；部分输出后不自动换模型拼接回答。本地辅助不可用时核心仍正常。

### CR04 可信接入 事件身份 与对话入口

**复用入口：** [bot_message_context.py](../../projects/tianshu-platform/services/platform/bot_message_context.py)、[参与准入](../../projects/tianshu-platform/services/platform/participation/admission.py)、现有 QQ/TG 接入与人物绑定。

**改造：** 输入形成 TrustedContext 与 InputEvent；保留作者、提及、引用、当前人和关于谁的区别。按 producer/event_id/revision 处理新消息、编辑和重放。明确 reply/observe/ignore 模式后再获取必要附件；命令快速路径与正常聊天选定一个回复负责人。保留会话共享两轮次和防抖合并语义。

**消费者与删除：** 来源登记、conversation、旁听和状态识别消费同一输入。删除迁入部分按正文名字猜身份、多个入口各自去重与重新解释群策略的重复规则；不添加十分钟内一律忽略的时间闸门。

**完成条件：** 同一事件重复送达只处理一次，合法编辑仍生效；归档不会重新执行旧命令；引用中的“我”不会变成当前用户；忽略附件不触发下载；允许旁听不会自动取得发送权。

### CR05 来源 覆盖 修订 与遗忘传播

**复用入口：** [source_authority.py](../../projects/tianshu-memory/src/tianshu_memory/source_authority.py)、现有来源注册、actor_memory 来源协议与真实阅读覆盖。

**改造：** 原始来源、精确片段、版本、受众与原始/派生类型进入一套来源领域；保留必要支持组、替代支持组和失效原因。读取和动作提交按实际依赖复核，复用 R58–R61 的短占用、去重与批量经验。遗忘传播到 claim、手记、画像、风格、缓存和未执行动作；设置新的 intake epoch 与最小删除标记。

**消费者与删除：** memory、context、life、作品和能力读取统一使用；移除各投影独立全历史扫描和全库写屏障，不能移除真实来源保护。外部 Chat Audit/AssetLibrary 仍通过适配访问。

**完成条件：** 部分必要支持缺失会失效；独立替代支持仍可成立；撤回后后台任务不能写回旧结果；私聊派生手记不出现在群材料中；遗忘内容不从日记或旧导入重新学回来。

### CR06 事实 持续事项 与增量抽取

**复用入口：** [memory_events.py](../../projects/tianshu-memory/src/tianshu_memory/memory_events.py)、[retention.py](../../projects/tianshu-memory/src/tianshu_memory/retention.py)、现有 memory_learning 与 actor_memory。

**改造：** 用一份正式类型表达 speaker/subject、转述与本人确认、条件、否定、时间、状态槽和冲突关系；形成 ClaimRevision、Episode 与 Matter。明确提醒/改期/取消当轮落地，普通认识在后台增量整理。稳定偏好保留规则与实际 schema 一致；后来收到旧材料不覆盖已经生效的新安排。

**消费者与删除：** CR07 回答、CR08 画像和 CR10 计划正式消费 MatterView，不能只给管理页看。删除旧孤立 event_projection、不可达 negations 保留分支和另一份执行状态；memory 不自行确认 life 的承诺完成。

**完成条件：** “甲转述乙考试 → 乙私聊改期 → 群内询问”有正确主体、当前态与范围；不同人矛盾陈述可并存；明确长期偏好不因时间/未提及变短期；普通闲聊不强制建 matter。验证真实输入到实际回答/计划消费者，不能只测投影纯函数。

### CR07 有界检索 与统一上下文组装

**复用入口：** [read_v2.py](../../projects/tianshu-memory/src/tianshu_memory/memory_context/read_v2.py)、[merge.py](../../projects/tianshu-memory/src/tianshu_memory/memory_context/merge.py)、[persistent_retrieval.py](../../projects/tianshu-memory/src/tianshu_memory/persistent_retrieval.py)、[dialogue_prompt.py](../../projects/tianshu-companion/src/tianshu_companion/dialogue_prompt.py)。

**改造：** 结构化当前事项先行，授权范围内 SQL 候选检索后只加载需要的正文；固定一个 1024 维 embedding profile，中文分词/GIN、专名、时间和向量互补。MMR 缓存最大相似度并到 k 停止，最终融合不让 rerank 抹去有效期与主体约束。所有模型材料走 ContextAssembler，保留完整人格、条件与证据单元，计入工具及多模态成本。

**消费者与删除：** chat、plan、extract 和研究使用同一个组装契约，各任务有不同选材配置；删除全授权域正文预加载、向量 JSON/BYTEA 双读、全候选 MMR、多个地方无预算追加提示。完整列表查询不由 top-k 冒充。

**完成条件：** 两个人和多个事项在有限预算下不串人；代词短句能接上文；稳定偏好与当前取消状态可用；无关日记不能挤掉当前问题。小授权集合用精确检索，大集合验证过滤后的候选足量；1024 维质量用本任务样例确认，不宣称降维无损。记录读取行数、时延与实际输入预算。

### CR08 画像 关系 与分场景认识

**复用入口：** [profiles.py](../../projects/tianshu-memory/src/tianshu_memory/profiles.py)、Memory 现有角色—人物关系代码、原关系冻结合同与管理页。

**改造：** 分开明确设置、共同经历、当前相处认识、用户临时处境与群主题；支持本人纠正、共享范围、关系冻结/解冻。好感重整为关系背景，数值仅可作为有用途的内部特征。关系、联系回应、短期情绪和权限独立。access/content/expression/plan 的版本含义分别实现。

**消费者与删除：** 对话称呼和背景、life 联系策略、界面读取同一投影。删除自动涨跌分、八阶段解锁、签到/离线惩罚及旧页面配置；不保留旧分数供其他模块继续决策。

**完成条件：** 离线不掉关系；冻结不阻止事实纠正和短期感受，也不在解冻时补算；该人物的自动相处风格不能绕过冻结；普通称呼变化不阻断下一条合法私聊；关系亲密不扩大工具权或私人资料范围；群主题不等于全体成员兴趣。

### CR09 人格 情绪 与角色手记类型

**复用入口：** [personas.py](../../projects/tianshu-companion/src/tianshu_companion/personas.py)、[life_affect.py](../../projects/tianshu-companion/src/tianshu_companion/life_affect.py)、[actor_memory.py](../../projects/tianshu-companion/src/tianshu_companion/actor_memory.py)。依据[人格与情绪详案](tianshu-model-concurrency-persona-design-2026-10-31.md)。

**改造：** 完整作者原文加情境解释，稳定人格与后天认识分开。事件评价包含目标相关性、归因、控制感等；连续心境、混合感受和少量带滞回的调节模式服务表达/可选活动。定义短 ReflectionProposal，明确它是角色主观感受，保存依据和范围，复用 Memory 类型。收到澄清时修订同一事件，不重复加分。

**消费者与删除：** conversation、life 选择与 CR16 整理都消费同一状态；删除单轴 mood 作为唯一判断和重复的独立情绪源。无强制每轮第二次情绪模型，无正文正则私密通道，无每秒情绪 tick。

**完成条件：** 同一事件重放不叠加；同一角色对不同情境有有因差异；情绪能恢复与重评；私密感受不通过全局 mood/风格泄露；有效来话和明确承诺不被情绪吞掉。状态数值是工程表示，不当心理诊断或校准置信度。

### CR10 目标 日程 承诺 与联系时机

**复用入口：** [life_goals.py](../../projects/tianshu-companion/src/tianshu_companion/life_goals.py)、[life_activities.py](../../projects/tianshu-companion/src/tianshu_companion/life_activities.py)、[life_goal_context.py](../../projects/tianshu-companion/src/tianshu_companion/life_goal_context.py)、[life_salience.py](../../projects/tianshu-companion/src/tianshu_companion/life_salience.py) 与主动反馈。

**改造：** 将持续目标、事项承诺、当日安排和下一步活动连接；明确 waiting 原因与时间/事件唤醒。AttentionPolicy 合并分享、关心和群参与的联系预算，消费用户临时可用状态、明确免打扰、当前对话与新信息。保持允许沉默和随机选择，但普通主动联系必须有内容理由。actor 生命周期由 worker 与持久状态管理。

自主自拍从活动、穿搭、创作与分享动机形成 PhotoIntent；支持 create_only、share_candidate 和 requested_delivery。生成前检查创作预算/分享可能性，成品后再检查当前联系时机；不把定时器当强制发送，不要求角色必须等待用户开口。日程明确约定和普通随兴分享按不同的时效处理；暂停联系不自动禁止角色自有创作。

**消费者与删除：** 对话登记承诺，worker 执行候选，CR11 负责动作，界面显示真实等待。删除重复持久 tick/cron、无理由 paused-null 和因普通表达更新重置整天计划的路径；不引入 keepwarm。

**完成条件：** 改期/取消使旧触发失效；早睡期间正常来话仍回答；可用状态过期不自动补发；重启不产生过时消息风暴；R67 保护期暂停活动不反复恢复；无变化不调规划模型。明确提醒和用户命令不需要通过普通分享的新奇度筛选。

自拍意图必须有 CR17A/B 和 CR13 的实际消费者；成图事件不自激触发下一张图，延后候选过期不补发，用户新请求复用同一合适作品时撤销重复主动候选。

### CR11 能力目录 插件宿主 动作执行 与结果回流

**复用入口：** [skills/registry.py](../../projects/tianshu-companion/src/tianshu_companion/skills/registry.py)、[actor_capabilities/application.py](../../projects/tianshu-platform/services/platform/actor_capabilities/application.py)、既有插件管理与原生 handler。

插件的直接复用位置包括 [manager.py](../../projects/tianshu-platform/services/platform/plugins/manager.py)、[packages.py](../../projects/tianshu-platform/services/platform/plugins/packages.py)、[process.py](../../projects/tianshu-platform/services/platform/plugins/process.py)、[actor_bridge.py](../../projects/tianshu-platform/services/platform/plugins/actor_bridge.py) 与 [SDK api.py](../../projects/tianshu-platform/tianshu_plugin/api.py)。安装、配置、生命周期和回执是已有基础；命令/工具声明必须接上实际消费者后才算本卡交付。

**改造：** 统一 discover/invoke/get/reconcile/cancel 和实际可用性。Action、Attempt、Receipt 记录意图、真实执行、外部效果、覆盖和产物；源/范围版本在提交前检查。取消、超时、明确未开始、已提交、unknown 分开。结果按同一个 action 身份进入 episode 提案与后续活动，不让插件另记一套事实。

保留内置/ZIP/目录/Git 包安装、多实例配置与密封凭据、作用域绑定、启停升级卸载、自定义页面桥和独立进程。SDK 从同一动作定义导出命令/模型工具；事件订阅使用真实范围和持久消费者，计划交统一调度。接通已声明且本批能力所需的模型、产物/文件、来源和消息端口：模型经过 Broker，输出经过统一表达/渠道，状态经受管 repository；插件不直接写核心记忆/关系库。当前受支持 SDK 保留有价值的公共语义，不为旧 Core 私有依赖提供兼容 shim。

**消费者与删除：** 明确命令和模型工具共用执行者，长任务关联原轮次返回。删除重复 handler、内部协议壳、以 job success 冒充业务完成的分支；真正外部 API 保留必要协议。

**完成条件：** 未装 handler 不可规划为成功；同意图重试保留身份；未知发送/下载先查回执；取消工具不吞其他请求；成功结果能进入一个真实 episode，失败不会被日记写成成功。注册、调用与结果消费必须同时具备。

插件必须实际完成安装/配置/启用→发现/调用→结果展示；SDK 示例可运行，未配置/缺端口明确不可用。生命周期状态和版本范围正确，故障不占死前台；停止实例后不再接收新调用。CR12/13/17 完成命令、工具、日程、表达及既有插件的最终消费，不能只验插件中心网页存在。

### CR04A OneBot 完整消息 事件 API 与传输

**复用入口：** [sdk.py](../../projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/sdk.py)、[rpc.py](../../projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/rpc.py)、NoneBot OneBot SDK 与现有连接/发送账本；[详案第 6 节](tianshu-multimodal-onebot-selfie-design-2026-10-31.md)。

**改造：** 用有序 MessageEnvelope 取代 TextEvent 加媒体尾缀；完成固定 v11 标准 23 个消息段条目、38 个公开 API、隐藏快速操作与消息/通知/请求/元事件目录，支持四种传输。音视频、GIF、引用回复、@与@全体保留原生结构；群公告、群文件等按实际 NapCat/SnowLuma 提供者扩展接入。配置显示实现、对端能力和验证状态。

**消费者与删除：** CR12/13A 获得真实媒体，CR13 发送有序片段，来源消费撤回，群管理消费明确 API 回执。删除媒体/@all 导致整条 unsupported 的分支、纯文本唯一入站合同、只拼文字后附媒体的下行结构及内部旧格式回退。未知段保留有界原生信息，不丢其他段；对端未实现的功能准确显示原因。

**完成条件：** 混合消息不丢字、不串引用作者；出站 reply/@/@all 与群公告是真正协议动作；重连、echo、重复、异步/未知结果正确。API 支持不授予模型凭据/群管理权限。合成协议验证与实际 QQ 收发分别记录，不为验收擅自向真实群发公告或 @全体。

### CR12 对话编排 与前台完整闭环

**复用入口：** [core.py](../../projects/tianshu-companion/src/tianshu_companion/core.py)、[context_preparation.py](../../projects/tianshu-companion/src/tianshu_companion/context_preparation.py)、原入站、工具循环和投递恢复。

**改造：** 将轮次控制、上下文准备、生成、工具调用和投递拆为职责明确的用例，以小范围端口代替整个 Core 私有状态。聊天先回答，普通抽取后台进行；明确取消/改期则及时落地。工具运行释放模型 permit，长后台活动释放对话位；结果回同一角色和关联轮次。新消息可以废止未提交的主动草稿。

插件命令、模型工具调用消费 CR11 同一动作目录。显式命令有明确回复负责人，外部 AstrBot/NoneBot 原有命令可交原宿主处理；角色主动调用需实际能力适配，不能因插件“已安装”就编造工具可用。网页、对话和日程沿同一 action 身份恢复，不能在插件端和陪伴端各执行一次。

**消费者与删除：** QQ/TG、网页聊天与主动表达使用同一 conversation 用例；删除旧 Core 路径、跨文件访问 `_core` 私有细节和同功能客户端回退。不要仅搬方法再把 Core 对象传进新文件。

**完成条件：** 两轮共享限制仍正确；直接命令不额外消耗陪伴轮次；关系表达更新后连续私聊不复发旧冲突；可选记忆故障不卡所有正常回复；来源/授权未知不会被当成允许。工具和普通生成有正确的完成、失败与取消结果。

### CR13B Fish 语音 MCP 原生 API 与稳定音色

**复用入口：** [books/audio/providers.py](../../projects/tianshu-platform/services/platform/media/books/audio/providers.py)、现有音频文件与回执能力。通用合成提供者提取复用，书籍章节逻辑保持归属。

**改造：** 优先交付 FishConnection、VoiceProfile、SpeechIntent、AudioArtifact。官方 MCP 通过 OAuth 使用 Plus 网页额度，原生 API 通过独立 Key/余额支持 HTTP、SSE 时间戳和 WebSocket 增量合成，并完成两种传输的文件 ASR 与带原音频引用的转写结果。MCP 长文本多片段完整收取，未支持参数准确报能力差异。服务端直接连接，不增加另一个语音代理。支持音色搜索/自有声音、绑定/试听、按需克隆与明确可见性、中文发音词典、S2.1-Pro 与可选 Drama 3 的语气编译。两种传输共用执行记录、缓存和资源入口，旧 Edge/兼容 speech 作为显式可选路线。

实现期间按以下顺序收口本卡内部内容：连接/权益与能力读取 → 原生/网页 TTS 与文件 ASR → 角色音色与语气/词典 → HTTP/SSE/WS 差异及缓存 → BOT、主动表达与 books 消费者。本卡先于 CR13A，后者直接消费已实现的 Fish 转写端口；CR13 最后统一装配理解与表达，不形成循环依赖。

支持文字/语音/双模态和自然选择，稳定声线与瞬时情绪分开；主动自拍附言也可选择语音。短回复、长听书和转写按共享账户容量隔离。缓存包含作用域、音色、实际模型/版本、台词、语气、词典、语言和格式。OneBot文件与网页播放分别适配；本卡不额外建设已排除的网页实时通话或 Fish 托管 Agent 平台。

原生 `s2.1-pro-free` 明确列为可配置选项，支持 `reference_id` 固定音色和受支持参考录音编码；不另建免费提供者，也不把切模型等同于换音色。免费、Plus 网页和付费 API 的实际计费来源分别显示；免费模型条件变化时不隐式换收费路线。验收包含免费模型绑定定制音色、无访问权音色报错及配置实际生效；协议验证不替代真实试听。

**消费者与删除：** CR13 使用同一 ExpressionPlan 发送；CR13A 消费 Fish 文件 ASR，books 复用新的合成端口并保持章节逻辑。删除旧 book 专属通用提供者副本、重复配置/容量队列、默认 S1/旧转写回退和复制整套听书工作流朗读一句话的路径；不把 TTS 指令读成台词，不把语音副本再次学习为新经历。

**完成条件：** 已配置路线实际生成并能在目标渠道播放；中文专名、日期、断句、稳定音色与情绪方向符合样例。MCP OAuth 刷新/重启/失效、正确团队与网页额度、API 独立计费、多个返回片段均核验；模型名拼错不被无声接受，词典应用不能只凭 HTTP 200。长听书/ASR 不占尽短语音容量；402/429、开始后断流、取消晚到和发送 unknown 不双答/重复扣费。未配置 API 时接口验证与真实付费验证分开，不用免费模型冒称全年稳定承诺。

### CR13A 图片 语音理解 与辅助感知

**复用入口：** [role_actions.py](../../projects/tianshu-companion/src/tianshu_companion/role_actions.py)、[knowledge_media.py](../../projects/tianshu-memory/src/tianshu_memory/knowledge_media.py)、原来源读取与 CR04A MediaRef。

**改造：** MediaResolver 按实际协议端文件 ID/URL/路径归属取得原件，处理 QQ 语音编码、长图和动态多帧。普通图片/语音优先直送主 Gemini 路线；辅助视觉/ASR承担 OCR、批量标注和明确需要的补充。需要转写时优先 Fish 文件 ASR，原生显式指定 transcribe-1-pro，MCP 按工具支持的参数选择；底层连接复用 CR13B 的 Fish 适配，不另建一套账户和预算。保存真实模态、覆盖、不确定性和转写对应关系；不用一串强制前置模型调用替代主模型直接理解。

**消费者与删除：** 当前对话实际消费原件/表示，Memory 只按原事件抽取一次；网页、视频、表情标注和图像检查复用同一感知入口。删除新路径中媒体仅作占位文本、转写另算一条用户消息和只取 GIF 中间帧当全部内容的处理。

**完成条件：** 语音专名/日期与听不清片段正确处理；图片关键文字/布局可读取；后半段动图线索可见；原件确实到达所选模型。辅助服务失败不拖住所有聊天，模型“不支持音频”不能以文字成功掩盖。

### CR13C 表情包管理 与语义选择

**依据：** 实际安装的 AstrBot 插件、陪伴 `v6.4.5b` 向量实现与表情包管理器上游机制；详细边界见[多模态详案第 5 节](tianshu-multimodal-onebot-selfie-design-2026-10-31.md)。

**改造：** 形成一个 reactions 素材权威：图包导入/导出、分类、批量管理、GIF预览、OCR/动作/用途标注、按角色与会话选包、增量向量、去重、检索试用和纠错。用实际回复的用途/态度构造 ReactionIntent，再进行作用域过滤、向量与词法候选、少量复选、发送前版本复核。普通照片不自动成为共享表情。

**消费者与删除：** CR13消费稳定素材ID并发送原图；所有索引使用PG/pgvector与已验证embedding profile，不增加第二个FAISS/记忆服务。没有同时运行两个自动附图者。外部图包可一次性导入，但不双写AstrBot旧库。

**完成条件：** “用户失落、角色安慰”按回应意图选图；反讽和文字含义不被忽略；GIF不静默变静态；改描述/删文件后旧向量失效；换模型不会混用向量空间；无合适图时正常文字；同一表达只发一次，不强制每几轮发图。

### CR13 多模态表达 与真实任务进度

**复用入口：** [expression.py](../../projects/tianshu-companion/src/tianshu_companion/expression.py)、[bot_delivery.py](../../projects/tianshu-platform/services/platform/bot_delivery.py)、现有媒体出口、TTS 和图库/资产适配。

**改造：** 装配 CR13A/B/C 的理解、语音与表情能力，同一 ExpressionPlan 支持文字、贴纸、图片、视频、语音、引用和提及，保持公开字段与私密状态分开。进度来自真实任务阶段，合并重复更新。附件转换依据模型/渠道限制并记录实际表示，普通文本不等待可选媒体处理；视频和群公告等动作的成败有准确回执。

**消费者与删除：** 对话和主动分享使用同一表达、投递和反馈链。去掉重复发送、按段多次记忆/反馈及目标从正文推断的路径；不引入强制表情计数器和正则剥心声。

**完成条件：** 三段文字加一段语音仍是一次逻辑表达；未知投递不重复发；私密字段不进入任何公共片段；无合适贴纸时自然降为文字；代码、金额、否定、时间与引用不会被语气处理改坏。

### CR14 网页 论坛 与 B 站内容探索

**复用入口：** [content_discovery/service.py](../../projects/tianshu-platform/services/platform/content_discovery/service.py)、[content_discovery/media.py](../../projects/tianshu-platform/services/platform/content_discovery/media.py)、[knowledge_sources.py](../../projects/tianshu-memory/src/tianshu_memory/knowledge_sources.py)、[reading.py](../../projects/tianshu-companion/src/tianshu_companion/reading.py)。

**改造：** 支持带问题研究、兴趣闲逛、追更/继续三种活动，复用 life 活动游标、已看集合、候选与预算。网页读取正文、标题、时间和原 URL；论坛保留楼层、作者、引用、分页与编辑；B 站从订阅/本地媒体扩展到实际内容发现。结构化接口/文本优先，必要时使用受控浏览器和已配置账号会话。

**消费者与删除：** 取材进入 sources，实际读取进入 episode，未解问题回 life。删除目录里只有名称却无实际实现的占位消费者；不另建浏览专用记忆、模型路由或任务队列。

**完成条件：** 搜索摘要不能冒充读完正文；论坛转述不会变成用户自述；重复内容不再次成长；登录/站点失败只影响该来源；中断后能从真实位置继续。浏览网页中的指令不覆盖人格/目标；发帖、点赞、投币等外部写操作不隐含在读取里。

### CR15 实际视频理解 与持续观看

**复用入口：** [knowledge_media.py](../../projects/tianshu-memory/src/tianshu_memory/knowledge_media.py)、[role_actions.py](../../projects/tianshu-companion/src/tianshu_companion/role_actions.py)、Reading 进度与模态表示。

**改造：** `video.observe` 获取实际可读媒体，按时序切段/选场景，发送真实画面与音轨给验证过的路线，关联字幕/OCR；支持疑点回看和跨段综合。ConsumptionReceipt 保存请求范围、实际音频范围、帧时间/抽样方法、已处理段、遗漏与续读点。长片后台分段；用户等待的局部片段可用 interactive 类。

**消费者与删除：** 回执进入阅读覆盖、真实经历、作品观点和后续探索；删掉仅凭标题/字幕/下载完成就声称看过的接线。不删除仍有用的抽帧工具，而是准确标记其覆盖局限。

**完成条件：** 小规模片段集覆盖仅画面线索、仅声音线索、前后顺序、字幕冲突和断点续读；实际模型调用证明收到相应模态。不能只用“请求成功”证明看懂。长片后台运行时普通聊天保有预留资源，局部缓存按来源版本和允许范围命中。

### CR16 日记 后台整理 兴趣 与风格成长

**复用入口：** [diary_materials.py](../../projects/tianshu-companion/src/tianshu_companion/diary_materials.py)、[diary_sharing.py](../../projects/tianshu-companion/src/tianshu_companion/diary_sharing.py)、[actor_memory/interests.py](../../projects/tianshu-memory/src/tianshu_memory/actor_memory/interests.py) 与既有 reflection。

**改造：** 原始经历水位驱动一次合并整理，派生手记、日记、兴趣、相处认识和风格投影，分别保持归属。日记按角色/日期/材料修订去重；风格有原因、范围、反例与撤回，last_reviewed_at 和 last_changed_at 分开。下次对话/计划实际读取相关变化，不能只生成看板。

**消费者与删除：** ContextAssembler、life 选择和用户界面使用投影；共享日记继续执行原读者/来源规则。删除重复摘要/重复来源扫描，不引入独立心声库、五天强制变化、每轮日记提醒或“先整理后聊天”。

**完成条件：** 同一经历经三次改写仍只算一个证据；角色猜测不变成用户事实；来源更正/遗忘传到风格和候选；无新材料不调模型或编变化；后台失败不阻塞前台；至少一个实际经历确实改变后续选择，而非只把文字存进库。

### CR17A 主被动自拍 场景快照 与提示词规划

**复用入口：** [skills/image.py](../../projects/tianshu-companion/src/tianshu_companion/skills/image.py)、[runtime_photo.py](../../projects/tianshu-companion/src/tianshu_companion/runtime_photo.py)、[runtime_execution.py:690](../../projects/tianshu-companion/src/tianshu_companion/runtime_execution.py:690)、[image_prompt.py](../../projects/tianshu-companion/src/tianshu_companion/image_prompt.py)、衣橱、活动、小屋及 [web_weather.py](../../projects/tianshu-platform/services/platform/web_weather.py)。参考插件已核实的主动生成/统一发送机制见[详案第 7.0 节](tianshu-multimodal-onebot-selfie-design-2026-10-31.md)。

**改造：** 用户请求、明确日程和角色自主 PhotoIntent 都形成 SceneSnapshot 与 ImageIntent：固定角色/参考、当地时间、实际活动、地点、天气来源与时效、当前或本次服装、动作表情、镜头与风格。创作可仅归相册，分享时再关联明确目标；生成前与成品后分别复核用途和联系时机。冻结拍摄快照但保留发送时刻，延后分享使用正确时态；缺天气/衣橱时采用合理场景，不加重复确认。沿通用 Action 保存来源、窗口、去重键和产物引用，不建专属第二账本。

**消费者与删除：** CR17B按原生引擎编译；CR13在原会话或已确定主动目标投递，相册与Memory只记真实生成/分享经历。演进 `intent_from_life` 与 RuntimePhotos；删除工具自发图和主动分支重复发送、照片专属轮询/状态副本以及各消费者重复拼天气/服装的规则。actor_only 创作通过明确用例开放，不解除所有活动工具的范围限制。

**完成条件：** 无用户新发言时也能从实际活动形成照片任务并主动分享；日程约定、随兴分享与纯相册创作均可运行。下午雨天阅读与夜间计划不混淆；用户变忙/取消可暂存或撤销分享；生成重启与晚到事件不重抽，用户请求消费同一产物不会再被主动补发。人物参考、天气时效、时间与服装正确；预览展示动机、快照与实际提示。

### CR17B ComfyUI NAI GPT Image 三引擎

**复用入口：** [image_provider.py](../../projects/tianshu-companion/src/tianshu_companion/image_provider.py)、[image_comfy.py](../../projects/tianshu-companion/src/tianshu_companion/image_comfy.py)、[image_catalog.py](../../projects/tianshu-companion/src/tianshu_companion/image_catalog.py)、原工作流编译与提交恢复。

**改造：** 在同一ImageProvider体系中实现EngineProfile、语义编译、提交、产物与可用的查询/取消。ComfyUI复用实际工作流槽和角色/LoRA配置；NAI使用官方图像请求/结果格式；GPT Image使用Images生成/编辑接口。参考、负面、采样、尺寸和音画资源限制按各引擎能力编译，不强行使用统一提示字符串。

**消费者与删除：** 角色、日程和明确命令调用同一images用例；CR13投递、相册归档。删除ComfyUI唯一硬编码和旧内部路径，保留有价值的工作流/未知提交语义。不增加第二套生图队列或空壳engine占位。API未配置时明确未配置；实际代码和合成验证完成不冒称付费API已实测。

**完成条件：** 三引擎可配置、可编译、能处理真实契约成功/失败/未知结果；ComfyUI按已配置本地服务验证，角色主动生成包含在实际路径。NAI的人工触发条件由真实任务来源满足，普通自主日程默认 ComfyUI/其他允许路线，不伪造用户请求。取消不误停别人的Comfy任务，回执丢失不盲目重生；产物按 CR17A 用途投递或暂存，角色/场景不明显冲突。

### CR17 现有媒体 创作 设备 项目能力接线

**复用入口：** Platform 现有 media/books/comics/music、插件能力、ComfyUI/创作、小屋、设备/HA/Docker、项目工具和外部产品适配。按 CR01 清单逐组处理，不重写已经成熟的下载器/阅读器。

**改造：** 统一输入身份、能力请求、产物、实际结果和经历回流；命令与自然语言共用 handler。小屋模拟、作品内容与真实软件行为明确区分。用户自己的语音/文字副本、下载副本和书籍摘要不反复制造独立经历。外部产品通过已允许接口连接。

content-discovery、comic-files 及 CR01 确认的其他自有插件随新 SDK/PG 同批适配；保留其实际业务能力。AstrBot/NoneBot 连接器保持真实宿主边界；GsCore 等其他桥接按 CR01 确认的实际状态保留或接线，未实现项不宣称已可用。跨宿主插件只通过实际接口/动作适配，不承诺任意插件代码无修改直装。插件公开结果/产物进入相同表达与经历链。

**消费者与删除：** 角色能发现、调用、继续并解释已实现能力，web 使用相同状态。删除本轮替换后的重复内部桥、轮询与专属模型入口，保留真实外部协议。历史未实现项明确保持其状态，不用占位成功冒充已接入。

**完成条件：** 每类发生装配变化的已实现能力有一个实际关键路径或有效既有证据；下载不等于阅读，创建草稿不等于发布，计划重启不等于设备重启。未改变的独立产品不要求全量重测或操作真实设备。

### CR18B 角色 记忆 关系 生活 与成长界面

**范围：** 角色管理、网页聊天、人格版本/世界设置、人物与群画像、记忆/依据/事项、关系冻结和联系偏好、生活目标/活动/约定、情绪/手记/兴趣/风格、日记和创作书架、衣柜及小屋。

**改造：** 页面按新对象与权限投影展示，认识纠正、承诺改期取消、活动暂停继续、风格撤回均调用真实领域用例。删除 score/stage/adjust_affinity 及其旧表单；保留小屋渲染与资源，改接统一生活快照。私密手记不泄漏到公共日记/聊天。账号关联和身体活动依实际后端状态显示，未实现的不伪造数据。

**完成条件：** 普通用户可顺畅查看、修改和理解真实状态；功能按钮有真实后端消费者，加载/空/失败/权限/过期状态完整；关系冻结、纠正和取消影响后续真实行为。

### CR18C 内容 多模态 阅读 与自拍界面

**范围：** 视频/音乐/书籍/漫画订阅、链接下载/任务/媒体库/存储、阅读器与听书、网页论坛和视频消费覆盖、资料/资产/项目知识、图包管理、语音模式/试听、衣柜/相册/自拍。

**改造：** 保留现有下载与阅读体验，展示真实获取/理解/播放覆盖和继续位置；聊天支持有序图片/音频/视频/引用。表情包提供导入导出、动图、语义标注/索引和检索试用。ImageStudio/衣柜从 ComfyUI 单一路径改为三引擎共用场景与参数预览，展示主动动机、暂存、待分享和投递结果，支持查看作品与明确重拍。

**完成条件：** 所有原有效媒体入口保留并接新 API；图、音、视频实际可访问/播放，失败不显示成功，未知回执不重发；音色/图包/场景配置真正作用于模型和投递。

### CR18D 模型 插件 连接 与系统界面

**范围：** 模型供应商/功能路线/实际能力/共享额度、插件中心与自定义页、OneBot 接入和能力矩阵、Fish OAuth/API/免费模型/音色与词典、天气/资产/HA/技能/节点/访问域名、任务/链路/设备/容器/Dockge/游戏服等现有系统入口。

**改造：** 区分配置、实际可用和验证状态，展示前后台真实资源等待。Fish 网页积分与 API 余额分开；插件保留安装/配置/绑定/启停升级卸载和调用记录，复用新版页面桥。OneBot 显示标准与提供者扩展差异。系统操作保留真实外部授权、受理和完成回执，密钥只写不回显。

**完成条件：** 配置与实际执行一致，没有无效开关或只有表单的空能力；插件页、模型路由、连接及现有管理操作完整接线，失败和未知准确表达。

### CR18 前端完整接线 与交互收口

**复用入口：** 原模型配置、记忆展示、角色/关系、日记书页、小屋、任务及设备页面，保留已认可视觉。

**改造：** 围绕“她现在做什么、她记得什么、我们怎样相处、接下来有什么约定”组织页面。能纠正认识、查看允许的依据、调整关系冻结和联系偏好、查看/撤回风格变化、暂停/继续真实活动。模型页同时显示配置与实际可用路线、前后台是否共享额度；运维详情解释等待在哪种资源。

本次同时提供图包/动图管理、语义标注与索引状态、检索试用、音色及输出模式、OneBot标准/扩展能力矩阵、三引擎配置和自拍场景/提示预览。设置必须影响实际消费者；插件已安装、模型已配置和真实功能可用分别显示。现有已认可视觉继续沿用。

Fish 设置显示“Plus 连接/网页额度”和“开发者 API/余额”、实际可用功能、音色试听/读音词典与默认路由；一次连接说明 MCP 产物链接属性，不能显示成统一无限额度。照片页显示创作动机、生成/暂存/待分享/已投递以及等待原因，用户可以自然请求查看现有作品或明确要求重拍。

保留插件中心的发现/安装、实例设置与绑定、动作表单、自定义页面、启停/升级/卸载、日志/调用记录和开发入口；显示当前 SDK 范围、实际可用动作及缺失原因。用户完成一次配置后正常调用不增加逐次批准弹窗；已有具体权限和数据边界仍有效。

**消费者与删除：** 页面只调用领域用例，不自行计算第二份关系/日程状态。删除旧好感阶段和无效配置、重复入口；不把内部协议字段或状态机参数塞进普通用户必填流程。

**完成条件：** 页面设置对真实消费者生效；数据缺失显示未知而非健康/零；进度不冒充理解正确率；人物范围一致；原日记阅读与小屋体验可用。用受影响页面直接验证，不默认全浏览器套件。

### CR19 必要联合验证 与体验对照

**输入：** 本轮先完成全部实现与集成，再集中运行适用的领域用例和联合场景；不逐块启动测试。可复用此前已经通过、相关代码和输入确实未变的证据。测试使用隔离数据，真实模型/渠道证据单独标明。

**集中场景：** 转述与私聊范围；改期取消与晚到旧消息；冻结与普通关系版本更新；来源遗忘；角色手记不自证；夜间免打扰与正常来话；网页/论坛/视频实际经历影响下一次选择；多模态表达未知回执；Broker/worker/数据库取消与重启；前台持续有请求时后台仍有推进。

新增专项集中验证：原生Gemini实际收到图像/音频；OneBot混合消息、GIF、引用、@、@全体、公告与事件；语音真实播放；表情语义和索引更新；自拍场景快照及三引擎编译/执行边界。协议契约通过、实际模型理解、真实渠道结果分别标明，不能互相替代。

Fish 增加 OAuth/额度域、完整片段、专名试听、同账户长短任务并发和付费未知恢复；自拍增加无需用户发言的生成/分享、生成途中变免打扰、主动和被动竞争同一作品、图文语音部分失败及重启去重。复用各卡有效检查，只补装配差异，不另开一套全量验收。

插件补必要联合场景：同一真实示例经网页/命令/模型工具/日程分别进入同一 handler；结果只回流一次；停用后旧派工失效；升级失败和重启不重放未知写入；外部宿主命令无双答。复用现有 plugins 与 actor_capabilities 定向用例，并检查一条发生迁入变化的实际插件路径，不扩大成所有外部生态测试。

**性能与质量：** 在明确硬件/路线/数据规模/并发下，分开记录 SQL 与核验、模型排队、首输出、发送结果、后台积压和成本。比较仅人格、情境人格、有来源动态状态的小组样例，观察自然程度、具体性、纠正、重复与打扰；不把模型自评或消息数当结论。

**完成条件：** 核心故事实际跑通、结果与局限明确；现有能力未静默丢失；不得用 health、微基准、纯函数或人工修改时间冒充完整业务效果。旧 PC 的真实日数和失败分母不改，新系统长期观察从新起点记录；研究性长期效果不伪称已证实。

### CR20 空数据安装 切换 与旧实现退役

**范围：** 只处理天枢自有代码、schema、服务与运行数据。用户现已明确授权完整实施、最终停用旧版、清理旧数据并部署新版；本卡在集中验收通过后由主协调者单写执行，目前未执行清理和切换。

**改造：** 确定新构建输入和专属资源清单，停止旧接入/调度/后台写者，处理仍在途动作；从空库安装并设置新 intake epoch。静态角色/模型配置可显式选取复用，旧记忆、关系、任务、队列不导入。启动唯一新系统，验证关键入口与恢复，再移除旧运行入口、旧自有产品树、旧数据库适配及专属旧数据。

**必须删除：** SQLite 与旧迁移链、向量双格式、旧全域检索、旧积分关系、重复模型准入/调度、未消费投影、旧内部协议壳、遗留配置和仅支持旧路径的测试。逐项核对引用，不仅把源码挪进 `legacy`。外部协议兼容、有效授权与未知回执语义继续保留。

插件旧核心私有导入、双后端和重复调度随消费者迁入退出；保留当前 SDK、必要插件进程协议和外部宿主适配。新装可以不导入旧插件业务数据，不为此留下旧天枢兼容层；其他宿主自身的插件包/配置不属于天枢专属清理范围。

**完成条件：** 干净环境能够完整安装；只有新接入与 worker 写入；当前版本重启和受影响恢复机制通过；旧逻辑在构建/运行依赖中无引用；共享 PG/Redis 实例、Chat Audit、AssetLibrary、外部媒体与其他服务不受影响。不要求为已允许丢弃的旧业务数据做兼容导入；新版本自己的备份恢复仍有明确方案。

## 5 旧需求与本轮新增需求的去向

原 requirements-v2 的 status 是旧规划字段，不能当作当前实现状态。下面只映射归属；实际已实现/局部/未实现取 CR01 的最新源码与交付核对。

| 原需求 | 本轮负责队列 |
|---|---|
| U01–U02 | CR04 CR04A CR12 CR13A CR13B CR13 |
| U03–U05 | CR09 CR10 CR18 |
| U06–U16 | CR04 CR05 CR06 CR08 CR09 |
| U17–U18 | CR06 CR07 CR14 CR15 |
| U19–U26 | CR10 CR17 CR18 |
| U27–U33 | CR11 CR16 CR17 CR18 |
| U34–U40 | CR03 CR11 CR13 CR17A CR17B CR17 |
| U41–U42 | CR11 CR17；保持不在网页新增实时通话/共同观影的原边界 |
| U43–U47 | CR05 CR06 CR07 CR16 |
| U48–U50 | CR04 CR11 CR17 |
| U51–U56 | CR11 CR14 CR15 CR17 |
| U57–U65 | CR11 CR17 CR18 |
| U66–U68 | CR05 CR06 CR10 CR17 |
| U69–U74 | CR05 CR07 CR11 CR17 |
| U75–U79 | CR03 CR18 |
| U80–U83 | CR01 CR18 CR20 |
| U84–U88 | CR04 CR07 CR08 CR12 CR19 |
| U89–U91 | CR03 CR07 CR11 CR14 CR17 |
| U92–U100 | CR01 CR11 CR17 CR18 CR20 |

| 本轮明确新增或强化 | 进入队列 | 完成后可见的结果 |
|---|---|---|
| 好感重整为关系连续性 | CR08 | 记得相处细节和边界，离线不扣分 |
| 人格档案与有因情绪 | CR09 | 情境反应连贯，可恢复、可纠正 |
| 前后台模型与任务不相互占满 | CR02 CR03 CR12 CR19 | 可观测资源预留、正常聊天、后台公平推进 |
| 网页 论坛 B 站自主探索 | CR10 CR14 | 有兴趣、有游标、可继续，能区分没读和读过 |
| 真正看视频 | CR15 | 实际视听、时序理解、回看与覆盖回执 |
| 角色手记 | CR09 CR13 CR16 | 感受有出处、不会泄露或自证为用户事实 |
| 风格成长 | CR09 CR16 CR18 | 有经历、有范围、有变化记录，可撤回 |
| 早睡 外出 免打扰 | CR08 CR10 CR12 | 主动联系合适，用户来话仍正常回答 |
| 表情 语音与进度 | CR13 | 真实媒体、统一回执、一次表达一次反馈 |
| 图片和语音理解及辅助视觉 | CR03 CR04A CR13A | 主模型收到实际模态，辅助处理不成为必经阻塞 |
| Fish Plus优先与完整语音 | CR03 CR13A CR13B CR13 CR18 | MCP网页额度、原生API独立额度、稳定音色/语气、转写/合成、真实播放 |
| Fish 免费模型与定制音色 | CR13B CR18 | 固定 voice/reference 与模型分开，真实路线可查，不隐式转收费 |
| 图包管理与向量选表情 | CR13C CR13 CR18 | 图库可管理、按回复语义选图、动图与索引正确 |
| OneBot完整消息与API | CR04A CR13 CR18 | v11全目录与实际扩展，真实能力/权限和结果可查 |
| 主被动日程天气自拍与三引擎 | CR10 CR17A CR17B CR13 CR18 | 自主生成/分享与用户请求同链，暂存/取消有效，三引擎按真实来源运行 |
| 原生插件与外部宿主扩展 | CR01 CR02 CR03 CR11 CR12 CR13 CR17 CR18 CR19 CR20 | 安装/配置/启停、SDK、命令/工具/日程/页面接线；外部插件按真实协议适配 |
| 高内聚低耦合与旧逻辑清理 | 全部，CR01/CR20 统一收口 | 一份规则、一条生效路径，旧实现退出 |

## 6 交付记录和停止条件

每卡沿用项目已有交接方式，只需记录实际源提交、完成行为、消费者、删除项、必要验证及剩余限制。不要另造三套状态账本，也不要凭一项局部通过把整卡标为生产完成。

建议状态依次为 `planned → implementing → locally_verified → integrated → deployed`，其中自然使用质量另记证据和观察起点；它不能被部署状态自动替代。若被其他窗口修复提前满足，复用有效结果并更新卡范围，不为了队列完整再重写一遍。

最终交付同时满足：完整闭环有真实消费者；配置与实际路由一致；前后台资源有边界；角色经历能影响后续行为且不会自我制造事实；当前功能有去向；空库安装可用；被替代逻辑和数据适配退出运行树。完成后停止，不追加无关框架、兼容层或循环全量审查。
