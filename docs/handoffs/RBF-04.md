# RBF-04 能力观测与媒体阶段恢复交接

## 基线和范围

- 工作树 `C:/Users/Administrator/.codex/worktrees/rbf-capabilities/tianshu-peiban-bot`，分支 `codex/rbf-capabilities-20261105`，基线 `33e5282f60fe4c803c3b16d498d1d924621ed1b9`；本交接所属提交以 `git rev-parse HEAD` 和回执为准。
- 仅修改 `src/tianshu/capabilities/`、`tests/capabilities/test_rbf_*.py` 和本交接。没有修改共享 product、Runtime、Models、Life、迁移注册或生产。

## 变更与接线

1. Catalog 把最近成功的语义观测与当前执行可用性分开。`planning` 每行 `semantic_availability` 为不含 `verified_at`、叠加当前角色策略的最近成功 `Availability`；未观测为 `None`。`freshness`、最近成功的 `observed_at`/`expires_at` 和 `observation_error` 单列，过期/失败仍 `can_execute=False`，真正执行仍即时复核。TTL、时间和同语义重新观测不算语义变化；配置版本或角色策略的实际变化仍由各自投影进入决策材料。
2. `catalog.observation_digest(context)` 是完整 TrustedContext 下已成功原始 handler 观测的无 I/O、无探针稳定摘要。`on_change(context, capability_ids)` 只通知首次成功或真实语义变化；`on_ready(context, capability_ids)` 通知已知语义恢复当前可用。两者在短窗口内分别合并已完成 ID，慢探针或慢回调不阻挡其他已完成结果；处理中新增的缺失探针会补排。根需分别接至 Life 的语义变化和仅唤醒等待资料工作；ready/TTL 不进入语义 fingerprint。Life 仍需明确等待必要材料，并禁止将未知资料当作可执行。
3. `content.read` 的 `result.phase` 是 `acquisition`、`parse`、`commit` 或 `completed`，`outcome` 是 `wait`、`failed`、`continue` 或 `completed`。`recovery` 是 `resume_material_read`、`resume_commit`、`observe_original_attempt` 或 `none`。读取失败携带 `material_read={phase,method,url}`、`retry_at`、`attempt_number`、`max_attempts`；同一原 Action / 原材料在 HTTP 与浏览器切换、独立 Action attempt 之间共用 RecoveryPolicy 五次物理读取预算（含首次）。正常 HTTP 转浏览器仍是一轮读取，续读只进入已失败的材料方式。排队前只观察是否到期，实际 worker 准备读取时才占用物理尝试；准入遇 `RecoveryWait` 返回 `failed/none` 加 `outcome=wait`，不记 `capability.failed` 事件，也不花物理预算；Life 必须按 outcome 等待并在到期后续接。读取后冻结有界原输入及来源信息；只有 Runtime 分类确认事务回滚、可重试且不需先查回执时提供 `resume_commit`，它不重新读取或消费物理预算。完整原始/归一来源检查点已存在时可通过 reconcile 读取原结果；其他提交未知只观察。成功或来源失效时清除 Action 中的内容输入检查点，来源撤回同样清理；请求正文最大受原 HTTP/browser 4 MiB 限制，待提交阶段会暂存于 Action document。
4. `video.observe` 在原 Action 内按获取、模型、解析、消费提交保存来源、完整冻结请求、模型回执及实际输入。解析范围越界返回确定的 `failed/none`、`phase=parse`，保留原模型回执/来源，不 clamp、不伪造覆盖或消费。提交恢复读取原 Models 成功正文，不重复下载或模型调用；`consumptions` 使用原 Action 首次 attempt 身份去重，并在持来源锁后校验当前 Action/owner/generation。未知原效果保留原观察；从 unknown 经权威回执解决时归档原 unknown 回执。
5. 视频登记未确认时，即使本地曾标记开始提交，若 Models 的 `pre_submit` 显示原 ID 未登记，也只在来源、授权、代际、期限、Action CAS 均有效时以完整同一冻结 ModelRequest、同 attempt ID 补准入；已确认登记、运行、unknown 或已有终态不重发，成功正文经 `reusable_result` 复用。到期的未登记请求不延长原期限；已有成功结果仍先观察/复用。并发同 ID 的单次物理外发依赖 Models Broker/ledger 合并和 CAS，在集成测试中联合验证。
6. `TrackedBroker` 调用 Broker 之后无法观察原回执时保留 dispatch unknown；Broker 前本地校验失败或明确 `not_started` 不错误记作已提交。来源失效会移除阶段检查点和缓存的历史回执正文。
7. 跨 RPC 的 `RecoveryWait` 先经 `recovery_wait_retryable(wait)` 合并本地/远端回滚证据。可证明安全的视频主模型等待给 `phase=model`、`outcome=wait`、`recovery=resume_model` 和 `retry_at`，Life 到期仍以原 Action CAS 续接同一模型 ID；远端否定回滚时 Action 保持 `unknown/unknown`、`recovery=observe_original_attempt`，并向上抛原异常，不创建新动作或伪记终态失败。可选重读和摘要也不吞掉不安全等待。

