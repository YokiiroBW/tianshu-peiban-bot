# RBF-06 Models 状态查询与原回执交接

- 基线：`33e5282f60fe4c803c3b16d498d1d924621ed1b9`。
- 实现提交：`171b432bdcaacee2969a265cae3ecde9cd41b78d`；本交接位于后续独立提交，集成时取本分支 HEAD。
- 工作树：`C:/Users/Administrator/.codex/worktrees/rbf-models/tianshu-peiban-bot`，分支 `codex/rbf-models-20261105`。只修改 Models、`tests/models/test_rbf_*.py` 和本交接。

## 查询与观测合同

- `await models.status(attempt_id, phase="pre_submit")` 表示尚未确认注册、准备使用同一冻结 ID 的检查；模型账本未找到时，HTTP `GET /internal/v1/models/attempts/{attempt_id}?phase=pre_submit` 返回 200，正文含 `attempt_id`、`lookup_phase=pre_submit`、`lookup_state=absent`、`receipt=null`、`cancel_requested=null`。找到时返回原 receipt，`lookup_state=found`。客户端要求服务端返回与原 attempt 和阶段相符的元数据。
- `status(attempt_id)` 和 `phase="observe"` 保持原状态读取：实际找不到仍是 HTTP 404 `attempt_not_found`，details 带原 attempt、`phase=observe`、`original_observation=missing` 和 `action=observe_original_attempt`。为保留既有调用语义，`ModelClient.status` 仅把这个明确的 observe 404 返回 `None`，不再吞掉其他 404；真正需要原回执的调用方使用 `await models.require_receipt(attempt_id)`，缺失时抛同一错误。不存在不能证明未发送，也不能据此建立新的物理 attempt。
- HTTP 状态路由为事件中间件提供 `request["model_attempt_id"]`、`request["model_lookup_phase"]`、`request["model_lookup_state"]`（`found`/`absent`/`unavailable`）。RBF-08 需在 `http.finished`、事件序列化和统计投影中接入这些字段；预检查 absent 是 200/succeeded，observe absent 仍是 404/failed。不得按整条路由或所有 404 降级。严格复用结果的 POST 同样提供 observe 阶段信息。现有 Runtime 中间件不在本卡范围。
- 提交 ACK 丢失仍只查原 ID，不重新发送；若查无原回执，`model_submission_ack_unknown` 的 details 标记 `original_observation=missing`，查询不可用标记 `unavailable` 并带控制错误代码。queued/running/unknown/succeeded 的真实回执仍保留原身份与摘要，不把缺失转成 `dispatch=not_started`。

## 原成功结果的复用

- `await models.reusable_result(attempt_id, *, context, expected_request_digest=None, expected_data_generation=None)` 读取既有结果，不触发新提交、重选路由、变更模型或预算；跨进程沿用现有 `POST /internal/v1/models/attempts/{attempt_id}/result`，body 的 `reuse` 对象包含 `request_digest` 和 `data_generation`。原 `result()`、submit、native 和 streaming 接口不变。
- 读取重新校验原场景与完整来源，仅在原 receipt 是 `succeeded`、非 `explicit_cancel` 且 Models 确实保存结果时返回。传入原请求摘要或代际时必须匹配；传入代际时还需等于当前 Runtime 代际。原 usage 和 `metering=unknown` 原样返回，不推断费用。缺失、queued/running、unknown、失败/取消、正文缺失、来源失效分别返回错误；`completion` 原样交给领域判断，Models 的物理成功不是业务语义成功。
- 这是读取边界，不代替领域的当前 Identity/Sources、代际和 CAS 检查。若业务提交失败，领域应持原 attempt ID、原摘要/代际再次读取成功结果，只重做失败的本地提交；不应再发模型请求。完成用户取消时不得复用或自动重试。
- 已核对 Runtime `RecoveryPolicy.record_computation_unknown_on` 与后续 reservation：纯计算 unknown 要求原 attempt、代际、finished_at、未明确取消，以及原 lineage/version/预算；本卡没有复制或重置该预算。对尚在执行、回执缺失或控制查询不可用的工作继续观察原 ID，不根据错误字符串创建继任请求。

## 领域接线

