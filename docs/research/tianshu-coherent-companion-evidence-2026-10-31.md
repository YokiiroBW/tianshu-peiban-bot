# 天枢统一陪伴系统：证据、文献与功能映射

综合设计入口为[完整改造说明](tianshu-coherent-companion-system-2026-10-31.md)，唯一详细施工顺序为[改造队列](tianshu-coherent-companion-refactor-queue-2026-10-31.md)。本索引保留各轮来源与证据边界；鲸鱼娘见第 11 节，多模态/AstrBot/OneBot 见第 12 节，主动自拍与 Fish Plus/API 见第 13 节，插件保留及免费定制音色见第 14 节。

日期：2026-10-31。配套[完整方案](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/tianshu-coherent-companion-system-2026-10-31.md)。本索引用来区分原问题、当前实现、发布证据和设计推导；不是新的 CI 门禁或验收报告。

初稿的 R57 与源码截面保留为历史依据；后续模型、并发、代码质量和人格研究已更新至 R67，最新核对见第 9 节及[专项详案](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/tianshu-model-concurrency-persona-design-2026-10-31.md)。

## 1. 调查覆盖与证据等级

本轮索引了本地会话元数据中与天枢工作区匹配的 93 个会话记录，包含子代理记录；对其中 21 个非当前主聊天提取首轮与最近用户/最终结论，重点阅读指定两窗、相关原始报告、源码及修复记录。另通过应用工具检查相关媒体与语音聊天。这个覆盖不等于逐字审阅所有工具输出，也不能证明未开放、已丢失或不在当前索引中的历史聊天均已读到。

等级：**源码**证明当前检出的实现；**发布记录**证明发布者记下的部署和验证；**真实业务证据**需要实际来源/渠道/模型结果；**论文实验**只支持其样本与场景；**方案**是结合证据作出的工程选择。没有在本轮重新 SSH 或运行真实模型/QQ 验收。

### 指定聊天及关联聊天

