# RBW-retrieval 交接

## 版本与范围

- worktree：`C:\Users\Administrator\.codex\worktrees\114e\tianshu-peiban-bot`
- 分支：`codex/rbw-retrieval-20261105`
- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`
- 领域代码及测试提交：`b708a995965d707a5b5f541beb04497824167325`，补充原文检索与重排修复：`d137a6a91b9bbb33fbaca5e36aafdb6a61cbb2b6`；本交接另行提交。
- `.codex/config.toml` 是主协调者关闭 Fast 的已有未暂存修改，不属于本提交。

## 本包实现

- 结构化筛选优先；词法及向量独立并行、各保留成功结果，候选来源按 AND/OR 批量证明并有界补位。中文完整词、双字和合法单字均参与召回；延用 RRF/MMR，仅在需要时重排。默认本地向量及来源候选窗口为 2.0 秒，嵌入与重排仍是各自独立的 30 秒执行窗口。
- `memory.search_vectors` 是按实际嵌入空间分区的可重建投影；不同模型/预处理空间不混查。索引身份包含代际、对象版本、模型空间和规范化输入摘要。原语义材料不变且投影缺失时可观察原成功回执恢复，材料改变时另建任务，保留原回执；晚到写回重新检查权威来源、当前 head、内容及 generation。
- `current`、`known_at` 与 `valid_at` 分开读取实际保留的 Episode 版本，`valid_at` 对 Episode 只按发生时间过滤并注明限制。学习正文与状态归 Growth；Memory 只保存索引词和版本引用。行为规则和 reflection 分别经其公开解析口读取；原生工具返回 `behavior_refs`、`learning_refs` 和来源闭包。
- 来源补充失效时不再返回含补充内容的旧摘要或执行结果。写入域产生空摘要的新 revision 后，索引任务从 Sources 重取授权原文并重建词法，`episode_original_terms` 保留各历史版本的原文词法投影；原文正文仍从 Sources 读取。原来源及当前 head 仍须通过复核，缺失媒体标注 `media_unavailable`。
- 已接入索引物理 attempt 的 RecoveryPolicy 与可重建统计补投；原 unknown 只观察原回执。Operations 精确动作只唤醒领域已有工作，实际模型尝试及预算归索引 worker。

## 公开接口与 DTO

- `retrieval.bind_behavior_resolver(growth.resolve_behaviors)` 和 `retrieval.bind_learning_resolver(growth.learning_records)`；前者按 `{rule_id, revision, semantic_revision}` 解析，后者对反思使用 `{kind: 'reflection', id: proposal_id}`。返回的反思证据另带 `learning_ref={kind,id,revision}`；行为证据带 `behavior_ref`。同 ID 多个行为版本按 `requested_revision` 和 `requested_semantic_revision` 对应，历史行为正文按 `historical_revision` 确认。
- `await retrieval.index_readiness(context, records, budget=None, *, profile_key=None)` 接收最多 128 个 `RecordRef` 或 `{kind,id,revision}`，按输入顺序返回 `{kind,id,revision,profile_key,generation,state}`，`state` 为 `ready/pending/failed/unavailable`。只有当前可授权版本及其当前内容摘要对应的实际向量存在才是 `ready`；排队或旧成功本身不代表 ready。省略 profile 时读取当前真实 embedding profile。
- `retrieval.query(...)`、`candidates(...)`、`search(...)`、`revalidate(...)` 支持 `data_generation=原UUID`。未显式传入时通过 Runtime 的 `current_execution_generation()` 取得当前 worker 已冻结的原 generation；非 worker 前台为 None。后台没有该上下文的调用方必须显式传原值。
- `index_jobs.index_object(kind, object_id, revision, generation)`、`index_jobs.run(index_id, completed_attempt_id=None, generation=...)`、`index_jobs.recover(generation)` 和 `backfill_statistics(generation, limit=64)` 均要求原冻结代际。`memory.index_object`、`memory.index_retry`、周期 `memory.recover`、显式重建及所有重排队 payload 必须在原事务冻结并传播此值；`JobSpec.data_generation` 和模型完成通知都必须是同一值。不得省略旧异步任务的值后重新绑定当前代际。
- `index_jobs.bind_observability(statistics=..., recovery_policy=..., guard_on=...)`；窄恢复口为 `recovery_inspect(recovery,target)`、`recovery_invoke(recovery,target,facts,attempt_id)`、`recovery_observe(...)`，以及按原记录重建可信身份的 `context_for_recovery(kind,id,revision)`。`target={kind,id,revision}`。注册 `memory.index.rebuild` 时使用 `domain_managed=True`，错误族为 `memory_index_failed`、`memory_index_missing`、`memory_vector_candidate_budget`。
- `await index_jobs.recovery_candidates(limit=32)` 从当前代际、当前 profile 的索引与原 attempt 账本只读筛选，返回 `{eligible,due_at,dependency,error_family,version,material_id,role,target}`；不会把 unknown、运行中、耗尽或失权当可恢复。投影缺失时 `due_at` 是该任务最后成功状态更新时间，不能解释为投影丢失的发生时间。
- Growth 写入投影时调用 `put_learning_reference_on(connection, context, *, rule_id, revision, semantic_revision, kind, source_refs, scope_ids, search_text)`；源撤回使用 `purge_learning_references_on(connection, source_refs, kind, *, actor_id=...)`。Memory 写入域需经自己的公开 `index_learning` 端口装配，不能直接访问 Growth 私表。

## 必须的集成与迁移

1. 在 Runtime 新代际/恢复合同、原 `0023_memory_index_retry.sql` 和写入域 `0025_memory_write.sql` 之后注册并执行 `src/tianshu/memory/rb_retrieval.sql`。本 DDL 的 `index_jobs.generation` 与 `content_hash` 为非空，按本次已授权的全新空业务库执行；不是保留旧业务数据的在线迁移。其 Episode 查询依赖写入域的 `episode_revisions` 和 current/history `supplement_refs`。
2. 写入域将 `MemoryQueryPlan.evidence_kinds` 默认的 `reflection` 改为 `learning`，提供 `MemoryEvidenceUnit.behavior_refs`，移除旧 Reflection 表、codec 和 HTTP 的存活引用。共享 `ContextContribution.behavior_refs`、Models `ModelRequest.behavior_refs/behavior_purpose/data_generation`、`JobSpec.data_generation` 和 `current_execution_generation()` 均由其归属窗口提供。
3. Models 的 `embedding_profile()` 需返回实际 `route_id/route_revision/provider_id/model/quota_group`；固定路由与版本，并在真实出站前重新验证所有行为依赖。异步提交事务必须校验 request 与 completion 的冻结 generation，不能在 reset 后按新代际提交旧请求。
4. 主装配注册本 SQL、两个 Growth 解析器、statistics/RecoveryPolicy/guard、domain-managed RecoveryAction、`recovery_candidates` 及前端 readiness。Runtime 的 `memory.index_object`、`memory.index_retry`、`memory.recover` 注册及其所有写入方需按上述签名接线。部署示例和新装默认配置也要同步 `vector_sql_seconds: 2.0`，显式用户值保留。

## 已知限制与集中验收

- 图片补充失效到写入域清理并生成新 revision、索引任务重建之间，检索不会利用已失效的旧摘要；新 revision 的关键词投影是异步恢复的。原文重取及索引材料目前每条最多读取 24,000 字符，超过时读取结果标记 `original_text_partial`；这不是 token 用量。无有效原文时不会为占位文本生成向量。
- reflection 在候选序列中保留本地排名位置，不把其正文送入重排；其他候选仍可重排。跨域统一的非行为学习依赖尚无下游冻结/复核字段。历史 reflection 只在 Growth 能给出所请求的真实版本时返回，不将当前正文冒充历史。
- 按本批授权，此窗口未运行测试、构建、烟测、真实模型或生产操作。已完成静态 diff 审阅、`git diff --cached --check` 和 16 个 Python 文件的 AST 静态解析；不代表运行通过。
- 主协调窗口集中运行新增 `tests/memory/test_rb_retrieval_behavior.py`、`tests/core/test_rb_retrieval_index.py` 及现有受影响的 Memory 查询/索引回归；重点覆盖大 AND/OR、授权撤回后的补位、历史与补充撤回、中文、profile 切换、同版本内容变化、unknown、物理恢复预算、代际切换和 UI readiness。再核对同规模召回质量/延迟及实际模型、业务路径，不用容器健康或队列为空替代真实验收。
