# MC-D：完整证据检索与实际模型消费

状态：`implementation_complete_written_not_run`。执行者 gpt-6.1-sol / max；仅隔离 worktree 本地代码、用例、差异阅读和 Git 提交。用户要求整批完成后统一验收，本包未运行测试、类型检查、解析器/编译、构建、模型调用或实机验证。未操作生产、账号、真实原文或凭据。

## 基线与集成

- 接续 MC-A 的 `bad09f4`。
- 本 worktree 为阅读装配合同 cherry-pick 了 B `913d106` → `7d1a9f7`、B `6bebf03` → `cf06392`。它们是父级已经接收的依赖，**不要再次集成这些 B 副本**。
- 父级应只集成本交接所在 D 提交及其必要后续修订；MC-A 提交已经另行交付。

## 已实现

- `candidates.py` 在既有 Claim/Hypothesis、Matter、Episode、Reflection 表、词法字段及 1024 维向量字段上统一筛选。当前 actor、scope/support OR 组合、必要派生 AND 依赖和业务/记录时间约束在 SQL 候选与距离排序之前应用。词法含中文分词及旧空索引文本回退，向量复用现有 embedding profile；有界 ANN 不足时在 PG 授权集合内作精确距离补齐。
- `retrieval.py` 以 kind/id/revision 融合词法与向量，RRF、MMR、可选完整单元 rerank。重排前后重读版本、语义关系两端及全部来源；失效单元整体丢弃。Hypothesis/Reflection 的 assertion、interpretation、counterexamples、conditions 不转成事实。
- `MemoryQueryPlan` 共用 `query_plan.py` 校验。`current`、`valid_at`、`known_at` 有实际查询分支。Claim 先选知识版本再看有效期；事项按历史报告选过去状态；记录时间未知保留 NULL 并暴露 coverage 缺口，不伪造入库时间。
- `coverage_mode=list` 返回跨类型 kind/id 稳定分页，游标绑定 principal/actor/conversation/scopes/access_epoch/查询计划以及首次页 as_of。空 query 为结构化范围清单；非空 query 为词法条件范围，不宣称语义全集。预算整条裁剪并保留可续游标。`ranked` 明示 `coverage.complete=false` 和 `ranked_selection`，不提供伪完整游标。
- `indexing.index_text(unit)` 供 B 的唯一 `memory.index_object` 使用，索引含语义、条件、状态及实际回执的语义字段，排除来源/回执 ID 和重复关系正文。未另建向量库或 Claim-only 默认索引路径。0021 补 Episode 唯一可证作者字段与三个旧空 lexical 补齐用 partial ID indexes；B 已实现先词法、后可选 embedding 和有界旧记录补齐。
- 共享 `ContextAssembler` 从 B `projection.evidence_units` 读取当前人物/群背景，与检索命中按对象合并；自动补入的记忆在进入模型前再次复核版本和完整来源。模型消息保存单元全部字段，条件/冲突两侧同进同出，来源撤回移除整个可选单元，保留有效当前输入。多个单元的 content_revisions 不再互相覆盖。
- 现有 `memory.search`、`memory.claims`、`memory.matters` 工具统一调用查询入口，返回完整单元及分页/coverage。tool-only `subject_aliases` 复用 Identity 精确授权解析；未解析不退全域，重名保留多候选及 ambiguity coverage。未添加每消息 classifier。
- 新 `memory.source.trace` 仍走既有 Catalog/Action/Attempt/Receipt。它仅接受真实 Action 已选中的相同 SourceRef/locator，position/长度只能缩小；最多 8 项、默认总 4000 字符、服务端硬上限 12000，同一真实前台 expression/activity 的第二个 Action 被拒绝。总 deadline 贯穿来源读和验证。复用 CommandStore 记录真实 Action 身份和一次额度，原文继续只归 Sources；未知/重试不创建新执行身份。
- 所有读工具顶层显式返回完整 `source_refs` 闭包，适配现有 `ConversationService._tool_receipt/_await_tools` 的真实依赖传播。
- 父级追加授权的闭包修订：`required_refs` 统一解释派生、反例和完成依据的必要来源，读选择/关系端点/完整单元共用；候选 SQL 在排名前核验。`revisions.source_changed_on` 把仍展示的反例及完成依据作为 AND 依赖，即使主证据有独立 OR 支持，撤回必要反例仍失效并清空条件/索引。`workers.index_object` 使用完整单元来源，词法写入检查当前版本 CAS 后才进入 embedding，最终再核验完整依赖。
- 经父级授权添加薄 HTTP `/api/v1/memory/matters/get`，只读现有 `claims.get_matter` 当前 head，给历史记录编辑前取当前版本。

## 真实消费者与接口

