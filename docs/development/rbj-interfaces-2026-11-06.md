# RBJ 公共合同，2026-11-06，版本 1.0

用户已要求分发修复。本合同随准备提交冻结；语义调整先由根更新版本并通知受影响窗口，不各自改名猜接口。基线 39da2d28（产品代码 8034743c）；仍保留六角色，领域业务状态机不转移到 Runtime。

## 1. 机械事实与共用限制

- 原 ID/请求/回执/unknown、来源权限/版本/CAS、data_generation、总 attempt 和连接/计算预算保留。403/429 与备用路由不改。
- 复用现有 JobQueue、execution facts、ActionResultEvents、结果消费者与领域事务；不建立平行恢复调度器，不清库、不直接修生产表、不重放未知外部效果。
- 锁序保持 Sources/身份→Action head→领域/Goal/Memory→消费证明。Runtime 机械领取短事务完成后释放锁，再进入领域事务；不带新 Runtime 行锁跨昂贵 Sources 证明。
- 持久 due 比较使用数据库时间。资源等待、提供方 Retry-After、hold_until、原生 due 不混为同一预算。57014 不统一改免费等待。

## 2. Sources：保持公共入口，替换有界求值内核（RBJ-01）

保留 check_groups/check_many_on/validate_persistence/prepare_event_writes 的现有参数与返回兼容语义。节点/边/工作量预算在一次公共调用的取图、子批与求值中共享，不能每拆一批就续预算。预算耗尽用 Sources 自有明确 AppError；来源 false 只表示权威业务证明不成立。

普通 DAG 共享求值，不因超过 64 层回退指数递归。带范围、disclosure、撤回、OR 与有环场景仍保持原语义，并受同一预算约束。新 SQL 只能进新命名 rbj_*.sql，root 单写迁移注册。

同 context/locator 精确一致的准备可合并。事务证明若跨调用复用，必须绑定该事务、完整权威读集及相关数据变更，保护持续到提交；viewer scope 锁本身不证明依赖 scope epoch 已保护。没有安全条件时保留新鲜校验，不推断授权。原图不删除/压缩；新生产者收敛须证明语义等价且由所属域实施。

## 3. Actions：原终态结果的补唤醒（RBJ-03 与 RBJ-04）

由 Capabilities 单写 actions.py：

```python
async def ensure_result_consumption(self, context, action_id, *, data_generation,
                                    expected_result_revision=None,
                                    expected_receipt_id=None) -> dict: ...
```

expected 两项同时缺省或同时提供。调用者快照可落后；端口重读同 Action、同代际的新权威 head，真正更高版本可以交接；同版本不同 receipt/内容、回退版本拒绝。context/actor/scope/source 以及 generation 照原合同验证。pending/running 只能 waiting；terminal succeeded/failed/cancelled/unknown 才交接已有原事件。404/不可见不构造替代 Action。

由 Conversation 单写 results.py：

```python
async def ActionResultEvents.ensure_on(self, connection, event) -> dict: ...
async def ActionResultEvents.publish(self, connection, event) -> None: ...
```

publish 兼容原调用并委托 ensure_on。Actions 在持有既有来源/Action head fence 的同一事务中调用 ensure_on，使用现有事件构造与 receipt/context，不由 Life 伪造信封。发生新 head 竞争时回滚旧事务再有界重读，不裸吞 409；不能因补唤醒执行 invoke/resume/model。

统一返回 state=accepted|waiting|blocked|verified，reason 必有；可含 job_id、due_at、budget。Actions 另补 action_id、receipt_id、result_revision（尚无 terminal receipt 时为 None）。waiting 表示准确依赖/进行中观察，blocked 保留执行预算耗尽、历史未知等原因；不能将 pending job 或“消费者运行过”冒称业务已应用。

## 4. Runtime：持久唤醒身份与历史归属（RBJ-02）

复用 enqueue(connection, JobSpec(...), queueing_lock=K)。明确登记 life.activity_reconcile、conversation.result 为持久态唤醒；静态路由生产者与执行者获得同一登记。resource_recovery=True 不自动登记其他命令。

原逻辑键、冻结 task/载荷摘要与 generation 持久写入现有 job_keys 的增量元数据。原生 queueing_lock 仅为原生 pending 约束，retry 清空它也不能丢逻辑身份。复用原生 lock 和 fair_claim_job 的对象串行领取；不使用 actor 全局锁串行全部业务。同一对象最多一个有效 doing+一个有效 pending，历史物理记录允许保留，covered 唤醒在昂贵工作前廉价退出，不能伪记业务成功。

历史缺元数据任务只能由所属领域核对原信封后调用：

```python
async def JobQueue.adopt_wake_on(self, connection, job_id, *, expected_task,
                                expected_generation, expected_payload,
                                wake_key) -> dict: ...
```

该口原子验证 job/task/载荷/代际、原 job_keys 后记录机械归属，不改任务 body/attempt；错误拒绝。不由 Runtime 猜 activity_id 或替领域判终态。doing 不取消/强杀。合并保留最早合法 due 及等待下界；retry/后继/退出的交错不得丢 wake。最小诊断复用既有事件合法 outcome 或 metadata；新事件 schema 需求交 root，不越界改 runtime/events.py。

