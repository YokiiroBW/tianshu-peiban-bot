# RBF 共享接口确认

本文件为2026-11-05本批最终接口记录；异步早期建议有冲突时按本文件和后续明确版本更新处理。各窗口仍只写自己的任务范围。

## Runtime

- `RecoveryWait` 位于 `tianshu.runtime.recovery_policy`，继承保留AppError.code，含reason、retry_at（aware UTC datetime）、dependency。依赖冷却、探测占用、未到期等不终结业务、不耗新模型attempt预算。
- `policy.inspect_wait_on(conn,lineage_id,owner=...,expected_version=...)` 返回RecoveryWait或None；None不是新attempt授权。
- `jobs.register(...,resource_recovery=True)` 显式声明该handler能从同代际原持久状态/原动作回执恢复，默认False。只对确定本地资源等待/明确回滚续跑；commit未知先对账。同一显式opt-in handler也可抛typed RecoveryWait，由原Job按retry_at延后观察；不改旧claim/token，下次重新核验，等待事件为job.deferred/deferred。
- `from tianshu.runtime.database import DatabaseFailure,classify_database_failure`；分类器读Database在原异常上附的database_failure，不猜消息文本。字段reason/failure_phase/pool_name/role/sqlstate/rollback_confirmed/retryable/receipt_required/retry_at。取连接未开始事务、事务已确认回滚与提交不明分开；receipt_required不可自动重做。

## Catalog 与 Life

- `catalog.observation_digest(context)->str` 无I/O、不启动探针；完整TrustedContext下最新成功Availability语义的稳定摘要，排除TTL/verified_at/失败刷新，真实A→B→A保留变化。
- 回调仍 `on_change(context,capability_ids)`；根product.py用 `life.signal(context,'capability-observation:'+catalog.observation_digest(context))`。
- planning行增加semantic_availability（最近成功语义，叠加actor policy）、单列freshness/observed_at/expires_at。过期不授执行权，can_execute仍需当前证据。Life指纹使用稳定语义，机械通知项不入指纹；必要候选缺资料时等待刷新，不能先按临时空候选付费重算。
- Life使用通知revision与语义fingerprint分开，原子保存当前工作/目标版本/唤醒。域迁移计划增加wake_context、submission_confirmed；回填以真实原账本证据为准，根注册产品增量迁移。

## Memory 与 Growth

- 最终采用 `MemoryService.lock_episodes(context,refs,*,connection,extra_source_refs=())`。先读episode heads并合并source_refs/supplement_refs/extra refs，一次Sources验证，再锁头并复核revision/source/supplement/scope；返回原EpisodePage。
- Growth先准备自身request/relation/affect refs，一次传extra_source_refs；不同时保留外层重复完整证明和新helper两套路径。OR证据组不拍平成AND。

## Sources

- 无领域锁时先准备完整依赖，Identity→完整Sources集合→领域行CAS。
- `prepare_event_writes(connection,context,events,*,additional_refs=())`：events是含producer/event_id/revision/parts/support_refs的dict序列；批量锁全部事件和来源。实际会修改的target/关联根排他，纯support/additional只共享，按一致ID顺序。
- 单事件 `prepare_event_write` 对应同一逻辑。后续record_event使用相同事件标识、revision、parts、support，防止持领域锁后扩锁/锁升级。历史具体死锁成因仍需独立证据。

## Models 与 Observability

- `models.status(attempt_id,phase='pre_submit')` 仅用于未确认注册的原ID预检；HTTP GET ?phase=pre_submit 缺失200 receipt=null。observe缺失仍明确attempt_not_found；其他404不吞。已提交/unknown观察不能倒退为未发送。
- Models request标记model_attempt_id/model_lookup_phase/model_lookup_state；事件字段映射attempt_id/lookup_phase/lookup_state。只有明确pre_submit+absent+200正常，不全局降级404。
- `models.reusable_result(attempt_id,context=...,expected_request_digest=None,expected_data_generation=None)` 只复用原成功持久正文，拒绝明确取消、无正文、来源/身份失效及摘要/代际不符；不发新请求，不替代域提交时CAS。
- 根rb_logs_query的去敏metadata白名单加入lookup_phase/lookup_state/pool_name/failure_phase；RBF-08维护api/events/logging和Operations消费。等待事件按明确状态表达，不计模型失败。

