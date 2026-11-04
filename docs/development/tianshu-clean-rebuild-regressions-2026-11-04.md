# 天枢最终重构：历史行为与当前问题

状态：规划与证据映射。23项历史修复/合同、41个已有测试引用、22组当前问题（7组运行问题、8组源码问题、7组结构发现；第五版补录SF-07/08）。新发现未在生产注入故障；测试引用存在不代表本轮执行通过。

[主方案](tianshu-clean-rebuild-plan-2026-11-04.md) · [最终审计](tianshu-final-system-audit-2026-11-04.md) · [26卡](tianshu-clean-rebuild-tasks-2026-11-04.md)

## 历史行为

### PF-01 排队与执行时钟

- 类型：historical_fix。
- 负责卡：RB-04、RB-05、RB-14、RB-22。
- 保留行为：排队不扣执行时长；实际执行开始后固定时钟；真实业务截止仍生效，重启/查询不能刷新截止。
- 修复依据：[docs/development/natural-runtime-closure-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/natural-runtime-closure-2026-11-02.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/models/test_execution_timing.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/models/test_execution_timing.py) — `test_queue_wait_may_outlast_execution_window_and_stays_cancellable_and_bounded`
- [tests/models/test_execution_timing.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/models/test_execution_timing.py) — `test_execution_is_capped_by_the_business_deadline_without_renewing_it`

### PF-02 RPC错误不能覆盖原Models结果

- 类型：historical_fix。
- 负责卡：RB-04、RB-08、RB-10、RB-15、RB-23。
- 保留行为：RPC internal_error/断开不等于模型失败；先观察原attempt，控制面不可用不成为重复提交许可。
- 修复依据：[docs/development/runtime-recovery-closure-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/runtime-recovery-closure-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/core/test_memory_rpc_recovery.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_memory_rpc_recovery.py) — `test_rpc_wrapper_uses_original_receipt_instead_of_internal_error`
- [tests/core/test_memory_rpc_recovery.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_memory_rpc_recovery.py) — `test_missing_control_never_becomes_permission_to_retry_on_later_wakes`

### PF-03 纯计算让位、独立尝试与只应用一次

- 类型：historical_fix。
- 负责卡：RB-03、RB-04、RB-08、RB-09、RB-10、RB-15、RB-22、RB-23。
- 保留行为：临时计算失败释放业务活动位，后续材料可推进；新attempt独立，迟到结果不重复应用；明确取消、源失效及永久配置错误不自动重试。
- 修复依据：[docs/development/nr06-diary-and-computation-retry-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/nr06-diary-and-computation-retry-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/companion/test_growth_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/companion/test_growth_retry.py) — `test_unknown_yields_exact_versions_tail_advances_and_retry_cannot_regress_cursor`
- [tests/companion/test_growth_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/companion/test_growth_retry.py) — `test_late_original_response_cannot_apply_after_successor_was_selected`
- [tests/core/test_memory_index_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_memory_index_retry.py) — `test_transient_index_yields_to_later_objects_then_retries_once_after_restart`
- [tests/life/test_computation_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_computation_retry.py) — `test_an_unknown_diary_does_not_block_a_later_registered_day`

### PF-04 日记scope与正文成功后的状态对账

- 类型：historical_fix。
- 负责卡：RB-02、RB-10、RB-11、RB-15、RB-19、RB-23。
- 保留行为：作者范围较宽仍定位同一合法闭日，歧义不得任取；成功正文只对账派生状态，不重新生成、不改变原时区和来源范围。
- 修复依据：[docs/development/nr06-diary-and-computation-retry-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/nr06-diary-and-computation-retry-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/life/test_diary_automation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_diary_automation.py) — `test_saved_success_reconciles_its_unique_narrower_day_without_rewriting_content_or_receipts`
- [tests/life/test_diary_automation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_diary_automation.py) — `test_multiple_narrower_days_are_ambiguous_but_an_exact_original_scene_keeps_priority`

### PF-05 后台请求与供应商上下文隔离

- 类型：historical_fix。
- 负责卡：RB-04、RB-10、RB-12、RB-15。
- 保留行为：后台按角色/场景/功能/intent隔离，不继承聊天、前日日记、失败输出或上游previous-response引用；聊天保留明确的会话连续性。
- 修复依据：[docs/development/nr06-diary-and-computation-retry-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/nr06-diary-and-computation-retry-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/models/test_provider_transport.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/models/test_provider_transport.py) — `test_session_identity_is_stable_for_continuation_and_distinct_for_other_scenes`
- [tests/models/test_provider_transport.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/models/test_provider_transport.py) — `test_independent_requests_cannot_inherit_provider_state_from_native_or_route_options`
- [tests/life/test_diary_materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_diary_materials.py) — `test_each_day_has_no_chat_previous_day_memory_or_failed_partial_context`

### PF-06 规划截断、无效结果与合法留白

- 类型：historical_fix。
- 负责卡：RB-11、RB-13、RB-14。
- 保留行为：截断/不合法提案不能当成有效决策指纹而永远跳过；完整校验后才应用，有界纠正；合法留白可正常读取并在真实边界复查。
- 修复依据：[docs/development/life-runtime-fixes-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/life-runtime-fixes-2026-11-02.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/life/test_planning_stream_repair.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_planning_stream_repair.py) — `test_known_invalid_results_have_two_bounded_repairs_and_never_become_valid_decisions`
- [tests/life/test_planning_stream_repair.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_planning_stream_repair.py) — `test_repair_applies_one_complete_result_and_keeps_the_original_rejected_record`
- [tests/life/test_planning_stream_repair.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_planning_stream_repair.py) — `test_only_free_slot_returns_200_through_the_registered_day_plan_http_route`

