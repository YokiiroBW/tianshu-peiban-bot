# 天枢全链路解耦与空库重构任务说明

> 本说明已由 [RB 任务说明](tianshu-clean-rebuild-tasks-2026-11-04.md)承接。SD 编号仅作需求来源，不与 RB 重复派发；下方内容保留为历史候选方案。

全部任务为 planned，未派发、未开始产品实现或清除数据。[主方案](system-decoupling-redesign-plan-2026-11-04.md)解释取舍，[队列 JSON](system-decoupling-redesign-queue-2026-11-04.json)保存依赖、文件责任与旧需求映射。

角色是职责，非已创建窗口/代理。同文件同一负责人串行写入；领域可依据已固定合同先实现，完整装配后再关闭依赖。验证目录只是复用入口，执行者仅选择受影响行为，不将整目录列为默认必跑。

- R：Runtime/共享类型/数据库资源/进程/迁移注册。
- B：Models 请求、尝试、配额与异步提交。
- S：Identity/Sources 场景、原件、证据与授权。
- M：Memory 整理、事实、历史与检索。
- C：Companion 人格、习惯与学习。
- D：Conversation/Channels 接收、取材和表达。
- L：Life 日程、执行与日记。
- X：Capabilities/插件/内容及独立外围任务。
- F：现有前端页面与API客户端。
- I：应用总装配、交付包、共享文档与集成验证。

## SD-01 公共合同、功能保留与数据归属

本卡维护修复归属合同；领域实现按各卡 PF 对照，未标 PF 不表示可删除已有修复。

状态：planned；主责：I；优先级：P1；完成依赖：无。

固定最小引用、场景、请求/结果与模块端口，保留已有功能。

### 文件责任

- I：`docs/development`、`src/tianshu/runtime/product.py`、`tests/contracts`。
- R：`src/tianshu/types`。
- D：`src/tianshu/conversation/ports.py`。

### 交付范围

- 建立现有功能/API/配置保留映射，不把旧requirements的target标记当当前实现状态。
- 定义对象引用、scene_id、request/attempt、消费版本和当前行为资格合同。
- 共享schema注册由R、总装配由I单写，各域提供自己的迁移片段。
- 限定同步必要调用与异步派生消费者，移除跨域私表读写的目标清单。

### 必要验收

- 一个QQ连接一角色、独立订阅和现有外围入口均有明确归属。
- 公共类型不包含领域万能状态或授予权限字段。
- 原ML/LE范围逐项映射，新卡不重复派发。

数据变化：合同与基线schema清单；本卡不预先建所有业务表。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/contracts`。本轮没有执行这些测试。

证据：E29、E05、E26。

## SD-02 场景、原件和可复用证据集合

历史修复/约束：PF-04、PF-10、PF-12；实际行为与用例见文末清单。

状态：planned；主责：S；优先级：P1；完成依赖：SD-01。

原件与授权有单一归属，减少重复来源数组并解决scope与业务身份混淆。

### 文件责任

- S：`src/tianshu/identity`、`src/tianshu/sources`、`tests/core/test_source_memory_stories.py`。
- R：`src/tianshu/runtime/migrations.py`。
- I：`tests/contracts`。

### 交付范围

- 稳定场景身份与当前授权/epoch分离。
- 原件按真实版本存储；证据集合按完整AND/OR/反例语义复用。
- 建立来源到证据集合/派生对象的反向定位。
- 来源失效同步生效，派生清理异步，当前复检不依赖清理进度。
- 请求保存原授权上界/场景引用，执行以原范围与当前权限交集复检，不按当前更宽管理员范围重建旧任务。
- 单来源可直接使用原source/version引用，重复多源才使用共享集合；批读避免N+1，测量关联开销，不能只按JSON体积判断收益。

### 必要验收

- 作者范围变宽不会制造另一份日记或授予新权限。
- 集合复用不合并不同私域身份。
- 遗忘/撤回后旧投影和缓存不能读回。
- 完整来源依赖不因支持子集或去重缩小。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/core/test_source_memory_stories.py`、`tests/contracts`。本轮没有执行这些测试。

证据：E02、E27、E10。

## SD-03 事务通知与独立消费者

历史修复/约束：PF-03、PF-12；实际行为与用例见文末清单。

状态：planned；主责：R；优先级：P1；完成依赖：SD-01。

用现有队列的可靠通知取代生产事务中执行消费者业务。

### 文件责任

- R：`src/tianshu/runtime/jobs.py`、`src/tianshu/runtime/schema.sql`、`tests/runtime`。
- I：`src/tianshu/runtime/product.py`、`tests/core/test_incremental_consumers.py`。
- M：`src/tianshu/memory/service.py`。
- C：`src/tianshu/companion/growth.py`。
- L：`src/tianshu/life/diary_automation.py`。

### 交付范围

- 事务内业务提交与通知入队，载荷只含稳定对象/事件引用。
- 各消费者独立落库、幂等标记及失败续跑。
- 区分可合并脏通知与必须逐项消费的事实/命令。
- 消费游标处理倒序提交、版本缺口和重启，复用已有cursors，不另建第二总线。

### 必要验收