## 完整链路补充确认

- RPC等待位于error.recovery_wait，仅真实RecoveryWait序列化。Runtime提供recovery_wait_to_wire/from_wire，严格结构kind=recovery_wait/version=1/reason/retry_at/dependency/rollback_confirmed；Models客户端仅HTTP409且code/reason一致时还原，无效包协议错误。远端否定证据独立保存，本地回滚不能洗成可自动重试；API仅安全证据true记deferred。
- Catalog.on_ready(context,capability_ids)只表示已知语义恢复current，批量机械唤醒；根绑定life.planning_ready，它仅唤醒reason=planner_capabilities_refresh的当前actor工作，不改语义指纹并保留5秒到期兜底。on_change仍只真实语义变化。
- 幂等补准入：本地claimed/dispatch_started不证明Broker已登记。尚未确认登记时，可在同场景/来源/代际/期限及领域CAS有效前提下用存档完整同一request、attempt_id与completion幂等补准入，不能创建新ID或延长预算；Models driver合并、唯一键及queued→running CAS约束物理外发。已确认登记缺失及已有unknown只观察。
- Models出现跨owner启动CAS输者错误收尾覆盖赢家的具体窄缺口，RBF-06追加本人queued/not_started+broker_instance条件的原子收尾修复；不得改已有running/unknown/终态及赢家诊断。
- content.read result.recovery为字符串resume_material_read/resume_commit/observe_original_attempt/none；同级phase/outcome/retry_at/attempt_number/max_attempts。outcome=wait且attempt_number=0不代表已读取失败。原Action/原材料全部读取方式共用5次物理预算，提交续接不重新读取。Actions.resume携原attempt/receipt/result_revision CAS及可选活动recovery_guard；pending才路由切换，terminal阶段恢复。历史缺少可验证检查点不伪造恢复。
- Conversation公共GenerationRequests的确认字段用于其run/claim路径；Life直接submit的本域确认字段是独立消费者的单调观察证据，均不构成第二个模型执行权威。

## 最终收口

- 共享依赖 probe 使用已有 token 和 expires_at。真实 cooldown/Retry-After 优先；有效租约内仅一个 owner，到期后在 dependency 行锁内按旧 token/expiry 接管。新材料接管不改原 attempt、unknown 或 lineage 预算；旧 probe 的迟到成功/失败不能覆盖新 probe 的状态。
- Models 已接受但尚未外发的依赖等待保存在原 submissions，状态为 checking/scheduled/observe_only，带递增 recovery_version；普通排队不占 checking。安全等待到期沿原 ID 续跑，未确认等待不自动外发。ModelReceipt.recovery 暴露状态与是否需要对账。
- 显式对账沿现有 models.manage：原 attempt 的 recovery version、request digest、data generation、client_request_id、evidence_ref 和 decision=authorized_resume_original，事务中复核当前操作者、原场景与来源、取消、queued/not_started/started_at、原请求及 CAS。授权依据与原否定证据追加保存；不把任意引用当作机器验证的回滚证明。running/submitted/unknown 不适用此入口。
- Memory/Growth 的远端控制不确定与本地事务安全分开。只有本地已确认回滚或尚未开始时，才将确切原材料保存在已有隔离/重试记录并释放分区执行位；后续独立材料可以推进，旧批只观察原 ID、复用已成功原结果，不能自动或手动派生新 attempt。原结果迟到仍核对来源、版本、游标和读取集。本地 commit 不明先对账，不能猜测释放。
- 产品迁移在冻结 baseline 后按序增加 models_recovery_wait、models_prestart_reconciliation、0041_conversation_submission_confirmation、rbf_life_planning_wake。测试 execution_migrations 复用同一 Models 迁移定义；不重写已部署的基线，不清空当前数据。
