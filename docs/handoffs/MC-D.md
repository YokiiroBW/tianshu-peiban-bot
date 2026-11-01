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
