# 天枢记忆与自学习统一任务说明

范围更新：以下保留第二版任务及当时状态。用户允许空库重构后，后续候选实施改用[SD 完整任务说明](system-decoupling-redesign-tasks-2026-11-04.md)；本文件不再作为独立派发队列。

规划第二版，所有任务当前为 planned，未分配执行者、未开始产品实现。本文件由同名统一队列的任务字段生成；编号、依赖和状态以[队列 JSON](memory-learning-unified-queue-2026-11-04.json)为准，架构取舍见[主方案](memory-learning-unified-plan-2026-11-04.md)。

角色 I/M/C/F 是未来文件责任归属。相同文件即使出现在多张卡中，也只能由同一角色串行写入。proposed_files 是建议新增路径，不表示文件已存在；正式派发时核对实际基线与他人改动。完成依赖是功能收口依赖，装配对照不增加启动门禁，提前准备不代表实现完成。

默认完成顺序：ML-02 → ML-03 → ML-01 → ML-05 → ML-06 → ML-07 → ML-04 → ML-08 → ML-11 → ML-09 → ML-10 → ML-12 → ML-13 → ML-14 → ML-15 → ML-16。

## ML-01 统一材料身份和领域接口

状态：planned。主责：I。优先级：P1。完成依赖：无。来源映射：LE-01、LE-05、MEM-03、MEM-08。

为事实、历史反思、有效习惯、表达样例和词义建立可区分且可关联的材料引用，避免另建统一记忆权威。

### 文件责任

- I：`src/tianshu/types/context_materials.py`、`src/tianshu/conversation/ports.py`、`src/tianshu/runtime/product.py`。

### 交付范围

- 定义 MaterialRef、BehaviorBinding、SelectionRecord 和 CoverageAssessment 的版本化字段；实际名称可沿用项目类型。
- 明确 query purpose、semantic_role、原件完整依赖与实际支持子集的不同含义。
- 确定 Memory 返回历史证据、Companion 判断当前行为状态、Conversation/Life 冻结实际取材的接口。
- 明确普通回复、原生 memory 工具、Life、Growth、日记及历史读取的消费者清单。
- 以现有 ContextContribution/ContextPack 扩展最小跨域引用；Coverage/Support 字段保留所属领域，不强制所有材料携带同一大对象。
- 定义有界候选记录、最终选择和完整生成依赖的区别；选材原因不是模型思维链，也不是回答效果归因。
- 规则资格结果区分 semantic_change、support_only、unavailable，复用修订内容/原因或确定性指纹，不另建全局状态版本服务。

### 必要验收

- 一个学习产物经反思和习惯两条路径可识别同源。
- 历史可读不被解释成当前行为有效。
- 接口不允许模型生成权限或实际执行回执。
- 无新增前台辅助模型调用和跨模块私表查询。
- 最小合同可以独立交付，不等待所有未来页面/补偿字段实现。

复用验证位置：`tests/core/test_memory_evidence_retrieval.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：公共 DTO 与附属元数据优先兼容扩展；不建立通用策略引擎。

风险和恢复：共享接口扩展影响多个消费者；先固定语义和样例，各领域继续拥有自己的规则。

证据：E04、E05、E06、E07、E18、E20、E21。

## ML-02 不可用媒体的部分接收与原事件恢复

状态：planned。主责：I。优先级：P1。完成依赖：无。来源映射：MEM-01、RUNTIME-MEDIA。

媒体不可用时接收原事件中的有效文字与引用，保存附件缺失，不让一项附件失败吞掉整条消息。

### 文件责任

- I：`src/tianshu/conversation/service.py`、`src/tianshu/conversation/codec.py`。

### 交付范围

- 按原件可用部分构造输入和 Coverage，保留原作者、事件、修订与缺失附件身份。
- 区分收到附件、取得附件、理解附件，不伪造视觉结论。
- 复用原幂等接收入口恢复确证失败事件，禁止重新 ingest 新事件或改变回复策略。
- 恢复工具提供只读清单和同 ID 对账；真实执行留在后续授权范围。

### 必要验收

- 含有效文字和下载失败图片时文字可进入经历。
- 纯不可用附件明确记录理解缺项。
- 同事件重复执行不重复生成经历或发送消息。
- 原 failed/accepted 审计与 unknown 不改写。

复用验证位置：`tests/core/test_source_memory_stories.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：优先无 DDL；如已有输入结构无法表达不可用部分，只增量扩展该结构。

风险和恢复：恢复接收可能触发原业务后续；恢复身份、回复所有权与外部发送必须沿原策略，不能当批量重放。

证据：E01、E02。

## ML-03 局部引用编译与创建冲突修复

状态：planned。主责：M。优先级：P1。完成依赖：无。来源映射：MEM-01、RUNTIME-PROPOSAL。

沿用现有按目标分组的提案协议，消除跨目标局部创建别名碰撞，并保留有歧义输出的严格拒绝。

### 文件责任

- M：`src/tianshu/memory/proposal.py`、`src/tianshu/memory/extraction_contract.py`、`src/tianshu/memory/generation.py`、`src/tianshu/memory/consolidation_retry.py`、`src/tianshu/memory/materials.py`、`src/tianshu/memory/consolidation.py`。

### 交付范围