### PF-07 滚动日程不恢复任意次数和占比限制

- 类型：historical_fix。
- 负责卡：RB-13、RB-14。
- 保留行为：第五/第六活动可继续，排程只服从真实作息/承诺/保护；普通tick不反复刷新留白截止或无故重新规划。
- 修复依据：[docs/development/retrieval-life-closure-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/retrieval-life-closure-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/life/test_rolling_day_plan.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_rolling_day_plan.py) — `test_completed_history_does_not_limit_the_fifth_or_sixth_activity_and_completion_decides_again`
- [tests/life/test_rolling_day_plan.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_rolling_day_plan.py) — `test_long_activities_have_no_fixed_occupancy_ratio_but_obey_real_windows`
- [tests/life/test_rolling_day_plan.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_rolling_day_plan.py) — `test_idle_review_keeps_its_absolute_deadline_across_harmless_ticks_and_reconsiders_once`

### PF-08 保护期内不循环恢复和暂停

- 类型：historical_fix。
- 负责卡：RB-11、RB-13、RB-14。
- 保留行为：恢复前即检查保护窗口；重复唤醒不能新增步骤、重复执行或空增语义版本；暂停不阻止作息/房间显示推进，空闲后沿同一检查点续接。
- 修复依据：[docs/development/proactive-companion-repair-r67-2026-10-31.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-repair-r67-2026-10-31.md)。
- 覆盖说明：部分覆盖：本轮确认单次保护边界及指纹用例，尚未定位完整覆盖R67多次唤醒/版本/步骤/作息联动的现成用例。SD-14须先复用可用覆盖，否则仅补这组真实回归断言；不能将本条标成已验证。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/life/test_activity_continuation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_activity_continuation.py) — `test_due_continuation_respects_existing_time_and_current_activity_protection`
- [tests/life/test_activity_continuation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_activity_continuation.py) — `test_lifecycle_fingerprint_changes_on_due_and_slot_fences_without_per_minute_churn`

### PF-09 阅读失败后的同任务续接与执行上下文

- 类型：historical_fix。
- 负责卡：RB-11、RB-13、RB-14、RB-18。
- 保留行为：确认失败只跳过对应未读候选，保留成功和剩余目标；暂停/未知效果不套用跳过；重新安排后实际执行与冻结授权上下文一致。
- 修复依据：[docs/development/natural-runtime-closure-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/natural-runtime-closure-2026-11-02.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/life/test_read_failure_continuation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_read_failure_continuation.py) — `test_confirmed_timeout_skips_only_the_unread_candidate_and_retains_success_and_missing`
- [tests/life/test_read_failure_continuation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_read_failure_continuation.py) — `test_expired_failure_replans_the_same_activity_at_full_quota_with_original_remaining_arguments`
- [tests/life/test_activity_execution_context.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_activity_execution_context.py) — `test_explicit_resume_freezes_its_authorized_context_for_restart_reconciliation_without_replaying`

### PF-10 检索来源证明、阶段降级和预算

- 类型：historical_fix。
- 负责卡：RB-02、RB-07、RB-09、RB-12。
- 保留行为：保留批量来源证明的AND/OR/关系完整性、候选补位和最终复检；支路失败保留其他有效结果，真实取消/权限失效不能伪装普通降级；不恢复排队扣时。
- 修复依据：[docs/development/retrieval-life-closure-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/retrieval-life-closure-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/memory/test_candidate_sources.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/memory/test_candidate_sources.py) — `test_alternative_supports_never_discard_required_or_relation_sources`
- [tests/memory/test_candidate_sources.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/memory/test_candidate_sources.py) — `test_large_and_groups_and_many_or_alternatives_use_legal_source_batches`
- [tests/core/test_retrieval_timing.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_retrieval_timing.py) — `test_short_upstream_deadline_keeps_lexical_evidence_without_starting_remote_work`
- [tests/core/test_retrieval_timing.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_retrieval_timing.py) — `test_default_foreground_entrypoints_execute_both_thirty_second_phases`

### PF-11 图文整理、短引用和一次远端候选准备

- 类型：historical_fix。
- 负责卡：RB-07、RB-08、RB-10、RB-15、RB-17。
- 保留行为：先确定完整材料范围再调用必要远端召回，试装不反复付费；原图/作者/引用保留，短引用绑定冻结批次；仅完整语义完全相同才无损去重。
- 修复依据：[docs/development/runtime-recovery-closure-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/runtime-recovery-closure-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/memory/test_material_media.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/memory/test_material_media.py) — `test_preparation_fits_images_before_one_remote_recall_and_reuses_selected_range`
- [tests/memory/test_material_media.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/memory/test_material_media.py) — `test_repeated_source_version_keeps_each_reporting_subject_but_sends_one_image`
- [tests/companion/test_growth_short_refs.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/companion/test_growth_short_refs.py) — `test_short_mapping_cannot_be_borrowed_from_another_batch`
- [tests/companion/test_growth_short_refs.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/companion/test_growth_short_refs.py) — `test_only_identical_full_semantics_are_deduplicated`