- `MemoryRetrieval.query(context, plan, budget, *, source_refs=(), cursor=None, limit=50, service_class=INTERACTIVE) -> Page`；HTTP `/api/v1/memory/query` 已由 B 装配，deadline 由服务端产生。
- `MemoryRetrieval.candidates(context, query, budget, *, subject_ids=(), matter_refs=(), kinds=('claim','hypothesis','matter'), limit=32) -> tuple[MemoryEvidenceUnit,...]`；内部使用 ELASTIC；B 已注入 MaterialBuilder 的旧记录候选并集、read_set 和全部来源闭包。
- 保留 `search(context, query, budget, *, limit=12, source_refs=()) -> (tuple[ContextContribution], missing)` 调用合同，贡献类型统一 `memory_evidence`，text 为完整单元 JSON。
- `revalidate(context, records: tuple[RecordRef,...], budget) -> (tuple[MemoryEvidenceUnit,...], missing)` 用于自动背景进入模型前的当前版本复核，不偷偷把旧引用重绑新版本。
- `SourceTrace(memory).read(action)` 仅由实际 `memory.source.trace` handler 消费，不另增 MemoryService 平行读方法。
- 实际共享 Assembler 调用者：`conversation/service.py` 的聊天、`conversation/participation.py`，`life/planner.py`、`life/diaries.py`、`life/writing.py`。这些路径现已自然获得完整证据。Growth 的固定反思 batch 由 C 刻意使用无 retrieval 的 Assembler，继续以固定 Episode 输入为反思证据，未混入无关可选记忆。

## 父级追加的有限恢复与群回执修订

- M18 legacy 扫描不再仅硬编码少数错误名；旧 task/step/cursor 证明存在时，`invalid_claim_predicate` 等可读未完成旧错误进入固定转换清单。没有旧协议依据的任意字符串错误不盲目恢复，completed 历史不重播。
- 保留原 request/attempt，只观察原 Broker 结果。已知旧输出不可应用时保留原回执、放弃旧应用，按唯一新管线重新取材；未知外部结果继续观察原 attempt。原件缺失、来源/权限撤回、请求不可读形成一次明确终态转换回执，不无限扫同条或复活历史。
- `recovery._status_value` 仅在整个固定 batch 来源可读时返回 `targets=[{subject_id}]`，取最后一步冻结映射而非从原文再猜。尚无 target_results 也能显示真正冻结目标。`service._group_consolidation` 精确解析当前授权身份并过滤 targets；实际结果仍来自 commit receipt。

## 已编写、未执行的行为用例

- `tests/core/test_memory_evidence_retrieval.py`：五类真实候选、事实/假设保真；旧空 lexical 单字中文回退；valid_at/known_at 区别及未知记录时间；事项改期历史；绑定权限和计划的完整分页及后插入边界；冲突双方/条件/整条预算；rerank 中撤回来源；背景版本变更而原来源仍可读；真实 Catalog 时间查询/姓名解析/来源传播；一次有界原文补查与真实 Action 身份。
- `tests/context/test_memory_evidence_units.py`：完整字段进入实际 ModelMessage；多个单元版本标识；冲突整条预算；最后来源撤回不拆两侧且保留有效当前输入。
- `tests/core/test_consolidation_pipeline.py`：冻结 targets 可见性；旧 invalid_claim_predicate 一次转换、原 attempt 只观察；不重播 completed/无旧协议依据历史；无原件一次终态回执。
- 现有增量消费者用例从旧 `current_matters/memory_claim` 文本预期改为完整 `memory_evidence`，稀疏向量夹具复制完整新记录元数据。没有执行这些或其他用例。

## 明确边界

- Episode/Reflection 复用现有最新记录与 Sources；未捏造不存在的旧修订快照。known_at 遇到被改写、无法证明的更早记录版本会显式 `*_revision_history_unavailable`，未知 recorded_at 不进入已知事实集合。Matter 的过去有效时间来自报告时点时明确标记推断。
- 单个完整证据超过工具预算返回 413 与所需预算，未截断条件或冲突一侧。列表非空 query 的完整性限于词法筛选范围。所有模型和真实使用质量仍待父级整批集中验收；本文不表示检查通过、已部署或实机完成。

## 第三轮集中验收后的向量候选返修（2026-11-01）

- 基于集成 `f742edcc9547c780941a4d3a60f0b622fe079104` 新建
  `codex/memory-vector-repairs-20261101`；原 A/D 和前两轮返修分支/提交保留。
- 父级完整第三轮报告 97 项、96 pass、1 fail；旧 private fork、新增两节点与长消息
  尾部已经通过，不再修改。读取 `acceptance/backend-repairs-03.log` 的剩余失败：
  `test_sparse_authorized_vector_candidates_are_not_starved`，2100 Alice 私域近邻与
  96 Bob 范围远邻的夹具/12 条完整可读证据要求保持。原日志实际只有 vector_ann
  一条计时，5.171 秒 CancelledError，结果 0、missing=vector_query_timeout。
