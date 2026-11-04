# RBW-life 交接

## 范围与提交

- 基线：`e2529c95a92857f368111d9ae5b23ee66800b636`。
- 实现及本卡测试代码：`51984e385369152cf2d6a0ef37ca7ef3f5c2cb0c`，分支 `codex/rbw-life-20261105`。本文件单独提交，主协调者可合入本分支的两个连续提交。
- 范围为 RB-11、RB-13、RB-14 的 Life 日程、执行和续接；未改经验包独占文件、共享装配入口、配置或部署。工作树现存 `.codex/config.toml` 是主聊天关闭 Fast 的配置修改，不属于本提交。

## 已实现

- 统一生成、应用、续接使用的资格判断，按目标状态、活动版本、已确认的原动作和 receipt 提供候选键、原因、版本、实际下一能力。角色焦点与后台工作分离；仅真实日程、承诺、保护期与业务截止约束相关工作，不再以每角色一个 running 活动、固定 15 分钟/20 分钟间隔或 12 步上限阻挡后台。
- 完整模型响应按片段验证，独立有效活动可落地；目标修改和引用它的活动通过同事务 savepoint 成组，失败组回滚，其他组继续。目标、活动、联系和取消片段各自记录失败，不因部分失败取消旧合法活动。`DayPlans.apply` 返回 `PlanApplyResult(items, next_due_at)`；逐项 `PlanCandidateResult` 含 `candidate_key/status/reason/activity_id/slot_id/revision/next_due_at`，区分 `scheduled`、`intentional_idle`、`deferred_no_slot`、`stale_state`、`invalid_proposal`。仅真实留白保存留白复查，失败不写完整有效指纹。
- 计划模型使用 `models.submit(..., completion=JobSpec('life.plan', ...))` 异步完成通知，原请求冻结后按原 attempt 观察；共同 RecoveryPolicy 在真实物理提交处登记，初次、失败重试和成功后的有限语义修正共享最多 3 次，按稳定场景 owner 和实际模型 quota 分组，route/quota 切换不重置同一材料预算。unknown 先保存未知状态，仅凭 Models 原回执解决，不自动重发。源材料变化时通过版本和原请求 CAS 禁止旧结果应用。
- 规划读取 `catalog.planning(context)` 快照；执行前仅准备和核对选中能力的角色可见性、当前权限、可用性及输入约束。阅读保存原活动、动作、receipt、cursor 和候选进度；已知无效果读取失败只跳过本候选，缺失/未知原 Action 只观察。发现内容按 key 去重，重复无进展页面停止；有真实业务截止、用户暂停/取消时仍遵守。
- 用户主动安排/修改触发规划；首次夜间启动及跨日关闭对应本地日，已关闭日不反复触发日记生成。自主联系保持原到期并尊重睡眠和目标安静时段；显式约定与用户回复仍走既有语义。无实际观察的常规 rest 标记 `learning_value=low`、`experience_eligible=false`，仍可作为日记材料。
- 来源失效公开按 actor 枚举与独立事务入口；计划结果统计按原 generation/候选构建稳定事件，可按原账本补投，统计失败不回滚 Life 事务。活动单对象 Ops 接口只观察原 Action 并以租约 guard、原版本及来源校验围住实际业务提交。

## 公开接口与装配