- Growth/日记消费者异常不回滚已提交Episode。
- 重复/乱序/倒序提交不漏消费或重复副作用。
- pending唤醒合并不误取消doing。
- 权限失效仍在原读取/提交入口立即受控。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/runtime`、`tests/core/test_incremental_consumers.py`。本轮没有执行这些测试。

证据：E05、E06、E14、E28。

## SD-04 Models异步提交和唯一冻结请求

历史修复/约束：PF-01、PF-02、PF-03、PF-05、PF-13、PF-14；实际行为与用例见文末清单。

状态：planned；主责：B；优先级：P1；完成依赖：SD-01、SD-03。

后台等待释放业务worker；冻结请求与物理尝试统一归Models。

### 文件责任

- B：`src/tianshu/models`、`tests/models`。
- M：`src/tianshu/memory/steps.py`。
- D：`src/tianshu/conversation/generation.py`、`tests/conversation/test_generation_recovery.py`。
- R：`src/tianshu/runtime/migrations.py`。

### 交付范围

- 增加持久接受的submit/status/result/cancel，复用唯一Broker和attempt ledger。
- 完整请求只保存一次，领域持有引用，精确摘要与原参数可核对。
- 完成结果与持久通知可靠关联，重启有界补偿未消费通知。
- 前台流式继续使用同一结果身份；纯计算重试与外部效果恢复分开。

### 必要验收

- RPC断开后按同一attempt查证，不重复提交。
- 等待不占业务worker或DB连接，物理执行仍占配额。
- 迟到成功与新尝试最多一次应用。
- 取消与unknown不改成假成功，排队不扣执行时间。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：这是必要的公共调用链变更，须覆盖实际消费者；不保留多个冻结请求权威。

复用验证位置：`tests/models`、`tests/conversation/test_generation_recovery.py`。本轮没有执行这些测试。

证据：E03、E04、E15、E17、E10、E11。

## SD-05 前后台进程与共享资源隔离

历史修复/约束：PF-01、PF-12；实际行为与用例见文末清单。

状态：planned；主责：R；优先级：P1；完成依赖：SD-01、SD-04。

把前台接收/控制和后台计算的故障及资源竞争限制在明确边界。

### 文件责任

- R：`src/tianshu/runtime/process.py`、`src/tianshu/runtime/supervisor.py`、`src/tianshu/runtime/database.py`、`src/tianshu/runtime/execution.py`、`src/tianshu/runtime/config.schema.json`、`tests/runtime`。
- I：`src/tianshu/runtime/product.py`、`tests/deployment`。

### 交付范围

- 同镜像四职责进程，前后台worker消费不重叠队列。
- 复用35逻辑通道与Models调度，数据库/HTTP/线程总预算独立约束。
- 控制和真实到期操作保留可用资源，后台按年龄推进。
- 前后台健康分别暴露，单后台进程故障单独恢复。
- 模块start/stop按进程角色装配，未配置可选能力仅影响自身可用性；唯一连接拥有者不因多进程重复启动。

### 必要验收

- 后台退出不关闭QQ输入、取消或前台发送。
- 一个QQ连接唯一所有者、Broker唯一。
- 混合负载下没有连接预算叠加失控。
- 常驻内存增加与混合负载收益都有记录，不按进程数猜收益。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：共享DB/网络仍有资源竞争；不同功能独立进展不等于无限资源或零相互影响。

复用验证位置：`tests/runtime`、`tests/deployment`。本轮没有执行这些测试。

证据：E07、E17、E18、E26。

## SD-06 输入接收与消息单元

历史修复/约束：PF-13；实际行为与用例见文末清单。

状态：planned；主责：D；优先级：P1；完成依赖：SD-01、SD-02、SD-03。

先可靠保存原事件和可用部分，保持消息/引用/来源身份。

### 文件责任

- D：`src/tianshu/channels/service.py`、`src/tianshu/conversation/service.py`、`src/tianshu/conversation/codec.py`、`src/tianshu/conversation/types.py`、`tests/channels`、`tests/conversation/test_turn_rejections.py`。

### 交付范围

- 不可用媒体按部分覆盖接收，保留原事件回复所有权。
- 消息在上下文选择前保留event/turn/reply/source/version。
- 输入入账与后续Memory/Growth消费解耦。
- 恢复仅推进原输入处理，不产生伪造新消息。

### 必要验收

- 混合文本/坏附件仍保存文本且缺项准确。
- 重复输入不重复回复。
- 撤回和纯不可用附件保持真实状态。
- 跨群/跨角色不串消息。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/channels`、`tests/conversation/test_turn_rejections.py`。本轮没有执行这些测试。

证据：E01、E11、E27。

## SD-07 Memory当前状态、语义版本与检索投影

历史修复/约束：PF-10、PF-11；实际行为与用例见文末清单。

状态：planned；主责：M；优先级：P1；完成依赖：SD-01、SD-02。

分离语义数据、处理进度与索引，建立窄的可重建检索文档。

### 文件责任

- M：`src/tianshu/memory/schema.sql`、`src/tianshu/memory/types.py`、`src/tianshu/memory/service.py`、`src/tianshu/memory/revisions.py`、`src/tianshu/memory/index_jobs.py`、`src/tianshu/memory/indexing.py`、`tests/memory`、`tests/core/test_memory_revision_domain.py`。
- R：`src/tianshu/runtime/migrations.py`。

### 交付范围

- Claim/Matter保留类型化head/revision，Episode语义版本与提取进度分离。
- 当前search_documents按对象版本/profile唯一，完整业务记录仍为权威。
- 去掉索引/游标更新触发的语义修订和重复重算。
- 热点字段结构化，正文/证据引用与处理状态分开，新schema压平。
- 新search_documents替换原多处向量索引职责；按时间语义保留可检索版本，当前partial索引与历史读取明确区分，不在旧索引外再叠一套常驻副本。

### 必要验收

- current/valid_at/known_at语义保持。
- 索引落后能词法/直接读取且有freshness。
- 投影可重建，来源失效及时阻止读取。
- 元数据变动不无故重算同语义向量。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/memory`、`tests/core/test_memory_revision_domain.py`。本轮没有执行这些测试。

证据：E10、E23、E24。

## SD-08 共享整理、局部引用和增量算法

历史修复/约束：PF-02、PF-03、PF-11；实际行为与用例见文末清单。

状态：planned；主责：M；优先级：P1；完成依赖：SD-03、SD-04、SD-07。

一次共享理解保留多人归属，减少重复材料和机械协议失败。

### 文件责任

- M：`src/tianshu/memory/materials.py`、`src/tianshu/memory/proposal.py`、`src/tianshu/memory/extraction_contract.py`、`src/tianshu/memory/consolidation.py`、`src/tianshu/memory/consolidation_retry.py`、`src/tianshu/memory/preparation.py`、`tests/core/test_consolidation_pipeline.py`、`tests/memory/test_consolidation_contract.py`。

### 交付范围

- prepare/submit/wait/apply短阶段，原完整源集合与实际支持分别保存。
- 输出感知分段，确定材料范围后执行必要远端候选召回。
- 按目标局部别名限定，复用原编译/严格事务校验。
- 只对语义变化处理；精确重复与相似/矛盾/重复事件分开。

### 必要验收

- changed/no_change/unresolved完整覆盖，低频参与者保留。
- 不同目标n1不冲突，目标内歧义拒绝。
- 输出截断不部分提交，未知不堵所有分区。
- 少调用不冒称低token或低费用。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/core/test_consolidation_pipeline.py`、`tests/memory/test_consolidation_contract.py`。本轮没有执行这些测试。

证据：E01、E03、E16、E24。

## SD-09 检索、重排与向量计算复用

历史修复/约束：PF-03、PF-10；实际行为与用例见文末清单。

