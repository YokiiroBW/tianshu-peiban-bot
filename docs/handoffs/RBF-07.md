# RBF-07 Growth 临时资源等待与恢复交接

- 工作树：`C:/Users/Administrator/.codex/worktrees/rbf-growth/tianshu-peiban-bot`
- 分支：`codex/rbf-growth-20261105`
- 基线：`33e5282f60fe4c803c3b16d498d1d924621ed1b9`
- 首批代码和回归用例提交：`3452fca7a1fe85dc59f8f0ee711cfabe976607b6`；首次交接提交：`c349a3334ab568f495e2234a1756b71a07c16d4d`。
- RPC 回滚证据补充代码和用例提交：`1fc6bac83099c732db1cb2868d70d118ed8b7362`。
- 控制不确定旧批隔离与回归补充：`272a72b31832a1e6a9d08268195d60944d6ba0f7`。

## 实现

- `companion.organize`、`companion.episode_changed`、`companion.growth.retry` 和 `companion.growth.input_retry` 显式接入 Runtime `resource_recovery=True`。仅有原 generation 和持久领域状态/回执可供恢复的任务获此能力；数据库本地资源等待交由原队列任务让位，队列重排不消耗模型尝试预算。
- 模型请求尚未准入而 `RecoveryWait` 到来时，Growth 在当前状态 revision CAS 下记录原因、依赖、原 material key、输入版本及 `retry_at`，同一事务安排唤醒。新 Episode 到来会为最新状态 revision 重新安排，提前唤醒只复用该等待；相同 Episode 通知沿用原输入去重。
- 已有反思重试批次遇到冷却、探测或尚未到期，保留原批次、原回执和预算，按 Runtime `retry_at` 持久等待并在同一事务入队；`claim_lineage_due_on` 返回空时调用 `inspect_wait_on`，不会把空值当作已获新尝试许可。`recovery_claim_lost` 不凭旧 claim 写领域状态，原样交给 Runtime 重排原 job 后重新读取和 CAS。
- 已确认本地数据库失败不转换成模型未知失败；成功结果后的本地失败保留当前批次，续跑读取原 Models 结果。提交结果不明时先核对领域批次，若同代同 material 仍活动，只在 Models `reusable_result` 确认原成功回执和正文后重新锁行 CAS 并唤醒原批次；已处理/被取代的输入不重放。账本不可读、结果不可复用或领域状态不足以对账时继续抛原异常，不新发模型。
- `companion.organize` 的相同 actor/scope/revision 唤醒复用现有 `JobQueue` queueing lock；`growth:` 通知及 Life 的语义边界保持，通用 Growth/风格变化仍是唤醒，规划是否变化由 Life 对实际兴趣材料判断。
- Growth 将冻结请求、proposal、affect 和已准备的表达示例来源一次传给 Memory `lock_episodes(..., extra_source_refs=...)`；Memory 合并当前原件/supplement 后先锁来源再锁 Episode。`CompanionService.apply_affect(connection=...)` 用同一连接验证身份/来源，不再在业务事务内另开 Sources 控制事务。
- 保留 `historical_only`、材料版本、watermark、已冻结请求、原物理回执、unknown、权限/来源重检以及原应用 CAS。没有修改 Runtime、Memory、Sources、Life、公共迁移、模型预算或部署文件。

## RPC 回滚证据与活动位隔离

- Growth 捕获配置查询、提交、结果读取和重试的 `RecoveryWait` 时，使用 Runtime `recovery_wait_retryable(wait)` 同时检查本地与远端回滚证据。任一方明确否定时不把它当作安全等待重排原动作，也不把控制不确定伪装成物理失败；按原 attempt ID 只读 Models，并原样传播异常供上层记录。
- 远端未确认且本地事务明确回滚或未开始时，已冻结的活动批次通过现有重试批次和输入版本占位进入 `control_observation`：保存原请求身份和回滚证据，释放 `active_batch` 并唤醒独立新材料；只为读取原 ID 排期，不更新 Runtime 物理 attempt 状态，不为旧批产生后继或输出纠正 attempt。重复的远端等待仍只安排原 ID 观察。
- 只接受同 attempt、intent 和 generation 的 Models 确定回执。原结果成功后沿现有来源重检、应用 CAS、`historical_only` 和不回退游标规则处理；真实失败才走既有 RecoveryPolicy 记账并阻止旧批自动或手动生成新尝试。来源/身份变化继续按既有规则处理。
- 如果本地回滚或隔离 commit 本身不明，先只读核对当前领域 owner，不据活动指针猜测回滚结果，不贸然释放；隔离提交失败作为原异常的 cause 保留。原 commit unknown 对账遇到远端不安全等待时同样传播原等待；安全 RPC 等待不能证明本地 commit 结果。
- 安全的准入前等待仍按领域原 due/revision CAS 保存；请求已冻结或重试批已激活时，安全等待交还原 Runtime job。新增及更新 `tests/companion/test_rbf_growth_remote_wait.py`，覆盖本地/远端证据、已冻结和已激活批、仅观察原 ID、新材料继续、原结果晚到、真实失败和未知 commit。该文件尚未运行。
- 共享 dependency probe 由 Runtime 另行实现并集成租约到期与 token CAS 处理；Growth 不直接修改 Runtime 表或 probe。源自真实 Models 终态的原 attempt 记账继续通过既有 RecoveryPolicy 接口。

