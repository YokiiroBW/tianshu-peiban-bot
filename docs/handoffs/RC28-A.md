# RC28-A：能力业务通道与前台级别传播

基线为线上源码 `271ad96e21e0035e7f605b345114c10703542a0d`，工作分支
`codex/foreground-capability-routing-20261101`。本块由 A 实现；Runtime 的
ExecutionLane、JobQueue、进程份额、ModelClient 传输池与诊断归 B，媒体后台重活拆分归 G。

## 实际修改

- `long_running` 仅决定 HTTP/聊天工具异步派工，不再覆盖已准入的 ServiceClass。
  手动 HTTP/插件调用、普通对话工具为 INTERACTIVE；自动活动、主动回应为 ELASTIC；
  到期承诺和插件定时保持 DUE。ExpressionRenderer 保留调用者的真实级别，自动联系显式传 ELASTIC。
- 嵌套 Action、插件模型端口、Reactions embedding/选择、Perception、天气/资产读取
  复用现有 `execution_service_class`，沿父 Action 的持久级别进入原 Models/Broker。
  不创建新模型路由、向量库、资源许可或工作流。
- 新 `capabilities/routing.py` 按服务器真实 capability ID 选择 B 的业务 lane。
  生图、短语音、有声书、感知、下载、表情图库、插件、基础设施与各订阅扫描分开；客户端
  `service_class`/`execution_lane` 不参与选路。普通目录和短协调仍归各自现有业务通道。
- `capabilities.execute` 注册 `lane_resolver=Actions.execution_lane_on`，按原 action ledger
  解析旧 todo 与新作业；`conversation.run` 按持久 turn.autonomous 解析 PROACTIVE/CONVERSATION，
  不接受 payload 伪造目标类型。其他注册、队列并发与原同会话顺序未改。
- 静态确认 Life.activity_step 已是 background 派工及原 reconcile/result 观察；
  Conversation 长工具也使用既有 background/result 链，不占对话槽等待整个生图或下载。
- 既有 get/find 新增 `execution`，合并 JobQueue.observe_on 的真实 job ID、lane、状态、
  排队原因及 Action.document 的 ServiceClass。Receipt、Attempt 与实际效果仍由原账本决定。
- 任务页、原命令查询和生图未决任务复用一份 ExecutionStatus；34 个正式 lane 有中文名，
  原始未知值可在详情查看。scheduled、native_task_queue、executing、execution_lane_unresolved
  分别显示未到期、等待本功能执行、执行中和路由未确定；不把排队解释成 GPU/模型忙，unknown 保持未知。

## 原 pending 作业的安全处理合同（仅 root 执行）

仍使用现有 `/api/v1/capabilities/resume`，不增加 UI 按钮或生产脚本。先用 get/find
取得原 action/attempt 以及 execution.job_id，再以原合法会话/actor/person/conversation
调用 resume，携带 `client_request_id`、`action_id`、`expected_attempt_id`、`expected_job_id`。

pending 分支经原上下文/来源核验，事务内按 Sources → Action → 原 Job 校验；只在 pending、
dispatch=not_started、未 started/finished、无 provider_handle/执行 owner，且原 Job 仍 todo/attempts=0
时修改原队列元数据和 Action.document。job/action/attempt/receipt/result_revision/args 全部保留；
既有 control 幂等身份也保留。同一次控制请求可重读原结果，不能变成新 attempt。

若原 job 已开始、尝试数变化、来源撤回或 ID 不符，拒绝重分流并继续观察原执行；
不要取消、克隆或重发 unknown。旧 failed/cancelled/unknown 的显式 resume 行为不改，
其新 attempt 现在会同步保存正确的 class/lane/job_key，重复请求仍返回原控制结果。

root 报告的两个原 Job 38877、38892 可用此合同；A 未读取或修改其生产状态。

## 与 B/G 的装配

