# NR-06C Memory 向量索引持久恢复

- 基线：`c218515c21cb4b221dbda390aada8c9141693e46`。
- 分支：`codex/nr06c-index-retry`。
- 产品提交：`217af414df78f111fba2d63209f768fab88f5211`。
- 状态：本地实现与静态检查完成，未运行测试、构建、真实模型或生产操作；由根统一集成验证。

## 实现

`memory/index_jobs.py` 是唯一索引计算写者，原 `memory.index_object` / `memory.index` 入口委托它。保留原 canonical evidence/index_text、关键词先提交和 Source/Identity 校验；没有改变事实、多人归属或整理算法。嵌入新请求执行窗口仍为 30 秒，普通排队不占执行时间；extract 的原 300 秒策略未改。

`index_jobs` 持久记录 kind/id/revision/profile、状态、尝试数、next_due、last_error 和 owner lease；`index_attempts` 保存各次独立 intent/step/attempt 关系。失败后释放 owner 与执行位，使用 B 的同一分类/30、60、120…900 秒封顶退避。到期先读原结果；queued/running 只观察，已成功消费原结果，可恢复失败才选择新的后继 attempt。原 unknown ledger/model_steps/request 不伪改成功、不重发。后继选定后仅当前 attempt/owner 可提交，迟到原结果不再写回。

显式取消与来源/权限撤回、当前 revision 淘汰、profile 换代、永久输入/配置错误不自动重试。可信 `receipt.cancellation_reason == 'explicit_cancel'` 优先停止逻辑工作，即使旧物理结果仍是 unknown/consumer_interrupted；创建后继前重新查询该字段。旧 attempt 保留其物理 unknown/error，而不是伪造 cancelled 回执。

每份 logical work 用现有 JobQueue `queueing_lock` 合并一个 todo 唤醒，任务名 `memory.index_retry`，始终走 `MEMORY_INDEX`，没有第二队列。新 revision/profile 有独立 logical ID，不被旧重试占位。lease 在等模型排队时续租；失去 owner 后不选择后继、不提交向量、不清除后来 owner。

缺向量普通恢复使用 4 个部分索引与持久 keyset 游标，每类每轮最多 24 行；到期 logical work 另取最多 64 行。游标跨轮前进并最终回卷，扫描不以关键词为空为唯一条件，不为已存在的 todo 重复造任务。配置不可用时关键词仍能恢复；无有效 profile 不发送嵌入，永久配置失败终止对应 placeholder。

自动 Claim 队列与显式 hypothesis 重建原先有不同标签，本次按实际 semantic kind 合并 logical work，首个计算采用已有记录中的原 step/attempt。`Embeddings` 恢复以原冻结 request 为底本保留 timing/scope/route，仅将当前语义输入和权限依赖交给原 ModelSteps identity 比较；仍检查当前 profile。旧 wire 缺 scope 按 A 的 conversation 合同规范化比较，原 JSON 不回写；新后继使用新 ModelRequest 的 intent 默认。

## 依赖与装配

- B helper：`249326745a76e5cfb9875a8847aa6450069c968b`、`afd44032d3da516179816038b5bd8271edc4113a`。本分支取用的等价 SHA 为 `ad90aec5`、`9fa254a9`；根已集成原提交时只取 C 产品提交，不重复取 helper。
- A scope 接口：`d6139830`；另依赖 A 的可空 `ModelReceipt.cancellation_reason` 与显式取消优先投影。对应接口用例必须在 A 集成后运行。
- 根单写共享注册：`memory.index_retry -> ExecutionLane.MEMORY_INDEX`；迁移注册精确名 `0023_memory_index_retry`，资源 `tianshu.memory/0023_memory_index_retry.sql`。Memory module 已注册本域迁移和任务。
- 现有 MemoryIndexRebuild 已明确“排队不代表索引已经完成”，本卡不改 UI 或公共 Web API。

## 0023 DDL 与回滚边界

只新增 `memory.index_jobs`、`memory.index_attempts`、`memory.index_scan` 三张表，5 个索引及 4 条游标初值。不回写既有事实、embedding、Sources、Model ledger 或 model_steps。正常代码回滚可保留这些附加表及审计；需要先停止该受影响 lane 的新任务消费，并通过原生队列操作收敛精确 task `memory.index_retry` 的未开始 todo，不能把 doing 或 unknown 原请求当成可重发任务。已合法提交的向量与旧检索结构兼容。不要为回滚清除原尝试链或整个 Memory 域。

## 当前 7 条旧索引的自然恢复

无 ID 白名单、无历史恢复 API。正常 memory.recover 扫到当前有效、无 embedding 的记录（lexical 非空也包括）后建立 logical work，采用当前相同 profile 下已有的 `memory-index:<kind>:<id>:<revision>:<profile>` step；原成功可消费，原 unknown 保留，并在退避到期再次观察后创建独立后继。源撤回/版本变化/profile 变化会停止对应旧工作。若 A 的历史取消投影给出 explicit_cancel，遵守其保守终态，不按这 7 个 ID 例外绕过；需根核对真实取消证据。真实恢复效果仍待根部署后验证。

## 最小统一验证目标

本代理仅执行 `git diff --cached --check`（通过）并静态阅读调用链，没有运行下列用例。

1. `tests/core/test_memory_index_retry.py`：16 个展开用例，覆盖让位/持久退避/重启、queued/running 观察、显式与永久取消、来源/版本/profile 写回竞态、7 条旧 nonempty-lexical unknown 自然补齐、别名合并、旧 scope/新 scope、后来显式取消优先、owner 接管、keyset 界限及 5 类语义对象的统一写者。
2. `tests/core/test_retrieval_timing.py::test_existing_index_step_keeps_frozen_timing_and_rejects_changed_payload`（原 2 个参数例）。
3. `tests/core/test_retrieval_timing.py::test_new_index_step_gets_thirty_seconds_and_unavailable_step_read_never_submits`。
4. `tests/core/test_memory_evidence_retrieval.py::test_withdrawn_counterexample_retires_interpretation_despite_independent_new_or_support`（原依赖捕获替身补了 profile 接口，仍断言反例来源不得省略）。

B 的 `tests/models/test_retry.py` 由根与 B 统一执行一次即可，无需 C 重复验证。