- RBF-02 Life：`life/planning_engine.py` 约 232、315 行的首次提交前检查可显式使用 `phase="pre_submit"`；已有回执或 unknown 的观察保持 observe/严格读取。恢复成功结果后仅重试本地应用，提交时继续来源与 CAS。
- RBF-03 Memory：`memory/index_jobs.py::_submit_or_observe` 的首次同 ID 检查可显式使用 `phase="pre_submit"`；`_failure` 的 prepared 且原 status 缺失分支需由领域确认是否仍可能在途，不能直接把缺失当未发送。`memory/model_outcome.py::observe_original` 当前对 succeeded 直接返回，需同时尊重同一回执的明确取消及来源撤销后的空正文，可接 `reusable_result`。已告知协调者转交。
- RBF-04 Capabilities：`capabilities/tracking.py::TrackedBroker._dispatch` 对 status 调用抛错会标记 dispatch=unknown，但成功返回 `None` 时没有标记，可能把未确认的实际发送当未发送；应由领域所有者将此情形保守处理为 unknown。已告知协调者转交。
- RBF-07 Growth 等其他领域：保留原 attempt/lineage，对 unknown 先读当前回执、沿原 RecoveryPolicy 预算调度；成功结果仅重试业务提交。任务不会因增加 Models 读取接口自动改变各领域调用方。

## 验证及限制

- 本窗口按授权未运行测试、构建、烟测，也未操作生产、真实模型或 QQ。已做完整 diff 静态审阅、`git diff --cached --check` 和 6 个变更 Python 文件的 AST 静态解析；不将这些描述为业务验收。
- 新增 `tests/models/test_rbf_model_receipts.py`，覆盖按阶段区分 200/404、事件元数据、其他 404 与服务不可用、原回执身份、提交 ACK 丢失、queued/running/unknown/succeeded、成功结果读取与模拟业务提交回滚后复用、身份/来源/摘要/代际/明确取消、原正文缺失和未知 usage。建议集中运行此文件，以及相关既有 `tests/models/test_result_recovery.py`、`tests/models/test_rb_stream.py`、`tests/runtime/test_rb_recovery_policy.py` 和域接线受影响测试。
- 无新 DDL、模型绑定或配置变更。跨域接线、最终集中验证、部署和真实业务恢复均由协调者确认；此提交不宣称这些工作已完成。

## 2026-11-05 启动所有权竞态与等待信号补充

- 经源码追踪，旧 Broker 的独立 PostgreSQL owner 连接失效后，advisory lock 立即释放，但旧进程要等 owner watcher 才停驱动；新 Broker 因此可能与旧驱动同时恢复同一 queued attempt。原先 `started` CAS 的败者在 `_events` 中无条件写 observation 和 `finish(failed)`，可覆盖胜者 running；`_drive` 的先读 queued 再 finish 也有同类竞态。
- 使用既有 `models.attempts.broker_instance`：恢复前在账本事务中核对原冻结请求摘要、当前代际与 queued/not_started，并领取归属；重新执行读取已保存的原始请求，避免同摘要重投改变未纳入摘要的 budget/trace/cancellation 身份。`record_wire` 和 `started` 校验归属；败者的排队失败状态、诊断和 timing 在单条带归属和状态约束的更新内写入，失败者不得写胜者结果或发出虚假的 finished 事件。consumer_interrupted 取消墓碑也按归属约束，显式取消仍可操作原 attempt。无新 schema。
- `results.read_result` 对 `RecoveryWait` 先透传，不把来源校验等待变成空的成功正文。Models 客户端采用 Runtime `recovery_wait_from_wire`，只从可信 Models 响应中的显式 409 `error.recovery_wait`（reason 与 error.code 一致）恢复类型；无效结构返回 `broker_protocol_error`，普通 409 仍为原 AppError。内部 NDJSON 流在已发送 HTTP header 后使用同一 envelope；外部 native 流不改。丢失提交 ACK 后的观察仍保持原有 unknown 保护。
- 集成依赖：必须先集成 Runtime 的 `RecoveryWait`、`recovery_wait_to_wire` / `recovery_wait_from_wire`、远端和本地回滚证据合取，以及通用 HTTP 错误序列化；本分支基线早于这些共享实现，单独检出此提交不能直接导入 Models。`rollback_confirmed=false` 不能被调用方事务成功回滚洗成可自动续跑。
- 本批新写 `tests/models/test_rbf_start_ownership.py` 和 `tests/models/test_rbf_recovery_wait_transport.py`，供协调者集中运行；覆盖新所有者 queued/running 两个交错、原始 budget、旧 owner 取消与写入限制、正常排队失败、typed/普通/畸形 409、远端回滚布尔值、来源读取及内部流。仅做 diff 静态审阅、格式检查及 AST 解析，未运行测试/构建/烟测/生产。
- `started` 的数据库提交确认丢失时不能推定未发送；本修改不会把已变为 running 的原回执写成 failed，后续应按原回执与恢复机制处理。accepted 后的等待边界见下节。