状态：planned；主责：M；优先级：P1；完成依赖：SD-04、SD-07。

在窄投影上先候选后正文，按需要调用远端计算。

### 文件责任

- M：`src/tianshu/memory/candidates.py`、`src/tianshu/memory/query_plan.py`、`src/tianshu/memory/retrieval.py`、`src/tianshu/memory/retrieval_budget.py`、`src/tianshu/memory/embedding.py`、`src/tianshu/memory/indexing.py`、`tests/core/test_memory_evidence_retrieval.py`、`tests/memory/test_retrieval_degradation.py`。

### 交付范围

- 结构过滤、中文完整词项与受控回退，词法/向量预算内并行。
- 候选只读必要字段，批量来源证明和最终复检。
- 延续RRF/MMR、ANN补位；精确查询避免无益重排。
- 请求内向量/计量复用，同文本/profile索引复用，跨请求缓存按收益取舍。

### 必要验收

- 人物/否定/时间/群范围召回质量不退化。
- 某支路不可用不抹掉其他有效证据。
- 当前与历史/list严格区分。
- 共享正文不等于共享权限或任意共享向量。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/core/test_memory_evidence_retrieval.py`、`tests/memory/test_retrieval_degradation.py`。本轮没有执行这些测试。

证据：E23、E24、E18。

## SD-10 习惯规则、纠正与运行投影

历史修复/约束：PF-03、PF-11；实际行为与用例见文末清单。

状态：planned；主责：C；优先级：P1；完成依赖：SD-01、SD-02、SD-03。

当前行为由一个规则权威负责，学习提案与支持可追溯。

### 文件责任

- C：`src/tianshu/companion`、`tests/companion`。
- M：`src/tianshu/memory/evidence.py`、`src/tianshu/memory/tools.py`。
- I：`tests/core/test_incremental_consumers.py`。

### 交付范围

- 稳定规则head/revisions，持续明确偏好/临时要求/推断分开。
- 运行取材脱离管理view，先筛选active/用途/对象再限量补位。
- 支持、完整依赖、反例、反馈分开；历史反思带行为关联。
- 明确命令及时修订，语义失效与仅支持变化区分。

### 必要验收

- 撤回不能借Memory/工具/Life重新生效。
- 41条有效规则不被前40条失效记录挡住。
- 原文/日记/机器人自用不重复增加支持。
- 自然语言当次遵守与持久提交状态区分。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/companion`、`tests/core/test_incremental_consumers.py`。本轮没有执行这些测试。

证据：E08、E05、E10。

## SD-11 按用途并行取材与上下文预算

历史修复/约束：PF-05、PF-10、PF-14；实际行为与用例见文末清单。

状态：planned；主责：D；优先级：P1；完成依赖：SD-06、SD-09、SD-10。

当前对话依赖优先，去掉无关聚合和反复整包计量。

### 文件责任

- D：`src/tianshu/conversation/context`、`src/tianshu/conversation/service.py`、`tests/context`。
- R：`src/tianshu/types/context_materials.py`。
- L：`src/tianshu/life/planning_engine.py`。
- M：`tests/core/test_memory_evidence_retrieval.py`。

### 交付范围

- 相互独立的Memory/规则/Life小状态在资源预算内并行。
- 来源跟随完整消息单元，最终只汇总真实直接依赖并保留衍生闭包。
- 单元token估算缓存+最终整包校验，不依赖分词可加假设。
- 冻结request/round/attempt取材清单和版本，当前未整理输入参与理解。

### 必要验收

- 刚才那个所需上文不被可选样例挤掉。
- 必要来源失效仍阻止不当使用，未采用且未参与衍生计算的候选不阻断回复。
- 同kind多规则版本不覆盖。
- 不新增前台评价LLM，质量与完整token用量分别比较。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/context`、`tests/core/test_memory_evidence_retrieval.py`。本轮没有执行这些测试。

证据：E08、E11、E12。

## SD-12 Life对象模型与统一执行资格

历史修复/约束：PF-04、PF-06、PF-08、PF-09；实际行为与用例见文末清单。

状态：planned；主责：L；优先级：P1；完成依赖：SD-01、SD-02。

分开目标、活动意图、实际步骤和注意力时间，统一续接资格。

### 文件责任

- L：`src/tianshu/life/schema.sql`、`src/tianshu/life/types.py`、`src/tianshu/life/state.py`、`src/tianshu/life/day_plan.py`、`src/tianshu/life/planner.py`、`src/tianshu/life/continuation.py`、`tests/life`。
- R：`src/tianshu/runtime/migrations.py`。

### 交付范围

- 活动生命周期、typed wait和步骤回执分离；checkpoint按实际活动类型组织。
- 同一资格函数供候选构建/应用/执行使用。
- 目标有效性、保护期、用户暂停、来源和未知结果进入资格。
- 日记day-session和作者业务身份独立于调用方scope哈希。

### 必要验收

- inactive goal对应旧活动不作为可选续接候选。
- 空checkpoint可解释且不导致页面500。
- 保护期内不反复恢复/暂停或增长无用版本。
- 外部未知步骤不被伪装成未开始。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：生命周期、等待与尝试不能合成一个万能state；减少语义重叠而非只减少枚举数量。

复用验证位置：`tests/life`。本轮没有执行这些测试。

证据：E09、E19、E21、E13、E02。

## SD-13 滚动规划与确定性时间安排

历史修复/约束：PF-06、PF-07、PF-08、PF-09；实际行为与用例见文末清单。

状态：planned；主责：L；优先级：P1；完成依赖：SD-04、SD-12。

模型提出有依据意图，程序保持承诺、作息和有效安排。

### 文件责任

- L：`src/tianshu/life/planning_engine.py`、`src/tianshu/life/planner.py`、`src/tianshu/life/day_plan.py`、`src/tianshu/life/opportunities.py`、`src/tianshu/life/directions.py`、`tests/life`。

### 交付范围

- 冻结当前可执行候选，模型选短引用/新意图，程序生成身份与时间。
- 先强约束过滤再稳定排序，复用现有next_gap和语义指纹。
- 只重排受影响时间段；远期意向与近期可执行步骤区分。
- 按依赖成分校验提交，失效候选有限修正，失败保留既有有效计划。
- 规划准备只读轻量能力目录；远端可用性后台刷新并标明时效，真实派发仍复检，不每次规划遍历全部外部探针。

### 必要验收

- 单个过期候选不清空全天，也不部分应用相依目标/活动。
- 合法留白与规划拒绝分开。
- 不恢复每日次数/固定排程占比限制。
- 旧计划过期或材料变更时不照旧执行。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：不放宽来源/目标/能力校验换成功率；格式修复和刷新过期输入分开。

复用验证位置：`tests/life`。本轮没有执行这些测试。

证据：E01、E09、E19、E20。

## SD-14 到期唤醒、资源等待与活动推进

历史修复/约束：PF-01、PF-06、PF-07、PF-08、PF-09；实际行为与用例见文末清单。

状态：planned；主责：L；优先级：P1；完成依赖：SD-03、SD-05、SD-12、SD-13。

必要事件和真实时间边界驱动执行，等待不占角色全局锁。

### 文件责任

- L：`src/tianshu/life/clock.py`、`src/tianshu/life/events.py`、`src/tianshu/life/worker.py`、`src/tianshu/life/planning_engine.py`、`src/tianshu/life/generation_control.py`、`tests/life`。
- R：`src/tianshu/runtime/jobs.py`、`tests/runtime`。

### 交付范围

- 下次due取承诺/时段/事件/重试/必要复查最早值。
- 按业务对象合并脏唤醒，保留正在执行之后一个后继。
- 角色等待外部图像等结果时释放不必要注意力占用。
- 只按真实回执推进，用户暂停/保护期与恢复共用资格。

### 必要验收

- 无变化不反复调用规划模型。
- 到期提醒不被后台LLM排队挡住可执行阶段。
- 同角色独立活动的后台结果正常推进，同一动作不重复。
- 跨日/时区/重启/事件乱序不丢承诺。
- 保护期重复唤醒不新增步骤或空增语义版本，暂停时作息显示继续推进；PF-08未覆盖部分明确补齐或复用证据。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/life`、`tests/runtime`。本轮没有执行这些测试。

