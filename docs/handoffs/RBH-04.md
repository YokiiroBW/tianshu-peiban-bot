# RBH-04 Memory：连接回执与分区等待

## 范围与基线

- 基线 `ec245f9e30f935f7bcadbc441373a45c9f9d07a4`，分支 `codex/rbh-memory-20261105`。
- 接入 Runtime 的 `note_not_sent_on`、`computation_budget_on`，实际未发送判证、计算及连接预算均归 Runtime；Memory 使用 Models 正常状态查询和既有原始请求校验，不直读 Models 表。
- Memory 的原回执在交 Runtime 前绑定原 `attempt_id`、`intent_id`、安装代际及非空 Models `submission_digest`，并检查冻结请求与 Runtime 原预约的 Memory 摘要一致。它们不是同一种摘要，不互相比较。
- 移除整理恢复、Operations 和重组恢复中的原总尝试数上限判断；保留来源/权限、版本/代际、原回执、读集、批次和 episode CAS。Operations 的依赖等待复用 Runtime `inspect_wait_on`。
- 自动依赖等待通过已有分区级 `memory.consolidate` 队列锁汇总最早持久到期时间；显式恢复旧依赖失败仍按批次定向排队。已确认预算耗尽的失败批次保留原状态并停止自动排队；未决原尝试仍走原观察路径，新材料继续按已有分区路径处理。

## 给根的接口和装配

```python
await core.memory.consolidation.reconcile_transport_waits(limit=32)
# {"inspected": int, "scheduled": int, "ineligible": int, "changes": [安全身份/原因]}
```

供既有 Operations/tick 接线；合法 `limit` 为 1–128。单次最多检查 `limit` 个批次，至多进行相同数量的 Models `status` 查询，不执行模型。每个批次按尝试序号从旧到新分次核对，全部处理后再次查询最新原回执、调用 Runtime 幂等 `note_not_sent_on`，只有共享预算可用且 Runtime 返回持久 `retry_at` 才安排原有恢复。多尝试批次需要多次 tick；已完成批次不纳入扫描。进度和阻断原因保存在现有批次 manifest 的 `rbh_transport_scan`、`rbh_claim_budget` 和 `recovery_budget_blocked` 字段，不新增 Memory SQL 或迁移。返回的 `scheduled` 代表已安排队列，不代表业务完成。

依赖 RBH-01 Runtime 接口与其增量迁移已由根注册。根已安排修复 Runtime 的重复最新回执恢复 due 边界：最新回执先于旧回执分类，旧证据后来释放预算时，重复 note 最新回执也应幂等恢复原 lineage 到期时间；本卡不修改 Runtime。配置和发布装配均由根处理。

## 回归与未验证项

- 新增 `tests/memory/test_rbh_transport_recovery.py`，覆盖有界历史扫描及幂等、乱序证据、原请求/回执与单调尝试号、暂停/撤回/unknown/变更来源/摘要冲突、共享分区唤醒、同依赖单探针、预算耗尽、原结果只应用一次和显式定向恢复。
- 更新 `tests/memory/test_rbf_wait_recovery.py` 中原先依赖逐批自动定向任务的断言，检查实际同分区合并任务身份；其他现有用例保持。
- 按本批授权仅作 Python AST 解析和 diff 静态检查；未运行测试、构建、烟测、真实模型、QQ 或生产操作。请根在 Runtime 及迁移集成后集中执行上述 Memory 用例，并按共享行为影响选取 `tests/core/test_memory_rpc_recovery.py`、`tests/core/test_rb_consolidation_write.py`、`tests/core/test_consolidation_pipeline.py` 中相关回归。集成、业务收敛和部署尚未验证。