## 2026-11-05 后台未外发等待的持久化与唤醒

- 新增 Models 域增量 SQL `src/tianshu/models/recovery_wait.sql`，给 `models.submissions` 增加 `recovery_state`、`recovery_due_at`、`recovery_wait` 和 scheduled due 索引。状态为 NULL、`checking`、`scheduled` 或 `observe_only`；等待记录保存 Runtime versioned envelope，以及本地和远端各自的三态回滚证据。该文件已受现有 `tianshu.models` 的 `*.sql` package-data 覆盖。
- **根装配要求**：在共享 `runtime/migrations.py` 的 `product_migrations()`（基线之后）和 `execution_migrations()`（生成测试库的 Models 依赖之后）注册同一迁移 ID `models_recovery_wait`，读取 `files("tianshu.models").joinpath("recovery_wait.sql").read_text(encoding="utf-8")`。本窗口不修改共享迁移入口；上述注册未完成前不能运行该提交。
- 仅对原 attempt 仍为 queued/not_started、归属当前 Broker 且未取消的状态保存等待。`recovery_wait_retryable` 判定安全后记 `scheduled` 和原 due；任何本地/远端否定或无效证据只记 `observe_only`。等待发生在接受之后时，释放本地供应商 permit/slot，原 attempt 保持 queued，不写 failed、不生成后继，也不发 provider。观察原结果和同 ID 重投显式返回原 typed wait；状态查询仍返回原 queued receipt，并在内部 HTTP 附加只读 `recovery.state`、`due_at` 和 wait envelope。
- 复用既有 completion 维护循环分页扫描到期等待及其他 Broker 遗留的普通 queued 请求，按原 request 和原 ID 重新进入现有驱动；账本在同一事务检查安全证据、due 一致性、当前代际、取消和 broker_instance 后才允许领取。补扫其他 Broker 的普通队列是为了覆盖启动扫描期间旧 owner 刚结束复检并退回排队的交错。普通队列和恢复等待均不重置请求预算、创建模型 attempt 或跳过来源/行为复检；过期代际的安全 scheduled attempt 按既有 `data_generation_expired` 取消。已 running/submitted 的异常继续原有 unknown/回执结算，本卡未增加模型结果缓存。
- 防止收到 remote=false 与保存证据之间进程崩溃：对恢复准备和实际提交前的来源/行为复检，先在原 submission 写 `checking`；普通排队时清掉，成功启动时与 started CAS 同事务清掉。复检期间失联时，新进程不自动外发，直接观察/重投返回 `model_prestart_observation_required`，保留原 queued 身份等待事实核实。已保存的 `observe_only` 同样跨重启禁止自动外发；新的本地复检不会洗掉原远端否定。明确取消仍走原取消路径。
- 新增 `tests/models/test_rbf_prestart_waits.py`：accepted 后等待可被观察；未到期不启动；安全到期并发唤醒同 ID 只发一次；远端否定跨恢复不自动发，后续本地等待不能改写否定；复检中断不自动发、普通 queued 保持可恢复；明确取消、代际切换、已提交后的 unknown。原 `test_rbf_start_ownership.py` 的交错点改到 scheduler 排队，继续覆盖胜者 queued/running。既有 `test_output_budget.py::compiled_broker` 与 `test_native_settlement.py` 的假 ledger 需由协调者给 `prestart_check` 添加 async mock。所有用例尚未运行；仅静态审阅、格式检查及 Python AST 解析，集中验证后才能宣称通过。
