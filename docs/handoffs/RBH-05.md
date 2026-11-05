# RBH-05 Life：完整唤醒与连接恢复

基线 `ec245f9e30f935f7bcadbc441373a45c9f9d07a4`，分支 `codex/rbh-life-20261105`。本文件随本卡提交，精确提交 SHA 随回执提供。实现窗口不测试、不构建、不操作生产。

## 变更

- `PlanningEngine` 在可用角色和例程的正常返回前，锁定当前 watermark 并重新安排仍有效的最早 `repair_due_at`/`review_after`，继续使用原 `life.plan` 队列及 actor 合并锁。更早观察、模型完成或能力通知合并后运行，不会丢掉后续到期任务；并发变更按最新 actor 指针处理。停用、明确取消及终止状态不恢复；睡眠期间的到期唤醒不早于实际起床时间。
- 规划与日记/章节的 `GenerationRecovery` 通过 Runtime `computation_budget_on` 代替领域总 attempt 次数判断；序号和原三次计算额度保留。原 Models failed 回执按既有流程归档后交 Runtime `note_not_sent_on`，只依其 `can_retry`、`retry_at` 与原依赖门决定是否重排，不在 Life 判定未发送证据或识别证书类别。
- 原回执通过现有 `GenerationRequests.validate_observed` 绑定冻结请求，另核对 intent 和代际；新 attempt 不覆盖原失败或 unknown。来源校验先于领域/恢复锁；若来源撤回，保留物理失败但不分类、不产生后继。日记和写作的薄装配将 Life 来源能力注入 `GenerationRecovery`。
- `RBGPlanOperationsRecovery.reconcile_transport_waits` 扫描原 Life 账本，逐条向 Models 取得原回执并交 Runtime 分类，先处理旧记录、最后处理当前原失败。规划仍走 `life.plan`；日记/章节仅在原对象、来源、授权、版本、代际和当前回执有效时重新安排原 `life.diary_generate`/`life.chapter_generate`。自动日记仅在原日期和材料 revision 未变化时恢复原 generation 关联。取消、撤回、过期、已发送 unknown 等不会被当作明确未发送；恢复队列不表示业务完成。

## 主协调者装配

1. 将 `src/tianshu/life/rbh_transport_recovery.sql` 注册到正式产品迁移和 Life DomainModule。它只增加当前 transport-wait 和原 lineage 查询索引，不建平行账本、不修改业务数据。执行迁移后再启用周期入口。
2. 复用现有 `RBGPlanOperationsRecovery(worker, guard_on)` 实例，在 Operations 既有周期调用 `await adapter.reconcile_transport_waits(limit=32)`，返回 `inspected`、`scheduled`、`ineligible`、`changes`。单次最多检查 `limit` 条原记录；实例的非权威游标遍历结束后间隔十分钟再扫，重启可安全重复。需要人工立即重新扫描时可调用 `force=True`；它从头扫描一批，并不指定目标 ID。不要将该方法当作模型执行或业务成功。
3. 必须先集成 Runtime RBH-01 的 `note_not_sent_on`、`computation_budget_on` 及其迁移，和 Models RBH-02 权威回执证据；不得让消费者自行读取 Models 表或复制证据判定。原 request/receipt、attempt 单调序号、来源/CAS 和 `data_generation` 均保留。`GenerationRecovery` 的正式构造由 `DiaryService.bind_observability`、`WritingService.bind_observability` 提供 `life`，非产品替身如有直接构造需同步。

## 定向验证与限制

新增 `tests/life/test_rbh_planning_wakes.py`：真实队列合并后的提前观察、未来重排和到期单一后继，重复完成/能力通知，睡眠、停用及取消。新增 `tests/life/test_rbh_transport_recovery.py`：连续四次确证未发送、矛盾证据按原计算额度消耗、原 intent 不符、撤回时保留失败、旧规划与自动日记的版本/取消边界。`tests/life/test_rb_plan_recovery.py` 的 Runtime 替身已同步有效预算接口并保留原恢复断言。

建议根集中运行以上用例及受影响的 `test_computation_retry.py`、`test_rb_plan_recovery.py`、`test_rbf_generation_recovery.py`、`test_rbf_planning_recovery.py`、`test_rbg_plan_retirement.py`，使用隔离 PostgreSQL/pgvector 和已装配的新 Runtime/Models；以实际结果为准。本窗口只静态审阅和解析，不宣称测试、部署、真实模型或业务验收通过。
