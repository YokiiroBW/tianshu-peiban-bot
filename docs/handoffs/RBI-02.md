# RBI-02 Models 原请求失败归因与输出复用证据

基线 `18d640c41bb3354ef82ffea41ff2e78b1338e3b3`，分支 `codex/rbi-models-20261106`；最终提交 SHA 见协调回执。修改 Models broker、Models 内部诊断模块、账本的窄状态守护及本域定向用例；无公共类型、DDL、迁移注册、配置或根额外装配。协调者已明确扩展本卡白名单允许修改账本的对应方法。

## 原 Growth 事件的证据限制

目标 attempt 为 `0e17c5e9-24e0-58cb-b08a-1eace1535da9`。协调者补充的只读去敏证据 `.runtime/rbi-execution-20261106/dispatch-evidence.json` 对应原 intent：`failed/submitted/invalid_upstream_response`，`result_present=false`、`output_observed=true`、`native_completed=true`，completion 为 complete/stop，http_timing 均有 `read_complete` 与 `first_valid_output`；当时的 failure_stage、error_type 和 transport 分类为空。它证明系统曾读完响应并观察到有效输出，原结果未保存；不能证明旧异常的具体类型或发生阶段，也无法从未保存的内容本地恢复。静态调用链确认旧 broker 会将完整读入后的 JSON 解码、程序异常、数据库/结算异常及关闭异常等非 AppError 统归该错误码。历史回执未修改，归因仍 unknown；未复制运行数据或读取正文。

## 本次实现

- 原 `ModelReceipt.diagnostic` 增加冻结合同中的 `failure_stage`、`error_type`、`original_output_reusable`；原 `diagnostic.transport`、原物理 attempt、身份、请求摘要、代际和用量语义不变。错误类型只输出受限模块的安全类名或 `OtherError`，不写异常消息、正文、凭据或 URL。旧回执缺字段时保持缺失，不推断历史原因。
- 请求/读取/关闭、JSON 解码、响应结构解释、领域维度校验和本地结算使用实际执行位置分类。明确的 JSON/UTF-8 解码及已知原生协议校验为 `protocol_decode`；明确的向量维度校验为 `domain_validation`；可确认的网络错误为 `transport`；明确程序异常为 `local_program`；数据依赖的解释异常、HTTP 状态本身及无足够证据的情况为 `unknown`。`read_complete` 只表示读到 EOF，不再单独证明供应商输出错误。
- 对新产生的完整响应，只有确认的解码异常仍用 `invalid_upstream_response`；本地程序异常使用 `model_local_error`，无法归因的解释异常使用 `model_processing_unknown`，关闭/网络异常保留 `transport_unknown`。已有 AppError、取消/超时、原状态与物理结算继续走原路径。异常诊断的 `category` 不再把完整读取后的本地或未知错误写成 `known_invalid`。用量规范化本身发生异常时，异常路径不重复失败而丢失回执：保留用量未知和原预留，不虚构已报告用量。
- 只有原 RBH 证据严格证明未发送时，能确定没有该物理请求的输出，标记 `original_output_reusable=false`。其他失败路径没有事务性证据证明输出可复用，标记 null；无依据写 true。Models 现有 `reusable_result` 只接受有已存结果的成功回执，并复核身份、当前来源、请求摘要和代际；该路径可让领域在本地重新解析已保存内容，不产生新模型请求。旧失败异常路径不保存完整原输出，不能声称它可重解析；也没有为了补诊断保存新正文。Growth 语义修正由 Growth 处理。
- 429 保留 failed/submitted 和 `upstream_http_429`，502/503 保留 unknown/unknown 及各自原码；不改判未发送或零费用。429 的 quota、503 的 route 继续通过现有 Retry-After、持久冷却和调度器；原 `hold_until` 逻辑不变。账本 `charged_units` 中的估算/预留不代表实际账单。之前已恢复的五批材料不重做。
- 已提交 terminal 的结果随后遇到释放或观察关闭异常时，直接按已结算用量再次释放，不将原请求记作未开始。若 `finish` 已提交成功但返回/关闭时报错，失败分支先通过账本原子守护写诊断；守护未命中时只读取原回执，不重写已成功或其他 owner 的回执。后续事件和释放依据实际读回的权威状态与用量。

## 账本守护接口

`AttemptLedger.save_observation(attempt_id, *, timing=None, diagnostic=None, only_running=False)` 已在本包实现：默认调用保持原行为；`only_running=True` 时，原 UPDATE 在同一条 SQL 原子追加 `AND status='running' AND broker_instance=%s`，参数为该 ledger 的 instance_id，并返回布尔值表示是否实际更新。broker 只在 true 时继续按原路径 finish；false 时只读取权威回执。它没有通过 UPDATE 前独立 SELECT 判断，避免成功提交与诊断写入之间的竞态；无 DDL。

## 同事务原回执与取消守护

