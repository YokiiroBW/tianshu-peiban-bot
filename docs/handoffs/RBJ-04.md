# RBJ-04 Conversation 结果消费与定向修复交接

## 交付与边界

- 基线：`ec4b26a652d53d3dc40ab42a9f61734ff12e320d`；合同：`a5e5c5f0db09221e2e2fe8b7262b74054b004b61`，RBJ 合同 1.7。
- 实现及用例提交：`4caddc3d6a56e4e85f4b0a98c1de8f62ddd26029`。本交接另行提交；请按实现、交接的顺序集成。
- 范围限 Conversation 的原始 Action result 唤醒、消费、撤回和 Life 定向本地修复，以及 Growth 所需的原始 generation request 只读访问。未修改共享装配或迁移入口，未执行生产操作。

## 实现要点

- 原事件的 generation、action、receipt、revision 和材料签名贯穿检查；`ActionResultEvents.ensure_on` 复用原唤醒与消费账本，返回已实际记录的各消费者进度。已排队的旧 job 不掩盖四次正常执行失败；同 revision 不同 receipt 按冲突处理。
- `ResultRecovery.authorize_local_repair` 是供可信协调者调用的授权入口：校验原事件、来源/Action head、Life 目标、完整失败历史和失败摘要，向 Runtime 申请绑定，再记录缺陷证据、原材料和 Life owner。任务 payload 不能自行授予权限。`recover_result` 接受原目标及 `repair_ref`、`repair_consumer`，只安排该原事件对应的已授权修复。
- 修复任务沿用原事件，额外携带 `repair_ref` 和 `repair_consumer`；Runtime 队列锁使用修复目标的派生 key，同时保留原 result 的 `wake_object_key`。绑定和入队在同一事务中进行；旧无锁唤醒按 Runtime 合同 adopt/cover。部分 cover 会提交已完成进度后等待原任务续跑。
- `ResultConsumer.consume` 仅允许原生 job 上执行定向修复，且只调用对应的 Life activity 或 commitment 本地结果路径，不重跑其他消费者、模型、外部 Action 或消息发送。Life 目标、Sources、Action head、修复绑定和执行预算再次校验；proof 与目标本地事务共同决定结果。原执行或提交不确定时保留 `unknown`；找到精确匹配 proof 时才协调 Runtime 的执行完成与授权关闭。
- 开始前已证实没有业务应用的停止，先通过 Runtime `record_repair_preflight_stop_on` 与 Conversation 证据同事务持久化，再返回等待或终态。非业务性的 SQL 错误、取消或其他异常不伪装为免费等待。
- Source 撤回使用 Runtime `redact_wake_on` 原地擦除匹配 job 的事件内容，保留任务身份、原始摘要和撤回事实；受影响内容变化时重新核对来源、actor 与片段。真实 Runtime 对象锁等待通过 `RecoveryWait` 延后原通知；已抓取旧 payload 的 worker 必须从 Runtime 确认撤回后才退出。已持久化的消费者 proof 不被抹成成功或失败。
- `GenerationRequests.request(attempt_id, context, *, connection=None, material_key=None)` 支持复用调用方事务并校验冻结的材料/上下文身份，原本无 `connection` 的调用保持可用。

## 协调者装配

1. 集成 Runtime RBJ 唤醒及执行修复实现和 Life 原结果修复端口。注册 Runtime 的 `rbj_wake`、`rbj_execution_repair` 后，再注册 Conversation 的 `src/tianshu/conversation/rbj_result_repair_evidence.sql`（迁移 ID `rbj_result_repair_evidence`，其中有授权证据及 preflight stop 两表）；还需保持现有 `rbg_result_consumption`、`rbi_result_reconcile` 已注册。上述 SQL 不会因本提交自动装配。
2. 共享 product 装配中，创建用于 Operations 的 `ResultRecovery` 时传入实际的 `life_worker`；`ResultConsumer` 自身的恢复入口已传入其 `life_worker`。保持 `conversation.result` 注册到 `ResultConsumer.consume`，允许 job 参数传递修复字段。`ConversationDependencies` 将已有 `jobs` 传给撤回逻辑；无实例时使用 Runtime 提供的同名模块级端口。
3. Runtime 需提供合同 1.7 的 enqueue `wake_object_key`、`adopt_wake_on`、`cover_wake_on`、`redact_wake_on`（包括模块级入口）、observe 的覆盖/撤回证据，以及 repair registration/authorization/binding/begin/resolve/close、`record_repair_preflight_stop_on`；Life 需提供 `inspect_result_repair_on` 和支持 `expected_owner_id` 的两个本地结果路径。集成时以双方最终提交核对参数、返回状态与事务锁顺序。
4. 只有可信协调流程可先注册已验证缺陷，再调用 `authorize_local_repair`。禁止从普通 job 参数、诊断结果或推测自动注册修复；保留实际原请求、回执、数据代次及未知结果。

## 验证与尚未解决