## Life 和共享接口

- 根产品接线：`on_change` 使用 `catalog.observation_digest(context)` 的稳定语义水印；`on_ready` 走机械就绪唤醒，只唤醒确实等待资料的当前工作，不能令 TTL 进入指纹或单独触发付费重算。
- Life 对同一原 Action 调用 `actions.resume(action_id, context, client_request_id=..., expected_attempt_id=原attempt, expected_receipt_id=原receipt, expected_revision=原result_revision)`；运营/活动围栏可传 `recovery_guard=(guard_on, recovery_attempt_id, owner_token)`，在来源与 Action 行锁事务内调用 `guard_on(conn, recovery_attempt_id, owner_token)`。pending 的队列改派才使用 `expected_job_id`；terminal 走阶段恢复。调用返回新 pending Receipt；用 `actions.get` 取得新 `result_revision` 并核对当前 attempt 身份后再更新原 activity。重复相同 control ID 返回同一尝试；旧原尝试和回执保留。
- 依赖 Runtime `RecoveryWait`、`RecoveryPolicy.inspect_wait_on`、`classify_database_failure`；依赖 Models `status(..., phase='pre_submit'|'observe')` 和 `reusable_result(..., expected_data_generation=...)`。本卡不新增 schema/迁移。新增内容物理读取策略通过现有 `Capabilities.bind_recovery_policy` 注入。

## 验证与边界

- 按授权未运行测试、构建、烟测、真实模型、QQ、设备或生产操作。已编写 `test_rbf_catalog_observations.py`、`test_rbf_read_recovery.py`、`test_rbf_video_recovery.py`、`test_rbf_tracking.py` 和 `test_rbf_action_resume.py`；静态检查见最终回执。
- 协调者合入共享 Runtime/Models/Life 并完成 product 接线后，建议集中运行 `pytest -q tests/capabilities/test_rbf_*.py tests/capabilities/test_rb_catalog_planning.py tests/capabilities/test_video_consumption.py tests/capabilities/test_dispatch_evidence.py tests/capabilities/test_rb_action_dependencies.py tests/capabilities/test_content_exploration.py`，按项目 Windows shell 对通配符适当展开；另验证 Life 的等待/就绪唤醒、恢复原 activity CAS、来源撤回、事务回滚/提交未知、同 ID 并发准入和 product 接线。
- 修复前的 unknown 视频若缺少可验证来源/完整输入检查点，不推断失败、不重下载、不伪造消费，也不新增读取模型原请求正文接口；只能保留原 unknown 并由协调者按原账本观察。检查点持久化前发生的获取或输入提交未知同样不得凭错误码盲重放。代码完成不代表部署或真实业务恢复。