证据：E13、E14、E17、E20、E21。

## SD-15 独立反思、经历与日记闭环

历史修复/约束：PF-02、PF-03、PF-04、PF-05、PF-11、PF-14；实际行为与用例见文末清单。

状态：planned；主责：L；优先级：P1；完成依赖：SD-03、SD-04、SD-10、SD-12。

经历变化分别驱动反思和日记，任一失败不阻止另一功能。

### 文件责任

- L：`src/tianshu/life/diaries.py`、`src/tianshu/life/diary_automation.py`、`src/tianshu/life/diary_materials.py`、`src/tianshu/life/diary_retry.py`、`tests/life/test_diary_automation.py`。
- C：`src/tianshu/companion/growth.py`、`src/tianshu/companion/growth_materials.py`、`src/tianshu/companion/growth_observations.py`、`tests/companion/test_growth_retry.py`。

### 交付范围

- 消费真实结果语义视图，内部补查/反思/日记不变成新独立证据。
- 每角色/日期/功能独立请求，不继承上次聊天或失败上下文。
- 手动/自动使用同day-session，正文成功时业务提交直接更新权威状态。
- 暂时纯计算失败释放活动位，按原尝试观察或独立重试。

### 必要验收

- 一角色日记失败不阻止其他角色/日期/记忆。
- 作者scope宽于闭日scope时正确匹配同一对象。
- 已保存正文状态对账不重新生成。
- 未知/partial真实进入材料，不能改写为完成。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/life/test_diary_automation.py`、`tests/companion/test_growth_retry.py`。本轮没有执行这些测试。

证据：E02、E04、E05、E22。

## SD-16 表达、发送与业务结果收敛

历史修复/约束：PF-13；实际行为与用例见文末清单。

状态：planned；主责：D；优先级：P1；完成依赖：SD-03、SD-04、SD-11、SD-14。

冻结取材、表达和真实外部回执关联，纠正失效只影响未提交部分。

### 文件责任

- D：`src/tianshu/conversation/expression.py`、`src/tianshu/conversation/rendering.py`、`src/tianshu/conversation/results.py`、`src/tianshu/channels/delivery.py`、`src/tianshu/channels/service.py`、`tests/conversation`、`tests/channels`。
- X：`src/tianshu/capabilities`。
- I：`src/tianshu/runtime/product.py`。

### 交付范围

- 实际选中规则语义依赖沿expression/action引用传递。
- 结果/渲染释放/未提交发送边界做必要批量复检。
- 已提交或unknown外部效果只沿原身份对账。
- 业务消费和派生页面事件在领域提交时一起登记。

### 必要验收

- 支持新增不取消回复，实质撤回影响依赖该规则的未提交表达。
- 等待语音/发送期间变化仍可处理。
- 多段部分送出不能记成全未发生。
- 模型成功、业务应用、发送、送达不混为一态。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/conversation`、`tests/channels`。本轮没有执行这些测试。

证据：E25、E11、E03。

## SD-17 按需表达样例与群词义

历史修复/约束：PF-11；实际行为与用例见文末清单。

状态：planned；主责：C；优先级：P1；完成依赖：SD-08、SD-10、SD-11。

保留LE完整功能，采用既有学习和Memory通路。

### 文件责任

- C：`src/tianshu/companion`、`tests/companion`。
- M：`src/tianshu/memory/materials.py`、`src/tianshu/memory/revisions.py`、`src/tianshu/memory/service.py`、`tests/core/test_memory_evidence_retrieval.py`。
- D：`src/tianshu/conversation/service.py`。

### 交付范围

- 样例按真实turn/回复/合并关系关联，只保留必要原文引用。
- 群词义复用真实group主体和Claim修订，区分speaker/assertion。
- 当前输入匹配时有界取材，规则足够时零样例。
- 来源/规则撤回与负反馈同步停止当前采用。

### 必要验收

- 多人相邻发言不误配对，私聊样例不越域。
- 单人解释不升级群共识。
- 样例中的旧事实不进入当前答案。
- 不按每词/每样例新增模型调用。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/companion`、`tests/core/test_memory_evidence_retrieval.py`。本轮没有执行这些测试。

证据：E08、E16、E27。

## SD-18 外围功能独立任务与能力接线

历史修复/约束：PF-09、PF-12；实际行为与用例见文末清单。

状态：planned；主责：X；优先级：P1；完成依赖：SD-01、SD-03、SD-04、SD-05。

外围媒体、插件、订阅和基础设施能独立运行并提供真实结果。

### 文件责任

- X：`src/tianshu/capabilities`、`tests/capabilities`。
- R：`src/tianshu/runtime/media.py`、`src/tianshu/runtime/comics.py`。
- I：`src/tianshu/runtime/product.py`、`tests/integration`。

### 交付范围

- 保留现有插件/媒体/阅读/项目知识等公共能力，不以重构删功能。
- 订阅/管理任务保持actor可空，不依赖BOT对话/Life状态。
- 长外部等待采用提交/结果回调及真实资源许可。
- 错误和能力可用性通知只影响关联任务，不全局重规划。

### 必要验收

- 未配置引擎准确不可用，不用占位成功。
- 独立订阅无需角色在线。
- 共享GPU/账号限额不因不同模块重复分配。
- 后台长任务不会堵控制通道。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/capabilities`、`tests/integration`。本轮没有执行这些测试。