- 新协议保留每目标 nN 局部别名；按冻结 subject 顺序、目标内操作顺序和输出槽位编译为全批唯一别名，继续由原提交器生成正式对象身份。
- 同目标 nN 只解析本目标声明；跨目标新对象引用采用显式 subject_id/local_ref；已冻结 rN 引用保持原语义。
- 创建 Claim/Matter/link 和 transition successor 均进入同一局部映射；先收集声明，再解析引用，不因模型改变目标数组顺序改变绑定。
- 目标内重复声明、未定义/越界引用和错误类型保持明确诊断与原有限纠正；不猜测对象身份、不按文本相似度合并操作。
- 复用现有领域操作排序/依赖校验、正式事务合同、来源/read_set/CAS/逐目标覆盖；不另建通用图编译器。
- 协议版本冻结于原请求；旧冻结请求/结果照原协议观察，新写入只走新默认路径，禁止重新解释旧结果。

### 必要验收

- 两个 subject 都声明 n1 时正确映射为不同对象，顺序变化仍不串人。
- 同目标重复 n1 或跨目标未限定引用不得被猜改为成功。
- transition 后继与其他 create/link 的局部引用一致，读旧 rN 仍校验来源和 CAS。
- 逐目标 changed/no_change/unresolved 全覆盖；引用失败不部分提交。
- 旧未知请求保持原身份/冻结内容，新协议不覆盖历史回执。

复用验证位置：`tests/core/test_consolidation_pipeline.py`、`tests/memory/test_consolidation_contract.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：协议按冻结批次版本分派；不修改已登记 ModelSteps 请求。

风险和恢复：当前运行记录只证实 duplicate_create，未证明所有实例均为跨目标碰撞；局部命名空间消除合法重名，不能承诺消除模型的同目标歧义。 新旧协议分读者，回退包必须识别已登记的新协议；不保留两个当前写入器。

证据：E03、E22。

## ML-04 分项覆盖诊断与模型语义视图

状态：planned。主责：M。优先级：P1。完成依赖：ML-01。来源映射：MEM-02、MEM-03、LE-01。

最终装配对照：ML-02，不增加启动依赖。

把原件、必要上下文、候选范围、语义处理和提交索引分开描述，并减少向模型重复发送机械账本。

### 文件责任

- M：`src/tianshu/memory/evidence.py`、`src/tianshu/memory/types.py`、`src/tianshu/memory/materials.py`、`src/tianshu/memory/indexing.py`、`src/tianshu/memory/consolidation.py`。
- C：`src/tianshu/companion/growth_observations.py`、`src/tianshu/companion/growth_materials.py`。

### 交付范围

- 扩展现有批次/经历回执的 coverage_assessment，保留旧 extraction_missing 原因及评估版本。
- 提供完整证据对象到模型视图的确定性转换；主体、作者、否定、条件、时间、关系、完成依据保留。
- 来源闭包、权限、CAS、游标和恢复状态留在正式对象与冻结请求映射中。
- 保持完整证据 API；模型局部引用可由原文追查端口解析。
- 明确 optional candidate omitted 与 required context missing，不能把未声明依赖当作已证明无关。
- 为模型语义视图区分新原文待整理、领域已提交与索引待就绪；不以事实表暂未更新否认本轮已接收输入。

### 必要验收

- 旧缺项不能因新分类直接变完整。
- 移除机械字段不改变语义断言和来源依赖。
- 冲突双方与否定条件不会被预算截去半边。
- 图像与完整文字单元仍按真实计数装配。
- Growth 的非工具 Episode 不再整包携带整理状态。
- 整理/索引延迟与事实不存在区分，保留已知和未知覆盖。

复用验证位置：`tests/core/test_memory_evidence_retrieval.py`、`tests/core/test_consolidation_pipeline.py`、`tests/companion/test_growth_budget.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：优先已有 JSONB 附属字段；显式标明旧记录没有分项诊断，禁止倒填推断事实。

风险和恢复：语义精简不能缩小生成来源闭包；完整依赖和实际支持在存储与模型视图中有不同职责。

证据：E04、E05、E19。

## ML-05 学习产物关联与当前行为状态一致性

状态：planned。主责：C。优先级：P1。完成依赖：ML-01。来源映射：LE-01、MEM-01、MEM-06。

最终装配对照：ML-04，不增加启动依赖。

使当前习惯撤回、失效和冻结在所有普通消费入口一致，同时保留可授权读取的历史反思。

### 文件责任

- C：`src/tianshu/companion/growth.py`、`src/tianshu/companion/dependencies.py`、`src/tianshu/companion/ports.py`、`src/tianshu/companion/habits.py`。
- M：`src/tianshu/memory/service.py`、`src/tianshu/memory/evidence.py`、`src/tianshu/memory/types.py`、`src/tianshu/memory/tools.py`、`src/tianshu/memory/candidates.py`。
- I：`src/tianshu/runtime/product.py`。

建议新增：`src/tianshu/companion/habits.py`。

### 交付范围

