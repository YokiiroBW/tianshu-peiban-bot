# RBJ 公共合同，2026-11-06，版本 1.9

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

1.4 将证明之前的 Sources 锁闭包明确纳入同一修复：RBJ-01 独占扩展 archives.py 的 lock_selected 及相关 selected/writable/alias_keys 遍历。supports 与 alias 的取图、可写目标和别名集合都须有界，不允许先无界递归或全量 alias 自连接，再在结果上 LIMIT。锁前收集、取得锁后的新鲜闭包复核与后续求值共用一次公共调用的预算；复核产生的实际工作也计入，不靠重试/拆批重置。直接调用 lock_selected 且没有外层预算时，也必须有自身有界预算。预算传递属于 Sources 私有实现，不更改公共签名、不按连接长期保存预算/权限缓存。

闭包完成之前预算耗尽必须抛出明确来源预算错误，不能截断依赖集合后继续授权或提交。保持 alias→root/来源 ID 的既有有序锁、共享/独占区分、新增根保护与写集合复核；并发导致集合变化继续回滚并按原 source_lock_set_changed 等待，不追加无序锁。新增必要用例覆盖高扇出 alias、深支持图、预算耗尽无业务提交、锁等待期间新增 alias/support 后复核、归档/撤回/更正的原权限语义。此为静态确认的无预算路径，尚不宣称已实测它导致现场慢 SQL。

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

1.1 明确响应新增 `consumer_progress` 映射，键是实际持久化 consumer 名，值包含 data_generation/action_id/receipt_id/result_revision/disposition/reason/proof；从本次对应原事件的持久化进度读取，缺行保持缺失/unknown，不造 pending 或 done。ActionResultEvents.ensure_on 所有正常返回分支都附此字段，Actions 原样透传。该字段仅给出既有 proof 与对象引用，不返回新业务正文。`verified` 可能是 completed/superseded/invalidated/旧 aggregate，不证明 life_activity 已应用。

Life 期限续接必须读取 life_activity 的明确 `disposition=done`，核对完整原事件身份、proof.activity_id、proof.last_result_revision、proof.last_receipt_id，再在自己事务核对当前 Life/Action/CAS；superseded/invalidated/not_applicable 或缺 proof 不授予旧失败续接资格。Actions 如果返回新权威 head，调用者必须识别新的 receipt/revision，不能把其 progress 当作旧回执证明。

expected 参数只有版本与 receipt_id，不包含调用者先前正文摘要；因此只保证这两个标识的比较，以及本次读取/锁内完整 receipt 和已持久化原事件的冲突检查。不声称能比较调用者未提供的历史正文摘要，本批不增加该参数。

## 4. Runtime：持久唤醒身份与历史归属（RBJ-02）

复用 enqueue(connection, JobSpec(...), queueing_lock=K, wake_object_key=None)，新增可选关键字 wake_object_key。明确登记 life.activity_reconcile、conversation.result 为持久态唤醒；静态路由生产者与执行者获得同一登记。resource_recovery=True 不自动登记其他命令。

原逻辑键、冻结 task/载荷摘要与 generation 持久写入现有 job_keys 的增量元数据。原生 queueing_lock 仅为原生 pending 约束，retry 清空它也不能丢逻辑身份。wake_object_key 未传时等于原逻辑键；传入时逻辑变体仍严格冻结完整 payload，跨变体共用同一对象原生 lock/fair_claim_job 串行领取，不使用 actor 全局锁。

普通 event 与 repair_ref/repair_consumer 变体用不同 queueing_lock，显式传同一原对象 wake_object_key；不能把 repair payload 塞进普通载荷身份。normal execution_identity 不变。同对象最多一个有效 doing+一个有效 pending：普通预算耗尽的物理 todo 不再具执行资格，可保留历史并在昂贵工作前 blocked/covered；repair 仅在普通预算确已耗尽时授权，同一结果身份的多个 repair target consumer 串行安排，不能同时有两个有效待修复目标。covered 唤醒不能伪记业务成功。

历史缺元数据任务只能由所属领域核对原信封后调用：

```python
async def JobQueue.adopt_wake_on(self, connection, job_id, *, expected_task,
                                expected_generation, expected_payload,
                                wake_key) -> dict: ...
```

该口原子验证 job/task/载荷/代际、原 job_keys 后记录机械归属，不改任务 body/attempt；错误拒绝。不由 Runtime 猜 activity_id 或替领域判终态。doing 不取消/强杀。合并保留最早合法 due 及等待下界；retry/后继/退出的交错不得丢 wake。最小诊断复用既有事件合法 outcome 或 metadata；新事件 schema 需求交 root，不越界改 runtime/events.py。