### PF-12 独立订阅的作者授权与故障隔离

- 类型：historical_fix。
- 负责卡：RB-02、RB-03、RB-05、RB-18、RB-23、RB-26。
- 保留行为：管理员独立订阅不绑定/借用角色；合法授权epoch变化可正常刷新，真实范围/权限丢失只阻止相关任务，不阻塞其他源。
- 修复依据：[docs/development/life-runtime-fixes-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/life-runtime-fixes-2026-11-02.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/capabilities/test_media_scheduling.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/capabilities/test_media_scheduling.py) — `test_global_admin_feed_refreshes_epoch_without_selecting_or_borrowing_a_role`
- [tests/capabilities/test_media_scheduling.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/capabilities/test_media_scheduling.py) — `test_global_admin_feed_stops_when_principal_scope_or_required_permission_is_lost`
- [tests/capabilities/test_media_scheduling.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/capabilities/test_media_scheduling.py) — `test_real_scope_or_source_loss_blocks_only_its_source`

### PF-13 外部发送未知及迟到取消不产生二次效果

- 类型：historical_fix。
- 负责卡：RB-04、RB-06、RB-16、RB-20、RB-22、RB-23、RB-26。
- 保留行为：外部已提交/unknown不按新任务重放；明确取消与消费者中断分开，迟到控制不能改写原物理回执。空库切换同样不得重放旧外部任务。
- 修复依据：[docs/development/nr06-diary-and-computation-retry-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/nr06-diary-and-computation-retry-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/channels/test_transport_cancellation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/channels/test_transport_cancellation.py) — `test_ws_disconnect_keeps_original_unknown_without_second_send`
- [tests/channels/test_delivery_contact_preflight.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/channels/test_delivery_contact_preflight.py) — `test_submitted_or_unknown_original_is_never_reclassified_as_a_new_candidate`
- [tests/models/test_cancellation_reasons.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/models/test_cancellation_reasons.py) — `test_submitted_model_cancellation_keeps_unknown_and_its_actual_cause`

### PF-14 固定人格与独立日记材料预算

- 类型：established_contract。
- 负责卡：RB-04、RB-10、RB-12、RB-15。
- 保留行为：固定人格不可被预算裁掉；动态材料不升为系统权威；保留当前独立输入上限与完整单元预算，发现/读到/想象/未知结果分开。
- 修复依据：[docs/development/nr06-diary-and-computation-retry-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/nr06-diary-and-computation-retry-2026-11-03.md)。
- 覆盖说明：已定位相关现有用例及实现入口；本轮未执行。新结构实施时复用或等价改写受影响断言，不继承旧通过结论。
- 本轮产品测试：未运行。

现有用例入口：

- [tests/context/test_budget_units.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/context/test_budget_units.py) — `test_full_persona_is_mandatory_and_estimates_are_honest`
- [tests/life/test_diary_materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_diary_materials.py) — `test_diary_uses_exact_independent_200000_input_budget_and_omits_whole_units`
- [tests/life/test_diary_materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_diary_materials.py) — `test_daily_semantics_preserve_discovery_read_failure_imagined_and_unknown_image_boundaries`

### PF-15 原图、上下文范围、记录全文和材料覆盖合同