`tianshu.models.rbi_outcome_guard.lock_recovery_facts_on(connection, attempt_id) -> Mapping | None` 是 Models 唯一公开事务事实端口；Runtime/领域负责比对回执、判定取消、来源、业务状态与预算。正常非空 Mapping 的精确键为 `attempt_id`、`intent_id`、`status`、`dispatch`（均字符串），`error_code`、`request_digest`、`cancellation_reason`（字符串或 null），`finished_at`（时间或 null）与 `data_generation`（UUID 字符串或 null）。没有原行返回 None；缺少冻结提交行时 `request_digest` 为 null，不从其他摘要猜测。入参非空字符串以外抛 ValueError。端口不筛选终态、不决策是否恢复，也不读取或返回请求/模型正文。

端口先复用 `models-admission:<attempt_id>` 事务 advisory 锁，再锁原 attempts 行，读取最新取消理由与 `models.submissions.request_digest`，外层事务结束前保持锁。即使当前没有 attempt，admission 锁也保留至事务结束，防止遗漏并发取消/创建。Runtime 需用该事实比对原 attempt/intent、Models submission digest、代际、failed 或已结束 unknown、finished_at 与最新 explicit_cancel；不混用 `models.attempts.request_digest` 或 Runtime 的请求 signature。`consumer_interrupted` 不冒充 explicit_cancel；首次 reserve 与 succeeded 续接不改变原合同。

静态锁序核对：端口本身不锁安装代际；Models.create/seal/started/prestart 先持安装代际再取得 Models 锁，prestart 在 attempt 行锁前检查 Sources；`request_cancel` 按 admission → attempt 行写入，不在事务内等待 Sources/Growth/Runtime lineage；`finish` 在更新 attempt 行前进行 Sources 校验，不获取 admission；`seal_admission` 在 admission 后对 Runtime 权威使用非锁定 MVCC SELECT。没有发现所核 Models 路径的反向持锁，但须在隔离数据库联合验证。Runtime 已提出采用 Models → Runtime lineage 的顺序；现有 Growth 路径先 `claim_lineage_due_on` 再 `reserve_attempt_on`，同一事务会预先持 lineage 锁，已提示协调者和 Runtime 必须核对全事务锁序，不能仅移动 reserve 内的调用。取消先提交应拒绝后继；资格/预留先提交则原 attempt 后来的取消不追溯撤销已合法建立的后继，领域 lineage 取消由领域处理。

## 验证与交接边界

### RBI-07 取消回执返修

集中验证基线 `438cf678` 的 `test_rbi_outcome_guard.py::test_late_explicit_cancel_is_visible_without_rewriting_the_original_result[failed/unknown]` 发现取消 API 返回的 ModelReceipt 缺少原 submission 的 request_digest 与 completion_state。原因是取消后的 `request_cancel`/`finish` 返回 Models attempts 原始行，而 status 使用 `ledger.get` 联接 submissions/cancellations 并形成完整投影。取消保留原有写入、终态、外部资源和调度行为；所有成功取得原行的公共取消路径结束后复用现有 status 投影，云端 consumer_interrupted 无动作分支复用同一 `_receipt` 投影。不改变原终态，不触发用量重算或退款；不存在的原行仍保持原来返回 None 的语义。

新增 `tests/models/test_rbi_cancel_receipt.py`，覆盖合成 HTTP 路径下 failed/unknown/succeeded 的取消回执与 status 完全一致，包括非空原请求摘要、已附着 completion 的摘要与 pending 状态，核对原 result、usage、charged_units、finished_at 等未被改变；另覆盖 queued 取消、云端观察者中断及未提交外部资源持有者的取消。原报错用例未删减，并扩展 succeeded。返修窗口只做静态审阅和 Python AST 解析；上述用例与集中验证剩余用例仍由协调者执行，不能将编写视为通过。

编写 `tests/models/test_rbi_failure_diagnostic.py`：完整响应 JSON/UTF-8 失败、原生结构拒绝、向量维度、读取后的本地用量错误及错误处理自身的用量解码失败、数据依赖错误归因未知、截断读取及流事件、旧 diagnostic、去敏、既有成功结果读取与本地解析，以及真实 Models 账本/QuotaScheduler 配合合成 HTTP 的 429/502/503 状态与冷却。另用真实账本模拟成功提交后释放失败、commit 后连接关闭导致 ACK 未知及 owner 变化，验证结果、诊断、身份及预留不得倒退。`test_rbh_transport_evidence.py` 增加未发送与关闭阶段诊断断言。成功输出复用用例只走既有结果端口，不做外部模型请求；跨域恢复与真实现场未由这些用例证明。

`tests/models/test_rbi_outcome_guard.py` 编写了各物理状态的真实隔离账本事实、失败/unknown 后显式取消、取消锁的两个事务顺序、consumer_interrupted、status 转变、submission 与 attempt 摘要不同、缺原请求摘要及无原行锁的用例。Runtime 负责在同版本修复资格和传入 failed/unknown 原回执的后继 reserve 中调用本端口并判定准确拒绝；Growth/Memory/Life 按合同传递原回执，由根集中做真实跨域联验。

本窗口只做完整 diff 静态审阅、`git diff --cached --check` 和变更 Python AST 解析；按授权**未运行测试、构建、烟测、真实模型、QQ、生产或部署命令**。协调者集中运行新增 Models 用例及受影响的 RBH transport、result/receipt、stream 与 Growth 跨合同定向用例；旧 attempt 的异常阶段继续保留 unknown，不触发猜测性重放或宣布原批恢复。