1.1 明确：同 wake_key 的不同冻结 payload 摘要不能直接 adopt/合并。Life 历史可能有 `{activity_id,context,sequence}` 与 `{activity_id,action_id,data_generation}` 两种格式，必须由 Life 先在当前领域事实/CAS 下证明旧信封被当前 canonical 唤醒覆盖，再通过单独窄口记录覆盖，不能靠 Runtime 猜等价：

```python
async def JobQueue.cover_wake_on(self, connection, job_id, *, expected_task,
                                expected_generation, expected_payload,
                                covered_by_job_id, expected_covering_payload,
                                wake_key, coverage_ref, not_before=None) -> dict: ...
```

该口只对登记的纯持久态唤醒生效，核对旧 job 与目标 job 的完整原 task/代际/载荷及 canonical wake 元数据，目标必须未取消、未覆盖且存在有效 todo/doing。coverage_ref 是可信领域调用方依据当前对象/CAS 形成的覆盖依据；Runtime 只保存与核验机械一致性，不把未可信 job 参数当作领域证明。不改原 body/attempt，不建立覆盖环或链式无界查找。

旧 todo 可记 covered_by，后续进入 handler 前廉价结束；不是取消/伪造业务成功。其他执行者的 doing 返回 waiting，不强行撤销；若是本原生执行者自身、且当前 job/attempt 验证匹配，允许它在昂贵工作前确认被覆盖并正常返回。已有真实执行不能由维护者标成没执行。

覆盖保留最早合法 due：目标 due 至迟为 min(目标原 due, max(旧 due, not_before))，同时不早于领域明确的 not_before。领域负责其等待下界真实性；下界与当前目标不兼容时返回冲突，由领域重新观察/安排，不能延迟别人的 doing 或直接改写旧任务。历史覆盖先由所属域验证，不要求 Runtime 或运维从 payload 猜业务。

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
async def bind_execution_repair_job_on(self, connection, *, owner, identity,
                                      data_generation, max_failures, repair_ref,
                                      consumer, job_id) -> dict: ...
async def close_execution_repair_on(self, connection, *, owner, identity,
                                   data_generation, max_failures, repair_ref,
                                   consumer, observation_ref) -> dict: ...
# 原参数不变，新增仅关键字可选参数：
async def inspect_execution_on(..., repair_ref=None, consumer=None) -> dict: ...
async def begin_execution_on(..., repair_ref=None, consumer=None) -> dict: ...
```

register 只由已授权协调/运维发布恢复流程在验证生效版本后调用，属于可信代码装配，不暴露为任意 job payload 可调用的授权入口。root 后续提供精确已验证版本/缺陷登记；本批准备阶段不登记线上修复。owner/defect_key/consumer 的修复登记绑定固定 repair_ref/revision，重复同项幂等，换随机 ref/SHA 不增发同缺陷额度。

authorize 需登记匹配、普通真实失败预算已耗尽、完整 failed 历史摘要 CAS（inspect 返回 failure_digest）、无 started/unknown/不完整历史；将资格绑定原 identity、原代际、目标 consumer、原失败摘要和登记修复。只有该缺陷相关原结果由 Conversation 证明确认后才能调用。每个准确目标 consumer 同缺陷最多一次真实修复失败；同项重入同一资格。同一结果身份至多一个活动/待执行 repair target，其他 consumer 修复串行。不提供清零普通预算能力。

Conversation 在重新核验可信资格的 repair enqueue 同一事务调用 bind，持久绑定真实 job→可信 grant；资格可由 authorize_local_repair 提前登记，安排时必须再次核验，不能让未绑定的 repair job 提交可见。如果同一请求同时授予资格与安排，则 authorize/enqueue/bind 同事务。Runtime 验证任务/代际与已授权 target、原 identity 和载荷 purpose；原生 handler 尚未进入、generation lookup/启动即失败的 attempt，历史导入也按此绑定继承 repair，不能误算 normal。bind 不从 payload 自授资格，begin/finish/history 依持久绑定取 purpose；缺绑定的伪 repair job 不具执行资格。

inspect 返回现有 normal 字段，同时明确普通计数与 repair 状态/remaining/资格引用；原调用者不传 repair 参数保持原语义。begin 验证当前原生执行身份与可信资格；finish/resolve 从已持久化 execution purpose 决定记账，不由 payload 改写 purpose。实际修复失败消耗资格；已确认回滚的资源等待保留同资格，started/commit unknown 停止 apply 并对账，不能自动再开一次。可信资格不能由纯 repair_ref 字符串自授。

1.3 增加 close 窄口：可信领域在 grant 授权后、begin 前读到精确原提交 proof，可用 observation_ref 请求关闭同目标 grant，不制造执行、不耗一次失败。Runtime 只在无 started/unknown 等未决事实且可信登记/资格一致时关闭；已有未决必须先依真实 proof 通过原 resolve_execution_on 对账，不能用任意字符串抹掉 unknown。同一关闭证明重入幂等，closed grant 不再安排。真实 repair 成功时 finish 也依持久 purpose 关闭 grant；资源 wait 保留同资格。

1.5 澄清 close 也可基于同一原目标明确持久化的合法非应用终态（superseded/invalidated 等），无需另造退役端口。可信领域先持正确来源/Action/领域锁确认终态，Conversation 在同事务写入精确原事件/owner/consumer 的对应终态与证据后调用 close；Runtime 仍只核验机械资格与无未决事实，业务 disposition 原样保留，不能把非应用终态算成 done/已应用。有 started/unknown 时不直接关闭、不以取消/替换推断旧执行未提交，仍先对账原物理事实。

## 6. Conversation：精准本地消费恢复（RBJ-04）

```python
async def ResultConsumer.consume(self, event, *, repair_ref=None, repair_consumer=None): ...
async def ResultRecovery.recover_result(self, job_id, *, # 所有旧参数保留
                                       repair_ref=None, repair_consumer=None, ...): ...