已 cherry-pick B 接口源提交 `4458c2da`（本分支依赖提交 `71d4fbab`），它不是 A 的实现。
最终完整 B 源提交 `d4c2f8aac6dcfaa48e10ce7de50e3c174fe83a2e` 已 cherry-pick 为依赖 `d2dd25d6`，
静态核对 `lane_resolver` 正式命名、最终 34 枚举（含 root 末次确认的 REACTIONS）、
observe_on/reroute_pending 与 `RuntimeDiagnostics.tasks` 的共享 job_observation 字段一致。
root 只需集成原 B 两提交及 A 最终实现提交，不应重复合入这里的 B cherry-pick 副本。
不保留旧 `legacy_lane_resolver` 关键字或 `legacy_route_pending` 展示分支。

G 独占 capabilities/service.py 的后台 dispatch/work 注册、实际媒体长循环与基础设施锁；
A 没有写这些文件。G 确认的书源、漫画、视频、歌单 scan 已在能力 ID 映射中对应各自 lane。

## 编写但未运行的有效用例

`tests/capabilities/test_action_routing.py` 共 20 个参数化节点：

- `test_http_long_work_keeps_user_class_and_native_business_lane`（2）
- `test_automatic_and_nested_actions_keep_admitted_class`（2）
- `test_pending_resume_reroutes_original_job_without_another_attempt`（2）
- `test_pending_reroute_rejects_started_unknown_or_mismatched_identity`（6）
- `test_explicit_resume_persists_the_new_class_for_nested_provider_work`（1）
- `test_conversation_jobs_and_long_tools_follow_persisted_turn_kind`（2）
- `test_expression_rendering_preserves_service_class_through_speech`（3）
- `test_reaction_and_perception_model_calls_inherit_parent_class`（2）

`web/tests/execution-waiting.spec.ts` 两个 UI 节点覆盖原任务/运行任务等待原因、未知结果、
未知 lane 与生图读取原 Action、保留当前 scope/task link/取消入口且不发起生成。

建议 root 最小复验为该 20 节点与两 UI 节点；原恢复保证只需复用或补
`tests/capabilities/test_execution_recovery.py::ExecutionRecovery`（4 节点），不扩大原用例集。
前端修改需 root 在本批代码齐备后统一构建一次。

本代理只做源码阅读、静态 diff 复核及提交；未运行测试、解析、typecheck、构建、模型、网络、PG
或生产动作。并行隔离与实际吞吐需由 root 在 B/G 全部集成后验证，不能由本地实现宣称已实机完成。

## Candidate08：图像规划围栏与原 Attempt 恢复返修

本块基线 `47df9a92c503bd7a2dbbc6c8402f411325f4bbe5`，独立分支
`codex/image-plan-recovery-20261102`。仅修改 Images 及其必要 Contracts/Actions 接点。
原授权私有证据显示：规划模型 `:plan` 已 succeeded/submitted、完成 STOP，正文是单个完整
JSON 围栏；原 Images.prepare 直接 json.loads 拒绝它。TrackedBroker 如实记录了规划模型的
提交，而通用 Action 异常据此把图片结果判成 unknown；实际 image_work 尚未创建。
证据只用于诊断，未把人格、原规划正文或凭据写入仓库。

- 共享小提交 `0646c4f7` 提供 `capabilities.contracts.model_json(raw)`。原 `loads` 不变；
  新函数只接受整文 JSON 或整文单个 JSON/无语言三反引号围栏，仍拒绝重复键、非有限数、
  多文档、围栏外垃圾和截断围栏。Reactions 调用方与其测试由 G 独占。
- `images/planning.py` 检查物理 succeeded、内容 complete、纯文本可见输出，忽略 thought；
  四个必填字符串与可选字符串数组 negative_constraints 严格验证，拒绝额外字段。
  已终结的无效/不完整规划返回 failed/none；物理规划未知仍是 execution=unknown。
  两者都明确 image_submission=not_started，并保留规划 receipt/usage/completion，不能把
  模型付费解释为图片已提交。图片提供方提交顺序与 image_work 耐久边界不变。
