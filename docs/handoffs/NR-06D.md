# NR-06D Life 纯计算中断重试

- 基线：`27fefcc059dc992a6cc16d7841afb8f070bffd73`。
- 分支：`codex/nr06d-life-computation-retry`。
- 产品提交：`5e58575e05dc98bc951f0eb084c20095b71ac46b`。
- 状态：本地实现、完整 diff 阅读及 `git diff --cached --check` 完成；没有运行测试、typecheck、构建、真实模型、QQ 或生产操作。根统一验证与发布。

## 行为与复用

规划沿用 `plan_watermarks` 的失败、due、原生 `life.plan` 与 `plan_generations` 指针。可信计算中断记录独立 `computation_retry_count`，按共同 helper 的 30、60、120…900 秒退避让位；输出合同错误仍保留原 `MAX_REPAIRS=2`，网络故障不会耗尽该格式修复次数。相同材料到期先观察原 attempt：可用成功先消费、queued/running 只观察、后来显式取消优先停止。原 proposal 已过真实业务窗口或当前材料已改变时取代旧 proposal，从当前材料生成新规划，旧活动不执行。原 Model request/结果/unknown 回执不改写。

日记和同一生成调用链的章节复用现有 `life.diary_generate` / `life.chapter_generate`、GenerationRequests、作者控制和恢复入口；没有新 lane 或第二队列。每次可信失败把计数和下一次 due 留在原领域记录，当前 worker 结束；到期重新检查原结果、取消和来源，再原子选择独立后继并排到原队列尾部。本次 worker 不立即调用后继模型。新请求有新 attempt/intent，使用 intent session，不注入旧 partial、模型响应或上游 continuation。原冻结请求仍由原 codec 读取，旧 conversation scope 不重写。

日记后继保留原完整采集 recipe、A 的 `diary_material_selection` 和 `original_recipe_hash` 绑定；只有材料版本仍当前时才能复用相同静态输入。若同日 episode 修订或 diary day revision 已改变，结束旧 generation 绑定，经原 automation 的 settle/enqueue/request/collect 正常重建当日新材料。其他日期独立推进。失败不再增加 `reviewed_revision`；永久失败仅封住当前 `blocked_revision`，后来有效新材料可以继续。已成功但正文/合同被拒绝的结果收敛失败，不会永远 observe。

`generation_recipes` 只保存每个 diary/chapter generation 的一份幂等来源审计，没有调度状态。选择后继时保留旧完整原件与 selection；Sources 失效通过现有 `LifeDependencies` 同域清除审计 recipe 及当前 recipe，旧原生 Model ledger 继续由其原权威管理。应用仍校验当前 generation、取消、来源和 episode/work revision；迟到旧结果不重复应用或完成回调。

明确取消、来源/权限失效、淘汰版本、永久输入/配置错误停止。外部 QQ、图片动作及其恢复没有改动。排队不消耗执行时间的既有合同保持，规划/反思/写作的执行预算没有增加。

## 依赖和共享装配

- 复用 B：`models.retry.transient_model_failure` 与 `retry_delay_seconds`（`24932674`、`afd44032`，基线已含）。
- 复用 A：新请求 intent / 旧 wire conversation 合同；取消提交 `b8ee1ce5233a0beb7cb1dab8ec0d384980c593ea` 的可空 `ModelReceipt.cancellation_reason`。本分支为开发取用的等价依赖 SHA 是 `d74d00a5`，根已有原片时不要重复取入，只取 D 产品提交。
- `cancellation_reason == 'explicit_cancel'` 在首次失败及选择后继前的原 status/result 观察中优先。旧模糊取消遵守 A 的保守投影。
- 本域 module 已登记 `0038_life_computation_retry`，精确资源为 `tianshu.life/0038_life_computation_retry.sql`；根单写全局迁移注册。无新 task/lane。

## 0038 DDL 和回滚范围

仅 Life 域：`plan_watermarks.computation_retry_count`，`plan_generations.previous_generation_key`，`diaries` / `chapters` 的 `generation_retry_count`、`generation_retry_due_at`，`diary_days.blocked_revision`，以及新增审计表 `life.generation_recipes` 与其来源 GIN 索引。附加列默认 0 或 NULL，不回写原 Model、Sources、既有 recipe 或尝试结果。

受影响备份为以上六张 Life 表；新增审计表保留原件，应与当前源依赖清除逻辑一起保留。代码回滚可保留附加列/表和完整审计；先停止受影响 Life 计算任务消费，沿原生任务操作收敛精确 todo，不取消 doing、不把 unknown 当成可以重发的旧 ID，不删除已保存内容或原尝试链。此前模型执行 unknown 不因回滚转成成功。

## 返回合同变化

- 领域行直出的作者 diary/chapter 列表、读取返回新增 `generation_retry_count: int` 与 `generation_retry_due_at: datetime | null`；原命令请求/返回字段不变。
- Diary generate/reconcile 的待重试返回可附加 `retry_due_at`；状态仍为既有 `unknown`，选好后继返回 `generating` 和新 `attempt_id`。当前材料被取代返回 `material_changed`。这些是控制/任务结果，未扩大公开读者数据。
- 原 automatic projection 仍用原字段；永久失败显示 `blocked`，不会显示 reviewed。内部 `blocked_revision` 不加入 automatic DTO。
- Clock planning 使用技术状态 `planner_retry_wait`；永久错误/取消通过既有 `planner_failed` / `planner_cancelled` 状态表达，`last_error` 保留实际代码。没有增加 clock DTO 字段，也未公开 recipe 审计接口。

## 根的定向验证目标

本代理只做静态阅读和 diff 空白检查，以下全部待根执行；根已计划的 `tests/life` 可一次覆盖，不需再逐块重复。

1. `tests/life/test_computation_retry.py`：11 个用例，覆盖网络次数独立于两次格式修复、活跃原请求只观察、后来取消优先、过期日程取代、完整 recipe/selection 与独立后继、跨日期让位、永久失败不假 reviewed、新材料继续、成功结果被拒绝收敛、dirty-day 到期正常重建、来源撤回清除、章节只保存当前后继一次。
2. `tests/life/test_planning_stream_repair.py`：原有限格式返修/来源拒绝/材料变化规则，其中旧 unknown 阻塞断言更新为保留原账本并让位给新材料。
3. `tests/life/test_planner_execution.py`：真实领域持久化与重启/epoch 恢复；旧 lost-response 用例仍断言到期前只观察同一个原 attempt。
4. `tests/life/test_diary_automation.py`、`tests/life/test_diary_materials.py`：日历闭环、A 原件选择绑定和当前修订检查。
5. `tests/life/test_writing_completion.py`、`tests/life/test_goal_source_transaction.py`：只提交当前内容与原来源事务。受影响 PG 夹具装配 0038；保存章节替身补当前 work revision。

共享 helper 和 A scope/cancellation 用例由根统一执行一次；D 不重复验证，不宣称上线或真实恢复已完成。
