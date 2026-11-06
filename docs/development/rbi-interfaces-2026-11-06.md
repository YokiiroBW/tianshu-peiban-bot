# RBI 公共接口与归属，版本 1.3，2026-11-06

状态：用户已授权对应窗口修复；RBI-00 的最小公共合同已冻结供本批实现。合同成立不代表代码已实现或验证。协调聊天为 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae。

以本合同、[修订 2 方案](domain-recovery-plan-2026-11-06.md)、[队列](domain-recovery-queue-2026-11-06.md)及对应卡为准。起点代码为 b32a606df8238e398c9dcd58e2e145f2ecff9fb3，正式开发从包含本合同的准备提交开始；精确 SHA 记录在派发清单。

## 1. 权威与机械边界

- Models 的 ModelReceipt/ModelResult 证明物理尝试；Memory 的 OriginalOutcome 为领域已有观测信封，不升级成全局结果状态机。
- Runtime RecoveryPolicy 唯一管理原 lineage、连接/计算预算、冷却、领取和租约；不读取领域完成含义，不提交模型或外部动作。
- Memory、Life、Growth、Conversation 分别拥有当前业务 head、材料版本、due、续接、业务提交和领域补漏。
- Sources/Identity 权限、来源完整读集、固定锁序、CAS 和 data_generation 均沿用原合同。
- 新物理唤醒不改变业务、模型 attempt 或原回执身份；提交未知先查询原回执，unknown 不直接重放外部效果。

## 2. 已知 failed 的同版本修复后恢复

先用真实调用链确认 due=NULL 的缺口。真实版本改变继续使用现有 update_version_on(..., resume_due=True)。同版本修复如确需新增入口，统一使用：

    RecoveryPolicy.resume_repaired_on(
        connection, lineage_id, *, owner, expected_version,
        original_attempt_id, model_receipt, repair_ref
    )

返回普通 mapping，不新增跨域 DTO：

    {"eligible": bool, "reason": str | None, "lineage_id": str,
     "retry_at": datetime | None, "repair_ref": str}

- 领域在当前有效材料/权限事务中调用，repair_ref 是精确故障修复引用，不能每次轮询随机生成。
- Runtime 核对原 owner、代际、版本、最新原 attempt 与同一权威 failed 回执（ID、结束状态、原请求摘要等沿用既有证据校验），不得仅信错误码或领域 status。
- 同一修复引用幂等；只建立一次合法 due/资格，不新建物理 attempt、不重置次数、不改原失败分类，不延后已有更早合法 due。
- 有有效原请求/未替代 unknown、reserved、活跃后继或 claim，及暂停/取消/撤回/换代/预算耗尽时，返回 eligible=false 和明确 reason，保留原状态。
- 身份/CAS/代际变化沿用现有准确 AppError；一般不合格用上述 mapping，消费者不能笼统吞 409。
- eligible=true 仍按 retry_at、原冷却与单探针走既有 claim_lineage_due_on/reserve_attempt_on，领域决定是否安排和提交。
- eligible=true 时 retry_at 必须为带时区 datetime，立即可领取也返回已建立的 now/due；不使用 null 表示立即。一般不合格才可返回 null，并带准确 reason。
- Runtime 的 request_digest 与 Models submission_digest 算法不同，不直接比较。领域在调用前按 models.ledger.submission_digest(冻结原请求) 校验 Models 回执的 intent/attempt/request_digest；Runtime 核对精确 ID、代际、结束 failed、非空 Models 摘要并将该摘要与 repair_ref 幂等绑定，不拿另一算法的摘要误拒原批。
- 若现有公开入口已能完整表达同样合同，Runtime 先回执可复用签名给协调者，协调者统一修订本节；禁止各领域直接改 Runtime 表或自行另造入口。

该入口不接受 unknown 原请求作为 failed。Memory 合格 unknown 的纯计算后继按原路径传递新鲜权威回执、保留原预算；QQ/发布等未知效果仅对账原动作。

## 3. 进程维护回调：Runtime 触发，领域决策