async def ResultRecovery.authorize_local_repair(self, job_id, *, data_generation,
                                              action_id, receipt_id, result_revision,
                                              repair_ref, repair_revision, defect_key,
                                              repair_consumer, defect_evidence_ref,
                                              material_digest=None) -> dict: ...
```

原生 conversation.result 的新关键字参数与上述一致；默认全兼容。repair Consumer 只允许 life_activity 或满足既有原子合同的 life_commitment。repair 路径只运行已授权本地目标回调与必要机械对账，不能走 renderer/dialogue/detached、模型、QQ 或外部 Action。normal identity 不加入 consumer 来续普通预算。

ResultRecovery 构造器新增可选 `life_worker=None` 注入，旧调用兼容；没有原领域检查能力时 repair 明确 blocked，不自行跨查领域私表。根 module/product 装配由根修改。

可信协调恢复流程先验证部署生效版本并 register，再显式调用 authorize_local_repair。repair_revision/defect_key/defect_evidence_ref 来自根已核实的修复及准确目标失败证据，不能从 worker/job payload 推断；缺证据不授予资格。该口校验原事件摘要、当前 head/来源/代际、完整失败历史和领域预检，取得 failure_digest 后调用 Runtime authorize。已存在准确 done proof 只对账，明确 not_applied 才可发资格；领域 unknown 在授权/begin 前停止，不消耗一次资格。

1.3 确认 Conversation 新命名 rbj_result_repair_evidence.sql，可持久保存原身份、修复/缺陷证据引用、material/failure 摘要及预检确认的 owner_id/owner_revision，root 统一注册。它是证据与目标绑定，不是新的调度/资格权威。实际修复从该可信记录取冻结 owner_id；不能从任意 job payload 或重新解析的新 owner 无声替换原授权目标。

恢复端口先查 proof+原业务引用，已原子提交的只对账；真实未提交且资格有效才 apply 原事件。资格发放/任务安排在同事务，begin 后领域 apply 在既有独立事务；异常或应答丢失必须读原证明而不是靠 activity.completed 推测 Goal/Memory 已提交。来源、head、CAS、代际变化按既有合法 superseded/invalidated 等语义退出。

candidates/inspect/invoke/observe/verify 返回一致的 normal eligibility 与 repair eligibility：普通耗尽保持 blocked；尚无可信修复资格不能显示为可执行。有合法资源 due 时明确 waiting；外部 unknown 和提交 unknown 保持未知。旧事件与 repair payload 的任务由 Runtime 记录不同 purpose，但不建立新的业务身份。

## 7. Life：控制/观察和原结果应用（RBJ-05）

- Life 观察已有 terminal 原回执只能调用 Actions 补唤醒，不能以 DirectResultFence 绕过共同消费者。
- pending/running 控制状态、404 unknown、原 Action 合法恢复、已消费失败后期限到达的新领域决定继续存在；不把这些决定当作重新消费旧回执。
- 期限续接依赖第 3 节的精确 consumer_progress/proof 与当前领域 CAS，不用整体 verified 或 last_receipt 单独代替消费证明。旧不同格式唤醒覆盖走第 4 节 cover_wake_on，不能把不同 payload 塞给同一 adopt 键。
- 读到旧 pending/running、fence 时 head 真正更高且为 terminal：旧事务回滚，重新核对同 Action/代际后有界交接。禁止重新 invoke 同一外部操作；同版本冲突、头回退、权限/来源变化不被这个分支吞掉。
- ResultConsumer 调用 _activity_result/_commitment_result，Life/Goal/Memory/consumer proof 在同事务。Domain 保持 CAS 和事务决定，Runtime 不接管业务。

Life 提供原业务预检窄口（RBJ-05 单写，Conversation 调用，不跨查领域私表）：

```python
async def LifeWorker.inspect_result_repair_on(self, connection, context, result,
                                            result_revision, *, consumer, owner_id,
                                            data_generation) -> dict: ...
