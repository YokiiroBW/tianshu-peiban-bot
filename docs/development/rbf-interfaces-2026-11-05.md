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