在已有 DomainModule 添加可选字段（Runtime 的 composition/process 归 RBI-01）：

    maintenance: Callable[[RuntimeServices], Awaitable[None]] | None = None
    maintenance_roles: tuple[str, ...] | None = None

maintenance_roles 未指定时沿用该模块已有 start role 归属；没有 maintenance 时行为不变。Runtime 复用现有进程有界维护周期，按 role 调用并逐域隔离异常、停止与资源占用，不新增全局扫描服务。模块仍由自己决定哪些记录应继续。

领域对根装配暴露以下短入口，实际依赖从现有服务实例获取：

    MemoryWorkers.reconcile_due(*, limit=32)
    GrowthService.reconcile_due(*, limit=32)
    LifeWorker.reconcile_due(*, limit=32)
    ResultConsumer.reconcile_due(*, limit=32)

返回 mapping 至少含 scanned/woken；如分页需返回或持久维护稳定游标并注明 complete 的精确范围，不把单页完成写成全域完整。根在各 module.py 中写薄 maintenance wrapper。

- Memory/Growth/Life 在 bot-background；ResultConsumer 在 bot-foreground。
- 入口只查本域有限的到期元数据并复用正常安排函数；不等待网络/模型，不执行外部动作，不持跨域长事务。
- 稳定游标、公平轮转、索引和失败退避由各领域负责；不得每轮全表扫或反复只处理同一组旧 unknown。
- 正常事件、启动与维护使用同一领域安排函数。维护周期不依赖上一次业务处理成功或 Operations 存活。
- 若真实类名/组装依赖使上述位置不合适，领域发最小接口修订回执并继续独立部分，由根统一更新；不私自反向依赖他域或添加全局注册器。

## 4. 唤醒身份与新鲜观测

### 4.1 Models 事务事实与锁序

唯一新增公开端口为 tianshu.models.rbi_outcome_guard.lock_recovery_facts_on(connection, attempt_id)，不提供 assert_outcome_on 转发层。Models 以原 admission→attempt 锁返回当前去敏事实；权威 request_digest 来自 models.submissions，不能用 models.attempts 的另一摘要替代。Runtime.validate_model_recovery_facts 统一校验与原回执/lineage的绑定；各域不复制弱化核验或读 Models 私表。

事务顺序为安装代际共享锁和既有完整 Sources/Identity/领域准备后，先 Models 事实锁，再首次 Runtime lineage 写锁。03/04/05 在任何同事务记账/claim前先取事实锁；新后继 reserve传新鲜receipt，原已提交后继的幂等读取不因旧attempt后来的取消而倒撤。显式取消先线性化则拒绝资格，资格/预留先提交则不倒改已提交事实；domain lineage取消是另一个权威。Models、Runtime和域实际锁序须集中并发用例验证，静态追踪不冒称已验证。

original_output_reusable 的 null 是复用能力证据未知，不是统一重算门禁；true走安全复用，false不证明未执行/零费用。已知failed且真实修复、有效原材料/来源、原预算与取消guard合格时，不能只因null再造永久阻断；历史错误阶段未知且修复本身未证实仍准确停止。

### 4.2 结果头与多上下文来源闭包

Capabilities 的公开 assert_result_head_on(connection, context, *, action_id, expected_receipt, result_revision, data_generation, source_refs=()) 由 RBI-09 唯一实现。先完整来源/身份，再Action头锁，再Life/Memory/Conversation业务行；直接Life结果路径和renderer/dialogue/联系人投影也不能仅事务外检查。

RBI-10 Sources 在既有 prepare_event_writes 增加 scoped_refs=()，各组为原context+refs，保持原aliases返回、主事件及additional_refs强制检查。先锁所有真实context的Identity，再将事件和各来源闭包一次规范锁定，分别按各自权限校验。scoped组可见性false不升级主事件整体失败，Goal按原校验逻辑跳过或执行，不能统一成宽权限context或因持锁而授予权限。可通过原同事务check_many_on重入取布尔，不新造返回DTO。

