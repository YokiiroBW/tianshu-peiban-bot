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