- 以稳定 rule_id/revision 和 origin proposal/reflection 建立关联；多次新提案可以指向同一规则。
- Companion 为当前行为状态唯一写者；Memory 只保留关联与可重建候选提示，不成为第二活动规则权威。
- 管理列表与运行时取材分离，active/用途/对象/授权先过滤再有界补位。
- Memory 历史证据显式传递 style/interest 类型和行为引用；普通工具结果不能把旧反思包装成现行规则。
- 源于 Companion 的状态变更通过领域端口/现有事件衔接；禁止 Memory 直接查 Companion 私表。
- 运行时 material 改为薄领域读取，不调用管理 view、审核/重试统计或无关 Memory style_view/projection；页面继续使用管理聚合。
- 每条规则形成独立引用/来源/版本单元，使用同一批读结果选材；只有明确互相依赖的规则组成不可拆完整单元。
- 资格接口以当前语义与状态判断兼容性，仅新增支持或管理计数不标成行为失效；不缓存跨请求授权判定。

### 必要验收

- 撤回习惯不能借普通 Memory 检索、memory 工具或 Life 读取重新生效。
- 前 40 条撤回后第 41 条有效规则仍可取到。
- 历史追问可读旧依据且明确当前状态。
- 同一学习产物不以反思与习惯重复注入。
- 状态查询失败时只舍弃对应可选行为材料，不声称已遵守该规则。
- 聊天取材不执行成长管理审核/重试查询；统计查询减少以调用链和实际记录核对。
- 单条可选规则失效不会使无依赖的其他规则一并丢弃。

复用验证位置：`tests/core/test_incremental_consumers.py`、`tests/core/test_memory_evidence_retrieval.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：只为无法从现有稳定 ID 证明的关联保留未知；不按文本相似度批量绑定旧记录。

风险和恢复：历史可读和行为有效必须独立判断；仅过滤成长页或仅过滤聊天入口均不足。

证据：E06、E07、E08、E17。

## ML-06 按用途选择上下文与冻结最终取材清单

状态：planned。主责：I。优先级：P1。完成依赖：ML-01、ML-05。来源映射：LE-01、LE-05、MEM-04、MEM-08。

最终装配对照：ML-04、ML-07、ML-11，不增加启动依赖。

先理解当前对话，再选择相关长期材料，并记录模型真正收到的内容及版本。

### 文件责任

- I：`src/tianshu/conversation/context/assembly.py`、`src/tianshu/conversation/service.py`、`src/tianshu/conversation/generation.py`、`src/tianshu/life/planning_engine.py`、`src/tianshu/types/context_materials.py`、`src/tianshu/conversation/results.py`、`src/tianshu/conversation/expression.py`、`src/tianshu/conversation/rendering.py`、`src/tianshu/channels/delivery.py`、`src/tianshu/channels/service.py`、`src/tianshu/runtime/product.py`。
- C：`src/tianshu/companion/habits.py`。
- M：`src/tianshu/memory/service.py`、`src/tianshu/memory/retrieval.py`。

### 交付范围

- 聊天、Life、Growth 和日记显式传递用途，避免用管理分页代替运行取材。
- 优先保留本轮输入及可确定的回复/引用/工具依赖，长期材料在剩余预算中选择。
- 将检索相关性保持到装配，区分明确约束、问题证据、推断习惯、样例和词义，允许零个性化。
- 冻结前批量复核来源及各领域当前状态/版本；同源相同用途去重，必要的历史与当前状态作为关联保留。
- 最小 SelectionRecord 随请求落在已有 generation/turn 附属清单；记录最终装入、舍弃原因和估计量。
- content_revisions 按领域稳定身份建键，不以 kind 覆盖同类多个规则。
- 消息在装配前保留 message/turn/event 身份、来源、修订和回复关系；预算选定完整消息单元后再汇总直接来源，替换先合并全部近期候选来源的做法。
- 只排除从未参与任何模型调用或衍生计算、且最终未采用候选的直接依赖；已选材料完整依赖、原生成/重排回执的来源闭包和当前来源复检不缩减。
- 本轮明确引用及有来源的新消息在长期整理未完成时仍可参与回答；现有会话与 Memory 证据按真实原件关系去重，不另建短期事实库。
- SelectionRecord 按 request_id/round/attempt_id 关联冻结清单，只记录有界候选和最终选择；表达/Action 沿原身份关联清单，正文仍由原冻结请求负责。
- 以实际装入的 rule_id 和语义版本判断冻结后的实质失效；support_only、无关对象/用途变化不取消生成。
- 将必要检查接到 Conversation 结果、ExpressionRenderer.release 和 channels.deliver 既有未提交检查位置；由上层端口注入，不在发送层查询 Companion 私表。
- 已进入外部提交/unknown 的表达继续原对账；未提交且确定失效者使用原取消回执和可见原因，不新建副作用、不自动重发；单纯偏好读取故障不伪称已确认撤回。

### 必要验收

- 刚才那个所需上文不被可选习惯/样例挤掉。
- 两条同 kind 规则分别有版本。
- 撤回发生在候选选择后、冻结前时不会继续带入。
- 历史未记录、真实空选择和发生错误有区别。
- 生成中变更沿既有取消/结果检查，不承诺收回已发送内容、不自动重发。
- 被预算舍弃且未参与衍生计算的历史来源变化不无故阻断本轮；已采用消息或衍生材料的来源失效仍受原保护。
- 新消息尚未整理时不被旧事实无条件覆盖；无法证明是纠正时并列保留冲突。
- 只增加支持的 revision 变化不会取消回复；确定撤回且依赖该规则的等待渲染/发送表达会停止。
- 当前明示的禁止性要求无法复核时保留未决处理；一般可选风格状态读取故障不引发持续取消风暴。
- 送达未知、已经开始提交和多段已提交表达不被标成从未发生；现有发送顺序/回执语义不变。

复用验证位置：`tests/core/test_memory_evidence_retrieval.py`、`tests/core/test_incremental_consumers.py`、`tests/conversation/test_expression_order.py`、`tests/conversation/test_render_contact_preflight.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：优先扩展现有请求 JSON；如需字段，只增加请求附属清单，不复制请求账本。