证据：E26、E29、E17。

## SD-19 页面读模型与状态解释

历史修复/约束：PF-04；实际行为与用例见文末清单。

状态：planned；主责：F；优先级：P1；完成依赖：SD-07、SD-10、SD-12、SD-15、SD-16、SD-17、SD-18。

现有页面消费真实统一结果，减少重复聚合和矛盾状态。

提前准备：在 SD-01 合同确定后准备实现/样例，不提前标记完成。

### 文件责任

- F：`web/src`、`web/tests`。
- D：`src/tianshu/conversation/module.py`。
- C：`src/tianshu/companion/module.py`。
- M：`src/tianshu/memory/module.py`。
- L：`src/tianshu/life/module.py`。
- B：`src/tianshu/models/management.py`。

### 交付范围

- 保留已认可页面结构和完整功能，按新接口接线。
- 业务/尝试/效果/投影freshness分开展示，主要状态由领域权威计算。
- 后台进度按实际版本事件刷新，列表有界分页不拉所有原文。
- 空、加载、未配置、unknown及部分完成均可正常操作。

### 必要验收

- 日记正文成功不继续展示另一权威的旧失败。
- 已装入不显示为已经改善效果。
- 请求未知不自动重发写命令。
- 桌面/窄屏和已有作者操作完整。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`web/tests`。本轮没有执行这些测试。

证据：E02、E08、E11、E29。

## SD-20 全链路计量与代表性评估材料

本卡维护修复归属合同；领域实现按各卡 PF 对照，未标 PF 不表示可删除已有修复。

状态：planned；主责：I；优先级：P1；完成依赖：SD-04、SD-05、SD-09、SD-11、SD-13、SD-19。

让耗时、质量和真实结果可归因，避免只看队列清空。

提前准备：在 SD-01 合同确定后准备实现/样例，不提前标记完成。

### 文件责任

- R：`src/tianshu/runtime/diagnostics.py`、`src/tianshu/runtime/events.py`。
- I：`scripts/evaluation/memory_closure`、`docs/development`、`tests/integration`。

### 交付范围

- 阶段计量区分DB等待/取材/队列/执行/首个有效输出/完整发送。
- 冻结同质量对照故事，按故事家族隔离保留材料。
- 准备前后台混合负载、进程故障、重启及长模型排队场景。
- 复用完整日志持久链，先测fsync/序列化再决定是否改批处理，不删日志。

### 必要验收

- 失败/取消/unknown包含在分母和费用范围。
- 单查询提速没有掩盖来源/召回退化。
- 日程有效应用与真实执行分开计数。
- 无未经测量的省钱/提速比例，既有成功检查复用。

数据变化：仅必要阶段字段与计量关联，不建第二日志数据库。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`scripts/evaluation/memory_closure`、`tests/integration`。本轮没有执行这些测试。

证据：E04、E07、E12、E17、E18。

## SD-21 新空库安装与单写者切换材料

历史修复/约束：PF-13；实际行为与用例见文末清单。

状态：planned；主责：I；优先级：P1；完成依赖：SD-02、SD-03、SD-04、SD-05、SD-18、SD-19。

交付压平后的新安装，不背负旧业务数据迁移和兼容。

提前准备：在 SD-01 合同确定后准备实现/样例，不提前标记完成。

### 文件责任

- I：`deploy`、`docs/development`、`tests/deployment`。
- R：`src/tianshu/runtime/migrations.py`。

### 交付范围

- 各域baseline schema注册，新库初始化；Procrastinate使用原库安装机制。
- 删除旧协议/旧状态兼容的生产启动分支，旧代码只作源码历史。
- 可选按字段导出作者配置，旧业务/会话/日程不自动续接。
- 编写旧写者停止、入站边界、外部已提交任务隔离及唯一入口切换方案。

### 必要验收

- 新空库真实可启动，功能所需配置缺失有明确状态。
- 不会因新库无账本而重放旧QQ/外部副作用。
- 清理清单限于已核实天枢资源。
- 代码回退不靠全库回滚覆盖新数据。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：用户已允许弃数据；本规划不执行删除。实际切换需严格限定天枢资源及旧外部请求边界。

复用验证位置：`tests/deployment`。本轮没有执行这些测试。

证据：E29、E03、E25。

## SD-22 完整装配与必要集中验收

历史修复/约束：PF-01、PF-02、PF-03、PF-04、PF-05、PF-06、PF-07、PF-08、PF-09、PF-10、PF-11、PF-12、PF-13、PF-14；实际行为与用例见文末清单。

状态：planned；主责：I；优先级：P1；完成依赖：SD-01、SD-02、SD-03、SD-04、SD-05、SD-06、SD-07、SD-08、SD-09、SD-10、SD-11、SD-12、SD-13、SD-14、SD-15、SD-16、SD-17、SD-18、SD-19、SD-20、SD-21。

把领域实现收为一个完整功能包，验证本次改变的边界。

提前准备：在 SD-01 合同确定后准备实现/样例，不提前标记完成。

### 文件责任

- I：`docs/development`、`src/tianshu/runtime/product.py`、`tests/integration`、`tests/deployment`。
- R：`tests/runtime`。
- B：`tests/models`。
- M：`tests/memory`。
- C：`tests/companion`。
- D：`tests/conversation`、`tests/channels`。
- L：`tests/life`。
- X：`tests/capabilities`。
- F：`web/tests`。

### 交付范围

- 收齐源码/合同/页面/安装包，单写者串行装配。
- 复用未变成功结果，集中运行受影响行为、混合资源及恢复场景。
- 实际模型/QQ/引擎动作按后续实施授权执行并独立记录。
- 记录实现、集成、验证、部署和实际效果各阶段，不以文件存在宣布完成。

### 必要验收

