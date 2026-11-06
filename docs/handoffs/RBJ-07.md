# RBJ-07：Memory 索引恢复统计的有界补齐

## 交付与归属

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbj-memory/tianshu-peiban-bot`，分支 `codex/rbj-memory-20261106`。
- 准备点：`ec4b26a652d53d3dc40ab42a9f61734ff12e320d`；参考冻结公共合同 1.5：`a5e6155c1d6ad5a680e4b2f4a46aa7abf98adcd1`。
- 产品代码和用例提交：`7b05551685459130226ddd6c33d16631f7578202`；本交接作为后续提交。精确交接提交由最终回执给出。
- 超出原卡的唯一授权路径：`tests/core/test_rb_retrieval_index.py::test_recovery_statistics_backfill_uses_original_attempt_identity`；仅调整该场景的生产者覆盖预期及 Statistics 替身使用的既有锁接口。

## 确认的问题与变更

`IndexJobs.recover` 先读取 embedding profile、重扫对象并安排原索引工作，最后才调用已有的 `IndexRecovery.backfill`；前面出现未处理异常时不会到达最后一步。该问题是调用顺序上的静态确认，不证明现场某次索引覆盖缺失一定由它导致。

`MemoryWorkers.reconcile_due` 继续由既有 Memory 轻维护回调调用，并在同一代际下并发运行索引统计补齐页和 consolidation 唤醒页。两者使用独立事务与各自有界 `limit`；一方普通异常不会取消另一方，均结束后如实向既有维护状态传播异常。已提交的一方不会因另一方事务回滚。两者仍共享数据库和连接池，数据库或池整体不可用时不声称绝对隔离。

补齐继续复用 `record_attempt_on`、`StatisticsProjection`、`memory.index_stats_cursor` 和原事件身份/修订；没有新统计系统、队列、迁移或重新嵌入。游标行使用 `FOR UPDATE SKIP LOCKED`，与原 `IndexJobs.recover` 的补齐重叠时返回 `cursor_busy_or_missing`，不等待、推进或声称完成。缺失和繁忙在此返回值中有意保持不可区分。

此前 producer 在最后一页不足 limit 时直接标 `complete=true`；但游标分页跨事务，已扫描区间可以出现更早的新尝试或新 revision，投影失败标记本身也可能失败。一次遍历结束仅表示扫描走到尾部，不能证明当前所有源事实均已投影。现在 `memory.index.recovery` 的 producer 保持 `complete=false`：

- 在独立短事务复用 Statistics 的 `lock_producer_on`，只在旧 producer 标完整或代际不匹配时先标当前代际 partial；已有同代际 partial、失败原因与水位不因新一轮开始而被覆盖。
- 页内投影失败停在最后成功处理的位置，原 cursor 与标记在同一事务提交；完成遍历时内部游标回绕，但报告水位仍为实际访问的最后键。没有实际访问键时水位为 `null`，不会把重置后的空游标当覆盖边界。
- 页结果 `complete` 仍只表示本轮遍历结束，新增 `coverage=partial`；producer detail 包含 `data_generation` 及 `pending/failed/scanned_unverified`。后续循环会重访较早插入或修订，幂等性仍由原事件 ID、revision 与 Statistics 投影负责。
- 如生产者锁或降级本身不可写，不能伪称旧标记已被更正；维护调用报告失败，生产者状态须按 unknown 对待。此时不能仅凭可能滞留的旧 producer 行推断覆盖。

## 根装配与验证

无新增公开函数签名或 SQL 注册。现有 `memory.module` 的 bot-background 维护必须继续调用 `workers.reconcile_due(limit=32)`；生产装配必须继续向 `workers.index_jobs` 绑定现有 `StatisticsProjection`，包括其 `lock_producer_on`、`record_safely_on` 与 `mark_producer_on` 接口。根若调整共享 module/product，需保持此路径。非默认适配器若只实现后两个接口，需要补齐已有的生产者锁端口。

已编写 `tests/memory/test_rbj_index_statistics.py`：对象重扫异常后仍能有限补齐；跨页更早插入/修订与循环重访；投影中途失败的真实水位；旧完整标记降级后 marker 失败的回滚及两个维护路径的故障/阻塞隔离；cursor 锁竞争、旧代际拒绝和新代际从头扫描。既有原 attempt 身份、revision、去重场景已按 partial 口径调整。

实现窗口只做 Python AST 解析与完整 diff/空白静态审阅；**未运行测试、构建、烟测、PG、真实模型、QQ 或生产操作**。根集中使用真实隔离 PostgreSQL/Operations 投影执行上述新用例、原 `test_recovery_statistics_backfill_uses_original_attempt_identity` 和受影响的原 Memory 维护用例；必要时核对共享装配。实际生产 producer 水位、真实投影缺口及后续业务收敛仍待根只读核验，不能根据进程 ready、遍历到尾或少量对象有向量推断全覆盖。
