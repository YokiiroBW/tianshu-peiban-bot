# NR-09B 检索响应与重排

基线 `c8ff95113b90cabf5805f9b633f0ff829cc132a2`，分支 `codex/nr09-retrieval`。旧 NR-08 分支及提交保持；工作树开始时 clean。本块只改 Memory 检索、同域预算/文本，以及指定检索测试；无 DDL、共享注册或 Conversation 改动。

## 实现

- `_ranked` 原生消费 A 的 `CandidateSelection.rows/missing`。词法部分结果及其缺项保留，超时未提供结构时显式标记该支路缺项，向量仍可推进。历史覆盖查询的阶段失败也不再直接丢失其他已验证结果。
- `optional_failure` 复用 Models 暂时错误分类，仅允许超时、SQL 取消、明确连接/事务暂时错误及可选模型能力不可用降级。可选 embed/rerank 未配置、路由不可用或能力未验证保留文字回退；权限、来源/身份上下文、坏请求和外部 `CancelledError` 均传播。不修改全局 Models 重试或配置。
- 父业务截止始终保持，阶段异常先检查父截止；父已到期仍传播。现有两次 30 秒模型执行窗口、队列等待余量、前台默认 70.6 秒预算及 SQL 短窗口均未放大。后台 candidate 入口继续无业务截止，排队不耗执行窗口。
- 准备阶段仅在有重排预算时检查所需重排候选。最终阶段只按实际需返回数量 reread、完整支持/关系 hydrate、Sources 和 Identity display 复检；失效时继续补下一批。整个补位共用同一只读事务与既定阶段截止，不为每批刷新；后续阶段超时只保留已完整复检单位，并标记缺项。
- `_checked` 事务入口只在当前事务关闭 JIT。A 的 lexical/page/has_vectors/coverage 自有事务分别设置，双方已确认不在 reread 重复设置。列表分页仍走原快照/游标合同，必需 page 或最终检查失败不会伪装成完整空列表；公共 `Page/Coverage` 形状不变。
- `rerank_text` 直接复用 `index_text` 的语义字段，补主体/说话者/当前昵称及关系两端含义，保留否定、条件、时间、完成依据、实际结果状态和反例；不截断语义。重排不发送 source/derivation 台账、完整机械回执或 coverage。`index_text` 原函数未改，无需重建索引；答案及上下文继续使用完整 `evidence_text`。一次重排失败沿基础排序回退。

## 集成接口

A 导出 `memory.candidates.CandidateSelection` frozen dataclass（`rows: tuple[dict,...]`、`missing: tuple[str,...]`）；不保留旧 list 兼容分支。A 的精确缺项 `lexical_candidate_timeout:<family>`、`lexical_source_budget:<family>` 原样透传。`page` / `vectors` 返回合同原样。

局部失败另使用 `lexical_candidate_timeout` / `lexical_candidate_unavailable`、`memory_coverage_timeout` / `memory_coverage_unavailable`、`memory_candidate_check_timeout` / `_unavailable`、`memory_final_check_timeout` / `_unavailable`；均位于既有 `Coverage.missing`。原有向量、重排缺项仍保留。

页面、工具、聊天和整理候选继续调用同一 `query/search/candidates/revalidate` 实现。无需根增加路由、模型绑定、配置或迁移。

## 精确验证目标与限制

只执行 `git diff --check`，通过；没有运行测试、编译、应用导入、构建、生产、真实模型或 QQ 调用。

根集中执行本块三个文件，避免扩大到未变全套：

- `tests/memory/test_retrieval_degradation.py`：部分词法结果及向量继续；词法超时/SQL取消/连接故障；SQL权限和坏查询不得降级；重排超时基础排序；可选能力未配置；各阶段权限/来源上下文/外部取消；最终撤回补位与单事务固定截止；后续复检超时保留已查单位；父截止；列表完整分页；后台执行窗口；重排语义与台账剔除。
- `tests/core/test_retrieval_timing.py`：共享入口 fake 改为新 `CandidateSelection`；复用前台 chat/tool/query 两个 30 秒执行窗口、短父截止和冻结请求用例。
- `tests/core/test_memory_evidence_retrieval.py`：复用真实来源组合、五类、时态/list、重排撤回及 Context/工具用例；新增 `test_rerank_final_recheck_fills_withdrawn_top_result_without_hydrating_all_candidates`，真实 Sources 撤回后 limit=1 的 reread 批次为准备 3 条、最终 1 条失效、再补 1 条，且同一 final 连接；原重排测试增加源台账未发送断言。

新增测试属于隔离夹具与受控模型，不能证明生产延迟或真实供应商语义质量。实际 HTTP 检索、自然聊天缺项与性能由根按 NR-09 授权串行验收；不可将原候选微基准当成端到端结论。

## MMR 按需前缀返修

根静态审查指出首版提前计算完整 128 候选的 MMR 顺序。本次使用同域 `_MmrOrder` 按需扩展并缓存已选前缀/相似度最大值，常规返回只计算所需轮次；准备阶段只取最多 `max(limit,24)` 个完整单位，最终失效补位才继续原 MMR 顺序。最终补位仍在原事务和固定截止内。每次遍历可从缓存前缀重放，准备超时取出但尚未完整校验的候选仍优先进入最终检查，不因 iterator 消费而丢失。

新增 `test_mmr_only_computes_the_needed_prefix_and_extends_in_the_same_order_on_refill` 四参数，128 候选下计数少量返回/一次重排/源撤回续取的实际比较次数，使用两组正交向量验证多样性排序；新增 `test_preparation_timeout_replays_the_most_relevant_unchecked_prefix_for_final_validation`，证明前置 24 条超时后仍重放最高相关前 6 条且不重算前缀。独立返修仅做 diff 检查，未运行测试、编译或构建；根集中验证上述新目标并复用其他有效结果。
