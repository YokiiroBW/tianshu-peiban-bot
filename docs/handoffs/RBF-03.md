# RBF-03 Memory 等待恢复与材料重复归因交接

## 范围和状态

- 工作树 `C:/Users/Administrator/.codex/worktrees/rbf-memory/tianshu-peiban-bot`，分支 `codex/rbf-memory-20261105`，基线 `33e5282f60fe4c803c3b16d498d1d924621ed1b9`；首个提交 `e66893d02ca57da7526f24780e50492a44895e1c`，后续返修提交 SHA 见本文件所在提交。
- 修改 Memory 模块、本卡测试和本交接；按协调者追加授权，仅适配三份既有 core 测试中的 Models fake。未运行测试、构建、烟测，未访问或修改生产、真实模型、QQ 或其他设备。本卡不是集成、部署或业务恢复验收。

## 改动和合同

- 将准备、纠正和上下文补充的原批次/请求先冻结，再使用原 `RecoveryPolicy` 申请物理尝试；统一 `RecoveryWait` 时事务内保存非终态原因、到期时间、原阶段和原批次唤醒，释放当前批次执行位，并继续处理其他新材料。纯计算的原 lineage、8 次额度、原 unknown、来源和回执不重置。远端回滚未确认且 Memory 本地事务已回滚或未开始时，隔离原批和原材料并保留否定证据，释放分区以处理其他材料；原 attempt 只读对账且不得产生新 attempt。Memory 本地提交不明时不释放活动位。重试恢复在同事务中取得可用 claim 再预约；`inspect_wait_on` 返回 `None` 本身不授予新尝试。
- `recovery_claim_lost` 不由旧 claimant 写等待状态、释放领域租约或排新领域任务；保留原异常交给资源恢复队列，租约自然过期后既有周期恢复扫描可再次发现。数据库确认回滚及 commit-unknown 按共享数据库合同向 Jobs 传播；后者只能检查现存批次/所选 compilation 和原回执后按领域 CAS 恢复，不能将未知提交伪称失败。
- 对准备或 `dispatching` 且本地仍无确认登记回执的模型请求，仅在原场景授权、全部来源、episode/读取集、当前代际、原业务期限及领域归属仍有效时，使用已存完整请求、相同 attempt ID 和相同 completion 幂等补准入；`None` 不是 not-started 证据。已记录 queued/running/unknown/终态回执后，即使查询暂时缺失也只观察。成功原结果调用 Models `reusable_result`，最终提交仍执行 Memory CAS；语义纠正保留旧 compilation 记录并撤销其当前选择，新的有效结果单独选择。
- 冻结 episode 增加 `content_digest`，只读 `POST /api/v1/memory/extraction/attribution` 根据版本、范围、来源、材料摘要、实际存档请求、场景和 attempt 类型区分新增、补上下文、纠正、独立重试以及证据充分的重复成功输入。返回身份、状态和摘要，不返回正文；历史未保存摘要或请求无法证明身份时返回 `input_identity_unproven`，分页截断/不可见依赖会标注 coverage。已冻结续步在配额等待期间复用，而非再次构造。
- 为 Growth 提供 `MemoryService.lock_episodes(context, refs, *, connection, extra_source_refs=())`：把 episode 当前来源、补充来源及额外来源并成一次完整来源闭包锁定，再按 ID 锁定 episode 并复核版本、来源和权限；原 `EpisodePage` 返回合同不变。
- `memory.consolidate`、`memory.index_object`、`memory.index_retry`、`memory.recover` 显式使用 `resource_recovery=True`。远端 rollback=false 且本地回滚已确认或本地事务未开始时，原批移至既有 `quarantined`，原 episode 标为 `consolidation_unknown`，保留批次/step/request/attempt/lineage/负面证据，释放活动位并唤醒独立 pending 输入。通过原 `extraction/resume` 对该批触发原 ID 只读对账；未看到可复用成功结果仍保持隔离。迟到成功先重新锁定并复核来源、权限、代际、原 episode 版本/游标/内容和读取集；保留 `last_unresolved_wait`，并禁止该原批再发起重试、模型纠正或补充尝试。仅 route profile 尚未准入模型的准备草稿，在来源和读集重验后可重观测只读 profile，并保留第一次负面证据。
- 无批次准备中的选择/材料读取属于只读，可按原等待到期重观测并保留首次及最新证据；候选召回效果不明则将实际选中 episode 的版本/游标隔离为无 step 的 preparation hold，标记 `wait_for_reconciliation`，不构造假模型回执，不阻断其他材料。若本地提交未确认，则先检查目标批次、selected compilation 和游标，不能将它当回滚；一般 commit-unknown 仍交既有批次/编译/游标读取和 CAS 路径，不主动清理。对于没有可证明完整闭包或没有可对账原 ID 的 preparation hold，当前需人工依据真实依赖证据诊断，不能盲目重放；这不是已恢复成功。
- 依赖 RBF Runtime 的 `RecoveryWait(reason,retry_at,dependency=...)`、`RecoveryPolicy.inspect_wait_on`、`classify_database_failure`、`recovery_wait_retryable` 和 `JobQueue.register(...,resource_recovery=True)`；依赖 RBF Models 的 `status(...,phase="pre_submit")`、`reusable_result(...,expected_data_generation=...)` 及 Broker 同请求/ID/completion 的并发幂等准入。未修改共享 Runtime、types、Models、迁移主线或装配根；无新增数据库迁移。三份协调者指定 core 夹具只适配 Models 当前接口和 receipt `data_generation`；它们直接使用真实 Sources，后者自身调用 `prepare_event_write`，无需平行 Sources fake。其他旧 fake 如参与扩大的回归仍可能需要独立适配。

