# 记忆扩展并行实施

2026-10-26，用户已授权“并行推进”。此前五项仅规划状态在本批转为本地实施；已发布核心保留，当前生产不再修改。任务、模型测试预算和共享合同由协调者单写。原模型测试账本17540/18000，初始分配0真实调用；真实模型和隔离PG仅协调者串行安排。不新推送/部署、不操作真实QQ或外站账号。

## 起点与写权

| 新卡 | 唯一写者 | 原物理worktree | 开工基线 | 完整交付 |
| --- | --- | --- | --- | --- |
| TS-313 | memory_pressure | TS-306/tianshu-memory | 62206f2 | 独立actor经历与来源闭环→查询/认识投影；群话题有界授权投影 |
| TS-314 | companion_prerequisites | TS-307/tianshu-companion | 68b85d0 | 后台公平/恢复、Chat真实工具、实际经历outbox与自身记忆最小闭环 |
| TS-315 | qa_takeover | TS-061/chat-audit-core | e6d8e21 | 原服务授权历史搜索/分页/邻读、同范围reply/@关系和热度 |
| TS-316 | 协调者 | TS-311/tianshu-platform | 506a820 | 权威身份/来源接点、历史与群图/点云/热度同页、自身记忆接线 |

四卡均在原检出新建`codex/memory-extensions-20261026`，保存原分支、提交、ignored测试缓存和旧context。新任务不续写旧完成任务。TS306/307/308/311按实际已完成发布收口；TS061原已完成，仅释放物理检出。TS236及其他任务不动。原allocator的依赖、容量、唯一任务验证和allocation锁仍使用；因用户要求复用既有worktree，采用一次性接管登记，不另复制四份检出。根原index保持原字节。

## TS-313：Memory

先闭AL02现候选六个episode操作及来源/actor核验、真实PG独立域。复用StorageRuntime，独立actor-memory逻辑源、小DDL/guard/锁与存储目录；用户Store、records/jobs/source_revision保持原业务归属，actor失败不拖垮用户端口。app/cli/contracts仅薄装配。新actor_memory领域就近组织，不为每个函数建文件。

来源内容、性质、覆盖、受众与执行状态只能来自真实producer；actor_check由Platform当前注册核验，source_facts/check由实际owner提供。缺authority不得伪成功，未知回执按原身份恢复。首个完整块可用既有AL01六操作，AL04的query/derive等缺口先给根最小schema提案，再按同候选冻结版本实现。

随后从已提交segment/topic/receipt提供固定角色+真实群范围的有界话题投影，复用现来源闭包、reader/origin、删除及当前授权校验。不得暴露私有ledger/event/整manifest、把共话题当共同同意、按昵称合人；不新增统计账本、整理队列或模型步骤。

范围为任务板所列actor_memory、新测试/文档、既有app/cli/contracts/browser/segments必要薄接线；更具体的公共路径变化先报根，保持一个产品写者。验收包括角色/用户/角色间隔离、同键异文、未知/重复交付、撤回/恢复不复活、迁移失败局部隔离、真实PG guard及来源联合。组件与真实生产者联合分别记。

## TS-314：Companion

首块AL05沿Life/Activities/RuntimeExecution现模块修活动与主动表达误占前台槽、多角色公平、暂停到期重评和unknown恢复。复用现ModelSlots及持久phase/operation marker，外部等待不持PG事务，不增总槽/会话active/恢复时限，不建立第二队列。Gateway实际类别由受保护service绑定决定，不能用请求自报workload冒称background；具体既有身份/配置接线与根核对后实施，未配置时如实保留兼容状态，不静默改凭据。

Chat块使用原Registry注册真实只读handler和窄客户端，当前context确定角色、账号和群私范围，模型只提供关键词/时间/作者等查询意图；不得传任意服务URL/robot/actor替换。搜索/翻页/取文均用服务端现授权和当前来源/删除约束，默认一页、按需查证，不每轮调用、不自动整库扫描。真实绑定和跨产品联合后才去掉not_integrated。

