# RBH-03 Growth 原批恢复与有效预算交接

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbf-growth/tianshu-peiban-bot`
- 分支：`codex/rbh-growth-20261105`
- 基线：`ec245f9e30f935f7bcadbc441373a45c9f9d07a4`
- 代码与回归用例提交：`4cee60d2335ea55a866b2502ec63c77dbfda2a13`；本文件由后续文档提交承载。

## 变更

- Growth 继续先归档原 Runtime attempt，再将合法 Models 原回执交给 `note_not_sent_on`；有效计算/连接次数和 `can_retry`/`retry_at` 均来自 Runtime。后继申请和 Operations 不再把物理 `attempt_number >= 4` 当成计算预算耗尽；实际 attempt 序号、原请求、原回执与历史均不重置。
- 失败回执先与冻结 request 的 attempt ID、intent ID 和 generation 绑定；非空 `request_digest` 必须等于现有 Models `submission_digest(request)`。旧回执缺少 digest 时不补造，也不声称已核对摘要：仍匹配原 ID/intent/代际，是否符合完整历史未发送证据由 Runtime 独立判定；空字符串或错误摘要拒绝。重试观察 unknown、终态和取消时也复核原绑定。
- 新增 `growth.retries.reconcile_transport_waits(limit=32)`，返回 `{inspected, scheduled, ineligible, changes}`。它有限发现有历史 `transport_not_sent` 的 blocked 批次，逐原历史 attempt 读冻结请求和 Models status，刷新原场景授权，在同一领域事务复核来源、输入版本、retry_key、当前 generation 和 row revision 后调用 Runtime note/budget，按原 retry_key 和 Runtime 持久 due 安排既有 `companion.growth.retry`。
- 最后一次可以是同原 ID/代际且真实失败的普通 transient：只有历史未发送证据已被 Runtime 确认、共同预算允许、持久 `retry_at` 存在且材料有效时续接；最新未证实未发送的尝试仍计计算预算。最新 unknown/缺回执、取消、永久错误、来源或版本变化、摘要不符不安排。真正已发送/unknown 不被改写成未发送。
- `companion.rbh_growth_transport_scans` 仅持久保存 generation、原 retry 身份、领域 revision 和下次检查时间。它使用 `FOR UPDATE ... SKIP LOCKED` 与五分钟检查租约，避免无证据行占满每次有限扫描或每个 tick 重查；领域 revision 改变时立即可重查。每批最多读取 64 个不同历史 attempt，超限返回明确 ineligible，不猜测或清理。并发最终由原 row revision CAS 和事务内入队防止重复安排。
- 回执绑定不复制 Runtime 的连接证据判定，不根据证书类名另建阻断；永久/暂停/撤回和共享探针语义继续使用现有 Runtime。控制面不确定的 `control_observation` 旧批仍只读观察，不被此端口放行。

## 共享依赖与根装配

1. 先集成 RBH-01：`RecoveryPolicy.note_not_sent_on(connection, attempt_id, *, owner, expected_version, model_receipt)`、`computation_budget_on(connection, lineage_id, *, owner, expected_version)` 及其 Runtime 域 SQL。两者返回合同中的共同预算映射，证据不足不扣预算，身份/版本/代际错误继续拒绝；`retry_at` 包含共享依赖和探针/claim 到期。Runtime 对重复最新回执在乱序分类后恢复持久 due 的补充也须一并集成。
2. 先集成 RBH-02：Models `ModelReceipt` 的 `rbh.transport.v1` 或符合冻结历史规则的未发送证据，以及原 status/result 权威查询；不增加并行回执接口。
3. 根注册 `src/tianshu/companion/rbh_growth_transport.sql`，建议迁移名 `rbh_growth_transport`，在 Companion 相关表之后应用；运行时测试所用 `execution_migrations` 也需先包含 Runtime RBH SQL。新增文件已由现有 `tianshu.companion = ["*.sql", ...]` 包数据规则覆盖。
4. 根在已有 Operations progress/tick 调用 `growth.retries.reconcile_transport_waits(limit=32)` 并消费计数与安全身份/原因；不可把扫描排队视为材料处理完成。Growth 现有 `operations.progress` 会读取 Runtime 预算；实际生成仍走既有队列。
5. 保持 Memory `lock_episodes(..., extra_source_refs=...)`、Identity、Sources、Models 和 Runtime 同代合同。未修改 `module.py`、全局迁移注册、product、types、配置或部署。

## 必要集中验证

- `tests/companion/test_rbh_growth_transport.py`：新旧四次连接失败、真实计算次数耗尽、历史四次续接并 reviewed、旧证据与最新普通 transient/缺证据混合、unknown、原摘要/intent/代际、取消/来源/输入版本、并发扫描、有限扫描、冷却和已撤回的证书特殊阻断。
- `tests/companion/test_growth_retry.py`、`test_rb_growth_pure_retry.py`、`test_rb_growth_async.py`、`test_rb_growth_operations.py`、`test_rbf_growth_recovery.py`、`test_rbf_growth_remote_wait.py` 的受影响路径；其中旧测试替身仅补两个 Runtime API 和正确 generation，不放宽原断言。
- Runtime/Models 对自身证据、连接上限、共享探针和身份错误的联合定向用例由根与对应窗口交付一并执行。有效预算允许不代替实际来源授权、业务截止、调度准入和最终业务结果。

仅对本次 9 个 Python 文件做 `ast.parse`，并执行 `git diff --cached --check` 和整块 diff 静态审阅。遵照本批约定，**未运行测试、构建或烟测**，未访问生产/真实模型/QQ；不能据此认定测试通过、已集成、已部署或旧业务材料已恢复。

## 恢复入口和边界

部署和共享迁移完成后，由根在既有 Operations 周期或定向调用上述端口逐批复核并安排原队列；检查 `changes` 中 ineligible 原因、原 Models 回执与 Runtime 预算。队列等待、后继申请、成功 reviewed 是不同阶段。未知回执、来源撤回、权限失效、真实永久失败、输入版本变化和未确认提交均保留供原领域对账，不直接清表、不改旧 attempt 结果、不新建平行模型恢复服务。

## 实时回执补充

本次补充为实时失败、控制面回执、unknown 转终态及迟到原回执接入同事务来源复核：先按冻结请求刷新原场景并锁定来源/Memory，再锁 Growth 输入核对版本和预留。材料失效时保留可确认的原物理回执，但不调用连接失败记账或安排后继；unknown 保留原 ID 并仅安排再次观察。迟到旧版本通过局部 savepoint 隔离 Runtime 的版本栅栏，避免记账失败回滚已经归档的旧结果。`tests/companion/test_rbh_growth_receipt_sources.py` 增加撤回竞态、输入版本变化、原请求失效和旧版本迟到覆盖；依赖根完成 RBH-01 Runtime API 和迁移后集中验证。本窗口仅做语法及 diff 静态检查，未运行测试、构建或烟测。
