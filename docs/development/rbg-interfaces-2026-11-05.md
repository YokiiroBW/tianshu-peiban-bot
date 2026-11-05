# RBG 接口回收

以下为实现者已确认的接口方向；最终签名以各卡交接和集成代码核实。全局装配只有根修改。

## RBG-01

`Models.seal_admission(attempt_id, *, lineage_id, data_generation, context, original_context, reason, expected_status=None)`：当前管理身份须有 life.manage，核对 Runtime 原 attempt→lineage 的 actor/owner/domain/代际，已有 Models 行再核对原 actor；original_context只包含原场景元数据用于证明归属，无需读取已失效源正文。reason为superseded/expired，不冒充explicit_cancel。封存和create/start按同attempt持久串行，running/unknown回执保留；缺回执只证明封存之后不再准入，过去结果/费用仍未知。正常换代传刚观察状态/absent作为CAS，变化则不封存、不切换。`inspect_admission_retirement` 为对应只读接口。

新增域迁移 `models/rbg_admission_retirement.sql`，根加至 `model_recovery_migrations()`，覆盖产品和独立域测试装配。`life/rbg_plan_retirement.sql` 给现有 plan_generations 增加业务退役证据，根注册全局和Life域迁移。`RBGPlanOperationsRecovery(worker, guard_on)` 提供 candidates/inspect/invoke/observe/registration，接现有 RecoveryAction 和 life.plan 唤醒，精确签名等待最终交接。

## RBG-02

新增域迁移 `conversation/rbg_result_consumption.sql`。原action/result_revision/receipt/data_generation/consumer绑定逐分支进度，保存原事件信封。旧总消费记录兼容；整体完成只在所有适用分支处理或明确终态后写入。当前回执已升级时，旧事件明确superseded并保证新版本存在持久唤醒，不静默换事件身份。

`consume(event)` 保持。`conversation.rbg_result_recovery.ResultRecovery(database,jobs,identity,guard_on)` 的 candidates/registration/inspect/invoke/observe 仅按原job/event和代际核验后重入既有队列，不在Operations构造或执行第二套ResultConsumer。新账本只存身份和进度，不复制源正文；原信封沿用原生任务。内部 `MaterialSnapshotChanged` 保留对外revision_conflict，仅确认回滚的后台边界有限重评。

## RBG-03

沿用 `Actions.resume(action_id, context, client_request_id=..., expected_attempt_id=..., expected_receipt_id=..., expected_revision=..., recovery_guard=...)`。

新可恢复解析失败回执：`execution=failed/effect=none/delivery=not_requested`，result含`phase=parse,outcome=failed,recovery=resume_parse,parser_version=2,model_attempt_id,segment,source_refs`。历史invalid_model_output须经只读分类后同入口恢复。分类端口为 `Actions.inspect_video_parse_recovery(action_id,context,expected_attempt_id=...)`，返回receipt_id字符串与整数result_revision，不含完整receipt对象。

机械恢复复用原成功模型结果、零新增模型调用。一个原Action全部segment共用最多一次新增语义纠正；RecoveryPolicy max_attempts=2包括原成功模型，固定原Action/材料身份，resume或parser版本不刷新预算。不能挪用content.read的物理下载次数。身份无法确认只允许本地解析；过期业务截止不能新增模型调用。根装配 `caps.perception.recovery_policy = policy`，预期无DDL。

RBG-02在Life worker消费resume_parse语义，公共新模型封存不改变现有status/submit调用方。三卡最终交接前不运行测试；根统一受影响验证。