1. 主装配对 LifeService 调用 `life.bind_recovery_policy(policy)` 和 `life.bind_statistics(statistics)`；前者必须是 `tianshu.runtime.recovery_policy.RecoveryPolicy`。计划调用前若 policy 或 Models profile 的实际 `quota_group` 缺失，明确不可用，不猜测。`generation_profile` 同时需提供实际 route_id/revision；能力快照由 `catalog.planning(context)` 提供 `definition`、`availability`、`can_execute`，可附 `freshness` 与 `verified_at`。远端可用性未知或过期不能伪装可执行；实际选择另走 `Actions.handler` 与 `catalog.availability`。
2. 统计生产者为 `life.planning`，事件 family 为 `life`，按候选填 `life_scheduled`、`life_intentional_idle`、`life_deferred_no_slot`、`life_stale_state`、`life_invalid_proposal`。补投调用 `await life.backfill_statistics(cursor=None, limit=100, source_quiescent=False)`；普通在线扫描完成仍返回 `complete=False`，只有调用方能保证源静默且完成全部页时才传 `source_quiescent=True`。启动的 `life.statistics_backfill` 分页扫描，`life.statistics_replay` 补投单 generation。
3. `worker.operations_recovery(guard_on)` 返回 `life.activity.reconcile` 注册 dict，带 `observation_only=True`。target 支持 `{kind: 'life.activity', id, revision}` 或 `{activity_id, actor_id}`；按保存的原 context 刷新授权。guard 需接受 `(connection, attempt_id, owner_token)`，Ops observing 租约须支持该 fenced 子任务；原 Action 查无结果继续 unknown，verified 需真实 receipt 已应用。
4. 来源变更由主协调者按 `await dependencies.actors_for_source(connection, refs, kind)` 获得 Life actor，逐 actor 独立事务执行 `await dependencies.invalidate_actor(connection, refs, kind, actor_id=actor_id)`；旧 `invalidate` 供尚未切换调用方。确保其他域各自的来源失效也按 actor 分区，不让某一角色失败阻止其余角色。
5. 读口 `POST /api/v1/life/activities/checkpoint` 按 `id` 返回 `id/revision/checkpoint_data`，先检查原 scope 与 source 授权；活动列表和状态只给摘要。`state` 增加 `role_status` 和 `background_work`；`day-plan` 增加最近计划 `application`。Life module 的 `diaries/review` 薄路由依赖经验包的 `diaries.review(context, body)`，所有现有日记/创作入口继续由经验包实现。
6. Growth 公开口须支持 `material(context, purpose='planning')` 并返回 `ContextContribution` 语义字段；Growth 对 rest 的学习筛选应消费 `experience_eligible=false`，Life 不直接改写 Growth。`bot-background` 是 Life clock、worker、diary、统计恢复启动角色；共享任务路由将 `life.recovery_observe_activity`、`life.statistics_replay`、`life.statistics_backfill` 放入 Life 控制通道。

## 迁移

Life module 注册 `0040_life_execution`（`src/tianshu/life/rb_life_execution.sql`），在既有 Life `0031/0033/0035/0038` 后执行，并由主协调者登记到共享迁移列表：删除 `life.activity_one_running` 唯一索引；为 `life.plan_generations` 添加 `application_results` 及 `recovery_lineage_id`、`recovery_owner`、`recovery_version`、`recovery_probe_token`、`recovery_attempt_number`、`recovery_state`、`recovery_dependency`、`recovery_current_dependency`。最后一项用于换 quota 后等待 claim，原 attempt 依赖仍保留在 `recovery_dependency`。共享 Runtime/Models 的策略和 profile DTO 必须先可用再启动 Life 计划执行；本卡未写共享 migration 注册。

## 静态检查与待集中验证

- 已静态审阅本卡 diff；`git diff --check` 通过，29 个变更 Python 文件经 `ast.parse` 成功。按授权未运行测试、构建、烟测、真实模型、QQ 或生产操作；没有集成、部署或真实业务验收结论。
- 新增 `tests/life/test_rb_plan*.py`、`test_rb_activity*.py` 覆盖独立片段、组回滚、五种应用结果、异步提交、原 unknown、共同恢复预算、quota 切换、单能力复检、跨日和阅读续接等；这些是测试代码，尚未执行。
- 集成后先更新历史测试契约再集中验证：`tests/life/test_activity_continuation.py`、`test_read_failure_continuation.py` 仍 import 已删除的 `MAX_WINDOW_STEPS`；`test_rolling_day_plan.py` 和 `test_activity_continuation.py` 有 `days.apply` 按整数计数的旧断言。旧计划 fixture 还使用同步模型、无 `quota_group`/RecoveryPolicy 的构造，相关断言须按新接口和后台可跨休息执行语义修订；这些文件不在本卡测试白名单。
- 集成重点验证 Capability 快照及真实单能力可用性、Runtime policy 的 claim/unknown/依赖切换与 JobQueue lane/代际装配、Operations 的 observation-only guard、Growth/经验包接口，以及按 actor 的来源扇出。排程/模型回执/统计补投通过不等于真实聊天或业务结果已验收。