风险和恢复：发送前检查有明确线性边界；不能承诺外部系统已接收后撤回，也不能以修订号不同等同内容失效。 缩小直接候选来源不等于缩小衍生材料完整依赖；若已进模型/衍生计算则保留其原完整闭包。 每请求只在实际跨越的冻结/结果/发送边界批量复核相关身份，不引入轮询或额外判断模型。

证据：E07、E09、E18、E19、E20、E21。

## ML-07 有范围的偏好修订和明确反馈

状态：planned。主责：C。优先级：P1。完成依赖：ML-05、ML-06。来源映射：LE-02、MEM-06。

让持续偏好、临时要求和推断习惯按范围演进，并防止撤回内容换 ID 复活。

### 文件责任

- C：`src/tianshu/companion/habits.py`、`src/tianshu/companion/growth.py`、`src/tianshu/companion/reflection_output.py`、`src/tianshu/companion/growth_materials.py`、`src/tianshu/companion/module.py`、`src/tianshu/companion/dependencies.py`、`src/tianshu/companion/habit_revisions.sql`。
- I：`src/tianshu/conversation/service.py`、`src/tianshu/runtime/migrations.py`。

建议新增：`src/tianshu/companion/habit_revisions.sql`。

### 交付范围

- 记录 preference_kind、适用对象/用途/范围/有效期与有依据的修订关系。
- 将 PreparedReflection.support_ids 的实际支持子集、完整生成依赖和独立事件身份分别保存。
- 以稳定 rule_id 维护有限 create/support/revise/retract，保留原 Proposal ID/回执。
- 复用现有命令身份和 CAS 接通纠正、撤回、恢复；明确纠正立即影响已确定范围。
- 可从最终取材清单定位反馈对象；归属不明时先满足本轮要求、保留未决，不全局误删。
- 复用撤回约束阻止已解析同一规则的重述自动恢复。
- 显式界面/已解析原生命令在原命令事务中修订，不等待 Growth；自然语言纠正由当前对话先遵守，后台依原真实事件提取并修订，不用关键词或时间新旧直接宣布持久偏好已写入。
- 修订记录区分语义/适用性变化与支持新增，向 ML-06 暴露足以判断当前消费兼容性的最小结果。

### 必要验收

- 今天少开玩笑不永久化，以后别这么叫我在换话题后仍有效。
- 当前明确要求覆盖旧推断但不能改他人偏好或工具权限。
- 同批无关材料、自身复述和重复注入不增加独立支持。
- 不同条件规则共存，语义近似不自动合并。
- 冻结保持自动更新约束，明确用户纠正有效。
- 旧记录支持子集无法可靠恢复时保持未知。
- 当前已遵守、长期已提交、仍待整理三种状态可区分；未解析自然语言不冒称已完成永久修订。

复用验证位置：`tests/companion/test_growth_output.py`、`tests/core/test_incremental_consumers.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：如命令收据不足以保存修订内容，增加 Companion 自有规则修订表；不新增可编辑 Reflection 版本权威。

风险和恢复：语义同一性并非仅靠相似度可证明；避免把未指明对象的负反馈扩散为全局禁用。

证据：E06、E08。

## ML-08 共享整理的依赖选材和输出感知分段

状态：planned。主责：M。优先级：P2。完成依赖：ML-03、ML-04。来源映射：MEM-03、LE-04。

在保留多人共享理解的同时减少不相关旧记录、重复上下文及输出截断。

### 文件责任

- M：`src/tianshu/memory/materials.py`、`src/tianshu/memory/consolidation.py`、`src/tianshu/memory/preparation.py`、`src/tianshu/memory/material_media.py`。

### 交付范围

- 复用回复/引用/事项链接形成材料连通范围，保留必要邻近消息，不额外调用主题分类模型。
- 显式同事项/待修订记录优先，其次按人物公平选择相关旧记录；候选不足如实记录。
- 输入和输出容量联合选取完整材料；输出不足按消息/依赖分段，不按人重复整段。
- 依赖优先级有依据，不能把所有历史都强制装入；必要补查仍有界。
- 复用现有 preparation metrics，记录原文、旧记录、协议、图像和纠正预留的估计量。
- 跨目标引用仍沿 ML-03 局部引用编译，不为每人重跑或新增通用依赖图系统。

### 必要验收

- 低频发言者和跨人物引用保留。
- 一段共享材料常规一次模型理解，必要纠正/补查独立可审计。
- 不为节省成本丢短句、表情或按每人固定条数截断。
- 输出截断不会部分提交或伪造 no_change。
- 200000 仍是上限，不能靠降低覆盖制造低成本。

复用验证位置：`tests/core/test_consolidation_pipeline.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：不改变原 Episode ID 和分区身份，原冻结批次沿原协议处理。

风险和恢复：仅靠结构不能可靠识别所有话题，未知关联保留为范围限制，不声称话题划分完整。

证据：E03、E04。

## ML-09 分用途检索和按需重排

