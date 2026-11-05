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

- 新增 `test_rbf_planning_recovery.py`、`test_rbf_content_recovery.py`、`test_rbf_source_preparation.py`、`test_rbf_generation_recovery.py`、`test_rbf_statistics.py`，覆盖语义变化与同语义通知、可用性恢复、合并唤醒、原请求中断/丢 ACK、原子替换回滚、远端未确认回滚、读取原回执和取消/暂停、来源聚合、回填完成条件。
- 已静态审阅 diff，`git diff --check` 与 Python AST 解析；按本批授权未运行测试、构建、烟测、真实模型/QQ 或生产操作。测试代码是否通过、共享实现联调及业务结果均由主协调者集中验证。
- 历史 Life fixture 的 Models 替身需要实现带 `phase` 的 `status` 和 `reusable_result`，Sources 替身需支持 `prepare_event_writes`；旧数据库 fixture 需应用本域增量迁移。旧统计测试中“在线扫描永远 incomplete”或“仍有 replay 即 complete”的断言需按新合同更新。