## 共享接线与集成顺序

先集成 RBF-01：`RecoveryWait(reason, retry_at, dependency=...)`、`recovery_wait_retryable(wait)`、带远端回滚证据的 RPC 编解码、`RecoveryPolicy.inspect_wait_on`、`classify_database_failure` 和 `JobQueue.register(..., resource_recovery=True)`，包括对 opt-in handler 抛出 `recovery_claim_lost` 后按原 job/payload/generation 延后观察；commit outcome unknown 不得当作确认回滚自动重试。

先集成 RBF-03：`Memory.lock_episodes(context, refs, *, connection, extra_source_refs=())`，由 Memory 一次合并原件、supplement 和额外来源，按 Identity → Sources → Episode 顺序锁定、复核 revision。`tests/companion/test_rb_growth_async.py` 与 `test_rb_growth_input_status.py` 的直接相关假端口已适配。

先集成 RBF-06：`models.reusable_result(attempt_id, *, context, expected_data_generation=...)`，只允许原成功且正文持久的结果，并复核当前身份、来源与 generation。Life 继续消费 `growth:` 唤醒；该包不要求新的 product 装配或 Growth DDL。

## 回归构造和检查状态

新增 `tests/companion/test_rbf_growth_affect.py`、`test_rbf_growth_material_locking.py`、`test_rbf_growth_recovery.py`，覆盖复用连接及授权失败、来源集合传递、材料变更、重复通知、任务注册、初始/后继依赖等待与到期、claim lost、共享预算不增加、本地失败复用成功结果、commit unknown 对账、已完成批次不重复应用及 `growth:` 唤醒。既有两个测试假端口仅适配共享方法签名及来源校验。

仅静态检查：首批 10 个改动 Python 文件、RPC 补充及活动位隔离各 4 个 Python 文件分别经 `ast.parse` 语法解析；`git diff --cached --check` 通过。按本轮任务卡，**未运行测试、构建或烟测**，未访问生产/真实模型/QQ，不能据此声称回归通过、部署完成或业务已恢复。

建议协调者在三项共享依赖集成后，使用已有隔离 PostgreSQL 验证驱动集中执行：

```text
tests/companion/test_rbf_growth_affect.py
tests/companion/test_rbf_growth_material_locking.py
tests/companion/test_rbf_growth_recovery.py
tests/companion/test_rbf_growth_remote_wait.py
tests/companion/test_rb_growth_async.py
tests/companion/test_rb_growth_input_status.py
tests/companion/test_rb_growth_pure_retry.py
tests/companion/test_growth_retry.py::test_unknown_yields_exact_versions_tail_advances_and_retry_cannot_regress_cursor
tests/companion/test_growth_retry.py::test_late_original_response_cannot_apply_after_successor_was_selected
tests/companion/test_growth_retry.py::test_resolved_unknown_with_invalid_output_uses_budgeted_correction
```

还需与 RBF-02 联合核对真实兴趣变化和普通 Growth/风格唤醒的规划行为，以及与 RBF-01/RBF-03 联合检查资源竞争和来源撤回并发；这些不是本窗口已完成的运行验证。

## 正常恢复与局限

服务启动时现有 `GrowthService.recover()` 会按领域当前批次和等待到期重新安排工作。已持久的反思批次可通过原 actor/scope、原 generation 和当前 revision 的 `companion.organize` 正常续跑；等待中的重试批按原 `retry_key` 与 due 调用 `companion.growth.retry`。旧的已失败 `companion.episode_changed` 若尚未登记到 Growth，需协调者从原队列回执核对原场景、Episode ID/revision/generation 后，通过现有 `GrowthService.consume_episode_change` 入口恢复，不能凭日志猜造材料或直接改数据库。

当前没有证据把历史 PoolTimeout 归因于某一具体连接池或锁链；已消除的是源码能证实的二次控制连接和来源锁集合扩展。若 commit 结果不明且原结果不可读取、当前批次不再可核对、权限失效或仍为 unknown，本实现保留失败/unknown 供正常领域对账，不以等待到期自动新建模型尝试。旧失败队列条目不会因新任务注册自动批量复活；是否恢复由协调者核对原回执与当前状态。
