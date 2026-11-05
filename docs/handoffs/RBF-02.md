# RBF-02 Life 规划与恢复交接

基线 `33e5282f60fe4c803c3b16d498d1d924621ed1b9`，分支 `codex/rbf-life-20261105`。本卡只修改 Life 域、`tests/life/test_rbf_*.py` 和本交接；未修改共享装配、其他工作树或生产。

## 变更

- 能力探测仅作为通知；规划指纹使用最近成功的能力语义与当前业务材料，排除 TTL、刷新时间和旧的 capability-observation 事件。按 actor 和安装代际复用一个待执行原生唤醒；旧完成回调读取当前水位。无 routine 的角色在重启时也可按已保存上下文恢复。资料暂不可用且没有可执行候选时等待刷新，并保留 5 秒兜底。
- 真正材料或场景变化时，先观察原模型 attempt；正在排队、运行及尚未确认结束的 unknown 不被替换。新请求冻结、策略预留、旧指针标记和新唤醒同事务完成。已在本地标记准入而 RPC 中断时，只有来源、授权、期限、代际及领域 CAS 仍有效，才以存档的相同 frozen request、attempt 和 completion 身份幂等补准入；收到原回执后只观察。成功正文经 Models `reusable_result` 及当前取消、来源和 RecoveryPolicy 校验后应用。
- 规划应用先聚齐完整目标、活动、日计划及派生方向来源，按 Sources 的事件批处理接口预锁，再对领域快照做 CAS；章节保存与所有候选目标和 milestone 来源同样先准备再持锁提交。快照改变时回滚并以原成功模型结果重新观察，不重发模型。角色通知上下文与原上下文在同场景下取授权交集。
- 写作、日记和规划使用本域单调的“已观察 Models 登记”证据；false/null 不证明没有发送。依赖冷却按原生成 ID 和实际 `retry_at` 延迟，丢失 claim 不允许旧 owner 写入。`RecoveryWait` 若远端未确认回滚，原样向上抛出，不转为业务失败或新 attempt。
- 读取与视频只消费结构化的 `phase/outcome/recovery` 和原 Action 账本；`content.read` 的 `resume_material_read` 对 acquisition 的 failed 或 wait（包括首次准入前等待）按原 Action、attempt、receipt、result revision 和 Life 活动 CAS 恢复，`resume_commit` 使用能力侧复核的持久 checkpoint；`video.observe` 仅对 model 阶段、wait 且 recovery 为 `resume_model` 走同样的原 Action CAS，由能力侧验证原模型与来源 checkpoint。暂停、取消、来源失效、原 Action 截止与 unknown 均保留；无 checkpoint 保留原回执并标明需要检查，已确认无恢复路径的失败仍按未读覆盖处理。
- 规划统计持久记录待补投证据；在线回填固定 `as_of` 和原账本上界，待投影、活动 replay 或并发失败存在时不能报告 complete，追平后可在 `mode=live` 下报告 complete。

## 共享接线与迁移

1. 产品增量迁移须注册整份 `tianshu.life/rbf_life_planning_wake.sql`：`plan_watermarks.wake_context`、`plan_generations.submission_confirmed/admission_started`、`generation_recipes.model_submission_confirmed`、`plan_statistics_pending`。仅从 Models 原账本回填可证明的旧记录，其余旧值保留 NULL。新表是未投影证据，不是另一个任务队列。该迁移需在既有 Life、Conversation、Models、Runtime schema 后执行；仅修改域内 module 清单不等于产品主迁移已装配。
2. `catalog.on_change` 由产品以语义 digest 发 `life.signal(context, 'capability-observation:'+digest)`；`catalog.on_ready = life.planning_ready`，签名为 `async planning_ready(context, capability_ids)`。只会唤醒已有 `planner_capabilities_refresh` 的角色，不更改规划指纹或生成新请求。
3. `writing.bind_completion(life.writing_completed, prepare=life.prepare_writing_completed)` 必须同时接入准备回调；否则直接调用仍可运行，但不能保证生产章节流程在任何领域锁之前准备完整来源集合。
4. 依赖共享 Runtime `RecoveryWait`、`recovery_wait_retryable`、`inspect_wait_on`、`jobs.register(..., resource_recovery=True)`；Models `status(..., phase='pre_submit')` 与 `reusable_result`；Sources `prepare_event_writes`；Catalog `semantic_availability`；Capabilities `Actions.resume` 的原 attempt/receipt/result revision CAS、`recovery_guard` 及结构化读取阶段。媒体 guard 必须在能力侧 Action/来源事务内执行。
5. 老版本未保存足以证明等价的完整语义快照。迁移后遇到这类旧在途计划先保守观察原 attempt；原结果结束且新旧材料无法证明等价时可能重算一次，不宣称旧哈希可以精确还原。缺少已确认登记的原回执属于观察异常，不由本卡新建 attempt 或伪造结果。

## 验证与限制

