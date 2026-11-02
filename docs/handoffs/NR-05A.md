# NR-05A：Memory 嵌入及重排执行窗口

基线：`c11e99af07c9a98e4692651fde6ecf63e5b8a666`。分支：`codex/nr05a-retrieval-window-20261102`。代码提交：`3927794928c5ed1c3b48d79bbbf608bc36fbec6a`。

唯一写入工作树为本卡 `tianshu-retrieval-window-20261102/tianshu-peiban-bot`，没有使用已归档旧工作树。实现完成不等于测试、集成或部署完成。

## 变更与权衡

- Memory 的新嵌入及重排请求默认各为 **30 秒供应商执行窗口**，由 Broker 准入后计时。新后台索引嵌入不再从 120 秒准备预算的剩余时间推算执行时长；后台候选检索继续无合成业务期限或普通排队 TTL，各阶段完成后再取得后续 SQL 预算。
- `memory/retrieval_budget.py` 是此处预算的唯一归属。默认前台响应预算由启用的远程阶段和本地步骤推导：覆盖检查、词法查询、排序前及排序后来源/版本复核各最多 2 秒；向量入口另含原 1 秒 Profile 读取和两次原 0.8 秒向量 SQL。向量+重排默认 **70.6 秒**，只启用向量默认 **40.6 秒**，均为上限，不会固定等待。
- ContextAssembler、`memory/query` HTTP 入口和记忆工具复用同一预算。取消默认的聊天检索 2 秒、HTTP 5 秒且最多 10 秒、工具 20 秒的旧覆盖，避免新 30 秒阶段被全部跳过。没有调整整个聊天执行时长、全局 HTTP 超时或 RequestBudget 一般语义。
- 作者显式 `context.retrieval_seconds` 或 `memory.retrieval.request_seconds` 仍生效；聊天原上游 budget、Action 的真实业务 deadline 总是限制前台响应。可选远程阶段只在尚能保留完整执行窗口和最后复核余量时准入，剩余空间用于排队；前台超出响应边界可降级，后台不把排队算作供应商执行时间。
- 较长响应预算没有传成较长 SQL/Profile 超时：前台 Memory 本地查询集中经过同一个 2 秒 local budget，别名解析亦复用此边界；Profile 继续 1 秒、向量 SQL 继续原配置 0.8 秒。后台 SQL 既有独立数据库故障边界保持。词法候选、MMR、证据整单元、权限、来源重检及版本语义没有更换。
- 若真实上游期限短，或排队已经占尽可选响应空间，嵌入/重排可分别跳过并保留 `embedding_budget_insufficient` / `rerank_budget_insufficient`；词法证据保留。默认预算容纳两个串行阶段，不保证在长期拥堵下两个可选远程阶段都运行。

## 原冻结模型请求

新默认不能覆盖旧索引 step 的原计时。Embeddings 先沿既有 `steps.recorded` 查同一 `intent:profile`；只有准确的 `memory_model_step_unavailable`/404 才建立新 step，其他读取异常直接保留。

已有 step 仅继承其原 `execution_timeout_seconds`、`business_deadline_at`、`max_queue_wait`，再进入原 `steps.run` 校验其余完整请求身份。原 context、来源、输入文本、profile、attempt 及存储 JSON 不改，legacy 绝对时钟仍按原记录，unknown 只读原结果；更改 payload 仍触发 identity conflict。没有修改 ModelSteps、consolidation、workers 或任务队列实现。

## 根需调整的生产作者配置

按根已读得的实际覆盖做以下精确变更，保留其余作者选择：

```text
删除 context.retrieval_seconds                     # 当前显式 2.0 会继续使远程阶段跳过
修改 memory.retrieval.embedding_seconds: 1.0 -> 30.0
新增 memory.retrieval.rerank_seconds: 30.0
```

保持 `memory.retrieval.vector=true`、`rerank=true`、`vector_sql_seconds=0.8`、`ef_search=128`、`max_scan_tuples=20000`；`request_seconds` 在根提供的配置中没有覆盖，继续省略即可自动推导 70.6 秒。无需在 context 重复写 70.6，也不需要改 Profile、SQL、聊天模型、容量并发或排队策略。示例配置已移除 context 的旧短覆盖，并列出两个 30 秒执行值；示例原 `rerank=false` 保持，不替作者启用功能。

本执行者未操作生产配置、真实服务、凭据或模型。

## 文件边界

产品及配置：

- `src/tianshu/memory/retrieval.py`
- `src/tianshu/memory/retrieval_budget.py`
- `src/tianshu/memory/embedding.py`
- `src/tianshu/memory/module.py`（仅 query 预算及无用导入）
- `src/tianshu/memory/tools.py`（仅读取入口及别名 SQL 预算）
- `src/tianshu/conversation/context/assembly.py`（仅检索专属预算及无用导入）
- `deploy/core/settings.example.json`

测试：`tests/core/test_retrieval_timing.py`、`tests/core/test_memory_evidence_retrieval.py`。未写共享 docs、Models、consolidation.py、workers.py、runtime/jobs.py 或 Channels。无迁移、依赖或前端代码变动。

## 实际检查与待跑 targets

仅阅读定向调用链、审查自有 diff 并执行 `git diff --check`；**没有运行测试、typecheck、build、真实模型或服务探针**。根收齐 NR-05B 后集中执行：

```powershell
python -m pytest tests/core/test_retrieval_timing.py tests/core/test_memory_evidence_retrieval.py::test_profile_control_read_obeys_the_query_budget_without_starting_a_model tests/core/test_memory_evidence_retrieval.py::test_query_reuses_one_profile_and_leaves_provider_execution_time_after_queueing tests/core/test_memory_evidence_retrieval.py::test_background_candidate_vector_queue_does_not_expire_the_following_sql_phase tests/core/test_memory_evidence_retrieval.py::test_empty_authorized_profile_lane_skips_embedding_without_using_lexical_results tests/core/test_memory_evidence_retrieval.py::test_budget_skip_preserves_all_lexical_units_and_single_candidate_needs_no_rerank tests/core/test_memory_evidence_retrieval.py::test_rerank_rechecks_sources_and_drops_the_whole_unit -q
```

新用例穿过实际聊天组装、工具及 HTTP query 路由，证明默认会发 embed/rerank 两个 30 秒请求，短上游期限保词法降级、SQL 预算不放大；后台新 step 30 秒、非 404 读取失败不提交，旧 legacy/120 秒未知 step 的整份原 request 不变且更改 payload 仍拒绝。既有来源重检用例在重排期间撤回原证据，确认最终整单元剔除；原后台队列用例以受控短时长证明等待超过执行窗口不会消耗后续 SQL 时间。需要 PG 的用例依赖根显式隔离库，skip 不代表通过。

不用为本卡运行网页/QQ/生图或无关全量检查。以上是待验证目标，不是通过声明。