- 类型：historical_fix。
- 负责卡：RB-06、RB-08、RB-12、RB-22。
- 保留行为：范围上下界、冻结 material ref、processed/new 覆盖必须一致；关系端点被省略时补取必要全文；原图保留，技术说明不冒充语义身份。
- 修复依据：[docs/development/memory-multimodal-recovery-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/memory-multimodal-recovery-2026-11-03.md)。
- 补充依据：[docs/development/background-budget-rerank-2026-11-04.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/background-budget-rerank-2026-11-04.md)。
- 补充依据：[docs/development/runtime-recovery-closure-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/runtime-recovery-closure-2026-11-03.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-16 内部补取动作不污染生活与学习

- 类型：historical_fix。
- 负责卡：RB-06、RB-10、RB-15、RB-18、RB-23、RB-26。
- 保留行为：内部 channels.read/get_msg/forward 补取保留原消息与动作审计，不重复生成无原件生活经历；不能把底层 I/O 当作角色学习事实。
- 修复依据：[docs/development/life-runtime-fixes-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/life-runtime-fixes-2026-11-02.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-17 无变化通知不重启高频空轮

- 类型：historical_fix。
- 负责卡：RB-03、RB-13、RB-14、RB-22、RB-26。
- 保留行为：真实新工作/入站/到期仍能唤醒；空通知不刷新已定 due，不不断增加版本、派发或恢复短轮询。
- 修复依据：[docs/development/proactive-companion-repair-r66-2026-10-31.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/proactive-companion-repair-r66-2026-10-31.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-18 多模态估算与供应商用量分开

- 类型：historical_fix。
- 负责卡：RB-04、RB-12、RB-18。
- 保留行为：内联图片按视觉输入估计，不把 Base64 字符当文本 token；estimated、reported、proposal byte ceiling 三者不混用。
- 修复依据：[docs/development/life-runtime-fixes-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/life-runtime-fixes-2026-11-02.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-19 图像工作流的固定默认、动态场景与未知结果

- 类型：historical_fix。
- 负责卡：RB-16、RB-18。
- 保留行为：保留固定人物/模型/LoRA/画风及明示分段默认；动态场景不隐式继承旧海景/姿势全文；ComfyUI 原 handle 取回，consistency unknown 不改成 matched，create_only 不自动发送。
- 修复依据：[docs/development/life-runtime-fixes-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/life-runtime-fixes-2026-11-02.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-20 输入处理中撤回的正常取消

- 类型：historical_fix。
- 负责卡：RB-02、RB-06、RB-16。
- 保留行为：已撤回输入不创建新 turn/新回复；处理中发现撤回按正常失效/取消收尾，原外部结果仍保留，不能以异常重试重建来源。
- 修复依据：[docs/development/nr06-diary-and-computation-retry-2026-11-03.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/nr06-diary-and-computation-retry-2026-11-03.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-21 同事务来源证明与真实目标推进

- 类型：historical_fix。
- 负责卡：RB-02、RB-03、RB-14、RB-15。
- 保留行为：新建回执与目标/经历提交使用同一数据库事务中的来源验证；独立连接尚看不见未提交回执不代表来源失效；越权仍拒绝。
- 修复依据：[docs/development/autonomous-life-closure-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/autonomous-life-closure-2026-11-02.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-22 首次夜间启动、日界线和日期映射

- 类型：historical_fix。
- 负责卡：RB-11、RB-13、RB-15。
- 保留行为：首次启动不能把未发生的醒→睡当成闭日；当地日期/时区/纯日期序列化一致；日记 DTO 与原日期吻合。
- 修复依据：[docs/development/autonomous-life-closure-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/autonomous-life-closure-2026-11-02.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

### PF-23 成功模型回执的合法消费上下文

- 类型：historical_fix。
- 负责卡：RB-02、RB-04、RB-13、RB-14。
- 保留行为：原冻结请求、物理尝试、合法观察者与真实回执对应；权限版本变化合法刷新，不陷入 model_result_context_forbidden 循环；真失权停止应用，不重造执行身份。
- 修复依据：[docs/development/autonomous-life-closure-2026-11-02.md](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development/autonomous-life-closure-2026-11-02.md)。
- 覆盖说明：已读历史修复说明；此条尚未绑定精确现有函数。本轮不声称已验证。改到相应路径时先复用已有相关用例，只补缺失的行为反例。
- 本轮产品测试：未运行。

尚未绑定精确现有函数；实施对应卡时优先复用已有行为用例，不将空缺冒称通过。

## 当前问题

### UF-01 不完整响应反复重试，并由 unknown 占位拖慢其他后台请求

- 证据类别：runtime_confirmed。
- 负责卡：RB-04、RB-05、RB-08、RB-19。
- 处理：区分本地执行许可与上游结果；本地调用结束即释放云 API 本地许可，保留 unknown；按同一失败形状改用有界恢复，不无限原样重试。
- 关闭条件：23 次不完整响应形状可复现；一个失败批不能让无关请求等 300 秒；上游未知与未知费用仍可见；迟到响应最多应用一次。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/runtime-chain-review-2026-11-04-evening.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/runtime-chain-review-2026-11-04-evening.md)。
- 依据：[.runtime/chain-check-20261104-1814/closing.json](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/closing.json)。
- 最新运行数量与局限以22:13的[最终审计](tianshu-final-system-audit-2026-11-04.md)为准；原18:33数字只作历史对照。

### UF-02 日程候选含失活目标；续接 key 与能力校验冲突

- 证据类别：runtime_confirmed。
- 负责卡：RB-11、RB-13、RB-14。
- 处理：构造、应用、执行复用同一资格规则；模型选择程序签发的候选键，程序解析下一步真实能力。
- 关闭条件：完成目标不再成为继续候选；content.read→discovery_page→content.discover 合法衔接；非法提案不清除已有合法日程。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/runtime-chain-review-2026-11-04-evening.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/runtime-chain-review-2026-11-04-evening.md)。
- 依据：[.runtime/chain-check-20261104-1814/followup.json](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/followup.json)。
- 最新运行数量与局限以22:13的[最终审计](tianshu-final-system-audit-2026-11-04.md)为准；原18:33数字只作历史对照。

### UF-03 不可用媒体使整条消息拒绝

- 证据类别：runtime_confirmed。
- 负责卡：RB-06、RB-16、RB-18。
- 处理：输入使用 available/unavailable/pending 的分片状态，保留可用内容及原身份；确实依赖缺失片段的能力局部返回缺失。
- 关闭条件：复现 39 片段、2 不可用视频、8 可用图片时仍正常入站；补全附件不重复生成 turn 或发送。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/runtime-chain-review-2026-11-04-evening.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/runtime-chain-review-2026-11-04-evening.md)。
- 最新运行数量与局限以22:13的[最终审计](tianshu-final-system-audit-2026-11-04.md)为准；原18:33数字只作历史对照。

### UF-04 Growth 完整响应的 JSON 语法错误没有进入纠正

- 证据类别：runtime_confirmed。
- 负责卡：RB-04、RB-10、RB-19。
- 处理：归一化 JSON/schema/引用错误诊断，已知完整响应可作一次受限纠正；结果未知不作语义修补。
- 关闭条件：原 succeeded+stop 但 JSON 无效样本进入一次纠正；再次失败只阻塞该批并准确展示；后续材料可继续。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/runtime-chain-review-2026-11-04-evening.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/runtime-chain-review-2026-11-04-evening.md)。
- 最新运行数量与局限以22:13的[最终审计](tianshu-final-system-audit-2026-11-04.md)为准；原18:33数字只作历史对照。

### UF-05 Memory 重复 create、引用范围和纠正输出失败

- 证据类别：runtime_confirmed。
- 负责卡：RB-07、RB-08。
- 处理：模型只生成作用域内别名；程序编译全局身份与依赖关系；有界纠正保留原错误链。
- 关闭条件：保留旧批原 maxItems→纠正 duplicate、新批 duplicate→纠正 JSON 无效的真实顺序；不靠任意合并异义记录或无限纠正通过。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/runtime-chain-review-2026-11-04-evening.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/runtime-chain-review-2026-11-04-evening.md)。
- 最新运行数量与局限以22:13的[最终审计](tianshu-final-system-audit-2026-11-04.md)为准；原18:33数字只作历史对照。

### UF-06 日程只读接口两次约 8.7 秒

- 证据类别：runtime_confirmed_root_cause_unresolved。
- 负责卡：RB-11、RB-19、RB-21。
- 处理：先分解 SQL、来源证明、序列化耗时，再优化已定位部分；读取精简投影、批量证明和分页。
- 关闭条件：报告同规模材料的阶段耗时与整体时延；不把两次样本叫 p95，不预先断言 N+1 或缺索引。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/runtime-chain-review-2026-11-04-evening.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/runtime-chain-review-2026-11-04-evening.md)。
- 依据：[.runtime/chain-check-20261104-1814/http-day-plan-recheck.json](C:/YOKI/Codex/tianshu-peiban-bot/.runtime/chain-check-20261104-1814/http-day-plan-recheck.json)。
- 最新运行数量与局限以22:13的[最终审计](tianshu-final-system-audit-2026-11-04.md)为准；原18:33数字只作历史对照。

### UF-07 监控健康但业务阻塞未表达

- 证据类别：runtime_confirmed。
- 负责卡：RB-19、RB-21。
- 处理：在现有诊断内统一展示输入、业务提交、独立重试、失败年龄、重复失败和 unknown。
- 关闭条件：主表正常、重试批 blocked 时仍显示真实失败；无样本、零值、未返回数据分开；模型成功不冒充业务成功。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/runtime-chain-review-2026-11-04-evening.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/runtime-chain-review-2026-11-04-evening.md)。
- 最新运行数量与局限以22:13的[最终审计](tianshu-final-system-audit-2026-11-04.md)为准；原18:33数字只作历史对照。

### SF-01 学习历史与当前习惯双写，撤回后可能从 Memory 再被注入

- 证据类别：source_confirmed_risk_no_live_bad_reply_proven。
- 负责卡：RB-07、RB-10、RB-12。
- 处理：Companion 唯一拥有学习记录、版本与有效状态；Memory 只建带类型的可重建索引引用。
- 关闭条件：撤回/修订后即使旧索引尚存，回复也不能采用旧行为；历史查询可以解释曾学过什么；不依赖双表状态同步正确。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/learning-enhancement-queue-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/learning-enhancement-queue-2026-11-04.md)。