状态：planned。主责：M。优先级：P2。完成依赖：ML-01、ML-06。来源映射：MEM-04、MEM-05、LE-01、LE-04。

最终装配对照：ML-04、ML-11，不增加启动依赖。

提高候选质量并减少无必要云端重排，保持来源和时间语义。

### 文件责任

- M：`src/tianshu/memory/query_plan.py`、`src/tianshu/memory/candidates.py`、`src/tianshu/memory/retrieval.py`、`src/tianshu/memory/retrieval_budget.py`、`src/tianshu/memory/indexing.py`。

### 交付范围

- 人物/事项/时间的可靠结构优先进入原查询计划，不可靠推断回到通用召回。
- 中文整词/短语/姓名与受控回退减少单字广匹配，保持合法单字查询。
- 连接池预算内并行独立支路并保留成功分支，继续有序批读和批量来源证明。
- 精确单对象/list 等场景跳过无益重排，歧义/接近候选才按需提交。
- 复用 RRF、MMR 缓存和紧凑重排文本；候选限量和排序参数以相关场景对照决定。
- 最终记录/关系/来源与当前行为资格复检保持；精确历史/list 不能被排序查询冒充。
- 检索观测区分候选未召回、已召回但被资格/预算丢弃及最终请求已装入，避免用召回数量替代答案所需证据。

### 必要验收

- 中文名称、否定、日期和跨人事实召回不退化。
- 前批无权后可继续补位。
- 一条支路超时不抹掉另一支路结果。
- 当前向量 2 秒、embedding/rerank 30 秒及排队计时语义保持。
- 不靠放松来源谓词、强行截断或扩大预算提高通过率。

复用验证位置：`tests/core/test_memory_evidence_retrieval.py`、`tests/memory/test_retrieval_degradation.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：仅根据实际查询计划增加必要索引；不默认换向量库、模型或全局 PG 配置。

风险和恢复：跳过重排必须在对应场景验证质量；不以响应变快掩盖必要证据被省略。

证据：E10。

## ML-10 向量重复计算消除与有界复用

状态：planned。主责：M。优先级：P2。完成依赖：ML-09。来源映射：MEM-05。

复用已证明相同的数学结果，同时避免跨域缓存成为新的授权通路。

### 文件责任

- M：`src/tianshu/memory/embedding.py`、`src/tianshu/memory/index_jobs.py`、`src/tianshu/memory/indexing.py`。

### 交付范围

- 先消除请求内重复查询与 profile 读取；索引语义文本和 profile 完全相同时复用已验证向量。
- 若代表负载确有重复查询，增加小容量短 TTL 成功查询向量缓存，键包含模型 profile 和权限/来源上下文。
- 仅缓存成功数学结果，不缓存最终答案或权限判定，不把 unknown 当命中。
- 记录冷/热路径、命中率、复检开销与实际省掉的物理请求；收益不成立时保留请求内复用，不上线跨请求缓存。
- 不改变 index_text 时不要求全库重建向量。

### 必要验收

- 模型/profile/授权域变化不误复用。
- 来源失效仍使派生内容不可读取。
- 独立实际模型 attempt 不重复计费统计。
- 缓存低命中场景没有明显新增开销或后台常驻扫描。

复用验证位置：`tests/core/test_memory_index_retry.py`、`tests/core/test_memory_evidence_retrieval.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：优先无 DDL；复用原 index_jobs 状态，不新建 embedding 权威。

风险和恢复：缓存只能优化计算；任何缓存命中都不证明本次来源可用或用户有权。

证据：E11。

## ML-11 可变经历历史与查询适用性

状态：planned。主责：M。优先级：P2。完成依赖：ML-01。来源映射：MEM-06。

最终装配对照：ML-04、ML-05、ML-09，不增加启动依赖。

补齐确实可变的 Episode 语义历史，保留 Reflection 不可变提案模型，避免过度建表。

### 文件责任

- M：`src/tianshu/memory/service.py`、`src/tianshu/memory/evidence.py`、`src/tianshu/memory/candidates.py`、`src/tianshu/memory/revisions.py`、`src/tianshu/memory/episode_revisions.sql`。
- I：`src/tianshu/runtime/migrations.py`。

建议新增：`src/tianshu/memory/episode_revisions.sql`。

### 交付范围

- 仅在 Episode 语义修订时追加快照，提取游标/运行状态变化不复制历史正文。
- current/valid_at/known_at 读取明确区分；旧缺失版本不倒填。
- Reflection 继续同 proposal_id 内容不可变；行为变化由规则修订承担。
- 当前事实、未结事项、完成事项与历史修订采用查询适用性排序，低频不触发固定天数删除。
- 来源撤回/遗忘涵盖新增历史快照与派生索引。

### 必要验收

- 编辑经历后能按真实记录时间读取已保留版本。
- 迁移前未知历史显式缺项。
- 旧事项或旧偏好不会无条件压过当前状态。
- 历史查询也不能绕过当前遗忘和权限。
- 不新增通用 Reflection 修订表。