AL03先选一个实际阅读事件：原事实事务保存source/outbox，Memory幂等写回，ACK丢失按原receipt恢复；既有fictional阅读事件与仅步骤completed不能直接变成亲历或全内容完成。AL04接口就绪后接认识/兴趣、自我检索、后续对话/日记/次日计划最小闭环。外站账号/点赞等继续原AL后续卡，非本轮五项的隐含实现范围。

允许原src/tianshu_companion和相应测试/文档，调用链变更集中、一次功能变化就近。完成每个完整块后集中相关验证；共享调度按原产品要求做完整组件检查，不逐小改重跑。无新增真实模型/QQ/外站动作。

## TS-315：Chat

沿现evidence_api/evidence_service及message_scope扩服务授权下的search/page/read；继续独立service token+固定account/来源mapping，群私与room严格一致，保留realtime关联和import EXISTS两条真实可见来源。禁止转发管理API、hydrate全应用或读取对方DB。无新聊天库/迁移。

搜索使用稳定keyset和绑定查询/范围的cursor；查询页和精确邻读保原作者/时间/hash/locator/覆盖，当前性与Memory suppression通过真实来源owner接线，不能把旧候选not_checked当完成。新互动聚合就近服务模块，沿同ArchiveScope计算reply/@与活跃作者/时间桶；同一消息到同一目标不双计，@all单列，歧义目标列unresolved，不用预览的“最新winner”或昵称猜归属。晚导入/删除在选定窗口有界重算，不宣称sent_at水位覆盖所有变更。

先向根提交精确请求/响应结构提案，由根放入共享合同后消费同一冻结版本；现已明确scope/授权与聚合内核可以先实现，禁止自行发明另一套公共schema。最窄evidence/query/search及聚合风险测试，稳定后Linux/真实PG/Comp消费者联合；Windows fcntl限制如实记录，不套旧结果。

## TS-316：Platform与共享入口

先按既有角色注册、观察/Core/source权限提供窄actor_check与context核验，复用已完成AL07候选90c0d44，经差异审查合入新分支，必要冲突在现入口装配；不重写能力执行器。真正source_facts/check仍由来源owner提供，平台不代替Companion证明阅读完成。

延续现MemoryPage群详情和原Cookie/CSRF/Origin，接Chat互动/热度与Memory话题投影；同真实账号/群/角色组合，不把私聊或其他账号同号群带入。先互动图、时间桶和覆盖，再同投影二维可缩放点云，SVG/Canvas复用现前端，无新图服务或embedding。点击节点/边可回真实证据，布局不写回事实；未接入/未整理/不可用分开显示。前端只消费脱敏投影，服务凭据不进浏览器。

根contracts、任务板、CURRENT和跨产品配置样例单写。合同沿既有actor候选及必要新Chat/群投影候选，完整schema/实例/消费绑定一致后冻结，不改用户memory-context/v2闭集。实现稳定后审完整diff，做相关组件、原生PG、实际本地服务联合及浏览器验证。没有发布授权时不接真实Chat库或生产配置。

## 收口

四卡实现→各自完整块自验→协调者独立源码/实际正文与关联核验→跨产品联合和必要负载→本地交接。每卡交docs/handoffs/新编号.md及精确提交/范围/失败/跳过/未覆盖。五项不是AL18全队列完成；已发布核心、当前本地候选、实际接通与未实现内容必须区分。


## 2026-10-27 本地收口

TS313–317已完成并标local_verified_not_integrated；五产品精确提交、集中验证、原失败和范围限制见[交付](memory-extensions-delivery-2026-10-26.md)及同名JSON。补充TS317仅闭原后台身份调用链；BOT真实素材公开日记暂缓，保内部/原受众召回；默认Knowledge回查按原Memory配置独立客户端，原容量/时限不改。0真实模型新增，17540/18000；未推送/部署。TS316共享入口已释放给另经授权的独立卡串行装配，AL11只交接口、不代分配或委托新合同namespace。
