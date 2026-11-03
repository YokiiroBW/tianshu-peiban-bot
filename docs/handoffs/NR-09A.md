# NR-09A：分批廉价候选与完整来源证明

基线 `c8ff95113b90cabf5805f9b633f0ff829cc132a2`，分支 `codex/nr09-candidates`，受管 worktree `C:/Users/Administrator/.codex/worktrees/memory-materials/tianshu-peiban-bot`。开始时 tracked/untracked 均干净；NR-08 分支保留。

`CandidateStore.lexical()` 原生返回 `memory.candidates.CandidateSelection`，frozen dataclass 的字段为 `rows: tuple[dict, ...]`、`missing: tuple[str, ...]`。NR-09B 直接消费这两个字段，需与 B 集成后再运行检索用例。没有为旧 list 返回值保留转发路径；`page`、`vectors` 合同保持。

词法通路复用 `selection()` 的角色、范围、主体、文本和时态筛选，在一个只读事务内按 `relevance DESC, recorded_at DESC NULLS LAST, ID ASC` 建立 PostgreSQL 命名服务端游标。每次取 128 行廉价候选，来源过滤后不足便继续取下一批，直到各族原有数量上限、游标耗尽或原截止到期；不固定截取前 128 行，不使用 OFFSET 或会因排名/版本移动而漏项的跨语句游标。返回的各族上限及最终 256 条排序与原路径保持。窄投影保留 ID/版本、来源校验输入、MMR 所需 `lexical`/`_content` 和排序字段，不传未使用的 embedding 大列。

`candidate_sources.py` 只组织证明，权限和递归来源闭包仍由现有 `Sources.check_many_on` 裁定。备选 supports 是 OR；同组来源、derivation、counterexample/completion 依据是 AND。按事项过滤时，至少一个匹配的 `about_matter` 关系自身的 OR supports 与 AND derivations 也必须通过。不同 locator 保持独立，不把相同 source ID 当作相同范围。证明只在当前读取批内去重，不缓存跨阶段或跨请求授权。

每个 Sources 调用保持至多 1,024 组和 2,048 唯一来源键。大 AND 组合法拆分后必须全部通过；依赖闭包聚合过大时在 savepoint 回滚后继续拆批。单个根仍无法在 Sources 的完整深度/依赖上限内证明时保留 unknown 并过滤该候选，不削减依赖或把它标为已授权。可读 OR 替代能够独立证明时仍可使用。

整个词法事务使用原传入 budget，不为家族、候选批或来源批创建新截止。`TimeoutError`/SQL `QueryCanceled` 保留此前完整通过的批次并返回 `lexical_candidate_timeout:<family>`；无法完整证明的单根来源返回 `lexical_source_budget:<family>`。权限/输入/上下文错误与外部 `asyncio.CancelledError` 传播。异常时由家族 savepoint/外层事务销毁游标，避免在失败事务中执行 CLOSE 而替换原异常。

JIT 仅在候选短读事务内关闭：lexical、has_vectors、page、known-at coverage。vectors 复用已有事务内设置。NR-09B 在最终 `_checked` 的新事务内关闭 JIT，A 未向 reread 叠加相同设置。最终来源/身份/版本复检仍由 B 保持。

新增定向验证目标：

- `tests/memory/test_candidate_sources.py`：OR/AND 与关系来源、超过普通来源批上限、依赖限额拆分及 unknown、locator/权限失效、真实任务取消传播。
- `tests/core/test_candidate_selection.py::test_lexical_continues_past_unreadable_batches_with_a_stable_cursor`：前 260 条来源无权、后续可读；查询期间另事务前插，不漏后面的可读条目，下一请求看见新条目。
- `tests/core/test_candidate_selection.py::test_lexical_matches_full_source_query_for_empty_chinese_and_temporal_plans`：空/中文/英文查询 × current/known_at/valid_at，四族 ID/版本/分数/顺序与原完整来源 SQL 相同，历史认识和事项版本按时态选取。
- `tests/core/test_candidate_selection.py::test_about_matter_requires_the_relationships_own_complete_sources`：事项关系必要来源失效后，即使记录本身可读，也不能通过该关系返回。
- `tests/core/test_candidate_selection.py::test_interrupted_family_keeps_verified_batches_and_releases_its_cursor`：真实 statement cancellation 和 TimeoutError 两场景，保留前批且继续其他族，原连接游标清空、可继续查询、JIT 设置恢复。
- `tests/core/test_candidate_selection.py::test_real_task_cancellation_and_context_revocation_propagate_cleanly`：真实 SQL 等待中取消任务不被降级，连接/游标/JIT 干净退出；过期上下文传播权限错误。

实际验证仅静态 diff 阅读与 `git diff --check`，未运行测试、构建、应用、生产或真实模型。候选微基准属于根此前同快照可行性证据，不代表本补丁已获得真实端到端耗时或聊天验收；根集中测试、部署并读回后确认。