| 聊天 | 实际采用的上下文 |
|---|---|
| [Review companion repair handoff](codex://threads/01a18901-a225-7c51-8fd6-9d0af3cd663c) | 原主动调查、PC 修复、R56/R57、尚未闭合的质量与超时问题 |
| [按文档改造并部署](codex://threads/01a188f1-1905-7500-89a1-1315db88475d) | 记忆独立调查、合并结论、开发文档、R36 与源码差距 |
| [复核天枢改造并评估 Laya](codex://threads/01a18be1-9c1d-7a01-824e-0711d49519f8) | 当前链路复核、来源扇出、本地模型任务边界；R53 数据不冒充 R57 |
| [排查橙汐人格不回复问题](codex://threads/01a18dfb-3356-7df1-a94e-c8f59a99bb00) | 好感版本变化造成下一轮在模型前失败的具体案例 |
| [规划天枢全链路监控](codex://threads/01a1880b-fa79-75f2-887e-25575373a382) | 监控与业务状态分离；普通日志 ledger 已退役 |
| [调研天枢 Bot 主动生活能力](codex://threads/01a15a96-9c82-7102-b512-9b592e1f4dff) | 自主生活、用户记忆与角色经历、长期兴趣及原模块复用 |
| [记忆系统隔离测试接续](codex://threads/01a169d4-bacb-74f2-b27e-136afb9c936c) | 来源、人物归属和隔离语义的既有约定 |
| [梳理天枢陪伴提示词组装链路](codex://threads/01a18851-306e-7523-bcca-e43eb0d1aaae) | 稳定人格前缀与动态材料边界，不把画像写成系统权限 |
| [调研聊天上下文管理与压缩](codex://threads/01a16dff-e66b-7710-a5b0-0499d4e5109f) | 会话摘要是工作状态；不自动成为长期事实 |
| [梳理天枢的13个服务](codex://threads/01a17c1d-482b-7621-9281-e0354f0e7365) | 统一运行时的历史与现状，避免重复提出已经完成的单容器 |
| [搭建天枢 UI 本地预览](codex://threads/01a182b0-d9db-7ab3-9b69-0ce50536015b) | 保留普通用户体验与已认可视觉方向 |
| [调研大肥鱼陪伴机器人](codex://threads/01a18208-e485-7cc2-b699-42b113684fed) | 自然沉默、相处细节；外接账号能力不作核心前置 |
| [规划 Bot 语音发送功能](codex://threads/01a1791f-e561-7541-b422-6daed920c24a) | 自然选择/用户指定媒介，书籍 TTS 复用，未知发送不双答；仍是设计 |
| [书籍订阅](codex://threads/01a142ff-ddd7-7da3-9275-e7832c83f02e) | 订阅、听读、原件与进度属于已有媒体领域；不把下载等同已读 |
| [漫画订阅](codex://threads/01a14e9c-05fa-7803-8413-97c7147d1266) | NAS、源插件、Komga/AssetLibrary、订阅删除与已存文件的独立性 |
| [音乐订阅](codex://threads/01a13b88-8906-7b11-9271-00f281003fda) | 音乐同步、歌词补齐与跨源结果；现成业务继续由原能力承担 |

其他已索引的项目话题包含模型供应商、NAS 迁移、视频订阅、Codex CLI 接入和项目研究。这些记录提供需求边界，未将历史部署状态逐项当成本轮当前实机事实。

## 2. 原始报告和修复来源

| 文件 | 本次用途 |
|---|---|
| [主动陪伴深度调查](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/proactive-companion-deep-dive-2026-10-30.md) | 原 R35 问题与 PC 方向；所有“尚未实现”均与最新记录对照 |
| [记忆独立调研](C:/YOKI/Claude/Tianshu/天枢记忆系统优化_独立调研报告.md) | 检索、语义与事件结构的最初假设 |
| [核对后的合并结论](C:/YOKI/Claude/Tianshu/天枢记忆系统优化_核对GPT后合并结论.md) | 持续事件、主体归属、时间版本与 evidence/oracle 评价 |
| [记忆开发实施文档](C:/YOKI/Claude/Tianshu/天枢记忆系统优化_开发实施文档.md) | M0–M3 实施意图；禁止 pgvector/保留双格式是旧批次约束，不带入新方案 |
| [运行与本地模型复核](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/tianshu-runtime-and-local-decision-model-research-2026-10-30.md) | 来源依赖扇出、检索实际热点、事件消费者缺口 |
| [六模型实测](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/local-small-model-test-results-2026-10-31.md) | 任务数据、延迟、语义错误、热限制及本地模型的现实用途 |
| [R56](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-repair-r56-2026-10-31.md) | 来源读取并行和预算、日记草稿已非零 |
| [W3 本地交付](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-w3-local-2026-10-31.md) | PC-12～17 的功能提交、消费者和定向验证 |
| [R57](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-repair-r57-2026-10-31.md) | 初稿部署截面；私聊恢复、残留超时、真实质量限制；后续见第 9 节 |
| [私聊恢复交付](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/docs/handoffs/PC-qq-context-recovery-2026-10-31.md) | 故障因果链和精确恢复范围；普通关系更新与权限更新的差别 |
| [角色自身记忆设计](C:/YOKI/Codex/tianshu-peiban-bot/docs/architecture/actor-self-memory-design-2026-10-23.md) | 用户/角色视角分开、真实经历、来源继承与共同经历，作为可复用设计 |
| [功能分流与归属](C:/YOKI/Codex/tianshu-peiban-bot/docs/architecture/functional-routing-and-ownership.md) | 明确命令快速路径、单回复负责人、共同 handler、多角色轮次共享 |
| [V2 架构](C:/YOKI/Codex/tianshu-peiban-bot/docs/architecture/tianshu-system-architecture-v2.md) / [U01–U100](C:/YOKI/Codex/tianshu-peiban-bot/docs/product/requirements-v2.json) | 完整功能边界；其中旧状态字段不是当前完成度 |

原报告中的若干技术判断需要更新：主体分区不等于取消全域装载；二进制优先不等于不读取 JSON；Generative Agents 的检索不能被误述成所有信号简单连乘；不能用笼统“10 万条以下不需要索引”替代实际查询计划和延迟。

## 3. 当前源码映射

初稿核对 Companion 为 `1447b50a8bf38d9b6d4f6b1843be18d425bd7357`，Memory 为 `152d7c8cb457bb04eb748a864626634aa7fe9ad4`。Platform 与其他仓库在调查中有活跃/未提交工作，下面是读取位置，不代表本轮将其冻结或发布；后续截面见第 9 节。

| 实际位置 | 读到的事实 | 方案中的处理 |
|---|---|---|
| [read_v2.py:41](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/memory_context/read_v2.py:41) | collect 仍装载授权域组、记录和文档等材料 | 索引候选先行、按候选加载 |
| [persistent_retrieval.py:87](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/persistent_retrieval.py:87) | SELECT 同时包含 vector 与 vector_bytes | pgvector 单一存储 |
| [merge.py:36](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/memory_context/merge.py:36) | MMR 反复与已选集合计算，默认可排序全候选 | 缓存最大相似度，有界 k |
| [retention.py:4](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/retention.py:4) | thought 长期保留分支依赖 negations | 抽取 schema 与保留规则统一 |
| [memory_events.py:113](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/memory_events.py:113) | 事件槽位和同 scope 前序关系较保守；不等于完整跨窗事项 | 由 matter/claim revision 接入实际消费者 |
| [profiles.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/profiles.py) | 显式来源和共享发布语义 | 私人认识与公开发布分开，复用边界 |
| [actor_memory/interests.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/actor_memory/interests.py) | 独立实际经历、去重、衰减与授权检查已存在 | 移入增量投影，保留行为 |
| [life_goal_context.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/life_goal_context.py) | 完整人格、紧凑材料、变化 fingerprint、到期/唤醒、usage | 继续作为便宜门控与规划输入基础 |
| [life_salience.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/life_salience.py) / [proactive_salience.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/proactive_salience.py) | 显著性、重复、前后检查与回应阈值已有 | 统一注意策略，不另造主动模块 |
| [context_preparation.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/context_preparation.py) | 可选历史依赖与关系冲突恢复，仍受多类版本影响 | 分开权限/内容/表达失效语义 |
| [dialogue_prompt.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/dialogue_prompt.py) | 人格、资料、关系表达与实际权限分开 | 保留边界，减少重复拼装 |
| [skills/registry.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/skills/registry.py) | 定义、已装 handler、角色配置、可执行状态已区分 | 扩展成唯一能力目录与结果接线 |
| [actor_capabilities/application.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-platform/services/platform/actor_capabilities/application.py) | 实际角色授权、绑定、invoke/reconcile/cancel 和执行状态 | 保留真实执行语义，去内部传输壳 |
| [plugins/scheduler.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-platform/services/platform/plugins/scheduler.py) | 持久 schedule、cron 解析、逐项 tick、重试相关状态 | 业务意图保留，派工交单一队列 |
| [gateway/scheduler.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-model-gateway/src/tianshu_gateway/scheduler.py) / [model_slots.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/model_slots.py) | 不同层次的准入、公平与排队 | 明确单一供应商容量与工作负载边界，消除重复等待 |
| Git 提交 `a84c0863d1834963a35772d41dc5eeef9d9cfded` 中的 `deploy/single_container/host.py` 与 `threaded_exchange.py` | 单业务进程，原生应用在线程循环中通过信封桥接；并非内部 TCP 微服务 | 新方案收敛模块装配，保留真实进程边界 |

运行时源最初定位于上述提交的临时 worktree；补充研究时该 worktree 已清理，但两个文件的 Git 对象仍可读取，已核验。协调根目录没有该部署目录，不能仅据此推断运行时缺失，也不再保留失效的临时路径链接。

## 4. 本地模型证据的准确含义

硬件记录：i7-7700HQ、约 16GB RAM、GTX 1050 Ti 4GB。下列是已有实测，不是本轮重跑。

| 实验 | 结果 | 能说明什么 |
|---|---|---|
| 48 条短结构任务，12 开发/36 评分 | Qwen3.5-2B Q4_K_M 31/36，中位约 513ms；MiniCPM5-2B 31/36，约 649ms | 在短任务上有辅助价值，仍有错误时间/可执行性判断 |
| Laya/GLiClass/GLiNER 短任务 | 分别 14/36、15/36、13/36 | 快不等于适合本任务语义 |
| 真实 planner 结构的 8 条合成案例 | 约束 JSON 后 Qwen 6/8、MiniCPM 5/8；格式均合法 | JSON 语法保证不能保证计划正确；样本很小 |
| 12 开发/48 测试路由 | Laya 26/48；GLiClass 33/48 | 当前不能做必经前置路由；高置信也出现误判 |
| 温度与持续负载 | 部分任务超过 90°C，planner 峰值约 95°C；相应测试停止 | 不能把 GPU 预热延迟当作长期常驻服务能力 |

HTTP 生成模型与进程内分类器的计时边界不同，不能直接按中位数横向排“整套系统快几倍”。没有改变生产模型路由，也没有通过本轮报告补称本地模型真实长期部署已验收。

## 5. 外部文献：采用机制与适用限制

以下为本轮及同一对话前轮核对的一手资料。核心架构论文查看了正文相关章节；部分行为研究采用论文摘要/可获取正文和作者版本，不把无法读取的全文细节当作证据。访问日期为 2026-10-31；预印本与已发表论文分开。

| 文献 | 采用什么 | 不据此声称什么 |
|---|---|---|
| [De Freitas 等，AI Companions Reduce Loneliness，JCR](https://doi.org/10.1093/jcr/ucaf040) | 被倾听和关系体验值得优化 | 不证明积分、依赖或长期替代真人关系有益 |
| [Skjuve 等，2022，IJHCS](https://doi.org/10.1016/j.ijhcs.2022.102903) | 关系发展与自我披露、回应的纵向线索 | 访谈样本不构成产品因果实验 |
| [Fang 等，2025，预印本](https://arxiv.org/abs/2503.17473) | 情绪使用、依赖与使用强度要单独观察 | 高使用关联不等于随机分配使用强度的效应 |
| [Cheng 等，2026，Science](https://doi.org/10.1126/science.aec8352) | 迎合可能削弱冲突修复判断；不能只优化“用户喜欢回答” | 不能推出共情和支持性回答都不好 |
| [Emotional Manipulation by AI Companions，预印本](https://arxiv.org/abs/2508.19258) | 结束时的情绪挽留与延长互动可以脱离用户收益 | 不把观察比例外推到所有产品/用户 |
| [Etkin，2016，JCR](https://doi.org/10.1093/jcr/ucv095) | 量化可能改变活动动机 | 非 AI 好感条的直接试验 |
| [Mekler 等，2017，CHB](https://doi.org/10.1016/j.chb.2015.08.048) | 分数与任务产出、内在动机不是同一指标 | 不能以任务产出证明陪伴幸福感 |
| [CoALA，TMLR 2024](https://arxiv.org/html/2309.02427v3) | 工作/情景/语义/程序知识与决策循环 | 概念框架不是长期效果验证 |
| [Generative Agents，UIST 2023](https://arxiv.org/abs/2304.03442) | 观察—反思—计划—行动相连 | 模拟社会可信度不等于真实人类长期福祉 |
| [Hello Again / LD-Agent，NAACL 2025](https://arxiv.org/abs/2406.05925) | 事件感知与用户/角色画像共同服务长期对话 | 不直接解决执行回执、权限和多用户泄露 |
| [Hindsight，2025 原论文](https://arxiv.org/html/2512.12818v1) | 世界事实、经历、综合认识、观点；来源关联与多路召回 | 作者的 benchmark 成绩不能平移到天枢 |
| [HINDSIGHT，ACL 2026 Demo](https://aclanthology.org/2026.acl-demo.27/) | 认识的可检查性与 retain/recall/reflect 接口 | 演示系统不等于完整陪伴产品 |
| [Zep，2025 预印本](https://arxiv.org/abs/2501.13956) | 事实有效时间与系统记录时间、动态关系 | 不必采用图数据库才能实现时间语义 |
| [LongMemEval，ICLR 2025](https://arxiv.org/abs/2410.10813) | 多会话、时间、更新、拒答等评价维度 | 单一记忆分数不证明日程正确 |
| [EverMemBench，2026 预印本](https://arxiv.org/html/2602.01313v3) | 多人多群、归属、更新与 oracle 对照 | 摘要/表格存在差异，不传播未经核对的 26% oracle 说法 |
| [PM-Bench，COLM 2026](https://arxiv.org/html/2607.12385v1) | 延迟意图、事件线索、跨日和变更敏感执行 | 多代理或固定 heartbeat 不是通用解法 |
| [Inner Thoughts，2025 预印本](https://arxiv.org/abs/2501.00383) | 先形成有理由的候选，再决定是否参与 | 不需要持久保存模型隐藏推理；不证明越多内心独白越自然 |
| [Proactive AI: Replannable Dialogue Timelines，2026 预印本](https://arxiv.org/abs/2610.05159) | 未发送行动可随新事件重规划 | 时间线本身不提供授权、幂等或业务验收 |
| [Do Proactive Agents Need an LLM to Decide When to Act?，2026 预印本](https://arxiv.org/abs/2605.30152) | 触发和上下文选择可由专门模型学习 | 专门训练的图模型结果不能证明零样本 Laya 可替代天枢 planner |
| [Sleep-time Compute，2025 预印本](https://arxiv.org/abs/2504.13171) | 将部分准备与推断搬到后台 | 总计算并非免费，论文速度数字不是天枢实测 |

## 6. 社区方案与组件的选择

| 方案 | 当前官方依据 | 决策 |
|---|---|---|
| [Hindsight](https://github.com/vectorize-io/hindsight) | retain/recall/reflect、事实类型、来源与投影 | 借鉴结构，保留天枢已有权限/人物/回执语义，不整套叠加 |
| [Graphiti](https://github.com/getzep/graphiti) | 增量时序图、多种图后端；参考服务器与生产服务边界明确 | 借鉴时间和关系更新；暂不引入新的图库与服务 |
| [Letta memory/dreaming](https://docs.letta.com/configuration/memory) | 当前使用 MemFS 与后台整理；旧 V1 memory blocks 文档为历史接口 | 借鉴分块与增量整理，不把旧 V1 API 当当前唯一实现 |
| [MaiBot 当前变更](https://docs.maibot.chat/en/changelog/) / [模型分工](https://docs.maibot.chat/en/manual/configuration/model-config) | Planner、Replyer、辅助工作分工，当前记忆与插件框架持续演进 | 借鉴角色决策/表达分离，不照搬多次模型调用的成本与时延 |
| [pgvector](https://github.com/pgvector/pgvector) | PG 原生索引、过滤注意点、iterative scan、维度上限 | 采用；先在 SQL 范围内召回，不能忽略 ANN 后过滤问题 |
| [Qwen3 Embedding](https://huggingface.co/Qwen/Qwen3-Embedding-8B) | MRL、不同尺寸、维度与指令配置 | 统一 1024 维 profile；质量必须结合中文实际案例核验 |
| [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | 最高 1024 维、多语言、可变维度 | 本地化首个候选；未实测不能称已适合该旧 GPU |
| [Procrastinate](https://procrastinate.readthedocs.io/en/stable/) | PG 作业、锁、延时/取消、异步；[同事务连接](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html) | 采用作业基础设施，保留领域 action/receipt；不冒充完整工作流平台 |

没有根据 GitHub star 数或厂商宣传给出性能结论。所有框架只吸收天枢需要的机制，不同时安装成平行底座。

## 7. U01–U100 的保留与重整

来源：[requirements-v2.json](C:/YOKI/Codex/tianshu-peiban-bot/docs/product/requirements-v2.json)。旧文档的 owner 名称可变，功能目标不因为换架构被静默删除。

| 原需求 | 目标归属和完整接线 |
|---|---|
| U01–U02 | channels：QQ/TG、私聊与群聊 |
| U03–U05 | companion/life：人格、共享世界、多角色、模拟生活 |
| U06–U16 | identity/memory/people：人物、画像、关系、跨场景连续性与隐私 |
| U17–U18 | capabilities + memory knowledge：联网、资料与角色知识 |
| U19–U26 | life + web：小屋、作息、持续目标、衣物与穿搭 |
| U27–U33 | capabilities/life：阅读、日记、创作、版本与现实/虚构区分 |
| U34–U40 | image capability：ComfyUI、工作流、角色 LoRA 与其他后端 |
| U41–U42 | 独立通话/观影及现实桥适配；不新增网页实时通话 |
| U43–U47 | Chat Audit 外部权威 + sources/memory；原文追溯、工作情境、长期整理 |
| U48–U50 | capability registry + channels：角色能力、GsCore、群娱乐 |
| U51–U56 | 原媒体插件：聚合、元数据、Komga/RSSHub、订阅归档 |
| U57–U65 | 设备/HA/服务器能力，命令和自然语言共用执行者 |
| U66–U68 | 有来源和时效的移动/身体数据，事项关心 |
| U69–U74 | 项目资料、编码工具适配、独立工作目录、经验与上下文预算 |
| U75–U79 | models：命名线路、凭据引用、原生模型映射与有状态调用 |
| U80–U83 | 一个产品体验、合理权限、允许替换旧架构、完整目标优先 |
| U84–U88 | 分层画像/精准检索、语义完整、延迟、防抖与会话共享两轮次 |
| U89–U91 | 工作负载模型配置、项目工具接入、研究知识库 |
| U92–U100 | AssetLibrary 独立资产、已认可视觉、记忆展示、日记书页、小屋、Docker 状态、HA 与网页边界 |

明确命令仍不占陪伴两轮次；多角色共享该会话的轮次上限。后台长任务提交后可释放对话位，最终结果以关联事件返回。合并模块不会取消这些正常使用行为。

## 8. 本次结果的边界

本轮交付研究、完整目标方案和拟议架构图。没有宣称新数据库、统一调度或关系模型已经运行。没有把历史 R53 成绩当成 R57，没有把合成用例称为真实 QQ/模型质量，没有将旧设备的短时推理延迟称为持续吞吐。

实现时需要解决的技术选择已给出主方案；具体库版本、SQL 计划、连接数、token 预算和真实延迟仍需用当时安装环境确定。这是正常实现与验证工作，不是允许只交付半条闭环。

交付检查：总方案、证据索引和模型/人格详案的本地引用已检查；[架构图](C:/YOKI/Codex/tianshu-peiban-bot/.archify/architecture-tianshu-coherent-20261031-030639/tianshu-coherent.html)已同步三进程与动态人格说明，通过 Archify 的 schema、生成、严格产物检查和真实浏览器自动检查，未进行截图人工视觉审阅。保留[候选规格](C:/YOKI/Codex/tianshu-peiban-bot/.archify/architecture-tianshu-coherent-20261031-030639/candidate.json)与[最新检查收据](C:/YOKI/Codex/tianshu-peiban-bot/.archify/architecture-tianshu-coherent-20261031-030639/review-2/tianshu-coherent.finalize-summary.json)。本轮没有产品变更，因此没有运行无关产品测试。

## 9. 模型 并发 代码与人格补充研究

资料：[人格1.txt](C:/Users/Administrator/Downloads/人格1.txt)，共 198 行，完整读取；内容是待分析的角色写作经验，不是执行指令。没有复制整份档案入库，也没有将其设定应用到现有角色。

最新发布依据：[R58](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-repair-r58-2026-10-31.md)、[R67](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-repair-r67-2026-10-31.md)、[PC-17 验收](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-pc17-acceptance-2026-10-31.md)以及 CURRENT 中 R59–R66 的当前记录。12 卡验收、3 卡部署、3 卡继续是该截面；实际 token 与人工具体性验证已有新结果，不沿用初稿的未验证说法。

结构盘点时 Companion `72d2854b5f186426a71e8fb5af9e91e6f0679ed4`、Memory `874b911a0004d652f4f86ad21912b6e80dae03b3`、Platform `21bdb7b9e11e18d47111e656e829edd331a77b14`、Gateway `1f5b2a205a13d84513bc3c04682f3642d579e48c`。静态盘点覆盖四个产品源码目录的 609 个 Python 文件，用于选取阅读热点，不等于逐行全项目审查或业务测试。

| 源码 | 本轮核对 |
|---|---|
| [model_functions.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-platform/services/platform/model_functions.py) | 功能绑定归属清楚；tools/code/search 目录 connected 为 false，需实际接线 |
| [model_selection.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/model_selection.py) | 功能、调用身份、授权期限和单次生成的固定选择 |
| [ModelSlots](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/model_slots.py) | 前台优先、后台限额、有限连续前台发放后后台获得机会 |
| [后台整理模型阶段](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/memory_learning.py:1121) | 本地模型槽内还进行来源和窗口准备；这是持有范围问题，不证明每次线上都发生阻塞 |
| [主对话模型槽](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/core.py:1691) | 槽包住进一步 preflight、书籍 capture 与完整 native 工具循环 |
| [上下文装配](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/context_preparation.py) | 依赖 Core 的多个私有方法，说明拆文件未自动解除耦合 |
| [Gateway scheduler](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-model-gateway/src/tianshu_gateway/scheduler.py) | 单进程容量、供应商限额、前台预留和公平，不能称完全没有前后台调度 |
| [Affect](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/life_affect.py) | 以事件/范围为基础的 valence 与半衰期；后续增强混合情绪、评价与恢复 |

新增研究采用 CAPS、Whole Trait Theory、EMA、ALMA、Gross 的调节框架、MECoT、FAtiMA、InCharacter、CharacterEval/CharacterCraft；工程部分采用 RouteLLM、Sarathi-Serve、Parnas、Python/Psycopg/Procrastinate 官方资料。具体引用与适用限制紧邻[专项详案](C:/YOKI/Codex/tianshu-peiban-bot/docs/research/tianshu-model-concurrency-persona-design-2026-10-31.md)中的相关结论。

MECoT 采用正文 Table 3 而非只看摘要：93.3% 属于 R1 Trained 设置。其 ETCC 平方相关系数会丢失符号，是本轮从公式直接推导的测量限制；没有据此否定整篇论文，也没有把该指标用作天枢的验收目标。

## 10. 网页 论坛 B站和视频理解的范围更新

用户最新要求将这部分纳入完整重构，已写入总方案第 7.5 节与模型功能表。旧记录中的浏览延期不再适用于本次范围；未请求的站外公开发言等动作不因浏览能力而自动放行。

| 实际来源 | 本轮核对到的事实 |
|---|---|
| [内容发现 service.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-platform/services/platform/content_discovery/service.py) | 网页依赖搜索服务和 query；来源枚举含 web/bilibili/music/books/comics，当前该入口无 forum；发现不标记已读 |
| [B 站候选 media.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-platform/services/platform/content_discovery/media.py) | 读取已配置订阅及媒体库、分页产出视频元数据；不能概括成已经实现自主逛 B 站 |
| [网页来源 knowledge_sources.py](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/knowledge_sources.py) | 已有实际 URL 获取、HTML 可见文字及来源控制，可继续演进正文与论坛结构 |
| [视频读取 knowledge_media.py:273](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-memory/src/tianshu_memory/knowledge_media.py:273) | 有界 PyAV 解码，带时间戳帧、音轨与实际字幕；约 7–8 帧；video 返回 complete=false 和抽样/未转录等 gaps |
| [多模态材料 role_actions.py:416](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/role_actions.py:416) | 帧转 image_url、音轨转 input_audio 作为实际附件；仍需核验所选供应商是否真实接受与使用 |
| [Reading:418](C:/YOKI/Codex/tianshu-peiban-bot/projects/tianshu-companion/src/tianshu_companion/reading.py:418) | 有 gaps 的读取不并成完整覆盖；已有暂停、继续及阅读来源/经历接线 |

外部依据：[Google 视频理解官方说明](https://ai.google.dev/gemini-api/docs/video-understanding)提供实际视频、音频、时间戳及采样机制；[Video-MME](https://arxiv.org/abs/2405.21075)分别考察视频时长、多模态和内容理解。采用机制和评价维度，不把历史榜单、厂商性能数字或一个原生 video 参数当作天枢已完成的实测。

本轮没有登录站外账号、下载视频、调用多模态模型或发布站外消息；更新的是完整交付范围和实现设计。

## 11 鲸鱼娘实践与完整施工队列

新增输入为用户提供的[技术说明](C:/Users/Administrator/Downloads/鲸鱼娘私聊系统-技术说明.md)和[插件清单](C:/Users/Administrator/Downloads/插件链接清单.md)。九个公开仓库通过固定 Git 提交静态核对；朋友未公开的本地 adapter 和四个插件没有被当成已审查源码。版本、具体函数、采用机制和限制集中见[鲸鱼娘实践核对](tianshu-whale-practice-review-2026-10-31.md)，避免在此再复制一套事实清单。

本轮重新读取两个指定聊天最新可访问回合、CURRENT 的最新记录及四个产品 HEAD，仍为 R67 相关截面；没有重新扫描所有项目聊天，也没有把活动窗口的观察状态改成全部完成。源码确认天枢已具备 reflection、日记来源、Affect、expression_id、来源/目标复核等基础；新增要求是完整接线和语义重整。

重点新增一手资料为 [MemoryBank](https://arxiv.org/abs/2305.10250)、[Lost in the Middle](https://aclanthology.org/2024.tacl-1.9/) 与 [Horvitz 的混合主动交互论文](https://www.microsoft.com/en-us/research/wp-content/uploads/2016/11/chi99horvitz.pdf)，并继续核对 [Inner Thoughts 正文](https://arxiv.org/html/2501.00383v2)的研究设置和限制。采用分层记忆、选材、注意时机及候选/表达分离机制，不以论文或社区个案证明特定陪伴频率和人格成长效果。

综合设计已更新为[完整改造说明](tianshu-coherent-companion-system-2026-10-31.md)；新增[CR01–CR20 改造队列](tianshu-coherent-companion-refactor-queue-2026-10-31.md)替换早期 A–F 粗分及专项文档中的重复顺序。队列包含依赖、规则归属、输入与消费者、替换删除和必要完成条件；全部为规划状态，没有修改产品代码、共享任务板、部署或真实数据。

## 12 多模态 AstrBot OneBot 与自拍生图

本轮在用户指定的 AstrBot 已登录页面只读确认实际安装版本：AstrBot 4.27.3、陪伴 6.4.5b、表情管理器 4.15.4、ComfyUI 工作流 1.1.2、生图扩展 0.4.1。当前查看的图库为 67 张、未语义化；陪伴面板空白，未声称其实验开关或向量选图已实际运行。浏览器临时令牌未保存到文档。

公开源码读取陪伴 `v6.4.5b` 标签和当前上游，另读表情管理器、图像扩展及固定 OneBot v11 标准。已安装版本、对应标签和最新上游分别注明，不能把上游新功能当作用户部署状态；具体提交与入口见[多模态和自拍详案](tianshu-multimodal-onebot-selfie-design-2026-10-31.md)。

| 天枢源码核对 | 实际意义 |
|---|---|
| [rpc.py:105](../../projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/rpc.py:105) / [sdk.py:109](../../projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/sdk.py:109) | 入站仅文本/引用/普通@完整受理，媒体和@all会使content_state不支持 |
| [rpc.py:146](../../projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/rpc.py:146) / [sdk.py:193](../../projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/sdk.py:193) | 下行已有图片、音频和视频，但没有GIF，结构仍为文字后附媒体 |
| [clients.py:1494](../../projects/tianshu-companion/src/tianshu_companion/clients.py:1494) | Companion主模型调用固定Chat Completions，需真实接通原生Gemini模态 |
| [books/audio/providers.py](../../projects/tianshu-platform/services/platform/media/books/audio/providers.py) | 已有Edge/Fish/兼容speech合成能力，可提取通用部分供BOT复用 |
| [skills/image.py](../../projects/tianshu-companion/src/tianshu_companion/skills/image.py) / [image_prompt.py](../../projects/tianshu-companion/src/tianshu_companion/image_prompt.py) | 自然语言自拍、衣橱/活动/天气提示已有，不按旧images.md误判缺失 |
| [image_provider.py](../../projects/tianshu-companion/src/tianshu_companion/image_provider.py) | 已有提供者接口，三引擎沿此演进 |

一手来源包括 [Gemini 3.8 Flash能力](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash)、[Gemini TTS](https://ai.google.dev/gemini-api/docs/speech-generation)、[OneBot标准](https://github.com/botuniverse/onebot-11/tree/d4456ee706f9ada9c2dfde56a2bcfc69752600e4)、[NapCat兼容表](https://doc.napneko.icu/develop/api)、[ComfyUI API](https://docs.comfy.org/development/comfyui-server/comms_routes)、[NovelAI官方图像API定义](https://image.novelai.net/docs/doc.json)、[GPT Image官方指南](https://developers.openai.com/api/docs/guides/image-generation)。表情研究补充 [STICKERCONV，ACL 2024](https://aclanthology.org/2024.acl-long.417/)的任务设计和评价局限；未将模型合成对话成绩外推为本地图库效果。

完整报告已增加听看、说话、语义表情/管理器、OneBot与自拍场景及三引擎；队列保留20主编号并增加6个专项块，共26块。没有修改产品代码、AstrBot配置或现有图库，没有调用付费/本地生成模型、发消息、执行群管理或部署。

## 13 主动自拍与 Fish Audio 优先接入

### 13.1 用户要求与源码证据

用户明确要求日程和角色自主意图也可调用生图并发送，语音优先支持已购买一年 Plus 的 Fish Audio。购买情况来自用户陈述，未读取其 Fish 账户、API Key、余额或账单。本次研究继续只修改方案文档。

| 来源 | 本次核实 |
|---|---|
| [天枢 runtime_execution.py:690](../../projects/tianshu-companion/src/tianshu_companion/runtime_execution.py:690)、[runtime_photo.py](../../projects/tianshu-companion/src/tianshu_companion/runtime_photo.py) | 已存在主动带图决策、候选/版本绑定生成任务、提交前来源检查、成品等待、原目标投递及完成事件防再触发；不能说天枢完全没有主动自拍 |
| [插件 proactive_engine.py:9645](https://github.com/menglimi/astrbot_plugin_private_companion/blob/30d7ed078daf325f9f449de6e834345d1c6eacb4/proactive_engine.py#L9645) | 安装对应 v6.4.5b 源码按活动/日程/天气/动机选择主动图文动作；未验证用户本机开关或实际调用 |
| [proactive_message.py:3347](https://github.com/menglimi/astrbot_plugin_private_companion/blob/30d7ed078daf325f9f449de6e834345d1c6eacb4/proactive_message.py#L3347)、[9490](https://github.com/menglimi/astrbot_plugin_private_companion/blob/30d7ed078daf325f9f449de6e834345d1c6eacb4/proactive_message.py#L9490)、[9757](https://github.com/menglimi/astrbot_plugin_private_companion/blob/30d7ed078daf325f9f449de6e834345d1c6eacb4/proactive_message.py#L9757) | 主动轮可调用 pc_generate_photo；有实际生成动作、来源/参考/预算处理、当日穿搭图复用；值得吸收行为，不复制其字符串交接和大 Mixin 耦合 |
| [插件 proactive_engine.py:10580](https://github.com/menglimi/astrbot_plugin_private_companion/blob/30d7ed078daf325f9f449de6e834345d1c6eacb4/proactive_engine.py#L10580) | 延后发送的真实图片进入主动统一发送链，说明“模型想发时调用”有实际落点 |
| [天枢 books/audio/providers.py:57](../../projects/tianshu-platform/services/platform/media/books/audio/providers.py:57)、[config.py](../../projects/tianshu-platform/services/platform/media/books/audio/config.py) | 现有 Fish 原生 SSE 时间戳实现和 S2.1/Drama 模型配置可复用；扩展 BOT、MCP、完整参数与容量，不误判为从零实现 Fish |

### 13.2 Fish 官方文档

| 直接来源 | 对方案的影响 |
|---|---|
| [MCP 连接与计费](https://docs.fish.audio/overview/mcp)、[工具目录](https://docs.fish.audio/overview/mcp/tools) | OAuth + Streamable HTTP 使用网页额度；TTS 链接、多片段、音色和文件转写能力；不推断音频流式/时间戳/所有 REST 参数可用 |
| [Plus 套餐](https://fish.audio/plan/) | 当前公开月额度 250,000 credits；开发者 API 独立计费，年费不等于无限 API 或全年额度一次发放；实际账户权益待连接读取 |
| [API 价格与并发](https://docs.fish.audio/developer-guide/models-pricing/pricing-and-rate-limits) | UTF-8 字节而非汉字/LLM token；TTS/ASR 同账户共享并发，长 ASR 占槽；网页容量不能套用 API 数字 |
| [模型](https://docs.fish.audio/developer-guide/models-pricing/models-overview)、[Drama 3](https://docs.fish.audio/features/text-to-speech/drama-3)、[退役](https://docs.fish.audio/developer-guide/models-pricing/deprecations) | 日常原生默认 S2.1-Pro，Drama 可选精细语气；新装不默认 S1/旧转写，不把短期免费模型当年费稳定基础 |
| [TTS 契约](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)、[OpenAPI](https://docs.fish.audio/api-reference/openapi.json) | 原有 with-timestamp 路线仍在；明确 model、音色、语言/归一化、字典、格式与原生控制；避免静默默认模型 |
| [WebSocket TTS](https://docs.fish.audio/api-reference/endpoint/websocket/tts-live) | MessagePack 双向协议，与 MCP/Chat SSE 不同；完整句意缓冲和真实资源释放 |
| [发音词典](https://docs.fish.audio/developer-guide/core-features/fine-grained-control/pronunciation-dictionaries)、[中文音素](https://docs.fish.audio/developer-guide/core-features/fine-grained-control/chinese)、[情绪控制](https://docs.fish.audio/developer-guide/core-features/emotions) | 语义台词与语音编译分离；专名要实际试听，模型方括号不通用等于 SSML |
| [创建音色](https://docs.fish.audio/developer-guide/core-features/creating-models) | voice/model/dictionary 分开；MCP 与 REST 的默认可见性不同，应显式设置所需范围 |
| [文件 ASR](https://docs.fish.audio/api-reference/endpoint/openapi-v1/speech-to-text)、[实时 ASR](https://docs.fish.audio/features/speech-to-text/realtime) | 文件转写本批接入；实时 Preview 的连接时长/并发不同，不为 QQ 文件常驻实时会话 |
| [官方 Python SDK](https://docs.fish.audio/developer-guide/sdk-guide/python/overview)、[更新记录](https://docs.fish.audio/developer-guide/getting-started/changelog) | 采用异步调用，SDK 未同步字段由原生适配覆盖；不为接入 Fish 再引入第二套托管人格/记忆代理 |

官方资料抓取缓存位于 `.runtime/research-fish-20261031/`，包含来源清单与完整 OpenAPI；仅供此次研究，不作为产品代码依赖。独立 AsyncAPI 下载链接返回 404，WebSocket 依据取自官方页面内嵌 schema；未执行供应商示例代码。公开能力与账户实际功能分开，未实测 OAuth 长期刷新、订阅工具参数、中文音色、付费调用或真实 QQ 播放。

### 13.3 设计更新与完整接线

主动自拍采用“实际动机 → 生成前用途/预算 → 场景快照 → 引擎产物 → 当前发送时机 → 投递或暂存 → 有范围反馈”，由 CR10、CR17A/B、CR13 承担。Fish 两种传输共享语音领域，CR03 管资源/账单域，CR13B 实现接入，CR13A 消费转写，CR13 统一表达，CR18 显示真实配置，CR19 补必要联合验证。

队列仍为 26 块；为消除转写接线的隐含依赖，CR13B 排在 CR13A 前，后者显式依赖 Fish 已实现端口。没有新增并行状态机、照片专属任务账本或第二套施工清单。语音、本地生图和主动联系的实机验收尚未进行，不能把本研究完成称为功能已上线。

## 14 插件保留与免费模型定制音色

本次确认原方案已保留有效插件，但此前对安装、SDK、命令/工具、事件与生命周期的施工要求较概括；已将这些作为同一核心重构的显式交付项，写入总方案第 13.2 节和原队列对应卡，不新增施工块。

| 当前直接核对 | 证据含义 |
|---|---|
| [plugins/manager.py](../../projects/tianshu-platform/services/platform/plugins/manager.py)、[packages.py](../../projects/tianshu-platform/services/platform/plugins/packages.py)、[process.py](../../projects/tianshu-platform/services/platform/plugins/process.py) | 已有包与实例生命周期、配置、进程及版本处理；保留行为，存储和受管调度迁入新核心 |
| [SDK api.py](../../projects/tianshu-platform/tianshu_plugin/api.py)、[贡献 schema](../../projects/tianshu-platform/services/platform/plugins/schemas/contributions.schema.json) | 动作、命令、工具、事件和页面结构已有基础；schema 中存在字段不证明全部消费者接通 |
| [actor_bridge.py](../../projects/tianshu-platform/services/platform/plugins/actor_bridge.py)、[actor_capabilities/application.py](../../projects/tianshu-platform/services/platform/actor_capabilities/application.py) | 角色动作绑定、可用性与执行投影已有；新系统通过同一目录接通对话与日程，保留身份边界 |
| [content-discovery](../../projects/tianshu-platform/services/platform/plugins/builtin/content-discovery/plugin.json)、[comic-files](../../projects/tianshu-platform/services/platform/plugins/builtin/comic-files/plugin.json) | 自有能力插件有实际包入口，随新 SDK/PG 一起适配，不因收敛产品而删除功能 |
| [AstrBot main.py](../../projects/tianshu-companion/integrations/astrbot/astrbot_plugin_tianshu/main.py)、[NoneBot adapter_plugin.py](../../projects/tianshu-companion/integrations/nonebot/tianshu_nonebot/adapter_plugin.py) | 外部宿主连接器实际存在；保留真实协议边界，不推断任意生态插件可无修改原生安装 |

[插件说明](../../projects/tianshu-platform/docs/platform/plugins.md)和[角色能力说明](../../projects/tianshu-platform/docs/platform/actor-capabilities.md)用于定位职责；其历史任务/未部署表述不作为当前线上状态依据，本次只核对相关源码，不重新验收线上插件安装。

Fish 官方[免费模型说明](https://fish.audio/blog/s2-1-pro-free-api/)明确免费档支持声音克隆，[TTS 契约](https://docs.fish.audio/api-reference/endpoint/openapi-v1/text-to-speech)支持 reference_id 和参考音频。CR13B、模型配置及详案已显式纳入免费模型/固定音色组合，复用原生适配与 VoiceProfile，不另外建立免费合成器；当前研究没有替用户选择生产默认路线或消耗额度。

本轮仅补文档与队列；没有安装/升级/停用插件、改产品源码、调用 Fish 或操作外部服务。验证范围限文档链接、任务依赖及保留映射，真实插件生命周期与媒体效果由相应施工卡完成。
