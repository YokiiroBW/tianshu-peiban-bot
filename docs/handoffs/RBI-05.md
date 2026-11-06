# RBI-05 Growth 恢复交接

## 基线和提交

- 独立分支 `codex/rbi-growth-20261106`，基线 `18d640c41bb3354ef82ffea41ff2e78b1338e3b3`。
- 实现及回归代码提交 `b3dc0be71a92ffb74cbd7ece4ba9927f6de0ceaa`；本交接文件位于其后的交接提交。
- 本窗口没有修改 Runtime、Models、共享装配、生产配置或其他工作树。

## 实现

- 修正已有人工复核把领域批次改为 `waiting`，而 Runtime `due_at=NULL` 时无人可领取的问题。复核先记录原 attempt 的待观察状态，由已有 Growth retry job 读取原始回执并重新核对材料；旧版已遗留的无合法 due 等待会回到 blocked，保留原错误和历史。旧版显式等待即使存在 due，也不能绕过非暂时失败的修复证据。
- 已知成功输出沿现有路径本地重解析，最多沿用既有一次输出纠正；已知暂时失败仍由 Runtime 的原 due、冷却和预算控制。`local_program`/`protocol_decode` 要求真实 failure stage、error type 和稳定的故障修复 `repair_ref`，由 Runtime 原 lineage 决定是否续接。`original_output_reusable=true` 但没有 failed 输出复用端口时明确停止；`null` 不推断原输出是否存在、是否可用或费用，仅由其他已证实的资格决定后续计算。
- `request_review` 增加可选 `repair_ref`，同一引用重复请求不会重开额度；`batch.rbi_review` 和 Growth view 的 `recovery_review` 保存待观察、允许或停止的原因。已有活动原请求只继续观察，不清空原 batch 或重建原业务工作。更新的原回执、明确取消、来源和身份失效、原材料变化、代际变化及原预算继续约束后继。
- 真实 Episode 新 revision 通过既有 `growth.changed` 形成新的材料消费；旧冻结 batch 因 `reflection_episode_revision_conflict` 停止，不伪造版本、不把新材料塞进旧 lineage。`update_version_on` 的终止调用保留真实原 version，不用合成版本散列表示取消。
- 新增 `GrowthService.reconcile_due(*, limit=32)`：retry、input、organize 三条 lane 持久 keyset 游标轮转，每次最多扫描 `limit` 个候选，按记录/版本短退避，补排现有原生 job；复用现有 queueing lock、稳定载荷和最早合法 due，不调用 Models、网络或业务执行。`complete` 只表示同次从各 lane 起点观察到的当前代际、未被锁定的候选范围；并发变动、其他代际及未扫描页不在此声明内。
- 输入材料复核只在 Memory 明确报告 `unavailable_sources` 或 `episodes_unavailable_or_revised` 时停止原自动到期，并保留原错误与停止原因；没有明确缺失原因的空结果保持未知，延后观察。正常事件、启动和维护复用相同的输入安排函数。

## 协调者装配和跨域合同

1. 在共享迁移入口将 `tianshu.companion/rbi_growth_maintenance.sql` 作为独立 Companion 域迁移，排在原 Growth、learning 相关表之后。该 SQL 只建立 lane 轮转、游标、扫描退避表和域内索引。
2. 在 Companion 的 `DomainModule` 增加薄 maintenance wrapper，在 `bot-background` 调用现有 Growth 实例的 `reconcile_due(limit=32)`；沿用 Runtime 的有界维护生命周期和域间异常隔离。
3. 集成 Models `7715afa5144be098a8b9431d1cda03b1a3aae4ec` 中的 `tianshu.models.rbi_outcome_guard.lock_recovery_facts_on`，以及 RBI-01 的 `RecoveryPolicy.resume_repaired_on(connection, lineage_id, *, owner, expected_version, original_attempt_id, model_receipt, repair_ref)`。Growth 要求 `eligible=true` 时 `retry_at` 为带时区 datetime；`false` 的 reason 会原样记录。原失败及 unknown 后继的 `reserve_attempt_on` 已传真实新鲜 `model_receipt`，包括 `cancelled/consumer_interrupted`，不伪造 status。
4. Growth 在权限/来源与当前领域输入校验、安装代际校验后，先取得 Models 原 attempt 的事实锁，再进入 Runtime 的资格/记账/领取/预留路径。Runtime 还须在其入口重入该锁并核对锁定事实与原回执，准确拒绝 explicit cancel、身份和状态变化。单靠本窗口静态审阅不能证明跨事务取消竞态已闭合。
5. Runtime 需在其失败分类归属处为已确认的 `consumer_interrupted` 生成正常有界 due，并接受真实 `cancelled/consumer_interrupted` 回执；其他 Models transient 白名单中可能同样落入 Runtime `OTHER` 的精确错误码已反馈协调者评估。若没有合法 Runtime due，Growth 保留原失败并停止空转，不自行写入 due 或制造修复引用。

## 必要集中验证与限制

- 已做：提交前静态审阅、8 个 Python 文件 AST 解析、`git diff --check`；没有运行测试、构建、烟测、真实 Models/QQ、生产读写或部署。
- `tests/companion/test_rbi_growth_recovery.py` 编写了真实 Models/Runtime 账本夹具，覆盖原 due 缺口、旧等待旁路、已知修复与相同引用、原材料 revision、预算/取消/换代、真实 429、真实 `cancelled/consumer_interrupted`、原成功重解析和纠正次数；需要合入最新 Models、Runtime 与其迁移后，在显式隔离的 PostgreSQL 环境运行。
- `tests/companion/test_rbi_growth_maintenance.py` 覆盖原生 todo 合并、doing 与后继、游标推进、lane 轮转、无权威缺失原因时的延后观察；需由协调者在同一集成环境运行，并联合验证 Sources/Models/Runtime 锁序及取消先后线性化。既有相关 Growth 用例中若使用不落 Models 账本的替身，应按真实权威回执合同调整，不能以替身通过冒称跨域通过。
- 历史 `invalid_upstream_response` 目标的原阶段和故障类型仍不可得：不能据此声称已经修复、编造 `repair_ref` 或重发；本实现会明确停止。已恢复的 15 个旧批次以及本轮 429 后恢复的 5 批/10 个 reviewed 版本未在本窗口重做或验证；实际材料 revision 2、线上收敛与业务结果由协调者独立核对。

## 事实核验增量

- 增量代码提交 `300d1a64c944ed3372d8cadfa0d3b5c3aa1ec71f`，依赖 Runtime 新增的 `validate_model_recovery_facts`。Growth 在原 attempt 的 Models 事实锁下、Runtime 首个 lineage 写操作前，核对 fresh receipt 的实际状态、原请求身份与代际；修复资格、后继 claim/reserve 及基于原回执的 unknown 记账均走此路径。来源或取消已失效时的原始历史归档不伪造成可续接事实。
- 仅当原回执同时满足 `status=cancelled`、`error_code=consumer_interrupted`、`cancellation_reason=consumer_interrupted` 才视为可续接的消费者中断。unknown 在事实未确认、身份不一致或状态发生变化时保持原请求观察，不据此提交后继。
- 已补真实 Models 账本回归代码：在最后一次回执读取后、领取前提交明确取消，断言拒绝新 attempt；另覆盖没有消费者中断原因的 cancelled 回执。此增量仅做了 5 个 Python 文件的 AST 解析和 `git diff --check`，没有运行用例。协调者仍须合并 Runtime 并集中验证实际锁序、拒绝码和竞争事务。