## 5. Runtime：可信修复登记与一次目标资格（RBJ-02）

结果普通 identity 仍由现有 execution_identity 产生，max_failures 不改；physical 主键 (generation,job_id,native_attempt) 不变。repair purpose 和 normal purpose 共用事实体系、可区分统计，普通 failed 不改 wait、不减少计数。历史导入尊重已有 purpose，禁止把 repair 再算 normal。

公共端口（JobQueue 转入所属机械实现，不访问领域表）：

```python
async def register_execution_repair_on(self, connection, *, owner, repair_ref,
                                      repair_revision, defect_key, consumer) -> dict: ...
async def authorize_execution_repair_on(self, connection, *, owner, identity,
                                       data_generation, max_failures, repair_ref,
                                       repair_revision, defect_key,
                                       failure_digest, consumer) -> dict: ...
# 原参数不变，新增仅关键字可选参数：
async def inspect_execution_on(..., repair_ref=None, consumer=None) -> dict: ...
async def begin_execution_on(..., repair_ref=None, consumer=None) -> dict: ...
```

register 只由已授权协调/运维发布恢复流程在验证生效版本后调用，属于可信代码装配，不暴露为任意 job payload 可调用的授权入口。root 后续提供精确已验证版本/缺陷登记；本批准备阶段不登记线上修复。owner/defect_key/consumer 的修复登记绑定固定 repair_ref/revision，重复同项幂等，换随机 ref/SHA 不增发同缺陷额度。

authorize 需登记匹配、完整 failed 历史摘要 CAS（inspect 返回 failure_digest）、无 started/unknown/不完整历史；将资格绑定原 identity、原代际、目标 consumer、原失败摘要和登记修复。只有该缺陷相关原结果由 Conversation 证明确认后才能调用。每个准确目标 consumer 同缺陷最多一次真实修复失败；同项重入同一资格。不提供清零普通预算能力。

inspect 返回现有 normal 字段，同时明确普通计数与 repair 状态/remaining/资格引用；原调用者不传 repair 参数保持原语义。begin 验证当前原生执行身份与可信资格；finish/resolve 从已持久化 execution purpose 决定记账，不由 payload 改写 purpose。实际修复失败消耗资格；已确认回滚的资源等待保留同资格，started/commit unknown 停止 apply 并对账，不能自动再开一次。可信资格不能由纯 repair_ref 字符串自授。

## 6. Conversation：精准本地消费恢复（RBJ-04）

```python
async def ResultConsumer.consume(self, event, *, repair_ref=None, repair_consumer=None): ...
async def ResultRecovery.recover_result(self, job_id, *, # 所有旧参数保留
                                       repair_ref=None, repair_consumer=None, ...): ...
```

原生 conversation.result 的新关键字参数与上述一致；默认全兼容。repair Consumer 只允许 life_activity 或满足既有原子合同的 life_commitment。repair 路径只运行已授权本地目标回调与必要机械对账，不能走 renderer/dialogue/detached、模型、QQ 或外部 Action。normal identity 不加入 consumer 来续普通预算。

恢复端口先查 proof+原业务引用，已原子提交的只对账；真实未提交且资格有效才 apply 原事件。资格发放/任务安排在同事务，begin 后领域 apply 在既有独立事务；异常或应答丢失必须读原证明而不是靠 activity.completed 推测 Goal/Memory 已提交。来源、head、CAS、代际变化按既有合法 superseded/invalidated 等语义退出。

candidates/inspect/invoke/observe/verify 返回一致的 normal eligibility 与 repair eligibility：普通耗尽保持 blocked；尚无可信修复资格不能显示为可执行。有合法资源 due 时明确 waiting；外部 unknown 和提交 unknown 保持未知。旧事件与 repair payload 的任务由 Runtime 记录不同 purpose，但不建立新的业务身份。

## 7. Life：控制/观察和原结果应用（RBJ-05）

- Life 观察已有 terminal 原回执只能调用 Actions 补唤醒，不能以 DirectResultFence 绕过共同消费者。
- pending/running 控制状态、404 unknown、原 Action 合法恢复、已消费失败后期限到达的新领域决定继续存在；不把这些决定当作重新消费旧回执。
- 读到旧 pending/running、fence 时 head 真正更高且为 terminal：旧事务回滚，重新核对同 Action/代际后有界交接。禁止重新 invoke 同一外部操作；同版本冲突、头回退、权限/来源变化不被这个分支吞掉。
- ResultConsumer 调用 _activity_result/_commitment_result，Life/Goal/Memory/consumer proof 在同事务。Domain 保持 CAS 和事务决定，Runtime 不接管业务。

## 8. 根的装配与验收范围

root 单写全部 module.py、runtime/product*.py、全局 types、已发布 schema/旧迁移、迁移注册、配置/依赖、共享记录、生产。必要新增 SQL 交付者提供名字与顺序，root 统一注册；如需超白名单内容先报明确接口需求，在自己职责内继续工作，不擅改别人文件。

实现者只完成整块代码、必要用例代码、静态 diff 审阅和提交，不运行测试/构建/烟测/模型/QQ/生产。根收齐后做必要 PG 联合验证，复用未变有效结果；不全量重跑 472 用例。发布和原对象恢复另由根串行执行，本次派发不代表已修复/验证/部署。