## 接线追补

- `life.plan` 首次 signal 和启动恢复冻结 `data_generation`，后台 signal 优先继承 `current_execution_generation()` 的原 job 代际，HTTP 等非任务调用在原事务取得当前代际；计划写事务、能力唤醒、恢复谱系都校验或携带原代际。`LifeWorker.plan(actor_id, revision, context, data_generation)` 接收原 job payload；Models Request、completion JobSpec 和后续计划任务保留同一 UUID。队列 handler 信封与 payload 代际不一致时拒绝。旧测试直接调用 `worker.plan` 三参需更新为含原代际的四参。
- `await worker.recovery_candidates(limit=32)` 从 Life 活动账本只读列出真实未解决原 Action，返回 `eligible/due_at/dependency/error_family/version/material_id/role/target`；`due_at` 为等待状态的实际更新时间，`target={kind:'life.activity',id,revision}`，仍在正常运行的 capability 标 `executing=True` 供 Operations 跳过。候选不执行动作，实际窄恢复仍重新校验原 context、来源和版本。
- Life module 在同一个 `GenerationControl` 实例注册 `life.diary_episode_changed=diaries.consume_episode_change` 与 `life.experience_statistics_backfill=generation_control.backfill_statistics`，均属 DIARY lane。时钟闭日去重键与经验包统一为 `identity.scene_id(context)`。
- `contacts.received(context, occurred_at=None, *, input_id, connection=None)` 可复用 Conversation 入站原事务，回复记录、候选取消和后续 `on_received` 共用该连接；未提供连接时维持原独立事务。新增用例检查重复 input_id、事务回滚与不再嵌套取连接。
- 本追补依赖 Runtime 的 `current_execution_generation`、`JobSpec.data_generation` 和 Models 的 `ModelRequest.data_generation`，以及经验包的两个任务方法和 Identity 的 `scene_id`；不改共享模块。按本批授权未运行测试、构建或烟测。

## 日程只读查询阶段计时

`POST /api/v1/life/day-plan` 原响应新增 `timings_ms`，以单调时钟测量，单位毫秒：

- `view_total`：`DayPlans.view` 内从进入到构造响应前，不包含路由层鉴权、HTTP 编码或传输。
- `identity_check`：view 内最初的身份检查；`state_lookup`：`life.state` 整体调用，内部也包含数据库、来源检查及角色状态投影，不能当成纯 SQL 时间。
- `snapshot_database`：日程计划、时段、未完成活动、最近结果的读取事务，包含取得连接、SQL、获取结果及事务退出；`snapshot_source_check`：这些对象的既有批量来源/身份检查及过滤，不包含 state 内的来源检查。
- `application_database`：存在已授权日程时读取最近应用结果的事务，未查询为 `null`；`planning_database`：规划水位读取事务；`projection`：应用结果与对外记录的内存投影，不含 JSON 编码。
- `json_serialization` 和 `http_total` 在该领域方法中无法观察，均为 `null`；不以 view 耗时或投影耗时冒充。现有来源授权及查询逻辑保持；读取不请求模型。新增 `test_rb_plan_timing.py` 验证阶段范围、未执行项与撤回来源过滤，尚未运行，交主协调者统一验证。