- 新增 `test_rbf_planning_recovery.py`、`test_rbf_content_recovery.py`、`test_rbf_source_preparation.py`、`test_rbf_generation_recovery.py`、`test_rbf_life_statistics.py`，覆盖语义变化与同语义通知、可用性恢复、合并唤醒、原请求中断/丢 ACK、原子替换回滚、远端未确认回滚、读取原回执和取消/暂停、来源聚合、回填完成条件。
- 已静态审阅 diff，`git diff --check` 与 Python AST 解析；按本批授权未运行测试、构建、烟测、真实模型/QQ 或生产操作。测试代码是否通过、共享实现联调及业务结果均由主协调者集中验证。
- 夹具追补：共享 Life fixture 已应用本域增量迁移；Models 替身按原请求生成 `request_digest` 和安装代际，支持带 `phase` 的 `status`，成功复用调用真实的 `validate_reusable_receipt`；Sources 替身按完整依赖批次验证 `prepare_event_writes`。旧统计测试保留业务提交及补投断言，改为在 replay 未完成时报告 pending。Life 统计测试更名避免与 Operations 同名文件冲突。
- 建议集中运行的最小既有用例：`test_rb_plan_submission.py`、`test_rb_plan_statistics.py`、`test_planner_execution.py`、`test_planning_stream_repair.py`、`test_rb_diary_experience.py`、`test_rb_writing_generation.py`、`test_pure_unknown_recovery.py`、`test_computation_retry.py`、`test_writing_completion.py`、`test_goal_source_transaction.py`；另选 `test_diary_automation.py::test_recovery_uses_original_business_expiry_without_rewriting_a_frozen_preparation_budget`、`test_diary_automation.py::test_unknown_same_scene_new_epoch_observes_original_and_late_success_commits_once`、`test_activity_continuation.py::test_model_approved_resume_reuses_activity_at_full_day_quota_without_replaying_discovery`、`test_rolling_day_plan.py::test_idle_review_keeps_its_absolute_deadline_across_harmless_ticks_and_reconsiders_once`。本卡的五个 `test_rbf_*` 文件也需集中运行。
- `test_rolling_day_plan.py` 另有历史用例期望 `existing_activity_continues` 和 `actor_resting` 规划原因；当前规划实现未设置这两个原因。这是静态发现、尚未执行，不在本次接口夹具修补中改写其业务断言，集中验证时需结合最新日程合同判断。

## 集中验收后的定点返修

- `test_day_plan_leaves_free_time_and_carries_only_unstarted_work`、`test_manually_moved_slot_cannot_cover_explicit_commitment` 的活动材料默认为有 capability 的后台活动；当前及基线 `33e5282` 均允许后台工作跨日、与承诺并行。用例现明确设置 `execution_mode=focus`，保留原有日终结转和承诺冲突断言，未改变日程业务代码。
- 两项日记失败来自既有恢复物料 ID 仅区分日记和最近一次成功模型修订，真实经历版本变化也误用旧失败或 unknown 的恢复链。日记现复用收集阶段对日期、时区和实际 episode ID/revision 的 `scan_hash` 区分材料；相同材料仍共享预算，作者编辑或新请求 ID 不重置预算，旧 attempt 和其回执保持原样。已有或继承的恢复链始终沿用持久原 material ID；新请求会查找同一成功修订周期内的旧 base-ID，同 hash 或旧配方已失效而无法证明不同均沿用原链及预算，只有能证明材料不同才使用新 ID。章节分组不变。
- 新材料替换旧请求时，`review` 必须取得匹配原 attempt 和安装代际的当前 Models 回执；unknown 还须证明已结束、时间有时区且非明确取消。缺失、未确认或身份不匹配时继续观察；明确取消走原取消路径。使用 `life.lock_row` 先预锁并校验完整来源、锁日记后复核完整快照，再在同一事务记录原物理结果、核对旧恢复链暂停/取消/撤回栅栏并收敛原 day。依赖冷却继续由新请求准入统一处理；旧预算耗尽不会阻止真正不同的材料。
- 定向验证建议：`tests/life/test_planner_execution.py` 的上述两例；`tests/life/test_computation_retry.py` 全文件（含新增回执身份、未结束 unknown、取消、恢复栅栏、来源锁序和快照竞态）；`tests/life/test_diary_automation.py` 的 `test_revised_material_rejects_late_success_then_legal_new_material_can_continue`、`test_cancelled_unknown_keeps_original_receipt_and_discards_late_content`、`test_source_loss_retires_diary_application_without_relabeling_unknown_model`；`tests/life/test_rb_diary_experience.py` 的 `test_author_edit_and_new_request_do_not_reset_exhausted_generation_budget`、`test_unknown_attempt_is_observed_then_definitive_failure_retries_same_lineage`；`tests/life/test_pure_unknown_recovery.py` 的 `test_finished_unknown_uses_delayed_distinct_attempt_and_applies_once` 与 `test_finished_unknown_does_not_retry_after_its_material_changes` 的 diary 参数。
- 本次只做静态 diff 审阅、`git diff --check` 和改动 Python 的 AST 解析；依协调要求未运行测试、构建、烟测或生产操作。上述行为仍须协调者集中执行验证。