### SF-02 习惯运行查询先取管理列表 40 条，再筛选有效项

- 证据类别：source_confirmed。
- 负责卡：RB-10。
- 处理：独立运行查询先按角色/对象/群/时效/active 筛选，再排序和限量，不借用管理分页。
- 关闭条件：前 40 条失效、第 41 条有效时仍能选择该有效项；运行查询不附带管理统计。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/learning-enhancement-queue-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/learning-enhancement-queue-2026-11-04.md)。

### SF-03 可选长期材料先于较早对话获得预算

- 证据类别：source_confirmed_risk_no_live_bad_reply_proven。
- 负责卡：RB-12、RB-16。
- 处理：优先保留当前问题所依赖的对话链、工具结果和完整人格，再分配可选记忆与习惯。
- 关闭条件：‘刚才第二种’的被指代内容保留；预算不足明确缺口，不把悬空句当完整上下文。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/learning-enhancement-queue-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/learning-enhancement-queue-2026-11-04.md)。

### SF-04 本次支持子集与整批生成依赖容易混作证据强度

- 证据类别：source_confirmed_semantic_boundary。
- 负责卡：RB-02、RB-08、RB-10、RB-17。
- 处理：support 与 generation_dependency 分型；独立证据按真实来源/作者/事件计算，派生重复不增加支持。
- 关闭条件：一条支持、整批 32 条上下文时支持数仍为 1；来源撤回按各类依赖语义失效。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/learning-enhancement-queue-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/learning-enhancement-queue-2026-11-04.md)。

### SF-05 同种材料的 content_revisions 可能覆盖

- 证据类别：source_confirmed。
- 负责卡：RB-12、RB-19。
- 处理：取材清单按完整记录身份+语义版本标记，不以 kind 作为唯一键。
- 关闭条件：同类两个规则同时入选时两条版本均保留；去重仅合并同身份同版本，条件不同的规则不合并。
- 当前状态：unresolved_in_current_evidence。
- 依据：[docs/development/learning-enhancement-queue-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/learning-enhancement-queue-2026-11-04.md)。

### SF-06 Memory 生产事务内直接调用 Growth/Diary 消费者