Life Goal与原receipt的原子合同保持。仅跨其他活动/承诺的派生wake可落本域持久意图、固定触发时候选id/revision并有界补漏，不调用Action，不丢原due，不把未完成派生工作说成整体完成。Memory已核实同context/同refs完整闭包可重入，沿用原observe_episode，不保留无实际需要的before_write hook。

JobQueue.enqueue 的普通永久 key 合同不改变。领域稳定载荷加资源行锁/queueing_lock，缺 todo 时新物理 key，有 todo 时合并最早合法 due；doing 期间可有一个后继。下一时间不混入业务身份，原生 retry 与并发后继必须联合验证。

Memory choose 直接消费同次 OriginalOutcome 的 receipt/result，不从同步前 attempts.status 反推状态。进入同一事务重核 head、原 attempt、冻结摘要、材料读集和代际，同步领域投影与 Runtime 账本。head/版本变化才重读；旧成功不得覆盖后继。

Life 原 Action 观察绑定 activity_id/action_id/data_generation；观察元数据与 frozen_action/执行 deadline 分开。旧入参只作必要适配，之后走同一安排函数。结果消费绑定 receipt_id/action_id/result_revision/data_generation；只补未完成分支，保留真实分支依赖和同事务完成证据。

恢复观察必须区分有效任务/消费者租约、合法未来 due、明确停止和缺失执行。Operations 自身 observing/续租不代表有业务执行；不得靠它无限掩盖已失败的后继任务。

## 5. Models 诊断字段与限流

保留既有 ModelReceipt 和 diagnostic 字段，在 diagnostic 内新增下列可选去敏项，缺失以 unknown/不可得解释：

- failure_stage：transport / protocol_decode / domain_validation / local_program / unknown。
- error_type：异常类名或明确分类；不记录异常消息、URL、凭据或正文。
- original_output_reusable：true / false / null。只有原输出已完整保存且现有复用端口可用才为 true；不得为了补证新增正文留存。
- 继续保留原 transport_evidence、error_code、part/coverage 等事实，不由 diagnostic 推断原费用。

Models 先定位原 invalid_upstream_response 的阶段；Growth 决定语义纠正。Models 若确定当前异常是机械解析且原输出可复用，沿用既有本地重解析路径；没有证据不能假造或再请求模型。

429/502/503 按原已发送/未知事实保留。429 不等于 transport_not_sent 或零费用；复用 Retry-After、依赖冷却和单探针。新 key/通知 revision 不重开相同工作的原预算，真实业务语义改变可形成新工作但仍受依赖冷却约束。

## 6. 共享入口、交接与证据

本轮接口确认补充：ResultRecovery 构造器允许 keyword-only actions=None，ResultConsumer 传 self.actions；根在 runtime/product_operations.py 注入已有 actions。缺端口时为 unknown/action_head_unavailable，不能假验证或补唤醒。Life 原子回调签名保持既有 on_complete 合同。 ExpressionService(..., sources=sources, actions=caps.actions) 由根在 runtime/product.py 注入；已有Action绑定的404或缺端口不能证明Channel-only，不绕过头fence提交成功/终态业务投影。明确无Action的Channel权威和本地生成unknown继续按自身CAS保留，不虚构Action。

### 6.1 跨物理结果任务的有限失败边界

新物理 job 不能重开每 job 的原生次数。既有 RetryStrategy(max_attempts=3) 表示最多三次重试，本批同一 receipt/action/revision/generation 的机械失败额度冻结为最多四次确定失败；固定身份和首次额度绑定不随新 key 改变。Runtime 新增极小执行事实账本，放在 rbi 模块/SQL，不新增扫描器或业务状态机，也不搬迁/合并 Operations 控制账本。