## 原两条材料的正常恢复

脱敏历史证据中，两条材料 `input:d53344c0-880f-5aae-8ccc-8d4e975a3315`（当时 revision 1、sequence 3）及 `input:5eca2593-0cd3-5bac-ab21-11425c759fee`（当时 revision 1、sequence 4）属于原批次 `memory_batch_5480d89054eb449f8179cced26560f48`，当时为 `recovery_dependency_probe_in_progress` 误判失败。该快照不包含模型回执或 lineage 详情，不保证当前状态仍相同。

协调者集成并验证后，在原作者/角色/会话授权场景下先对两条 episode 通过 `POST /api/v1/memory/extraction/status` 读取当前 revision、batch、来源和动作。确认同一批仍可恢复后，对其中**一条**使用既有 `POST /api/v1/memory/extraction/resume`，参数包括该 episode 的当前 `expected_revision`、唯一 `client_request_id` 以及正确的 actor/person/conversation 上下文；它会在一个事务中核对并恢复整批两条材料、冻结请求、原最新账本及权限。另一条随后只读状态即可，无须重建 episode 或直接 UPDATE。若返回权限/来源、代际、版本、原账本或材料不匹配等 403/409，应保留原记录和回执，重新诊断，不能猜测回放。恢复排队后分别核对两条状态、同一批次 ID、原 attempt/lineage 和最终实际应用，区分排队、模型结果和业务收敛。

## 编写的回归与集中验证

- `tests/memory/test_rbf_attribution.py`：无正文归因、版本/图文范围变化、权限与实际请求差异、未证明输入及已成功输入的区分。
- `tests/memory/test_rbf_wait_recovery.py`：准备等待和新材料让行、纠正等待冻结、旧失败批原 ledger/CAS 恢复及过期拒绝、丢失 claim、不确定提交、所选 compilation 复用、成长来源闭包、准备/dispatching 与已确认 Models 回执、RPC 中断/丢 ACK 同一请求恢复、远端 rollback=false 原批隔离、独立材料推进、禁止原调用重发及新 attempt、迟到原结果版本复检、本地提交不明不释放、准备阶段按读取/候选召回/profile 区分，以及向量索引对应行为。测试只使用显式配置的隔离 PostgreSQL/pgvector 和替身 Models；未在本窗口运行。
- 集中建议：先整合 Runtime、Models、Sources、Growth 的最终接口，再用项目隔离测试环境执行 `python -m pytest -q tests/memory/test_rbf_attribution.py tests/memory/test_rbf_wait_recovery.py`。本次 Memory 返修最小必跑既有用例：`test_memory_rpc_recovery.py` 的 `test_rpc_wrapper_uses_original_receipt_instead_of_internal_error`、`test_missing_control_never_becomes_permission_to_retry_on_later_wakes`、`test_source_withdrawal_before_recovered_result_apply_prevents_a_commit`；`test_consolidation_pipeline.py` 的 `test_unknown_is_held_without_replay_while_later_material_advances`、`test_late_stale_result_rebuilds_still_unconsumed_original_material`、`test_held_source_revocation_prevents_late_application`；`test_rb_consolidation_write.py` 的 `test_waiting_model_releases_worker_and_completion_observes_same_attempt`、`test_operations_recovery_guards_the_original_batch_and_never_replays_unknown`。按集成变更补相关 index、Models 和队列用例。`TIANSHU_TEST_POSTGRES_ADMIN_DSN` 必须指向允许创建并删除临时库的隔离 PostgreSQL/pgvector，不设置时已有 fixture 会跳过数据库测试；跳过不等于通过。Broker 并发同 attempt ID 单次 provider I/O 须结合 Models 侧真实 Broker 测试验证，Memory 替身只能验证请求及 completion 身份没有变化。
- 已做静态 diff 检查和 Python 源码 AST 解析；这不等于导入、测试或真实业务验收。其他未覆盖及集成时发现的问题由主协调者根据实际失败处理。