- 证据类别：source_confirmed_coupling。
- 负责卡：RB-03、RB-10、RB-15。
- 处理：生产者事务中只提交事实与原生队列通知，独立消费者提交自身状态和消费回执。
- 关闭条件：Growth 注册失败不回滚 Memory 事实；崩溃在提交前后都不丢通知，重复消费不重复产物。
- 当前状态：unresolved_in_current_evidence。
- 依据：[source:src/tianshu/runtime/product.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/product.py)。

### NF-01 监控子进程退出会触发整体关闭

- 证据类别：source_confirmed_cascade_path。
- 负责卡：RB-05、RB-26、RB-21。
- 处理：进程按实际必需依赖分类；监控/运维子进程独立重启，BOT和媒体分组恢复。
- 关闭条件：模拟Loki/Vector/Grafana退出，健康业务不被同时终止；重新接入继续补传。
- 当前状态：unresolved_source_finding_not_failure_injected。
- 依据：[docs/development/tianshu-final-system-audit-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/tianshu-final-system-audit-2026-11-04.md)。
- 源码：[src/tianshu/runtime/supervisor.py:133](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/supervisor.py:133)。
- 源码：[src/tianshu/runtime/supervisor.py:258](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/supervisor.py:258)。
- 源码：[src/tianshu/runtime/supervisor.py:292](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/supervisor.py:292)。

### NF-02 单个队列消费者协程退出会取消其他消费者

- 证据类别：source_confirmed_cascade_path。
- 负责卡：RB-05、RB-22、RB-21。
- 处理：消费者独立监管；BOT前台、BOT后台、媒体、运维分配各自进程和连接配额。普通单任务异常与消费者进程异常分别处理。
- 关闭条件：一个consumer异常只恢复其执行域，其他域持续提交；不误称普通任务失败会直接导致全停。
- 当前状态：unresolved_source_finding_not_failure_injected。
- 依据：[docs/development/tianshu-final-system-audit-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/tianshu-final-system-audit-2026-11-04.md)。
- 源码：[src/tianshu/runtime/process.py:222](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/process.py:222)。
- 源码：[src/tianshu/runtime/process.py:257](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/process.py:257)。

### NF-03 日志落盘失败可以阻止业务开始，慢落盘增加等待

- 证据类别：source_confirmed_coupling。
- 负责卡：RB-03、RB-05、RB-24、RB-26。
- 处理：业务事实与审计回执在领域事务持久化；运行日志独立缓冲/批量落盘/补传，后端监控故障不传播到业务入口。
- 关闭条件：Loki不可达、日志writer慢/失败的可恢复场景中业务可继续；缓冲耗尽/磁盘满明确容量处理和覆盖，不能丢日志还报完整。
- 当前状态：unresolved_source_finding_not_failure_injected。
- 依据：[docs/development/tianshu-final-system-audit-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/tianshu-final-system-audit-2026-11-04.md)。
- 源码：[src/tianshu/runtime/events.py:74](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/events.py:74)。
- 源码：[src/tianshu/runtime/api.py:103](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/api.py:103)。
- 源码：[src/tianshu/runtime/jobs.py:69](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/jobs.py:69)。

### NF-04 确定性同步退避与多处恢复缺少端到端尝试归属

- 证据类别：source_confirmed_policy_gap。
- 负责卡：RB-04、RB-22、RB-23。
- 处理：统一错误分类、逻辑请求尝试计数、随机退避和Retry-After；恢复只由领域owner发起，Agent调用同一入口。
- 关闭条件：大量相同429不会同时重试；agent、领域和传输层不叠乘尝试；三批27/21/18次的死循环形状在有限恢复后停止。
- 当前状态：unresolved_source_finding_not_failure_injected。
- 依据：[docs/development/tianshu-final-system-audit-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/tianshu-final-system-audit-2026-11-04.md)。
- 源码：[src/tianshu/models/retry.py:50](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/retry.py:50)。
- 源码：[src/tianshu/companion/growth_retry.py:79](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/growth_retry.py:79)。
- 源码：[src/tianshu/memory/index_jobs.py:301](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/index_jobs.py:301)。
- 源码：[src/tianshu/runtime/jobs.py:91](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/jobs.py:91)。

### NF-05 标准输出被统一标INFO，级别筛选不能直接信任

- 证据类别：source_confirmed_observability_gap。
- 负责卡：RB-24、RB-25。
- 处理：应用原生结构化输出保留真实level，外部原文无法解析则明确未分类，不伪装INFO。
- 关闭条件：原WARNING/ERROR在日志页筛选和计数正确；多行trace关联原事件，不能每行各报一次事故。
- 当前状态：unresolved_source_finding_not_failure_injected。
- 依据：[docs/development/tianshu-final-system-audit-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/tianshu-final-system-audit-2026-11-04.md)。
- 源码：[src/tianshu/runtime/supervisor.py:78](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/supervisor.py:78)。
- 源码：[src/tianshu/runtime/logging.py:27](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/logging.py:27)。

### NF-06 日志只按时间戳翻页存在同时间戳漏行风险