- 22卡交付与旧ML/LE功能映射无缺口。
- 来源/CAS/未知、日程资格、消费者隔离和空库切换均有必要证据。
- 没有因精简删除有效功能或遗留旧兼容当前写入器。
- 对应历史修复行为在新结构中保留，受影响PF回归与装配边界有实际证据；旧通过结果不直接继承到变更实现。
- 明确未配置/未触发/未验证项，不追加无关全量门禁。

数据变化：本卡在新空库结构上实现；不迁移旧业务数据。

风险与边界：保留原来源、版本和物理尝试边界；下游投影不成为第二权威。

复用验证位置：`tests/integration`、`tests/deployment`。本轮没有执行这些测试。

证据：E01、E02、E03、E13、E29。

## 旧任务映射

| 原任务 | 新范围 |
|---|---|
| ML-01 | SD-01、SD-02、SD-11 |
| ML-02 | SD-06 |
| ML-03 | SD-08 |
| ML-04 | SD-07、SD-08、SD-15、SD-20 |
| ML-05 | SD-03、SD-10、SD-16 |
| ML-06 | SD-06、SD-11、SD-16 |
| ML-07 | SD-10 |
| ML-08 | SD-08 |
| ML-09 | SD-09 |
| ML-10 | SD-07、SD-09 |
| ML-11 | SD-07 |
| ML-12 | SD-17 |
| ML-13 | SD-17 |
| ML-14 | 允许弃现有数据后不实施旧缺口补偿；面向未来的新任务恢复进入 SD-03/04/08/15。 |
| ML-15 | SD-19、SD-20 |
| ML-16 | SD-20、SD-21、SD-22 |
| LE-01 | SD-10、SD-11、SD-16 |
| LE-02 | SD-10 |
| LE-03 | SD-17 |
| LE-04 | SD-17 |
| LE-05 | SD-01、SD-11、SD-19、SD-20 |
| LE-06 | SD-20、SD-22 |

## 源码与故障证据

源码根：`C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot`。协调根：`C:/YOKI/Codex/tianshu-peiban-bot`。历史修复状态与当前源码观察分别标注，运行记录未重新实机核验。

| 编号 | 根 | 路径与定位 | 用途 |
|---|---|---|---|
| E01 | coordination | `docs/development/CURRENT.md:1` | 13:33 三处未恢复业务问题及运行记录；本轮未访问生产 |
| E02 | source | `docs/development/nr06-diary-and-computation-retry-2026-11-03.md:53` | 日记正文成功但scope关联状态未收敛，已在历史修复 |
| E03 | source | `docs/development/runtime-recovery-closure-2026-11-03.md:5` | RPC包装未知和短引用/重复提案故障及其修复 |
| E04 | source | `docs/development/natural-runtime-closure-2026-11-02.md:1` | 排队/执行计时、恢复与真实工作闭环的既有修复 |
| E05 | source | `src/tianshu/runtime/product.py:132` | Episode事务内调用两个消费者 |
| E06 | source | `src/tianshu/memory/service.py:52` | Episode观察者沿原事务执行 |
| E07 | source | `src/tianshu/runtime/process.py:202` | 多个队列worker同进程及统一退出收尾 |
| E08 | source | `src/tianshu/companion/growth.py:540` | 管理view与material运行取材共用读取 |
| E09 | source | `src/tianshu/life/day_plan.py:47` | 续接条件与unfinished候选范围；需结合planner最终校验 |
| E10 | source | `src/tianshu/memory/schema.sql:1` | 请求/经历/事实/反思及来源数组的当前物理结构 |
| E11 | source | `src/tianshu/conversation/schema.sql:1` | 生成请求、turn、expression与计划的当前结构 |
| E12 | source | `src/tianshu/conversation/context/assembly.py:269` | 逐候选完整计量与后续历史预算 |
| E13 | coordination | `docs/development/proactive-companion-repair-r67-2026-10-31.md:3` | 历史保护期恢复/暂停循环；已有修复须保留 |
| E14 | source | `src/tianshu/runtime/jobs.py:138` | 事务入队、pending合并及其语义 |
| E15 | source | `src/tianshu/models/client.py:76` | generate经RPC等待结果，取消与原结果边界 |
| E16 | source | `src/tianshu/memory/proposal.py:67` | 现有按目标提案编译，可复用局部引用处理 |
| E17 | source | `src/tianshu/models/scheduler.py:47` | 现有公平资源调度及unknown保留 |
| E18 | source | `src/tianshu/runtime/database.py:41` | 已有业务/控制/同步连接预算 |
| E19 | source | `src/tianshu/life/planner.py:393` | 续接活动最终资格校验 |
| E20 | source | `src/tianshu/life/planning_engine.py:72` | 已有语义指纹与重规划机制 |
| E21 | source | `src/tianshu/life/schema.sql:29` | 活动检查点、目标、日程与日记当前数据模型 |
| E22 | source | `src/tianshu/life/diary_automation.py:39` | 日记材料与自动状态拥有独立scope键 |
| E23 | source | `src/tianshu/memory/candidates.py:69` | 多类型当前/历史候选读取 |
| E24 | source | `src/tianshu/memory/index_jobs.py:246` | 向量回写各类业务对象 |
| E25 | source | `src/tianshu/channels/delivery.py:139` | 已提交边界及既有发送前检查 |
| E26 | source | `src/tianshu/runtime/execution.py:9` | 现有35个业务通道 |
| E27 | source | `src/tianshu/sources/schema.sql:1` | 原件、支持组、遗忘及来源变更 |
| E28 | source | `src/tianshu/runtime/schema.sql:1` | job keys、commands与projection cursors已有结构 |
| E29 | source | `docs/development/coherent-refactor-execution-2026-10-31.md:35` | 现有功能、公开API及唯一Broker等已落地基础约定 |


## 已修复行为与现有回归入口

本轮只核对修复来源、实现入口和用例定义，没有运行产品测试。下列函数是复用定位，不要求默认执行整个文件/目录。历史旧格式兼容可按空库路线退出，但有效行为断言须迁移到新结构；发生真实行为变化的链路不能拿旧版通过记录代替新检查。

### PF-01 排队与执行时钟

类别：历史修复。任务：SD-04、SD-05、SD-14。

必须保留：排队不扣执行时长；实际执行开始后固定时钟；真实业务截止仍生效，重启/查询不能刷新截止。

修复/约束记录：source 根的 `docs/development/natural-runtime-closure-2026-11-02.md`。

当前实现入口：`src/tianshu/models/timing.py`、`src/tianshu/models/scheduler.py`、`src/tianshu/models/ledger.py`。

