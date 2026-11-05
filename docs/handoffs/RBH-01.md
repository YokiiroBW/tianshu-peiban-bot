# RBH-01 共享恢复预算、探针与分类指标交接

- 工作树：`codex/rbh-runtime-20261105`；基线 `ec245f9e30f935f7bcadbc441373a45c9f9d07a4`；实现和本交接同一提交，准确提交号以本文件所在提交为准。
- 在现有 RecoveryPolicy 账本上增加未发送连接失败审计。原 `attempts` 与 `attempt_number` 不变且单调；有效计算次数为总数减已确认未发送次数，计算上限沿用 `max_attempts`，连接次数上限为每 lineage 16。inspect、continuation、reserve、failure、unknown 的恢复、版本修复及两种 claim 使用相同判断；旧结果不能改变后继的 due/claim/共享探针。
- `note_not_sent_on(connection, attempt_id, *, owner, expected_version, model_receipt)` 与 `computation_budget_on(connection, lineage_id, *, owner, expected_version)` 返回合同中的九个字段。`can_retry` 仍须结合原有依赖、来源和权限门。`retry_at` 在已有持久 due 上合并未过期的 claim、共享 cooldown 和 probe expiry；无可续接 due 或预算耗尽时为 None。
- 证据在 Runtime 唯一校验，接受 RBH-02 四种精确 connector 类名与相位，检查 ModelReceipt 原 attempt、代际、finished_at、status/dispatch/error、零输出、完整建连时序以及结构化字段；旧回执只走严格历史条件。无证据、已发送、unknown 不抵扣。非 ModelReceipt 或 attempt ID 不符为 `recovery_model_receipt_mismatch`；无该 attempt 为 `recovery_attempt_not_active`；owner、版本/暂停/取消/撤回、代际分别为 `recovery_owner_mismatch`、`recovery_version_changed`、`job_generation_expired`。不足证据且预算可用时 reason 为 `not_confirmed_not_sent`；可用的确认证据为 `confirmed_not_sent`；预算不足返回对应的 `recovery_budget_exhausted` 或 `recovery_connection_budget_exhausted`。
- 证书和 TLS 错误不引入新的永久阻断状态；能否自动恢复继续由原 failure_category 决定。已归档为非 `TRANSPORT`/`INFRASTRUCTURE` 的失败即使得到连接审计，也不会因记账产生自动 due。`can_retry` 只描述预算及 lineage 状态，不代表失败已获准自动重试。
- 历史证据按原 attempt 一次记账；旧 attempt 的迟到证据不会移动后继。若较老证据恢复预算后，当前最新 attempt 是另一条已有 definitive failed 回执，领域再次观察该最新回执时可恢复其 due，但不能把该回执当成已证实未发送。
- 原 `transaction_errors`、`commit_errors` 等计数含义不变；各池新增 `recovery_waits`、`transaction_conflicts`、`sql_errors`、`commit_unknowns`。只按 RecoveryWait、明确 SQLSTATE 或现有 DatabaseFailure 分类计数；普通业务异常、Python 取消、查询取消和连接类 SQLSTATE 不冒称 SQL 故障。`acquire_timeouts` 保持原计数。

## 主协调者装配

1. 将 `src/tianshu/runtime/rbh_not_sent.sql` 作为名为 `rbh_not_sent` 的增量迁移注册到产品 fresh baseline 之后的升级序列及 `execution_migrations`，在任何新 RecoveryPolicy 查询前执行。该表只有审计标志、时间和身份，不存回执正文、请求内容、URL 或异常消息。清空天枢业务数据时按外键顺序先清除 `runtime.recovery_not_sent`，再清理 recovery attempts/lineages，或由协调者既有一致清理方案处理。
2. Memory/Growth/Life 从 Models 正常 status/result 获取原 `ModelReceipt`，在自己的事务中先完成正常失败归档，再调用公共分类；原冻结请求、intent 与 Models submission_digest 由领域使用 Models 既有语义核对。Runtime 的 request_digest 和 Models submission_digest 不直接比较。
3. 注册 Operations 指标消费并完成领域装配；原失败分类、未知结果、共享 probe、真实冷却和 Retry-After 不得由领域自行弱化。`computation_budget_on` 是一次数据库快照，只读结果不能替代后续 reserve 时重新校验。

## 交付验证与限制

- 已编写 `tests/runtime/test_rbh_recovery_policy.py`、`tests/runtime/test_rbh_database_metrics.py`，覆盖 4 次历史未发送、16 次上限、重复记账、真实发送/unknown/缺证据、身份/版本/代际/取消/撤回、历史迟到、多个 lineage 的过期 probe 接管、非重试失败分类、同步/异步数据库分类和累计兼容。
- 本窗口按授权只作静态代码审阅、Python AST 语法解析及 `git diff --check`，未运行测试、构建、烟测、生产或真实模型/QQ。运行时 SQL、迁移注册及跨域消费仍须主协调者集成后集中验证；本交接不表示业务恢复或生产验收完成。