- 证据类别：source_confirmed_edge_case_not_reproduced_live。
- 负责卡：RB-24、RB-21。
- 处理：服务端opaque cursor携带边界事件身份，重叠读取、去重并完整耗尽边界；无法证明覆盖时显示缺口。
- 关闭条件：超过页上限的同时间戳日志跨页不漏不重；断线跟随和迟到日志语义明确。
- 当前状态：unresolved_source_finding_not_failure_injected。
- 依据：[docs/development/tianshu-final-system-audit-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/tianshu-final-system-audit-2026-11-04.md)。
- 源码：[src/tianshu/runtime/diagnostics.py:114](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/diagnostics.py:114)。
- 源码：[web/src/features/flow-graph/RuntimeView.tsx:192](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/web/src/features/flow-graph/RuntimeView.tsx:192)。
- 源码：[web/src/features/settings/TasksPanel.tsx:852](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/web/src/features/settings/TasksPanel.tsx:852)。

### NF-07 所有模块顺序启动，局部启动失败可阻止整组worker启动

- 证据类别：source_confirmed_coupling。
- 负责卡：RB-01、RB-05、RB-18、RB-26。
- 处理：按角色与领域装配依赖；非必需模块失败只降级所属能力，核心数据库/schema不可用则如实停止依赖它的写入。
- 关闭条件：无BOT角色/QQ不可用仍可启动独立媒体任务；某插件坏配置不阻止无关模块；状态区分unconfigured/degraded/failed。
- 当前状态：unresolved_source_finding_not_failure_injected。
- 依据：[docs/development/tianshu-final-system-audit-2026-11-04.md](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/tianshu-final-system-audit-2026-11-04.md)。
- 源码：[src/tianshu/runtime/process.py:200](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/process.py:200)。
- 源码：[src/tianshu/runtime/product.py:82](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/product.py:82)。
- 源码：[src/tianshu/capabilities/service.py:306](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/capabilities/service.py:306)。

### SF-07 日程应用只返回创建数量，全跳过可能被记作主动留白

- 证据类别：source_confirmed_path_not_reproduced_live。
- 负责卡：RB-13、RB-19、RB-25。
- 处理：返回逐候选应用结果，区分主动留白、排不下、状态变化与非法提案；调度与页面统计使用同一领域结果。
- 关闭条件：生成后状态变化、全候选无窗口、主动留白及混合结果分别验证；全部跳过不写主动留白成功指纹，已有成功效果不重放。
- 当前状态：unresolved_in_current_source；仅源码复核，未实机复现或执行产品测试。
- 依据：[src/tianshu/life/day_plan.py:218](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/day_plan.py:218)。
- 依据：[src/tianshu/life/day_plan.py:233](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/day_plan.py:233)。
- 依据：[src/tianshu/life/day_plan.py:240](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/day_plan.py:240)。
- 依据：[src/tianshu/life/planning_engine.py:318](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/planning_engine.py:318)。

### SF-08 线上2秒检索配置尚未进入代码fallback和新装示例

- 证据类别：source_confirmed_default_drift_risk。
- 负责卡：RB-09、RB-20。
- 处理：同步代码fallback、新装配置种子、部署示例和说明为2.0秒，本地查询/来源窗口与两个30秒模型阶段独立。
- 关闭条件：省略配置项、部署示例及显式覆盖三个入口读回正确；向量阶段降级仍能返回其他合法结果。
- 当前状态：unresolved_in_current_source；仅源码复核，未实机复现或执行产品测试。
- 依据：[src/tianshu/memory/retrieval_budget.py:16](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/retrieval_budget.py:16)。
- 依据：[deploy/core/settings.example.json:37](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/settings.example.json:37)。
- 依据：[docs/development/CURRENT.md:17](C:/YOKI/Codex/tianshu-peiban-bot/docs/development/CURRENT.md:17)。

## 用户需求闭环

| 需求 | 内容 | 负责卡 |
|---|---|---|
| FR-01 | 一个坏材料只阻塞真实依赖它的产物，其他材料、主体、模块和独立媒体继续推进；不能伪造完整覆盖。 | RB-03、RB-05、RB-08、RB-10、RB-14、RB-22、RB-21 |
| FR-02 | 确定性修复、有限模型纠正、依赖级恢复和已配置备用路由构成冗余；未知外部动作不盲重放。 | RB-04、RB-22、RB-23、RB-26 |
| FR-03 | 用户指定运维模型；简单登记动作自动执行并验证，复杂故障发送给配置的管理员或邮箱。 | RB-23、RB-26、RB-19 |
| FR-04 | 独立平台日志页，级别/域/时间/链路过滤、跟随/暂停、稳定分页、明示覆盖和脱敏导出。 | RB-24、RB-19 |
| FR-05 | 独立系统统计页，资源、消息、模型、业务积压、恢复效果和独立媒体分域展示，口径与数据新鲜度准确。 | RB-25、RB-19 |
| FR-06 | 保留全部已接受的Memory/学习/日程优化、历史修复和独立媒体边界；不新增常规用户审批门禁。 | RB-01、RB-18、RB-20、RB-21 |

## 旧队列映射

### SD

| 原编号 | 新归属/处置 |
|---|---|
| SD-01 | RB-01 |
| SD-02 | RB-02 |
| SD-03 | RB-03 |
| SD-04 | RB-04 |
| SD-05 | RB-05 |
| SD-06 | RB-06 |
| SD-07 | RB-07 |
| SD-08 | RB-08 |
| SD-09 | RB-09 |
| SD-10 | RB-10 |
| SD-11 | RB-12 |
| SD-12 | RB-11 |
| SD-13 | RB-13 |
| SD-14 | RB-14 |
| SD-15 | RB-10、RB-15 |
| SD-16 | RB-16 |
| SD-17 | RB-17 |
| SD-18 | RB-18 |
| SD-19 | RB-19、RB-24、RB-25 |
| SD-20 | RB-04、RB-19、RB-21、RB-22、RB-24、RB-25 |
| SD-21 | RB-20 |
| SD-22 | RB-21 |

