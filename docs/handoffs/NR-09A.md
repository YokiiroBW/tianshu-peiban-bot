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

## 实机向量支路补齐

根在运行 `89170d85458774309d921344295fc236d28604a6` 确认词法已为 0.507/0.2266 秒且无词法缺项，但完整查询仍有 `vector_query_timeout`。旧 exact 向量路径仍对完整域先做 Sources 递归证明；原 0.8 秒窗口外的只读定位也显示约 2.3 秒乃至 10 秒未结束。这是尚未覆盖到的同根因，非模型排队问题。补丁基线改为该实际运行提交；旧 A 分支因根 cherry-pick 历史无法 fast-forward，完整保留，在同一受管 tree 的 `codex/nr09-vector-candidates` 分支继续。

词法、向量与 profile 存在性读取复用同一证明投影和稳定批读取器。向量先做原 scope/版本/profile 筛选与廉价 cosine 距离排序，再按批调用相同 `check_rows_on` 完整校验；不足继续，直至足量、耗尽或原预算到期。exact 用距离 `+0` 和 ID 稳定排序以确保完整 scoped 域精确排序，取批前没有全域递归来源证明。返回 `vector` 真实值供原 MMR 使用；原 `vectors -> (rows, bounded)` 合同不变。

保留 `EXACT_VECTOR_CANDIDATES=2000` 原分流。较大域仍保留原可使用 HNSW 的 `ORDER BY embedding <=> vector LIMIT limit`、strict_order、ef_search 和 max_scan_tuples，未加 MATERIALIZED/二次排序或全局 PG 设置；该原始 ANN 前缀授权后不足便走新 exact 游标续取，不把 ANN 前 N 条当作完整空域。exact 回退若晚批失败，已证明的 ANN 与 exact 行按 ID/版本去重、距离排序后保留。SQL/截止超时或不可证明来源都会如实返回 `bounded=True`，但权限/上下文/外部取消传播。

`has_vectors` 不再做全域递归 EXISTS。它按廉价 scoped/profile 游标寻找并完整证明一个可读候选；证明失败的前批不会挡住后续可读条目。穷尽所有候选后才能返回 False，只有来源预算 unknown 而无法证明空时抛 `AppError('memory_vector_candidate_budget', status=503)`。与 NR-09B 新窄接线配套：仅在 vector_candidates 阶段映射为 `vector_candidate_budget`，检查父截止并保留成功词法，不调用 embedding。没有用 TimeoutError 冒充来源预算 unknown。

补充定向目标（仍仅编写，未运行）：

- `tests/core/test_candidate_selection.py::test_vectors_and_profile_probe_continue_after_260_unreadable_nearest_rows`：向量/存在性均越过前 260 条无权候选；保留实际 vector；隔离其他 profile/权限；来源预算 unknown 不伪报空。
- `tests/core/test_candidate_selection.py::test_exact_vector_batches_match_authorized_distance_order_and_required_sources`：与原完整来源/距离 SQL 的 ID/版本/分数/顺序一致；OR supports、AND derivation/counterexample 不丢。
- `tests/core/test_candidate_selection.py::test_about_matter_requires_the_relationships_own_complete_sources`：原词法关系用例增加向量及存在性证明，并覆盖事项 completion 依据不可读。
- `tests/core/test_candidate_selection.py::test_ann_shortfall_uses_exact_batches_only_when_needed_and_preserves_partial_fallback`：在测试中缩小分流阈值，实际执行 ANN 查询，模拟短结果后执行实际 exact；足量时不回退，晚失败不丢已证明结果。
- `tests/core/test_candidate_selection.py::test_vector_late_batch_failure_retains_proved_rows_and_cleans_cursor`：statement cancellation/Timeout 两场景，前 128 条已证明记录和向量保留，bounded=True，原连接/cursor 可用。
- `tests/core/test_candidate_selection.py::test_real_task_cancellation_and_context_revocation_propagate_cleanly`：参数化 lexical/vector，真实取消与上下文错误都传播，连接/cursor/JIT 退出干净。
- `tests/core/test_memory_evidence_retrieval.py::test_vector_jit_is_transaction_local_and_keeps_authorized_results`：仅适配私有 exact 新参数和‘局部 Timeout 保留有效行 + bounded’行为，原来源/JIT 断言保持。

建议复用并运行既有五类型/OR 支持、历史版本、空授权 profile 向量用例；共享扫描器装配变化由本文件原词法续取/取消用例覆盖。窗口、模型绑定、向量索引/DDL、来源权限规则与最终复检均未放宽。新增性能收益和真实缺项消除仍由根实际验证。