已有用例：

- `tests/models/test_execution_timing.py::test_queue_wait_may_outlast_execution_window_and_stays_cancellable_and_bounded`。
- `tests/models/test_execution_timing.py::test_execution_is_capped_by_the_business_deadline_without_renewing_it`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-02 RPC错误不能覆盖原Models结果

类别：历史修复。任务：SD-04、SD-08、SD-15。

必须保留：RPC internal_error/断开不等于模型失败；先观察原attempt，控制面不可用不成为重复提交许可。

修复/约束记录：source 根的 `docs/development/runtime-recovery-closure-2026-11-03.md`。

当前实现入口：`src/tianshu/memory/model_outcome.py`、`src/tianshu/memory/steps.py`、`src/tianshu/models/results.py`。

已有用例：

- `tests/core/test_memory_rpc_recovery.py::test_rpc_wrapper_uses_original_receipt_instead_of_internal_error`。
- `tests/core/test_memory_rpc_recovery.py::test_missing_control_never_becomes_permission_to_retry_on_later_wakes`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-03 纯计算让位、独立尝试与只应用一次

类别：历史修复。任务：SD-03、SD-04、SD-08、SD-09、SD-10、SD-15。

必须保留：临时计算失败释放业务活动位，后续材料可推进；新attempt独立，迟到结果不重复应用；明确取消、源失效及永久配置错误不自动重试。

修复/约束记录：source 根的 `docs/development/nr06-diary-and-computation-retry-2026-11-03.md`。

当前实现入口：`src/tianshu/companion/growth_retry.py`、`src/tianshu/memory/index_jobs.py`、`src/tianshu/life/computation_retry.py`。

已有用例：

- `tests/companion/test_growth_retry.py::test_unknown_yields_exact_versions_tail_advances_and_retry_cannot_regress_cursor`。
- `tests/companion/test_growth_retry.py::test_late_original_response_cannot_apply_after_successor_was_selected`。
- `tests/core/test_memory_index_retry.py::test_transient_index_yields_to_later_objects_then_retries_once_after_restart`。
- `tests/life/test_computation_retry.py::test_an_unknown_diary_does_not_block_a_later_registered_day`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-04 日记scope与正文成功后的状态对账

类别：历史修复。任务：SD-02、SD-12、SD-15、SD-19。

必须保留：作者范围较宽仍定位同一合法闭日，歧义不得任取；成功正文只对账派生状态，不重新生成、不改变原时区和来源范围。

修复/约束记录：source 根的 `docs/development/nr06-diary-and-computation-retry-2026-11-03.md`。

当前实现入口：`src/tianshu/life/diary_automation.py`、`src/tianshu/life/diaries.py`。

已有用例：

- `tests/life/test_diary_automation.py::test_saved_success_reconciles_its_unique_narrower_day_without_rewriting_content_or_receipts`。
- `tests/life/test_diary_automation.py::test_multiple_narrower_days_are_ambiguous_but_an_exact_original_scene_keeps_priority`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-05 后台请求与供应商上下文隔离

类别：历史修复。任务：SD-04、SD-11、SD-15。

必须保留：后台按角色/场景/功能/intent隔离，不继承聊天、前日日记、失败输出或上游previous-response引用；聊天保留明确的会话连续性。

修复/约束记录：source 根的 `docs/development/nr06-diary-and-computation-retry-2026-11-03.md`。

当前实现入口：`src/tianshu/models/protocols.py`、`src/tianshu/conversation/service.py`、`src/tianshu/life/diary_materials.py`。

已有用例：

- `tests/models/test_provider_transport.py::test_session_identity_is_stable_for_continuation_and_distinct_for_other_scenes`。
- `tests/models/test_provider_transport.py::test_independent_requests_cannot_inherit_provider_state_from_native_or_route_options`。
- `tests/life/test_diary_materials.py::test_each_day_has_no_chat_previous_day_memory_or_failed_partial_context`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-06 规划截断、无效结果与合法留白

类别：历史修复。任务：SD-12、SD-13、SD-14。

必须保留：截断/不合法提案不能当成有效决策指纹而永远跳过；完整校验后才应用，有界纠正；合法留白可正常读取并在真实边界复查。

修复/约束记录：source 根的 `docs/development/life-runtime-fixes-2026-11-02.md`。

当前实现入口：`src/tianshu/life/planning_engine.py`、`src/tianshu/life/planner.py`、`src/tianshu/life/day_plan.py`。

已有用例：

- `tests/life/test_planning_stream_repair.py::test_known_invalid_results_have_two_bounded_repairs_and_never_become_valid_decisions`。
- `tests/life/test_planning_stream_repair.py::test_repair_applies_one_complete_result_and_keeps_the_original_rejected_record`。
- `tests/life/test_planning_stream_repair.py::test_only_free_slot_returns_200_through_the_registered_day_plan_http_route`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-07 滚动日程不恢复任意次数和占比限制

类别：历史修复。任务：SD-13、SD-14。

必须保留：第五/第六活动可继续，排程只服从真实作息/承诺/保护；普通tick不反复刷新留白截止或无故重新规划。

修复/约束记录：source 根的 `docs/development/retrieval-life-closure-2026-11-03.md`。

当前实现入口：`src/tianshu/life/day_plan.py`、`src/tianshu/life/planning_engine.py`。

已有用例：

- `tests/life/test_rolling_day_plan.py::test_completed_history_does_not_limit_the_fifth_or_sixth_activity_and_completion_decides_again`。
- `tests/life/test_rolling_day_plan.py::test_long_activities_have_no_fixed_occupancy_ratio_but_obey_real_windows`。
- `tests/life/test_rolling_day_plan.py::test_idle_review_keeps_its_absolute_deadline_across_harmless_ticks_and_reconsiders_once`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-08 保护期内不循环恢复和暂停

类别：历史修复。任务：SD-12、SD-13、SD-14。

必须保留：恢复前即检查保护窗口；重复唤醒不能新增步骤、重复执行或空增语义版本；暂停不阻止作息/房间显示推进，空闲后沿同一检查点续接。

修复/约束记录：coordination 根的 `docs/development/proactive-companion-repair-r67-2026-10-31.md`。

当前实现入口：`src/tianshu/life/day_plan.py`、`src/tianshu/life/continuation.py`、`src/tianshu/life/worker.py`。

已有用例：