复用验证位置：`tests/core/test_memory_evidence_retrieval.py`、`tests/core/test_memory_revision_domain.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：增加 Memory Episode 语义快照；迁移只留当前可证明快照并标历史起点。

风险和恢复：recorded_at 与 occurred_at 不可互换；迁移时间不伪造最早已知时间。

证据：E05、E12。

## ML-12 有真实回复关联的按需表达样例

状态：planned。主责：C。优先级：P2。完成依赖：ML-06、ML-07。来源映射：LE-03。

在明确规则不足以表达具体说法时补充少量样例，避免群聊错配、私域泄露和自我强化。

### 文件责任

- C：`src/tianshu/companion/habits.py`、`src/tianshu/companion/growth_materials.py`、`src/tianshu/companion/reflection_output.py`、`src/tianshu/companion/dependencies.py`、`src/tianshu/companion/expression_examples.py`。
- I：`src/tianshu/conversation/service.py`。

建议新增：`src/tianshu/companion/expression_examples.py`。

### 交付范围

- 通过 Conversation 端口按真实 turn/回复/合并关系读取样例，不用时间相邻配对。
- 样例只存规则版本与原文定位引用，保留条件和否定。
- 发生/发送/送达/用户反馈分别表达，unknown 不伪装成成功。
- 利用既有后台批次形成候选；前台无新增样例评价模型。
- 规则说明足够时不附样例，允许零样例；历史人名地点数字不得当作当前事实。

### 必要验收

- 多人交叉发言与合并回复不错误归属。
- 私聊样例不能越域进入群聊。
- 规则/来源撤回与明确负反馈后停止当前取材。
- 样例重复被机器人使用不增加支持。
- 没有样例仍可正常回复，不强制个性化。
- 只改变措辞表达时不擅自引入样例中的旧人物/数量；有规则无样例与带样例分别评估。

复用验证位置：`tests/core/test_incremental_consumers.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：确需映射表时归 Companion，只保存引用，不复制聊天库。

风险和恢复：样例只能说明说法，不能提供新的事实、权限或真实效果证据。

证据：E08、E09。

## ML-13 真实群主体上的词义理解

状态：planned。主责：M。优先级：P2。完成依赖：ML-03、ML-06、ML-08。来源映射：LE-04。

理解群内简称和约定，保留不同说话者与歧义，不把单人用法升级成群体共识。

### 文件责任

- M：`src/tianshu/memory/materials.py`、`src/tianshu/memory/extraction_contract.py`、`src/tianshu/memory/revisions.py`、`src/tianshu/memory/service.py`。
- I：`src/tianshu/conversation/context/assembly.py`。

### 交付范围

- 冻结 language.term_meaning 的值与条件样例，复用 Claim 修订与真实 group:<conversation_id> 主体。
- 实际 speaker/assertion 独立保存；群主体不等于任何成员可 self_confirmed 全群事实。
- 在原多人整理请求中生成词义，不按词新增模型调用。
- 当前输入命中时提供有界解释，未知和多义保持不确定；人物别名继续归 Identity。
- 理解语义与主动模仿分开，默认不复读黑话。

### 必要验收

- 同词跨群隔离、单人解释不变成群共识。
- 词义冲突并存且带范围，来源撤回后旧解释不再被当前采用。
- 不用伪造 identity.people 或增设群身份系统。
- 未命中时无额外远端调用。
- 未命中、仅解释、确有需要时采用词义三类结果分别记录，匹配不自动代表有益。