### ML

| 原编号 | 新归属/处置 |
|---|---|
| ML-01 | RB-01、RB-02、RB-12 |
| ML-02 | RB-06 |
| ML-03 | RB-08 |
| ML-04 | RB-04、RB-07、RB-08、RB-10、RB-15、RB-19、RB-21 |
| ML-05 | RB-03、RB-10、RB-16 |
| ML-06 | RB-06、RB-12、RB-16 |
| ML-07 | RB-10 |
| ML-08 | RB-08 |
| ML-09 | RB-09 |
| ML-10 | RB-07、RB-09 |
| ML-11 | RB-07 |
| ML-12 | RB-17 |
| ML-13 | RB-17 |
| ML-14 | 仅BOT旧业务域的历史缺口补偿与旧数据迁移因本次空库取消；原故障以脱敏合成场景保留，未来新任务恢复进入RB-03/04/08/10/15/22；独立媒体原任务及共享回执不取消。 |
| ML-15 | RB-04、RB-19、RB-21 |
| ML-16 | RB-04、RB-19、RB-20、RB-21 |

### LE

| 原编号 | 新归属/处置 |
|---|---|
| LE-01 | RB-10、RB-12、RB-16 |
| LE-02 | RB-10 |
| LE-03 | RB-17 |
| LE-04 | RB-17 |
| LE-05 | RB-01、RB-04、RB-12、RB-19、RB-21 |
| LE-06 | RB-04、RB-19、RB-21 |

## 验证边界

行为验证与当前改动相称。旧测试在未变输入下的适用结果复用；新结构改变入口时保留语义断言，补新增隔离/恢复/日志/统计边界。RB-21只补集中装配缺口。没有新审批链，没有默认全库测试或生产故障注入；没有真实样本的能力不标实机验收。

## 第四版方案衔接补充

以下六项是实施合同与验收补足，不扩大20组现有故障计数；详细依据见[审计第14节](tianshu-final-system-audit-2026-11-04.md)。

| 衔接项 | 负责卡 | 必需反例 |
|---|---|---|
| CP-01 空库按业务归属与引用闭包处理 | RB-01、RB-02、RB-18、RB-20、RB-21 | 混合BOT/媒体隔离库执行切换后，BOT业务域为空、媒体订阅和原任务能续接、保留凭据能解密、共享文件仍可访问；不能靠重建媒体任务通过。 |
| CP-02 一次清空、切换隔离与未来升级分别定义 | RB-01、RB-03、RB-06、RB-16、RB-19、RB-20、RB-21 | 空库首装之后产生数据，再做一次受影响域升级，数据保留；旧代际命令明确过期而非新建重复动作；兼容回滚不覆盖新版合法写入。 |
| CP-03 六类角色采用总资源核算与公平推进 | RB-03、RB-05、RB-22、RB-24、RB-25、RB-26 | 六角色总资源在实际部署预算内；长时间前台/到期任务流量下后台与媒体有进展；日志/统计查询不能占尽控制连接。 |
| CP-04 备用路由不能破坏向量空间与冻结请求 | RB-04、RB-07、RB-08、RB-09、RB-12、RB-23 | 主备嵌入同维不同空间时不能混写/混查；新profile未就绪时聊天不因建索引全停；备选窗口装不下必要内容时不截断强制材料或冒充完整。 |
| CP-05 恢复预算贯穿整条故障链，人工操作优先 | RB-03、RB-22、RB-23、RB-26 | A→B→A路由切换与子任务换ID不能绕过恢复上限；用户取消/改配置后旧诊断动作失效；真正的新材料仍能独立继续。 |
| CP-06 空库第一天与必要材料缺失均有正常路径 | RB-06、RB-07、RB-10、RB-11、RB-12、RB-13、RB-15、RB-19、RB-20、RB-21 | 真正的BOT空库从首次配置、首条输入、首次事实/学习/日程/日记到一次重启能够按需推进；无材料不误报故障，无向量不导致全部查询失败。 |

## 第五版两来源窗口核对

两窗口共20项结论逐项登记在队列`source_crosscheck`及[审计第15节](tianshu-final-system-audit-2026-11-04.md)。这20项是来源映射，不是20个新故障或新开发卡。关键修正为整批事务、附件独立补充、固定编译输入、日程应用结果、2秒新装默认、HTTP计时和学习效果分别对照。全部产品修复仍为planned。

旧BOT存量补偿由本次空库决策替代；故障形状保留为脱敏合成验证，独立媒体原任务保留。Companion单一学习权威替代LE增量方案中的两种存储表示，历史可查询/撤回不复活的行为要求继续保留。


## 第六版完整来源与功能盘点

既有22组问题、23项历史行为和41个旧用例入口保持；本轮没有新增生产事故结论。17次原请求、44项来源任务、22类功能、六角色装配与10组RI规划补全详见[审计第16节](tianshu-final-system-audit-2026-11-04.md)，RI均已写入原26卡，不是新任务卡或新上线结果。原生Memory工具/语义失效/渲染发送、Episode历史、异步交接、功能保留和实际评估口径均已补全；旧检查仅在对应实现/输入未变时复用。