```

调用者先按 Sources→Action head 完成来源/权限与 head fence；Life 在同事务锁定自己目标、核对 actor/Action/代际/revision/CAS。返回 state=not_applied|verified|superseded|unknown，reason 必有，可含 owner_revision/proof。Conversation 先核对自己已持久化的精确 done proof；verified 只能基于完整同事务证明，不能凭 last_receipt、单个 Episode 或某条 Goal milestone 凑成完整提交。

1.2 仅澄清首次初始化前 owner_resolved=false 的合法场景：owner_id 可为 None，由 Life 自己按 consumer 对应领域、已授权 actor+原 Action 精确解析当前唯一所有者，并返回 owner_id/owner_revision；显式传 owner_id 时严格核对。缺失、多匹配、身份冲突或不能证明归属时保持 unknown，不猜旧/新对象、不创建对象，Conversation 不因此跨查 Life 私表。其余 1.1 签名与语义不变。

目标仍对应原 Action、版本明确低于原事件且允许应用时可返回 not_applied；明确取消/替换按 superseded；同版本异 receipt、回退、缺失/不一致，或同 receipt 已落账但无完整事务证明返回 unknown。旧直接路径缺 proof 的历史不补造 marker，不强推修复。实际 apply 在原领域事务重复同样检查；若预检后出现“同 receipt 已落账无 proof”的竞态，抛 AppError `local_result_application_unconfirmed`（409，带 consumer/owner 引用），Conversation 将本次执行记 unknown、保留同资格待对账，不作为真实修复失败、不再 apply；不把它包装为可自动重放的免费 wait。

1.3 给 _activity_result/_commitment_result（含必要内部转发）增加可选关键字 expected_owner_id=None，旧调用兼容。repair 必须传证据绑定中授予资格时的原 owner_id；锁内若明确已换绑按 superseded 且不 apply，无法确认归属则 unknown。预检、begin 和实际 callback 之间不允许重新选一个新 row 来替换原目标；同一 row 的业务变化仍按原领域 CAS/来源/head 检查作决定。

## 8. 根的装配与验收范围

root 单写全部 module.py、runtime/product*.py、全局 types、已发布 schema/旧迁移、迁移注册、配置/依赖、共享记录、生产。必要新增 SQL 交付者提供名字与顺序，root 统一注册；如需超白名单内容先报明确接口需求，在自己职责内继续工作，不擅改别人文件。

## 9. 1.1 修订范围

根根据 RBJ-01/02/03/04/05 读卡反馈，澄清 consumer_progress 精确证明、调用方摘要边界、不同历史 payload 的可信覆盖口、跨 purpose 对象锁、repair job 预绑定以及 Life 原业务预检；不改变六角色、原事件身份、普通预算或文件归属。Sources 的 prepare 多 context 独立判定并共享整次预算；允许修改现有 test_rb_proof_batches.py 的对应总预算断言。新增必要用例由各所有者编写，根集中执行。实现者按根通知中的不可变合同提交读取本修订，不 rebase/覆盖他人可变工作树。

## 10. 1.5 Growth 原批与冻结请求的同事务读取

复用并扩展已有 GenerationRequests.request，Conversation 在 generation.py 单写：

```python
async def GenerationRequests.request(self, attempt_id, context, *,
                                     connection=None, material_key=None): ...