复用验证位置：`tests/core/test_consolidation_pipeline.py`、`tests/core/test_memory_evidence_retrieval.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：复用 Claim JSON；派生词项索引确需时可重建，不建平行词库。

风险和恢复：无法证明群共识时只能表达有依据的用法或假设。

证据：E13。

## ML-14 存量缺口的定向补偿与原身份对账

状态：planned。主责：M。优先级：P2。完成依赖：ML-02、ML-03、ML-08、ML-11。来源映射：MEM-02、MEM-07。

在当前正确写入和历史语义具备后修复可恢复缺项，不全库重跑，不让旧材料覆盖新事实。

### 文件责任

- M：`src/tianshu/memory/consolidation.py`、`src/tianshu/memory/consolidation_retry.py`、`src/tianshu/memory/recovery.py`、`src/tianshu/memory/workers.py`、`src/tianshu/memory/coverage_repair.py`。
- I：`src/tianshu/runtime/migrations.py`。

建议新增：`src/tianshu/memory/coverage_repair.py`。

### 交付范围

- 复用原批次/队列增加 repair purpose、parent batch 和稳定缺口身份；身份绑定原 Episode/源版本、缺项语义和修复目标，算法版本进入 attempt metadata。
- 计算前重新核对原件、授权、当前版本和目标缺口是否仍存在；已修复、已被新事实替代或原件不可用者记录原因，不再发起相同计算。
- 同一缺口已有运行/unknown 尝试时沿原观察语义处理；确认允许的新尝试使用独立 attempt_id，旧回执不可覆盖。
- 通过原整理 writer 读取当前事实后补算并 CAS 提交；不制造新 Episode，不覆盖原回执，不因算法版本升级全库重跑。
- 补偿 recorded_at 为本次处理时间，有效时间依原件；与当前事实冲突时保留未决。
- 新输入优先，但在已有队列/资源准入允许时按等待年龄推进小批补偿；每批检查可继续范围，不独立改全局调度或无限让存量饥饿。
- 补偿机制完成、已修复范围、待原件/unknown、待处理范围分别报告；自然存量未清零不阻止功能包完成。

### 必要验收

- 同缺口不并发创建相同计算；已完成目标不会仅因算法版本变化再次付费补算。
- 未知外部尝试不当作失败重放；允许的新尝试及可能重复费用照实计量，不承诺无法证明的零重复计费。
- 遗忘来源不复活，旧材料不能恢复已由新事实替代的当前状态。
- 正常新输入优先且补偿等待年龄/推进进度可解释，不要求零积压。
- 原批次/材料/源版本/尝试和缺口结果可对账；失败或不可恢复保留解释。

复用验证位置：`tests/core/test_consolidation_pipeline.py`、`tests/core/test_memory_rpc_recovery.py`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：优先扩展原批次 manifest/purpose/parent；稳定缺口标识必要唯一索引，算法版本为尝试属性。未知和历史回执不迁改。

风险和恢复：新的修复尝试不是重放历史消息。外部 unknown 不能承诺恰好一次计费；有界补偿不得争抢现有前台资源或恢复遗忘内容。

证据：E03、E04、E12。

## ML-15 现有页面中的取材解释与质量观测

状态：planned。主责：F。优先级：P2。完成依赖：ML-06、ML-07、ML-11、ML-12、ML-13、ML-14。来源映射：LE-05、MEM-08。

可提前准备：ML-01 最小合同确定后准备页面状态/行为样例；完整接线和验收仍按完成依赖。

让用户看懂实际用了什么、当前是否生效、哪些仍缺证据，同时避免把统计量包装成学习质量分。

### 文件责任

- F：`web/src/features/life/GrowthPage.tsx`、`web/src/features/memory`、`web/src/features/companion`。
- I：`src/tianshu/conversation/module.py`。
- C：`src/tianshu/companion/module.py`。
- M：`src/tianshu/memory/module.py`。

### 交付范围

- 沿用 807 现有页面展示规则版本、适用范围、当前状态及原生纠正/撤回/恢复。
- 对照同一请求的最终 SelectionRecord 展示已装入与舍弃原因，历史未记录不补造。
- 区分进入上下文、产生回复、发送/送达、明确反馈和语义效果。
- 覆盖诊断、存量修复和历史版本就地展示，来源链接按当前权限读取。
- 展示取材/SQL/模型排队与执行阶段、实际 usage 和未知计量；普通日志不复制私聊正文。
- 页面按原接口先准备未知/空/待整理/有效/已撤回样例，依赖模块完成后接入真实数据，不等待全部存量处理完才开发。
- 展示明确纠正的接收时间、当前消费状态、持久规则提交状态与可见缺项；最终装入仅标使用，不宣传为造成效果改善。

### 必要验收

- 无反馈不显示好评或效果已提升。
- 被预算/来源舍弃的候选不显示为已注入。
- 同类多规则版本分别可见。
- 窄屏可读且现有完整诊断/原文入口保留。
- 操作不增加常规确认弹窗或新的学习总开关。
- 补偿尚在运行或不可恢复不会把整个模块显示为开发未完成；没有新数据不伪造零成本/零延迟。

复用验证位置：`web/tests/growth-coverage.spec.ts`、`web/tests/memory-closure.spec.ts`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：UI 只消费原领域和请求附属数据，不写第二份统计或规则权威。

风险和恢复：取材计数、记忆条数和请求成功率均不能充当真实学习效果评分。

证据：E14。

## ML-16 集中行为验证与同质量收益对照

状态：planned。主责：I。优先级：P1。完成依赖：ML-01、ML-02、ML-03、ML-04、ML-05、ML-06、ML-07、ML-08、ML-09、ML-10、ML-11、ML-12、ML-13、ML-14、ML-15。来源映射：LE-06、MEM-08、MEM-09。

可提前准备：ML-01 最小合同确定后准备页面状态/行为样例；完整接线和验收仍按完成依赖。

分别证明机制正确、代表场景收益与部署状态，完成整包交付方案，不用健康或平均分代替效果。

### 文件责任

- I：`scripts/evaluation/memory_closure`、`tests/core/test_memory_closure_acceptance.py`、`tests/core/test_incremental_consumers.py`。
- M：`tests/memory`、`tests/core/test_memory_evidence_retrieval.py`、`tests/core/test_consolidation_pipeline.py`、`tests/core/test_memory_index_retry.py`、`tests/core/test_memory_revision_domain.py`。
- C：`tests/companion`。
- F：`web/tests/growth-coverage.spec.ts`、`web/tests/memory-closure.spec.ts`。

### 交付范围

- 实现完成后一次运行受影响定向集合；失败修复只重验相关变化，复用未变成功结果。
- 现有 24 故事作为已知回归；新增盲保留故事按缺口选取，冻结后评阅，不强制固定样本数或全组合矩阵。
- 分别核对结构化写入、最终请求、回答和真实反馈；记录原文补答及未决状态。
- 比较当前实现与核心改造，再按相关场景单独比较样例/词义/重排/缓存，避免收益相互掩盖。
- 同模型/语料/授权/质量统计所有物理尝试及未知 usage，冷/热路径、队列/执行、全量长尾分别报告。
- 形成受影响迁移/恢复与串行发布说明；实际模型、QQ发送、存量补算和生产动作按后续当时授权执行。
- 评估样例随 ML-01 合同先准备，按最终实际实现选择必要验证；保留集按故事家族隔离，被用于调参后转回归。
- 按实际适用机会分别记录必要材料遗漏、无关习惯/样例侵入、明确纠正生效与持久提交时间；不用总消息量稀释失败。
- 将候选正确、最终装入、回答遵守和用户反馈分开；相关场景增加有规则无样例对照，结论说明小样本与主观评阅局限。
- 读取记录区分 SQL/管理聚合、模型调用、排队、执行、整理/索引时效；不得把取消/unknown 多了解释成提速。
- 功能包收口验证补偿的可续跑和原身份幂等，报告未处理范围；不把全部历史清零列为发布前提。

### 必要验收

- 关键人物归属、当前偏好、撤回、时间与历史反例正确。
- 没有新增前台辅助 LLM 调用和未解释的相关退化。
- 查询提速不以减少必要证据或缩短执行窗口实现。
- 同质量成本结论有完整分母；计量缺失保持未知。
- 本地完成、上线健康、实际效果和长期观察分别陈述。
- 后台普通支持更新不造成前台取消；未整理新消息、渲染等待期间撤回、同故事信息泄漏等新边界有定向证据。

复用验证位置：`scripts/evaluation/memory_closure`、`tests/core/test_memory_closure_acceptance.py`、`web/tests/growth-coverage.spec.ts`、`web/tests/memory-closure.spec.ts`。仅列影响范围，不表示本轮执行，也不把整目录全部测试列为默认必跑。

数据变化：仅核对 Memory、Companion、请求附属数据等实际变更域；保留原配置、有效挂载与模型回执。

风险和恢复：公开论文成绩不代表天枢收益；不预设省钱百分比或秒数为发布门禁。

证据：E01、E15。

## 证据定位

源码根：`C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot`；协调根：`C:/YOKI/Codex/tianshu-peiban-bot`。本轮不访问生产，运行信息来自已标明时间的 CURRENT 记录。

| 证据 | 根 | 路径与行号 | 核对内容 |
|---|---|---|---|
| E01 | coordination | `docs/development/CURRENT.md:1` | 13:33 运行记录与向量 2 秒配置；本轮仅读取既有记录 |
| E02 | source | `src/tianshu/conversation/service.py:57` | 输入准备与媒体检查；不可用媒体的缺陷来源 |
| E03 | source | `src/tianshu/memory/proposal.py:67` | 模型提案编译；结合 extraction_contract.py:182、consolidation.py:793 与有限重试 |
| E04 | source | `src/tianshu/memory/materials.py:271` | 关系端点去重、模型材料构造、旧记录选材与 coverage；结合 consolidation.py:439 |
| E05 | source | `src/tianshu/companion/growth_observations.py:32` | 非工具经历仍 wire 整个 Episode；与 memory/indexing.py 的现有语义视图区别 |
| E06 | source | `src/tianshu/companion/growth.py:350` | 反思/规则双表示、管理列表、material 与撤回命令 |
| E07 | source | `src/tianshu/memory/service.py:482` | Reflection/projection 读取；结合 tools.py 原生工具和 evidence.py 语义类型 |
| E08 | source | `src/tianshu/companion/reflection_output.py:184` | PreparedReflection.support_ids 与完整生成依赖的差异 |
| E09 | source | `src/tianshu/conversation/context/assembly.py:138` | 上下文装配顺序、最终来源检查与 kind 版本键 |
| E10 | source | `src/tianshu/memory/retrieval.py:245` | 混合召回、RRF、MMR、重排与最终复检；结合 candidates.py |
| E11 | source | `src/tianshu/memory/embedding.py:26` | query embedding 路径与版本化索引复用；结合 index_jobs.py |
| E12 | source | `src/tianshu/memory/service.py:72` | Episode 可变写入；311 行 Reflection 同 ID 不可变，结合 candidates.py:404 |
| E13 | source | `src/tianshu/identity/service.py:377` | 真实群主体及授权解析；群词义不另造身份 |
| E14 | source | `web/src/features/life/GrowthPage.tsx:1` | 已有成长管理与原文入口；保持现有 807 界面 |
| E15 | source | `scripts/evaluation/memory_closure/metering.py:1` | 既有实际使用量、物理尝试与未知计量汇总 |
| E16 | coordination | `docs/development/learning-enhancement-queue-2026-11-04.md:1` | 另窗口 LE 第二版规划，保留为输入来源 |
| E17 | source | `src/tianshu/companion/growth.py:540` | material 调用 view；view 包含 Memory projection/style_view 和成长审核/重试查询，运行取材可分离 |
| E18 | source | `src/tianshu/conversation/service.py:270` | 近期消息在转为 ModelMessage 前聚合 conversation_provenance；完整候选来源先进入 mandatory，后续才裁剪历史 |
| E19 | source | `src/tianshu/conversation/service.py:91` | 已通过原 turn 观察输入 Episode；近期原文已有会话读取入口，不需新增短期事实权威 |
| E20 | source | `src/tianshu/conversation/rendering.py:189` | 表达渲染完成后可能等待顺序；release 是实际表达发送链的一处复核位置 |
| E21 | source | `src/tianshu/channels/delivery.py:139` | 既有发送前检查只针对 Life candidate，begun 区分已提交/unknown；行为依赖需上层注入并保持已提交边界 |
| E22 | source | `src/tianshu/memory/proposal.py:67` | 模型协议已按 subject 分组；现有编译器提供 op_id/CAS 等，创建 nN 仍落在全批共享别名表 |