- `tests/life/test_activity_continuation.py::test_due_continuation_respects_existing_time_and_current_activity_protection`。
- `tests/life/test_activity_continuation.py::test_lifecycle_fingerprint_changes_on_due_and_slot_fences_without_per_minute_churn`。

覆盖状态：部分覆盖：本轮确认单次保护边界及指纹用例，尚未定位完整覆盖R67多次唤醒/版本/步骤/作息联动的现成用例。SD-14须先复用可用覆盖，否则仅补这组真实回归断言；不能将本条标成已验证。

### PF-09 阅读失败后的同任务续接与执行上下文

类别：历史修复。任务：SD-12、SD-13、SD-14、SD-18。

必须保留：确认失败只跳过对应未读候选，保留成功和剩余目标；暂停/未知效果不套用跳过；重新安排后实际执行与冻结授权上下文一致。

修复/约束记录：source 根的 `docs/development/natural-runtime-closure-2026-11-02.md`。

当前实现入口：`src/tianshu/life/reading.py`、`src/tianshu/life/continuation.py`、`src/tianshu/life/worker.py`。

已有用例：

- `tests/life/test_read_failure_continuation.py::test_confirmed_timeout_skips_only_the_unread_candidate_and_retains_success_and_missing`。
- `tests/life/test_read_failure_continuation.py::test_expired_failure_replans_the_same_activity_at_full_quota_with_original_remaining_arguments`。
- `tests/life/test_activity_execution_context.py::test_explicit_resume_freezes_its_authorized_context_for_restart_reconciliation_without_replaying`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-10 检索来源证明、阶段降级和预算

类别：历史修复。任务：SD-02、SD-07、SD-09、SD-11。

必须保留：保留批量来源证明的AND/OR/关系完整性、候选补位和最终复检；支路失败保留其他有效结果，真实取消/权限失效不能伪装普通降级；不恢复排队扣时。

修复/约束记录：source 根的 `docs/development/retrieval-life-closure-2026-11-03.md`。

当前实现入口：`src/tianshu/memory/candidates.py`、`src/tianshu/memory/retrieval.py`、`src/tianshu/memory/retrieval_budget.py`。

已有用例：

- `tests/memory/test_candidate_sources.py::test_alternative_supports_never_discard_required_or_relation_sources`。
- `tests/memory/test_candidate_sources.py::test_large_and_groups_and_many_or_alternatives_use_legal_source_batches`。
- `tests/core/test_retrieval_timing.py::test_short_upstream_deadline_keeps_lexical_evidence_without_starting_remote_work`。
- `tests/core/test_retrieval_timing.py::test_default_foreground_entrypoints_execute_both_thirty_second_phases`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-11 图文整理、短引用和一次远端候选准备

类别：历史修复。任务：SD-07、SD-08、SD-10、SD-15、SD-17。

必须保留：先确定完整材料范围再调用必要远端召回，试装不反复付费；原图/作者/引用保留，短引用绑定冻结批次；仅完整语义完全相同才无损去重。

修复/约束记录：source 根的 `docs/development/runtime-recovery-closure-2026-11-03.md`。

当前实现入口：`src/tianshu/memory/materials.py`、`src/tianshu/memory/material_media.py`、`src/tianshu/companion/reflection_output.py`。

已有用例：

- `tests/memory/test_material_media.py::test_preparation_fits_images_before_one_remote_recall_and_reuses_selected_range`。
- `tests/memory/test_material_media.py::test_repeated_source_version_keeps_each_reporting_subject_but_sends_one_image`。
- `tests/companion/test_growth_short_refs.py::test_short_mapping_cannot_be_borrowed_from_another_batch`。
- `tests/companion/test_growth_short_refs.py::test_only_identical_full_semantics_are_deduplicated`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-12 独立订阅的作者授权与故障隔离

类别：历史修复。任务：SD-02、SD-03、SD-05、SD-18。

必须保留：管理员独立订阅不绑定/借用角色；合法授权epoch变化可正常刷新，真实范围/权限丢失只阻止相关任务，不阻塞其他源。

修复/约束记录：source 根的 `docs/development/life-runtime-fixes-2026-11-02.md`。

当前实现入口：`src/tianshu/capabilities`。

已有用例：

- `tests/capabilities/test_media_scheduling.py::test_global_admin_feed_refreshes_epoch_without_selecting_or_borrowing_a_role`。
- `tests/capabilities/test_media_scheduling.py::test_global_admin_feed_stops_when_principal_scope_or_required_permission_is_lost`。
- `tests/capabilities/test_media_scheduling.py::test_real_scope_or_source_loss_blocks_only_its_source`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-13 外部发送未知及迟到取消不产生二次效果

类别：历史修复。任务：SD-04、SD-06、SD-16、SD-21。

必须保留：外部已提交/unknown不按新任务重放；明确取消与消费者中断分开，迟到控制不能改写原物理回执。空库切换同样不得重放旧外部任务。

修复/约束记录：source 根的 `docs/development/nr06-diary-and-computation-retry-2026-11-03.md`。

当前实现入口：`src/tianshu/channels/delivery.py`、`src/tianshu/conversation/expression.py`、`src/tianshu/models/cancellation_reasons.sql`。

已有用例：

- `tests/channels/test_transport_cancellation.py::test_ws_disconnect_keeps_original_unknown_without_second_send`。
- `tests/channels/test_delivery_contact_preflight.py::test_submitted_or_unknown_original_is_never_reclassified_as_a_new_candidate`。
- `tests/models/test_cancellation_reasons.py::test_submitted_model_cancellation_keeps_unknown_and_its_actual_cause`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。

### PF-14 固定人格与独立日记材料预算

类别：既有行为约束。任务：SD-04、SD-11、SD-15。

必须保留：固定人格不可被预算裁掉；动态材料不升为系统权威；保留当前独立输入上限与完整单元预算，发现/读到/想象/未知结果分开。

修复/约束记录：source 根的 `docs/development/nr06-diary-and-computation-retry-2026-11-03.md`。

当前实现入口：`src/tianshu/conversation/context/assembly.py`、`src/tianshu/life/diary_materials.py`。

已有用例：

- `tests/context/test_budget_units.py::test_full_persona_is_mandatory_and_estimates_are_honest`。
- `tests/life/test_diary_materials.py::test_diary_uses_exact_independent_200000_input_budget_and_omits_whole_units`。
- `tests/life/test_diary_materials.py::test_daily_semantics_preserve_discovery_read_failure_imagined_and_unknown_image_boundaries`。

覆盖状态：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
