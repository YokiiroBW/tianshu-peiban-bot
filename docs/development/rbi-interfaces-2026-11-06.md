# RBI 公共接口与归属，版本 1，2026-11-06

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

所有 module.py、runtime/product、types、既有 schema.sql、迁移注册、配置/依赖、活跃巡检探针替换及生产均由本协调者单写。process/composition 的机械回调边界单独归 Runtime；Life 回调业务归 Life，消费账本归 Conversation，根做真实域组合验证。

各实现者交付整块产品改动、必要用例和静态审阅后提交，不运行测试、构建、烟测、生产或真实模型/QQ命令。回执含 SHA、公开接口、必要 DDL/索引、根装配片段、用例范围及未验证项。原失败原样保留，已恢复对象不重做。

现场去敏证据只读入口为 C:/YOKI/Codex/tianshu-peiban-bot/.runtime/rbh-execution-20261105；新工作树没有该运行目录，不复制真实运行数据入库。缺少精确现场字段时交根定向补证，继续代码可完成部分；未关联事件不当因果。

用户已在当前聊天授权交付对应窗口，且既有 AGENTS 明确允许本批窗口协作派发/回执。接口和完成回执发回协调聊天 01a1ac6f-8494-7f82-8c4d-ec7d285f44ae，不回到已收尾的旧协调聊天；不向外部人员发送消息。
