# NR-08B：Memory 准备范围与一次候选召回

基线 `59603da3a45138b6c273f3f7ef7bc0e2a52d708b`，分支 `codex/nr08-memory-preparation`。复用受管 worktree `C:/Users/Administrator/.codex/worktrees/memory-materials/tianshu-peiban-bot`；旧 NR-07 分支保留。

`Consolidation._prepare()` 先以 `MaterialBuilder.build(include_records=False)` 装配新材料、原图、引用和已授权主体，用现有 counter 确定实际范围。试装重建只读域内缓存，不做旧记录远端召回。范围固定后 `attach_records()` 只执行一次本地与远端候选选择，再完成请求装配；候选过大时裁完整背景消息、旧记录正文及必要的整条候选，清理仅由被裁候选引入的主体和读集，不回头重建或缩掉新材料。

原图、来源版本、作者、报告者、会话/权限、显式引用与原有最终来源验证、读集/CAS 保持。最终候选阶段不移除此前已装入的必要引用背景。确实装不下最低原材料仍返回现有预算失败；输入 16,384、Memory 模型执行 300 秒及检索各阶段 30 秒均未扩大。`input_layout` 更新为 `consolidation-fitted-materials-v3`，使旧输入布局的已知预算失败可按现有配置变化机制重新判断。

`PreparationReads` 的缓存只存在一次准备中，键含完整授权上下文及相应来源版本/范围、查询、主体和筛选。来源窗口、来源集合、会话邻域、身份解析和最终候选集合可复用；不跨准备共享，不覆盖最终来源/权限/CAS 复核。纯图片的空文本查询会去掉连接产生的空白，不向云端发送空查询；仍保留图片和本地逐主体旧记录。

后台专用 `MemoryRetrieval.candidates()` 直接返回已有 `Page`，唯一生产消费者材料端保留 `Page.coverage.missing`，未改 `query/search` 或前台排序算法。旧记录端口抛错时继续使用本地候选；`selection_coverage.record_candidates_missing` 保留候选降级原因，包括重排不可用。排序候选不完整不冒充新原件缺失，也不据此宣布新材料或业务失败。

首次试装用生产 counter 测量有限的候选诊断结构，另以有限 ASCII 字节上界覆盖 tokenizer 差异，预留仍计入原 16k。v2 模型视图最多展示 8 个、各 96 ASCII 字符的缺项码，其余明确展示省略数量，完整原码保留 manifest；整裁候选仅向模型展示数量，避免悬空候选别名和无界诊断占满材料空间。

manifest 新增有限数字 `preparation`：

- `assembly_attempts`：材料范围装配次数；`trial_count`：实际请求试装次数。
- `remote_calls`：后台候选端口调用次数，本次准备最多一次；不是底层 embed/rerank 物理请求总数。
- `remote_failures`：候选端口抛错次数；`remote_degradations`：返回非正常 ranked_selection 缺项的次数。
- `final_episodes`、`final_materials`、`final_new_materials`、`final_images`：最终提交准备的范围和实际模型图片数。
- `elapsed_ms`、`remote_ms`、`local_records_ms`、`assembly_ms`、`source_ms`、`conversation_ms`、`identity_ms`、`fit_ms`、`route_ms`，以及对应实际读取次数。`assembly_ms` 包含来源/邻域/身份子阶段，不应把所有子阶段相加当总耗时。`elapsed_ms` 从创建本次准备缓存开始，到提交批次前，不含模型执行和批次写事务。

这些值与既有 batch/step 身份及 EventLog 的 job/Models 时间对账，不增加日志框架、事件注册或公共 Models 接线。

验证状态：7 个变更 Python 文件已做 AST 语法解析和静态 diff 检查，未导入应用、运行测试/构建、访问生产或调用真实模型。

新增测试文件为 `tests/memory/test_preparation.py`，覆盖真实后台端口保留 Page/缺项、诊断有界且 manifest 完整、纯图片不发空白查询。集中验证精确目标如下：

- `tests/memory/test_preparation.py`
- `tests/memory/test_material_media.py::test_preparation_fits_images_before_one_remote_recall_and_reuses_selected_range`
- `tests/memory/test_material_media.py::test_preparation_remote_cache_is_bound_to_authority_and_keeps_degradation`
- `tests/memory/test_material_media.py::test_large_old_candidates_cannot_evict_fitted_new_or_referenced_images`
- `tests/memory/test_material_media.py::test_candidate_diagnostic_reserve_protects_limit_sized_original_text_and_image`
- `tests/core/test_consolidation_pipeline.py::test_candidate_preparation_recalls_once_after_multiple_input_shrinks`
- `tests/core/test_consolidation_pipeline.py::test_candidate_preparation_degrades_to_local_records_without_missing_originals`（两个参数场景）
- `tests/core/test_consolidation_pipeline.py::test_stub_dependency_gets_one_deterministic_supplement_or_explicit_unresolved`（原替身接收新准备参数，两个场景）

配合 NR-08A 原成功结果在同一次调用消费，四处旧断言已保持其真正测试意图：`test_lost_success_smaller_attempt_is_consumed_with_its_original_material_map`、`test_lost_success_consumes_original_attempt_and_keeps_material_map` 验证原成功结果立即消费且地图不重绑；`test_budget_configuration_change_keeps_an_unknown_original_attempt`、`test_pending_receipt_reports_only_frozen_targets_without_inventing_results` 改用真正未决的 `HeldConsolidator`。

尚无本轮真实耗时收益、积压消化或语义质量结论；由根集中验证和发布后读回 `preparation`、独立 Models 回执及实际材料/队列状态。