```

不传 connection 时保持原有独立只读事务；传入时只使用该事务连接，不另开连接/事务，不插入/prepare/claim/confirm/submit，不重新取得 Sources 锁。保留已有 context_key 与 invalidated 检查；返回原冻结请求或缺行时 None。material_key 有值时额外核对同一既存行的材料键，不符报 generation_identity_conflict；同 attempt 的材料/上下文冲突不能返回 None 假装不存在。不改变既有 expired 请求可被观察的语义。

Growth 在 prepare 的 guard 已完成外层来源及 generation 锁、锁定自己的 current organize_state 后，先重载权威 active_batch，再使用原 attempt/context/material_key 和同一 connection 读取既有冻结请求，核对原身份/代际。命中原批但冻结请求缺失/不一致时必须停止当前 prepare、保持原批并明确缺失/未知，不继续插入 B 的候选请求，不重复 reserve、不补造谱系；有效时沿用原批 lineage/recovery_version 与原请求，后续使用权威材料继续记账。

Growth 不直接读取或更新 conversation.generation_requests，不复制其 context/state/解码规则；Conversation 不写 Growth 私表。RBJ-04 白名单增加 generation.py 及原 tests/conversation/test_rbj_*.py 范围内必要端口用例，RBJ-06 使用冻结接口写交错用例。两个原生 organize 的真实交错复现由根集中执行；静态已定位不等于实测通过。

## 11. 1.6 已证实未开始的修复 ordinal 退出

Runtime（RBJ-02）在现有机械退出事实体系提供：

```python
async def JobQueue.record_repair_preflight_stop_on(self, connection, *, owner,
        identity, data_generation, repair_ref, consumer, job_id, native_attempt,
        reason, observation_ref) -> dict: ...
```

reason 仅 closed/blocked/unknown，返回 state=preflight_stopped。Conversation 先在同事务保存准确原 target/预检结论/观察引用，再调用此口，在 handler 正常返回或依据已确认等待延期前留下本 ordinal 未 apply 的事实。Runtime 核验当前原生执行者及 generation/job/attempt、真实 job→grant、原身份/consumer；record 与 begin 使用同一机械互斥，已存在 begin/fact 不能写 stop，已有 stop 也禁止本 ordinal 随后 begin。相同证明重入幂等，冲突拒绝。

复用本批 wake-exit 事实，加 kind/reason/grant/ref，不新建业务恢复账本。closed 核对真实已关闭资格；blocked/unknown 不关闭、不消费资格，也不解析或消除其他 ordinal 的未决事实。历史导入只跳过此精确已证明未开始 ordinal 的业务执行，保留物理 job/attempt/事件及原 purpose，不因 native succeeded 伪造 completed，不因合法 no-op 新增虚假 unknown。

该口不是预检异常的免费通道。来源 SQL/计算失败、取消、提交不确定等异常不因被改写为 blocked/unknown 就可免责；没有已完成可信预检的明确证据，继续原失败/unknown 分类。缺 stop/covered 退出证明的 native succeeded 仍保留未决。已有 started/unknown 只能通过原物理事实对账，不用本口抹掉。

## 12. 1.6 覆盖压平与旧结果未决

若 B 已有直接 covered 子行 A，领域证明同对象 B 被有效 C 覆盖后，Runtime 可在对象锁下以有界原子或可恢复小批次将运行态指针压平到 C，追加保留 A→B 及重定向依据，不形成无界链，不改变物理 job/attempt/旧退出证据。保留所有最早合法 due/等待下界；不无界扫全历史或长持锁。旧 exit proof 依当时有效覆盖事实核验，不因后来指针改变就否定已证实的非执行退出。无法证明的其他 doing/未知仍等待。

新 Action head 不证明旧 consumer 未应用。pending/blocked/running 任一状态，只要 Runtime 有未决/不完整历史，或 Life 同回执历史落账缺完整事务 proof，都不能 blanket superseded/invalidated；保留或转 blocked/unconfirmed，旧 aggregate 不伪造全完成。只有精确目标可证明的合法终态才能收口；新 head 独立准确交接。目标 Life done 也不表示缺失的 renderer/dialogue/detached/memory 分支完成，repair 不顺带运行这些分支。

来源撤回后的正文删除、禁止后续使用与旧是否已应用是不同事实：去敏不能被冻结 payload 摘要挡住，已有 done 不被抹成未应用，started/unknown 不因撤回关闭。RBJ-04 独占 rbg_result_withdrawal.py 及必要 dependencies.py 调用适配；Runtime 负责单向去敏的机械 CAS/失效事实，原逻辑身份/原摘要保留，生效摘要/失效状态单独表达，不存原正文副本、不允许恢复内容。沿既有 Sources durable 通知，不另建业务队列；精确机械 API 见第 14 节，不能只改一端发布。

保留未知不是将所有新非终态都变为未知：完整可信历史及精确目标证据能够证明未开始/未应用的正常场景，照常按合法 superseded/invalidated 收口；只有未决或证据不足才 blocked/unconfirmed。pending 本身不能证明已应用或未应用，两类均需实际用例覆盖。

## 13. 1.6 索引覆盖的准确口径

Memory 继续复用已有 backfill。多事务游标尾页只证明本轮遍历完成，不证明较早键新增/修订或失败投影已全覆盖；没有完整覆盖证据时 producer.complete=False，detail 记录 partial/scanned_unverified、generation 和实际扫描边界。backfill 返回 complete 保持遍历完成含义，不把重置空游标当覆盖水位，不另建统计系统。RBJ-07 可限定修改 test_recovery_statistics_backfill_uses_original_attempt_identity 的完整性预期，原 attempt/revision/去重断言保留，必要用例由根集中执行。

## 14. 1.6 单向来源去敏与冻结载荷

Runtime 与 Conversation 已对齐并由根在本版本冻结：

```python
async def JobQueue.redact_wake_on(self, connection, job_id, *, expected_task,
        expected_generation, expected_payload, redacted_payload, invalidation_ref,
        wake_object_key=None) -> dict: ...