- 已审阅本次 diff；`git diff --cached --check` 通过，14 个变更 Python 文件通过 AST 解析。这些只是静态检查。
- 按任务分工未运行测试、构建、PostgreSQL、真实模型/QQ、部署或线上验证。新增用例覆盖失败预算、授权幂等、单 Life 目标、回滚、丢失完成回执、原生唤醒覆盖、head/owner/source 变化、未知保持、撤回竞态及 caller transaction；Life/Goal/Memory 部分为事务夹具，不能替代真实模块联测。
- Runtime 本窗口提交时尚未交付，API、迁移、并发锁和 PostgreSQL 行为均待协调者集中集成验证；Life 的真实 Source/Goal/Memory/progress 原子性也待联合验证。
- 本卡没有为 renderer、dialogue、detached、Memory 建立通用 proof-only / not-applicable 补账。若只有 Life 目标有 proof，而其他消费者没有足够证据，整条 delivery 仍可能保持 pending/unknown；不得把单个 Life 成功写成整条业务完成。旧版聚合记录或历史 superseded/invalidated 状态也不能单独证明旧消费者是否实际应用，应按原进度和执行事实核对。
- 新 head 出现时，本地调用会保守保留未证实的旧结果；已授权 repair 的后续关闭依赖原事件的协调恢复和完整 Runtime 事实。集成验收时须覆盖 head 竞争后调用恢复并核对 grant 收口，不把有疑点的旧结果改写为成功。

## 集中集成后的旧唤醒夹具返修

- 根在集成提交 `a3c4ea31888300281ca8491b3ad935fb467feb1f` 的真实 PostgreSQL 检查中发现两条旧无锁 result wake 用例的 `job_identity_conflict`。静态对照确认，两条夹具漏传 `subject_id`，而原始 `ActionResultEvents.publish`（`1d087637`、`06ce91b2`）及 RBI 的 result 唤醒（`27499db6`）均设置为事件的 actor ID。Runtime 冻结摘要包含该字段，因此原夹具与 canonical job 的身份不同。
- 两条夹具改为忠实构造历史生产者的 actor；另写用例确认相同 event 但不同 subject 不会被 adopt，原任务身份和待办状态不被改写。与 Runtime 单写窗口对照后确认不修改 Runtime 身份规则，不将不同摘要混并。若之后发现真实历史任务缺失 subject，须按实际证据另行判断，不能据此猜补身份。
- 本返修仅静态审阅和 AST 解析，不运行测试或 PostgreSQL；由根集中复验原失败用例及新增冲突用例。

## 1.8 成功修复后的证明观察返修

- 根在 `247ed3f7602d58da1c19ff316aa213faa996b6b5` 的真实 Sources/Life/Goal/Memory 回归中确认：目标本地事务与六消费者聚合均已提交，但成功 `finish_execution_on` 使用的机械关闭引用与再次观察的领域证明引用不同，触发 `execution_repair_close_conflict`。根的取消后全域回滚参数已通过；成功后的重复观察此前未通过。
- 按合同 `1d56370f` 第 16 节，Conversation 将原 generation、action、receipt、revision、consumer、repair_ref、冻结 owner、disposition、稳定 reason 和完整持久 proof 统一生成 `result-proof` 引用。预算历史对账 resolve 和领域 close 共用该规则；两条路径均先核对原目标与 Life proof，不因 grant closed 或 native succeeded 猜测业务完成。非应用终态保留原 disposition 和理由。
- Runtime 按 1.8 在同一已完成物理事实中绑定首次领域观察引用，保留最初的机械关闭引用；重复相同证明幂等，不同证明仍冲突。此提交不更改 Runtime、SQL 或公共签名；双方提交必须一起集成。若既有环境已持久记录本批旧格式的证明引用，不改写原证据或猜测两种引用等价，须由根按事实处理。
- 新增定向用例覆盖正常 finish 后同一原证明两次观察、不重跑 Life/Action、保留机械关闭和普通预算，以及故意改变 fixture proof 后继续拒绝冲突；补充已提交但 finish 回执丢失时由预算历史先 resolve、随后领域 close 的同引用对账。仅静态检查，相关 PostgreSQL 验证由根执行；真实域测试位于根提交中，不在本卡重复复制。

## 1.9 原失败与独立消费租约返修

- 根和 05 的集成用例确认：盘点时导入的 native unknown 随后出现真实失败，但同一 receipt 可由直接调用取得独立的新 consumer 租约。旧逻辑未保存 claim 到 native ordinal 的归属，却以旧失败批量解除所有 running token。按 `dbd390ff` 第 17 节，不能据此认定新租约属于旧失败。
- Conversation 在原 delivery 事务内按 DB 时间锁定已有 consumer 状态；有效租约或缺失/无效租约时继续有界导入 native 历史，但暂停 stranded unknown 的 resolve 和 claim 清理，保留失败证据。`ensure_on` 在采用已有 pending、预算判定或创建 successor 前按最早有效租约返回 `waiting/consumer_running`；缺失或无效租约保持 unknown。原 consumer retry due 和 native job 未改，已有 reconcile due 不被覆盖。
- 授权及已派发 repair 分支沿同一租约证据等待或保持未知，不凭租约绕过资格或消耗 repair 额度。普通已派发 native 沿现有预算和 consumer CAS 工作，没有新建预检退出机制。真正到期后按既有 proof/CAS 对账：原子消费者可恢复，renderer/dialogue 无完整证明不能直接换 token 重放；直接调用和原生入口均将不明效果保留为 unknown。
- 新增定向用例覆盖有/无 pending、`allow_successor`、有效租约下保留 unknown/失败证据、到期后原子/非原子处置、缺失租约、直接与 native 调用、repair 授权/额度等待。05 的独立真实 native 时序用例由根按其提交合入；不涉及新 SQL、公共 Runtime API 或生产数据。仅静态检查，PostgreSQL 测试及集成结果由根负责。