- 根因已由当前/历史实现静态对照定位：D 替换 `c80107b` 既有向量路径时，丢失了
  便宜 scope/profile 上界计数、小范围先 exact，以及 distinct 来源证明去重。
  新路径一律先运行带多次递归 Sources 谓词的 ANN 排序 SQL，仅正常返回不足 limit
  后才 exact；这次 SQL 已耗尽阶段预算，后置 fallback 无法执行。没有把未取得的
  EXPLAIN 计划或新性能结果当作已证实事实。
- 在同一 CandidateStore 恢复上述机制并推广至五类型：每 family 按 actor/state、
  业务/知识版本、profile、主体/事项以及真实 scope OR 条件计数，上界仍是既有
  2000/2001；计数不调用递归 Sources，不返回给模型，也不作为授权结论。
  Claim/Matter 加 scope_ids 重叠的必要条件，复用既有 claims_scopes GIN；实际 OR
  组授权继续执行，不能用 union scopes 全包含误伤可读独立支持。没有 principal 分区。
- 小域先 `scoped AS MATERIALIZED`，只保留 ID/revision/vector 和证明字段；
  再按 `(support_groups,derivation_refs,counterexample_refs 或 completion refs)` 的
  完整 tuple 去重，在 materialized support_sets 内核验真实 Sources，授权集合
  materialized 后才算距离。空的必要依赖等同于没有依赖，不重复构建空来源闭包。
  事项关系的结构预筛与实际关系 Sources 校验分开；其真实授权仍在距离排名之前。
- 大域继续现有 ANN、ef_search/max_scan_tuples；不足 limit 时复用同一个 exact
  实现。未修改向量 SQL 秒数、前台总 deadline、embedding profile、RRF/MMR/rerank
  或最后的版本/关系端点/完整来源复核；未新增索引系统、DDL 或第二份权限账本。
- 原稀疏用例的 2100/96 行和原断言保持，只加计数/exact 被选中且未先 ANN 的回归
  断言。新增 `test_vector_exact_keeps_five_types_and_public_or_support_across_owners`：
  不匹配词面的真实五类型向量证据、跨 owner 公开及 OR 支持、事实/推断保真，另涵盖
  matter_refs 的向量/清单与关系来源撤回。新增
  `test_vector_exact_uses_selected_knowledge_revision_before_validity`：无词面兜底时
  current/valid_at/known_at 真正选择不同向量版本。新增用例均 **written_not_run**。
- 父级最小受影响复验列表：上述稀疏原节点和两个新增向量节点；既有
  `test_withdrawn_counterexample_retires_interpretation_despite_independent_new_or_support`、
  `test_rerank_rechecks_sources_and_drops_the_whole_unit`，确认共享来源谓词和最终撤回
  边界。无需重复无关前端、private fork、长原文或全套产品用例。
- 仅静态阅读当前/旧源码、既有失败日志和差异，编写用例/交接；没有执行测试、
  解析、typecheck、build、PG、模型或生产操作。实际修复时延和通过状态待统一复验。

## 第四轮集中验收后的清单夹具修正（2026-11-01）

- 父级在 `a02b253` 完成五个定向节点，四项通过；原 2100/96 稀疏向量已返回所需证据，
  本包不重改、不重跑该通过结果。仅新五类型节点的完整清单集合断言失败，实际多出
  一条 Episode；后续关系来源撤回段尚未运行。
- 静态对照失败日志和真实调用链确认：`save_matter` 在领域事务内调用
  `domain_changed_on`，以正式 Matter ID/revision 和实际命令依据生成稳定
  memory-revision Episode，`matter_id` 明确就是该 Matter，actual_result 保存
  internal/event_kind/changed_records/affected_subject_ids。Episode 查询的
  matter_refs 条件正确使用 `r.matter_id`，没有查询漏筛。夹具只索引显式记录，
  这条真实命令观察尚无 embedding，所以向量结果没有它，完整清单应包含它。
- 夹具现在通过现有授权 Episodes 接口取得唯一匹配 Matter 的观察，精确核对
  origin、当前人物、实际 changed ref、来源、scope 和 Sources 当前合法性，再以
  实际 ID 断言完整清单恰为 Hypothesis、Matter、命令 Episode 三项；另核对该
  Episode 在此夹具仍未索引，解释两条查询路径的差异。
- 命令观察默认没有原始覆盖报告，coverage_unknown 继续显式保留；清单没有下一页，
  但不能把这条证据的未知覆盖写成完整。此点在原集合先失败后尚未执行，现一起修正
  为精确缺项断言，没有改动产品 coverage 或隐去缺项。
- 保留原关系来源撤回后的向量 Matter 单项断言，另精确核对撤回后清单仍有独立来源
  支持的命令 Episode，旧 Hypothesis 的事项关系消失；再次验证观察的当前来源。
- 仅修改该定向用例和本交接，没有改产品行为或放宽为任意额外项。**代理未运行测试、
  解析、build、PG 或模型**。最小待复验仅
  `tests/core/test_memory_evidence_retrieval.py::test_vector_exact_keeps_five_types_and_public_or_support_across_owners`。