- `images/recovery.py` 只证明本次规划失败的可恢复分支：原 Action/Attempt 未换、Attempt
  已结束、无执行 owner/产物/已确认效果、无撤回或取消、无 image_work；原 `:plan` 已终结且
  原生授权 result 可读；裸 Attempt ID 的独立图片请求确实不存在。缺工作行单独不构成证明。
  任何已有图片请求或工作行、未知规划、缺少结果、来源失效或版本变化都拒绝创建 Attempt。
- 原生 reconcile 对证明充分的旧 unknown 返回同 Action/Attempt 的 failed/none，附
  image_submission=not_started、规划回执与 resume_available；证据不足继续 unknown。
  已有工作行仍走原提供方观察路径。原 Action、Attempt dispatch、模型身份和回执均保留，
  Action 观察结果只经原 save/事件机制更新，不直接翻状态或删除旧执行。
- Actions.resume 仅为实现了 `resume_preflight` 的 handler 加两阶段接点：事务外读取
  Models；锁内 `validate_resume_on` 先 Sources 再原 Action/image_work CAS，仅本地核验。
  同 control 的既有结果先按原账本重读，锁内仍重复幂等校验；其余 handler 行为不变。
  Images 显式 resume 使用 Actions 新建的 Attempt、当前真实 scene、新 `Attempt:plan`，
  不把新 scene 配给旧模型身份。无新状态表、迁移、资源池或自动重试。

### Root 执行的原生恢复顺序

部署并完成必要验收后，使用原合法会话及 actor/context 调用现有
`/api/v1/capabilities/reconcile`（原 action_id）；先读回同原 Attempt 的
failed/none 与 image_submission=not_started，再显式调用现有
`/api/v1/capabilities/resume`，携原 action_id 和一个稳定 client_request_id。
这条终态恢复不传 pending 重分流的 expected_job_id/expected_attempt_id。
网络结果不明先 get/find 或用同控制身份重读，不创建另一请求。
本例原 Action.deadline_at=null，恢复保持现有各阶段默认 10min；没有修改绝对 deadline，
也没有无期限续期。真正未知模型或已提交图片只观察，不可借该分支重发。

### 编写但未运行的最小验证节点

`tests/capabilities/test_image_protocol.py` 新增 9 个节点：

- `ImageProtocol::test_model_json_accepts_only_one_complete_document`
- `ImageProtocol::test_complete_fenced_plan_ignores_thought_and_enforces_domain_shape`
- `ImageProtocol::test_unfinished_or_invalid_planning_never_enters_image_stage`
- `test_original_plan_reconciles_and_explicit_resume_uses_new_identity`
- `test_image_resume_refusal_keeps_original_head_and_attempt`（5 参数：unknown_plan、
  image_request、submitted_work、revoked、changed）

正向节点走真实 Images.prepare、TrackedBroker、Actions 与隔离 PG；提供方/模型是明确替身，
验证旧回执保留、事务外证明、显式一次生成、null deadline 默认、新 scene/模型身份、
同 control 在 pending 与成功后重读不重复生成。拒绝节点检查原 head/Attempt/Receipt/
control/作业不被新建，changed 参数只保留并发者的真实 result_revision 变化。
共享接点补验既有
`tests/capabilities/test_action_routing.py::test_explicit_resume_persists_the_new_class_for_nested_provider_work`
即可覆盖无领域 hook 的原恢复调用方；最小合计 10 节点。已通过且未变的其他路由/前端结果复用。

本代理未运行测试、解析、构建、typecheck、模型、网络、PG、部署或生产恢复；仅静态阅读、
实现、完整 diff 复核与提交。真实 ComfyUI 成品与原线上 Action 恢复仍由 root 集中执行。