- runtime.jobs.current_job_execution() 只读返回当前 wrapper 的 job_id/native_attempt/data_generation。
- JobQueue.seed_execution_history_on(connection, *, owner, identity, data_generation, max_failures, history, complete) 以有限、已核实机械事实幂等接续历史；history entries 含 job_id/native_attempt/outcome，旧等待还须可证原时间。complete=false 不初始化为零。
- JobQueue.inspect_execution_on(connection, *, owner, identity, data_generation, max_failures) 为入队前只读资格；JobQueue.begin_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt, max_failures) 为执行前再次检查并登记，均在领域行锁/同事务保护下使用。无资格不新建被拒空 job。
- JobQueue.finish_execution_on(connection, *, owner, identity, data_generation, job_id, native_attempt, outcome, error=None) 幂等结算 failed/wait/completed/unknown；JobQueue.resolve_execution_on(..., outcome, observation_ref) 只在领域先核对原提交证明后追加 started/unknown 的对账投影，原 unknown 审计保留。
- 状态 mapping 统一含 eligible/reason（适用时）、history_complete、total_executions、failed_executions、max_failures、retry_at；无完整历史为 history_unknown，无有效due不得猜提前放行。native_attempt 使用 wrapper 进入时序号，首次为 0，历史持久 job.attempts=1 对应 ordinal 0；跨新 key 不重置。
- 真实 failed 消耗额度；类型化 RecoveryWait/已确认回滚的资源等待不消耗真实失败额度，原总执行审计保持单调。开始后失联或提交 unknown 先按原业务回执对账，不能用新 job 跳过占用。
- 历史原/恢复 job 通过公开机械观察接续；不能加总 job.attempts 或 Ops 命令次数冒充真实失败。可关联的 native failed 是 handler 失败的机械事实，不等于领域业务未提交；没有型化中性等待证明时不退款，领域仍按原 head/提交证明/CAS 对账。无法核实的历史保留 unknown；无法证明历史完整时不自动初始化为零。
- 原生任务重试、Operations 控制预算和模型预算各守原归属，旧次数均保留，不混加、不清零。该机械账本不决定消费者完成或重放外部效果。

### 6.2 可选观测结构

既有 observation 可追加 business_observations：每项含 fingerprint、target(domain/kind/id/version/data_generation)、business(state/reason/evidence)、original_receipt(attempt_id/status/cost)、execution(live_job/valid_due/consumer/consumer_lease_until/observer_lease)；version 为字符串。live_job.valid 只能来自当前对象/代际和有效队列或租约核验。business.state 取 blocked/recovering/recovered/waiting_sample/terminal/unknown；没有准确领域证明不判 recovered，只有 observer 续租不判 recovering。

coverage 分 producer/real_samples/queries，并可有 target_sets[fingerprint]={complete,evidence}；只有准确受影响目标集合完整且有证明时才 issue 级 recovered，单对象恢复不自动关闭同类全部。金额与币种未知不能填零。issue 可追加 event_kind=evaluated/recurred/occurred 及 unscoped/uncorrelated 计数，不能从重复 open 推复发。全部为可选兼容字段，旧账本不改写；根在既有巡检写入模板供数，RBI-06 只交解析及装配片段。

### 6.3 单写与实际交付

所有 module.py、runtime/product、types、既有 schema.sql、迁移注册、配置/依赖、活跃巡检探针替换及生产均由本协调者单写。process/composition 的机械回调边界单独归 Runtime；Life 回调业务归 Life，消费账本归 Conversation，根做真实域组合验证。

各实现者交付整块产品改动、必要用例和静态审阅后提交，不运行测试、构建、烟测、生产或真实模型/QQ命令。回执含 SHA、公开接口、必要 DDL/索引、根装配片段、用例范围及未验证项。原失败原样保留，已恢复对象不重做。

现场去敏证据只读入口为 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbh-execution-20261105；新工作树没有该运行目录，不复制真实运行数据入库。缺少精确现场字段时交根定向补证，继续代码可完成部分；未关联事件不当因果。

用户已在当前聊天授权交付对应窗口，且既有 AGENTS 明确允许本批窗口协作派发/回执。接口和完成回执发回协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae，不回到已收尾的旧协调聊天；不向外部人员发送消息。