```

同一 conn-only 公开实现为 tianshu.runtime.rbj_wake.redact_wake_on(connection, job_id, *, 上述相同参数)，JobQueue 方法委托它。ConversationDependencies 的既有 jobs=None 调用使用该公开函数；不另建 Queue/连接，不保留直接 UPDATE 的平行路径。

返回 state=redacted|already_redacted|changed|missing|waiting，job_id 必有。redacted/already_redacted 可含 metadata_unverified=True 表示正文已去敏但历史元数据不能验证，另保留 invalidation_ref；不能把 metadata_unverified 当作未擦除而恢复原文。重复已确认去敏幂等；只有原生快照确实变化才返回 changed，不用永久历史元数据冲突造成无穷 changed。missing 表示 native 已不存在，不是业务消费完成证明。

仅用于本批登记的 conversation.result 来源撤回去敏，不是任意 payload 更新口。Conversation 验证精确来源/版本/fragment/actor，构造既有 redacted_event，保留原事件/context、receipt/attempt 与执行效果等身份元数据；Runtime 用纯机械规则只允许删除/清空与固定 source-invalidated 墓碑，不反向 import Conversation、不接受任意替换或恢复。

锁序为代际读保护→已登记对象锁→native job 行锁。expected_generation 核对该历史 job 的原代际，可为 None；不因当前 live generation 不同而禁止旧代际隐私清除，也不补造当前代际。Domain 从未锁定的候选快照调用 CAS，不能先 FOR UPDATE j 再调口；实际 native 载荷变化返回 changed，Domain 有界重读并重新判断 Source 相交。

原 job_keys.payload_digest 和原身份保持，新增 effective digest/失效与旧新摘要事实，不存原正文副本。已确证 native 原载荷需要去敏时，永久的历史 metadata 缺失/冲突不能阻断擦除：保留 metadata_unverified 等准确事实，不修造旧身份，不无穷返回 changed。已擦后的稳定 job/失效事实要能被后续对账定位，不能依赖已删除 source_refs 再匹配。未来 enqueue/adopt/retry 禁止恢复旧敏感载荷。

去敏不取消其他 doing，不抹已存在的 begin/unknown，也不自行关闭未决资格。handler 前遇 source-invalidated 的精确非执行退出沿同一机械退出事实留证，普通/repair 都不能因 native succeeded 伪称已消费；没有完整退出证明继续保留未知。只有领域已证明并持久化的合法终态才按既有 close 条件关闭未开始资格，不能先将未知写 invalidated 再拿该行循环自证。

RBJ-04 同时单写 dependencies.py 中向撤回 helper 传递既有 self.jobs 的最小适配，保留其他依赖失效逻辑。沿已有提交后 Sources durable 通知/正常对账，不增加第二个业务队列；根集中联合验证擦除与入队/领取/重试交错、历史缺元数据、旧事实保留、重复通知及来源片段边界。

## 15. 1.7 多对象锁争用与持久撤回延期

多 job 或历史冲突对象可能使撤回事务持有前一个对象/摘要锁再等待后一个；Runtime 可对新增对象/摘要使用非阻塞 try-advisory-lock，真实争用返回 waiting、reason=wake_lock_busy 及数据库时间生成的有时区 retry_at，不伪装成 payload changed。固定历史 metadata 冲突仍按上节擦除/metadata_unverified，不能变成永久 waiting。

Conversation 收到 waiting 后在原 sources.notify 派生通知事务外层抛出 RecoveryWait（source_redaction_lock_busy，dependency=runtime:wake_redaction，使用返回 retry_at），沿已有 resource_recovery 原任务延期，不成功吞掉待办。回滚的是尚未提交的派生通知，Source 权威撤回早已提交；不回滚权威状态、不手动提交调用者事务，不为局部进度加第二业务队列。其他已提交失效保持，未提交局部擦除按原事务重试。仅真实锁未取得可如此等待，普通 SQL/计算错误或提交未知不借此免费重试。

1.7 只补充去敏 waiting/已去敏/缺任务的返回与既有通知延期语义；第11节 preflight-stop、第12节保留未知及第13节索引口径保持，方法参数不变。handler 旧入参遇已去敏 native 时依精确原 action/receipt/revision 和 Runtime 持久失效事实判定本次退出；不恢复正文，不把旧业务推断为未发生，已 begin/unknown 原事实不抹。

## 16. 1.8 成功修复后的重复证明观察

真实集中验收确认：repair 成功的 `finish_execution_on` 使用 `execution:{job_id}:{native_attempt}:completed` 机械引用关闭资格；随后领域用完整原消费证明调用 close，引用不同会触发旧严格字符串冲突。机械完成与领域证明是两个可追溯事实，不能用跳过检查、改写首次关闭引用或只见 closed 就认定业务完成解决。

Runtime 保持 close 的公开签名。只有资格的首次关闭引用精确对应同 generation/identity/grant 的真实 `completed` 执行事实，且历史完整、无 active/unknown 时，才允许把第一个可信领域证明引用绑定到该事实已有的 `observation_ref`。首次 `closed_at/close_observation_ref`、原执行/失败事实均不改；同领域引用重入幂等，不同引用仍冲突。失败、unknown、等待和不对应原完成事实的关闭不走此分支；原领域直接关闭、resolve 关闭继续遵循已有严格证明规则。不新增业务判定、迁移或平行账本。

Conversation 对原 generation/action/receipt/revision、冻结 owner、consumer、disposition 和持久 proof 生成统一稳定引用；历史预算对账 resolve 与 close 使用同一规则。不得从 native succeeded、grant closed 或 Life 行状态推断完整消费证明。合法非应用终态保留自身 disposition 与理由，不改成 done。重复观察只对账原证明，不再运行原 Action/模型/外部发送，原普通四次失败与一次修复失败限制不变。

## 17. 1.9 原失败对账不得解除当前有效消费租约

集中复验及静态追踪确认：候选盘点会导入 native doing 的 unknown；随后该任务失败，普通直接调用仍可取得独立 consumer 租约。仅有原 native failed 事实不能证明这个后来取得的 token 属于该物理 ordinal。原按 receipt 清理所有 running token 的方式可能干扰有效消费者并新增不必要任务。

Conversation 复用原 result_consumer_progress 和队列，不新增 claim 系统或 SQL。所有 reconcile_stranded 入口均先以数据库时间检查并保护同一原 receipt 的当前消费租约；存在有效未过期 running lease 时，继续有界导入原 native 历史并保留失败证据，但不得 resolve 未决 unknown、解除/替换 claim 或另行派发新消费任务。返回 waiting/consumer_running 和最早真实 lease_until，携带未改写的执行预算；此规则优先于同原事件的普通/repair 预算拒绝或既有 pending 采用，包括 allow_successor。物理 worker 同样不得因历史对账解除租约或 resolve 未知；已派发普通 worker 沿既有预算登记及 consumer CAS 等待/观察，不另造普通预检退出机制。repair 不得借该租约绕过资格或消耗修复额度。缺失或无效 lease 的 running 状态保持 unknown，不能猜测为已过期。

租约期间保留原 failed/unknown/native ordinal、token、proof、due 和普通/repair 总预算；例如一条已归一 failed 加另一条先前 unknown、当前 native 已 failed 的执行仍应报告 failed_executions=1、unknown_held=1，不能提前改成两条已确认失败。消费者完成后依精确原 proof 对账；租约真正过期后再走已有来源/Action/领域 proof 与 CAS 路径。renderer/dialogue 没有完整效果证明仍 blocked/unknown，不能据另一 ordinal 的失败认定其成功或未发生。不得通过修改用例为 accepted、伪造 doing、重置预算或重放外部效果绕过。

实现者只完成整块代码、必要用例代码、静态 diff 审阅和提交，不运行测试/构建/烟测/模型/QQ/生产。根收齐后做必要 PG 联合验证，复用未变有效结果；不全量重跑 472 用例。发布和原对象恢复另由根串行执行，本次派发不代表已修复/验证/部署。


## 18. 2.0 发布实证：原生领取与业务 begin 分离

e317734f 的首个真实 repair145703:0 被 Operations 在业务 begin 前导入 native-start unknown；自身 cheap admission 随后拟写 preflight stop，却因已有 fact 抛 execution_repair_already_started。native retry 后 ordinal1 准确未应用退出，不能证明 ordinal0。Sources、Action和Life检查不是这次失败点，不能改模型路由或重置预算。

仅对已持久绑定可信 grant 的 conversation.result repair，区分“原生doing但业务begin尚未提交”的短暂活跃尾与已提交业务事实。foreign observer不得把前者永久写成unknown，也不得将其遗漏为可运行：分页/非分页读取显式报告native_inflight；所有inspect/authorize/bind对该原身份仍准确阻断。当前真实worker只能经JobQueue当前执行上下文校验豁免精确自己的generation/job/native_attempt，随后仍在同一身份互斥和原生状态核对下提交begin，之后才能apply。已存在started/真实unknown、普通/未绑定任务、缺失锚点、abort/提交不明继续原规则，不能泛化免费化。

native_inflight不是丢失历史，不设置Conversation rbi_history_gap或丢弃游标。历史的封闭前缀可完整，活跃尾由单独机械字段表达；snapshot必须在同一身份锁下核对该尾，使不带合法current_job的观察者始终不能借完整前缀获得eligible。超过有界可证明范围保持不可执行。Conversation在execution_budget_on向inspect传递真实current_job（JobQueue验证与当前上下文一致），保留真实分页、缺锚点与未知语义。确需真实续页时保存游标并沿同原任务有界等待，不能把未完扫描当完整或无due吞掉；是否需额外适配由必要用例确认。

对已经持久的145703:0，不覆盖原unknown/evidence_ref/recorded_at，不把未开始强称failed、completed或资源wait。复用公开resolve_execution_on增加严格resolved_outcome=not_started；仅可信bound repair可用。Runtime验证精确generation/identity/grant/物理ordinal、原fact为native-start观察导入unknown且未resolve（而非begin插入的started或finish产生的真实unknown）、锚点和完整native终结历史、无当前doing及冲突事实。受信调用者先锁Sources→Action→Life，核验原事件、owner/revision/CAS、未应用和无有效consumer租约；再基于当时不可变e317代码的begin/seed同锁程序约束及同ordinal持久preflight错误日志提供唯一observation_ref。不得仅凭后ordinal stop、native succeeded/deferred或Life当前行推断。新命名增量迁移扩展现有resolved_outcome检查，保留原事实并追加resolved/observation，不另建业务账本。重入幂等，观察引用或身份冲突拒绝，其他普通/外部unknown不接受此类型。

not_started仍计入原物理总执行历史，但不计业务失败/active/unknown，不关闭grant；普通失败仍4、repair失败仍0，原一次资格继续沿正常ResultRecovery恢复同一receipt。原生scheduled 557222.at=2026-11-06T14:18:20.664052Z是物理重排due证据，不能改成now或业务hold；它不单独证明回滚，也不需要把not_started伪装wait。历史再导入不得覆盖已验证not_started或制造新冲突。

RBJ-09（原Runtime窗口）实现机械合同、公开窄口/增量SQL和必要用例；RBJ-10（原Conversation窗口）只改结果预算current_job/typed-live/真实续页的必要适配及联合用例。全局迁移注册、共享记录、集成验证和生产仍归根。新分支/独立工作树，不续旧完成卡、不建聊天/子代理；gpt-6.1-terra/max普通速度。实现者静态审阅提交，不运行测试/构建/烟测/生产/模型/QQ。根只补这次竞态、真实原结果及受影响调用验证，复用279个有效旧结果；不得为本次缺口全量重跑。
