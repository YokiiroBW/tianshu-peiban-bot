# 天枢空库重构任务说明（第六版完整来源与功能盘点）

状态：第六版，26卡全部planned，未派发、未实现、未执行产品测试。17次原始请求、44项来源任务、22类现有/新增能力及10组方案补全已逐项核对。原22组问题、23项历史行为、20项两窗口结论与6项CP继续保留；生产仍引用此前22:13快照。

[主方案](tianshu-clean-rebuild-plan-2026-11-04.md) · [队列JSON](tianshu-clean-rebuild-queue-2026-11-04.json) · [系统审计与空库/升级复盘](tianshu-final-system-audit-2026-11-04.md) · [历史行为与问题](tianshu-clean-rebuild-regressions-2026-11-04.md)

视频、音乐、图书为独立模块，保留其数据、任务、文件与共享引用。本次BOT业务域可清空；新版以后正常升级保留新数据。26卡编号和依赖保持，RB-21最后装配。已有路径是复用入口，不代表全目录默认必跑。

职责：R：Runtime/共享类型/数据库资源/进程/迁移注册；B：Models 请求、尝试、配额与异步提交；S：Identity/Sources 场景、原件、证据与授权；M：Memory 整理、事实、历史与检索；C：Companion 人格、习惯与学习；D：Conversation/Channels 接收、取材和表达；L：Life 日程、执行与日记；X：Capabilities/插件/内容及独立外围任务；F：现有前端页面与API客户端；I：应用总装配、交付包、共享文档与集成验证；O：Operations事件聚合、指定运维模型、登记恢复动作、验证与管理员通知；不拥有业务结果真相。

## RB-01 功能保留、领域归属与最小合同

- 状态：planned；职责：I；优先级：P1。
- 领域协作：R、S、B、M、C、D、L、X、F、O；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：无。
- 可准备依赖：无；不等于提前完成。
- 目标：固定本轮结构和旧错行为，给各领域可并行使用的接口。
- 数据变化：合同与空库 schema 所有权清单；各域自行完成具体 schema。
- 历史行为：公共合同。
- 当前问题：NF-07。
- 本轮需求：FR-06。
- 衔接补充：CP-01、CP-02。
- 原规划：SD-01、ML-01、LE-05。
- 完整盘点补全：RI-02、RI-06、RI-07、RI-10（审计第16节）。
- 两窗口逐项结论：CW-06、CW-07（见审计第15节）。

复用/修改入口：

- [src/tianshu/runtime/product.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/product.py)
- [src/tianshu/conversation/ports.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/ports.py)
- [src/tianshu/types](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/types)
- [tests/contracts](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/contracts)

交付：

- 按当前 API/页面/配置列出功能保留清单；需求目录只补充产品意图，不冒称全部实现
- 定义 scene/source_ref、业务命令、冻结请求/attempt、结果通知、行为规则引用、取材清单最小合同
- 分清计划、开发、集成、部署、真实使用；23条保留行为、22组当前问题及两窗口逐项结论均有归属和具体关闭条件
- 共享装配、配置与迁移注册单写，领域提供接口及本域 schema
- 在实现前固定BOT reset/retain/seed数据归属表及共享引用闭包；区分本次基线重建、普通后续升级与发布回退。
- 固定原事件/消息修订与附件补充修订、规范提案/编译版本/别名映射、日程逐项应用结果三个最小合同；按所属领域实现，不另建万能状态表。
- 本次feature_inventory固定现有导航/领域API的22类能力及reset/retain/seed处置；覆盖创作章节、小屋服装、共读授权、群画像/关系、项目知识/经验、资源资产、设备/基础设施/游戏、天气、插件技能及原生模型代理，不再只写保留现有功能。
- 公共合同逐项列出生产者、实际消费者、事实权威与卡级负责人；学习消费包括聊天、原生memory工具、Life、Growth、日记和历史读取；六类角色的入口/连接/任务/start-stop归属随合同固定。

删除或替换：

- 不创建万能任务状态/统一事实大表；不新增任务审批、用户开关或流程门禁

最小有效验收（本轮未运行）：

- 聊天、角色、群、多媒体、记忆、关系冻结、成长、日程、日记、图像、阅读、订阅、插件、管理和诊断入口均有保留归属
- 所有旧 SD/ML/LE 需求有迁移映射；历史问题不能只列标题，没有对应行为
- 原17次用户请求、LE/ML/SD44项来源任务、22类功能均有保留/改造/明确替代处置；API/UI/配置/数据归属与负责人一致，不将存在菜单当作实机功能已验。

## RB-02 稳定场景、来源与分型证据

- 状态：planned；职责：S；优先级：P1。
- 完成依赖：RB-01。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：把身份、授权变化和证据支持分开，避免版本散落和来源证明重复。
- 功能清单：FI-01、FI-03、FI-04。
- 数据变化：稳定场景、原件版本、必要的证据集合；权限本身不由关系或学习授予。
- 历史行为：PF-04、PF-10、PF-12、PF-20、PF-21、PF-23。
- 当前问题：SF-04。
- 本轮需求：承接原优化。
- 衔接补充：CP-01。
- 原规划：SD-02、ML-01。
- 完整盘点补全：RI-01、RI-03（审计第16节）。
- 两窗口逐项结论：CW-15（见审计第15节）。

复用/修改入口：

- [src/tianshu/identity](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/identity)
- [src/tianshu/sources](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/sources)
- [tests/core/test_source_memory_stories.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_source_memory_stories.py)

交付：

- 使用稳定 scene_id，权限集合及授权 epoch 独立版本化；内部引用用明确类型
- 单一来源直接引用；多来源才用规范化 evidence set，区分 required、alternative support、generation dependency
- 提供同事务来源证明、批量证明与当前源状态查询；授权刷新仍受原 scope/permission 上界约束
- Sources 与 Identity 不反向依赖 Memory/Companion/Life
- 对保留模块的主体/scope/source及JSON嵌入引用保留最小完整集合；身份主键和已签发引用不能因BOT清空而变成悬空。
- 来源反向定位覆盖新版Episode语义历史、Companion学习历史、词义/样例与搜索投影；同步失去读取资格，异步清理不影响撤回生效。新进程/新任务沿原授权上界与当前权限交集执行。

删除或替换：

- 删除 scope 哈希充当业务对象身份和多处复制来源展开列表的实现

最小有效验收（本轮未运行）：

- 同事务新建回执可验证；别连接尚不可见不能把真实成功变成目标 0
- 来源撤回后立即失去使用资格；AND/OR 及多主体归属不退化为一个扁平集合
- 全局管理员订阅 actor_id=null 仍能合法刷新授权；一个源失权只影响该源

## RB-03 事务通知、短任务与到期唤醒

- 状态：planned；职责：R；优先级：P1。
- 完成依赖：RB-01。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：用现有持久队列解开跨域回调，等待时不占工作者。
- 数据变化：使用现有队列和本域必要的消费版本/唯一键；不另建事件总线数据库。
- 历史行为：PF-03、PF-12、PF-17、PF-21。
- 当前问题：SF-06、NF-03。
- 本轮需求：FR-01。
- 衔接补充：CP-02、CP-03、CP-05。
- 原规划：SD-03、ML-05。
- 完整盘点补全：RI-05（审计第16节）。

复用/修改入口：

- [src/tianshu/runtime/jobs.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/jobs.py)
- [src/tianshu/runtime/schema.sql](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/schema.sql)
- [src/tianshu/runtime/product.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/product.py)
- [tests/core/test_incremental_consumers.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_incremental_consumers.py)
- [tests/runtime](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/runtime)

交付：

- 原事务写领域结果并使用 Procrastinate 同连接入队；消费者独立事务写结果+消费回执
- 只对‘需要重新计算’的通知按 domain/key 合并，以 dirty revision 和稳定工作键处理消费期间的新变化
- 命令、实际输入、外部结果和计费 attempt 不合并；失败分型与 due 持久化
- 短事务领取任务/完成 CAS；网络调用和模型等待期间释放数据库连接，重启恢复通知
- 逻辑处理水位与覆盖完成水位分开；隔离材料形成明确待修集合，修好后新尝试回补，不把游标前进冒充全部成功。
- 任务归属与payload版本明确；旧BOT命令受数据代际隔离，媒体原任务身份保持；恢复链计数不因新job ID重置。
- 持久任务/结果记录是事实，LISTEN/NOTIFY仅唤醒。注册前结果已到、通知丢失、重启和通知乱序均可按稳定逻辑键补查；有界恢复扫描只查due/未收口工作，不恢复全表高频轮询。

删除或替换：

- 删除每个消费域的影子队列与生产者内业务回调；移除无变化通知重新启动高频空轮

最小有效验收（本轮未运行）：

- 提交前崩溃无幽灵任务，提交后崩溃任务可恢复；消费重放无重复产物
- A 消费中收到 B 变化时 B 不丢；没有新材料时不增加语义版本/不停唤醒
- 不用 max(sequence) 充当已消费证明，避免事务提交乱序漏消费
- 领域提交与通知入队、Models接受与调用方保存引用、结果落账与消费确认三个断点分别可恢复；重复同身份不同内容拒绝，同身份同内容返回原结果。

## RB-04 Models 单份请求、异步结果与云 API 调度

- 状态：planned；职责：B；优先级：P1。
- 完成依赖：RB-02、RB-03。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：一次冻结请求，物理尝试独立；云 API 本地占用与未知结果分离。
- 功能清单：FI-12、FI-19。
- 数据变化：request/attempt/observation 保持不同事实；请求正文唯一归属，局部引用不重建请求身份。
- 历史行为：PF-01、PF-02、PF-03、PF-05、PF-13、PF-14、PF-18、PF-23。
- 当前问题：UF-01、UF-04、NF-04。
- 本轮需求：FR-02。
- 衔接补充：CP-04。
- 原规划：SD-04、SD-20、ML-04、ML-15、ML-16、LE-05、LE-06。
- 完整盘点补全：RI-05（审计第16节）。
- 两窗口逐项结论：CW-02、CW-18（见审计第15节）。

复用/修改入口：

- [src/tianshu/models](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models)
- [src/tianshu/memory/steps.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/steps.py)
- [src/tianshu/conversation/generation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/generation.py)
- [tests/models](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/models)

- [src/tianshu/models/broker.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/broker.py)
- [src/tianshu/models/transport.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/transport.py)

交付：

- 统一 submit/observe/cancel 端口和可靠结果通知，冻结内容只存 Models 一份，调用域只保存 request_ref
- 普通云 API 取消固定 4/2/2 业务许可及 unknown 300 秒占位；保留真实连接/内存资源边界，明确配置的上游额度仍尊重
- local execution ended 与 upstream outcome 分开；真实 GPU 排队任务保留原 handle 与资源语义
- 已知完整但业务非法、传输失败、外部状态未知分型；保留可取得 usage、完成判据和有界诊断
- 同请求形状重复失败有有限自动恢复；429/Retry-After 延后受影响路由的任务，等待不 sleep 占 worker；不自动换用户模型
- 为每个用途配置主路由与兼容备用路由、实际quota_domain；尊重Retry-After、不同key同额度域不盲轮换；记录路由选择及降级原因。
- ops.diagnose 使用用户指定路由及独立容量/凭据范围；模型服务本身不可用时由确定性守护直接通知，不让Agent反复诊断自身。
- 备用route适配只影响具体attempt；保留逻辑输入和原结果，记录实际协议/route/wire摘要。嵌入备选必须满足同向量空间条件，否则交Memory建立独立profile。
- 在既有HTTP入口按attempt记录本地调度等待、连接池等待、DNS、建连、请求发送、响应头/首个body字节、首个有效输出和读取完成的可观测时间点；embedding/rerank非流式路径同样覆盖。
- 计时注明连接复用、阶段可观测性及嵌套关系；无法分离TCP/TLS时报告建连合计，不将DNS与包含它的建连时长重复相加。等待响应不命名为纯推理耗时；供应商内部排队/计算无证据时保持未知。
- 异步submit使用调用方预先持久化的稳定request/attempt与内容摘要；模型接受后RPC丢失先查原身份，结果早于通知订阅仍可观察。明确领域与Models之间的持久交接，不要求跨进程长事务。
- 前台流式、非流式及原生代理使用同一请求/尝试权威；观察者断开和用户明确取消分开。重连不能创建第二物理请求，重放已留存片段保持顺序/完成标志；未持久片段范围如实呈现。

删除或替换：

- 删除 Memory/Conversation 中重复整份请求；删除把 unknown 账本状态直接作为云并发许可的规则
- 取消固定空闲交互模型位和未实际配置的日 token 闸门；保留用户明确额度

最小有效验收（本轮未运行）：

- 本地请求结束后无关后台任务可提交；原 unknown 和未知费用保留，不写伪成功
- 排队不消耗执行时钟，执行开始后不续期；真实业务 deadline 始终生效
- 独立重试最多应用一次；迟到旧结果不能覆盖新结果；中断/明确取消语义保持
- 可重现输出已出现但响应不完整，诊断有内容形状且不泄露正文/凭据
- BOT重建后的上游会话身份纳入数据代际，不能继承重建前provider历史；同一新代际内的合法聊天连续性仍保留。
- 隔离HTTP夹具区分连接池等待、慢响应头、慢正文及提前断流；复用连接/非流式/取消的不可观测阶段不填伪0，首个网络字节不等于首个有效文本token。
- 接受成功但ACK丢失、结果先到、消费后崩溃、流式断连后观察、显式取消分别验证；无重复付费提交或重复应用假设，真实unknown保持，实际请求重试费用计入。

## RB-05 前后台进程和实际资源隔离

- 状态：planned；职责：R；优先级：P1。
- 领域协作：I、B、D、X、O；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-03、RB-04。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：按故障影响范围隔离进程、消费者和资源；监控故障不杀业务，BOT后台故障不杀独立媒体。
- 功能清单：FI-02、FI-13、FI-18、FI-19。
- 数据变化：进程/连接池配置和任务领取状态；不引入 Redis/Kafka/新微服务。
- 历史行为：PF-01、PF-12。
- 当前问题：UF-01、NF-01、NF-02、NF-03、NF-07。
- 本轮需求：FR-01。
- 衔接补充：CP-03。
- 原规划：SD-05。
- 完整盘点补全：RI-06（审计第16节）。

复用/修改入口：

- [src/tianshu/runtime/process.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/process.py)
- [src/tianshu/runtime/supervisor.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/supervisor.py)
- [src/tianshu/runtime/database.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/database.py)
- [src/tianshu/runtime/execution.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/execution.py)
- [src/tianshu/runtime/config.schema.json](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/config.schema.json)
- [tests/runtime](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/runtime)
- [src/tianshu/runtime/config.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/config.py)

- [src/tianshu/runtime/composition.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/composition.py)

- [src/tianshu/runtime/listeners.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/listeners.py)

- [deploy/core/health.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/health.py)

交付：

- 同产品镜像内按需装配 API、Models、BOT前台worker、BOT后台worker、媒体worker、Operations 六类角色；部署不强制拆成六个独立产品。
- 逻辑通道继续标识业务，实际 I/O、CPU、本地引擎分别使用资源池；长模型等待返回队列，避免 35 个固定串行孤岛
- 前台控制操作保留必要数据库容量；后台公平轮转，以实际持续工作量防饿死
- worker 租约只覆盖领取/提交语义，长外部等待用句柄观察；同一 owner 的旧租约不能继续写
- 监督器逐角色有限重启与退避；监控子进程不作为全产品退出条件；shared DB故障区分依赖范围。
- 一个队列consumer退出不能cancel所有worker；初始化按领域隔离，未配置的可选功能准确降级。
- 重算六角色实际总连接/线程/HTTP资源预算，包括每角色队列池、LISTEN连接、broker owner、媒体和运维；每角色只实例化所属执行通道。
- 按process_role_matrix配置唯一OneBot/QQ连接拥有者、唯一Models Broker、Life时钟与来源传播消费者、各任务队列的owner。API保留监听/控制；独立能力worker承接媒体及非BOT管理任务，不能每个角色调用完整build_product/start。
- 同步修改新角色启动参数、监听、配置schema、健康聚合、Docker探针与日志role过滤；liveness、角色ready、模块可用性、业务有进展分开。可选日志/运维/媒体未配置不导致API被整体重启。
- 退出时先停止领取/入站，持久保存在途引用与取消原因，再释放owner；租约过期后的旧owner不得继续提交。正常停止、崩溃和仅观察者断连不得全部解释为用户取消。

删除或替换：

- 不以一个全局并发 1 队列替换现有隔离；不以天枢的日程作息限制云后台工作

最小有效验收（本轮未运行）：

- 同一角色阅读、记忆整理和聊天可并进，某路模型卡住不占其他任务处理器
- 后台有持续进展且前台取消可达；重启不重复外部动作；数据库连接不会被模型等待耗尽
- 总资源预算按启用角色准确核算；持续前台/到期负载不饿死后台和媒体；统计查询不能耗尽取消与状态查询连接。
- 六角色同时启动仍只有一个QQ连接owner和一个Broker；单角色重启不复制clock、订阅或外部提交。旧三角色硬编码的ready/日志筛选/部署探针全部有新归属。

## RB-22 失败材料隔离与统一恢复策略

- 状态：planned；职责：R；优先级：P1。
- 完成依赖：RB-01、RB-02、RB-03、RB-04、RB-05。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：将普通失败变为局部、可解释且有限的恢复工作，让无关材料和模块继续。
- 功能清单：FI-22。
- 数据变化：复用领域重试表及原生任务；仅增加必要错误类别、逻辑尝试/覆盖信息与恢复策略版本。
- 历史行为：PF-01、PF-03、PF-13、PF-15、PF-17。
- 当前问题：NF-02、NF-04。
- 本轮需求：FR-01、FR-02。
- 衔接补充：CP-03、CP-05。
- 原规划：SD-20。
- 两窗口逐项结论：CW-08、CW-09（见审计第15节）。

复用/修改入口：

- [src/tianshu/models/retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/retry.py)
- [src/tianshu/runtime/jobs.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/jobs.py)
- [src/tianshu/memory/consolidation_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/consolidation_retry.py)
- [src/tianshu/companion/growth_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/growth_retry.py)
- [src/tianshu/life/computation_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/computation_retry.py)

拟新增路径（尚未创建）：

- `src/tianshu/runtime/recovery_policy.py`

交付：

- 错误按缺失输入、传输暂时故障、确定性无效输出、状态变更、来源失效、未知外部效果和基础设施故障分型；拒绝一律重试。
- 每个逻辑任务只有一个恢复owner，传输/业务/运维共享物理attempt计数和当前版本，不创建叠乘重试。
- 同一材料失败释放工作资源；进度水位继续前进，未覆盖集合保存原身份；真正依赖坏材料的产物标明局部等待。
- 采用随机退避与Retry-After、故障依赖级冷却和单恢复探测；新材料/新配置可触发新诊断周期，不无限原样重试。
- 仅确定性材料/输出容量问题使用有界语义拆批；服务商不可用不拆批轰炸；备选路由从配置且兼容的集合选择。
- 自动恢复先检查原结果；成功后按当前版本重新验证并提交，旧失败历史保留。
- 故障链预算按原语义材料/依赖与恢复lineage继承，拆批/换路由/增加revision不能无限续命；持续高优先级任务下仍有域/主体公平推进。
- 故障隔离范围与事务边界分开：一个共享批失败可进入独立恢复集合，其他批继续；只有能证明输入依赖闭合才重组，跨人关系不能按subject_id拆断。

删除或替换：

- 不新增全局熔断总开关、每日用户额度或统一队列长锁。
- 移除固定同步重试、仅封顶退避但无限重试、手工改DB状态假收口。

最小有效验收（本轮未运行）：

- 1坏+99好材料按真实依赖可独立成批时，99份仍可推进；坏批明确未完成。同一已冻结共享批不能靠提交99个人的输出绕过整批原子性；必要重组须满足RB-08完整输入/替代/回执规则。
- 三个27/21/18尝试形状不再无限循环；429故障不会乘以批数和层数产生请求风暴。
- 隔离修复后补回原逻辑工作，当前新事实不被旧提案覆盖；取消/撤回/外部unknown保持原语义。

## RB-06 分片输入与附件渐进补全

- 状态：planned；职责：D；优先级：P1。
- 完成依赖：RB-02、RB-03。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：缺一个附件只缺该附件，消息身份保持。
- 功能清单：FI-02、FI-03。
- 数据变化：消息原件/part 状态/附件引用保留一份；事实缺失和传输错误分开。 原消息与附件补充分别版本化；保留原件不可变和更新理解的关联。
- 历史行为：PF-13、PF-15、PF-16、PF-20。
- 当前问题：UF-03。
- 本轮需求：承接原优化。
- 衔接补充：CP-02、CP-06。
- 原规划：SD-06、ML-02、ML-06。
- 两窗口逐项结论：CW-05、CW-06、CW-09（见审计第15节）。

复用/修改入口：

- [src/tianshu/channels/ingress.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/channels/ingress.py)
- [src/tianshu/channels/service.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/channels/service.py)
- [src/tianshu/conversation/service.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/service.py)
- [src/tianshu/conversation/types.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/types.py)
- [src/tianshu/conversation/codec.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/codec.py)
- [tests/channels](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/channels)
- [tests/conversation](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/conversation)

交付：

- 接入、存原件、建立 turn、附件解析按明确状态接线；preserve 原始片段顺序与来源
- 不可用媒体作为结构化缺失进入上下文，原图可用时交给支持视觉的路径
- 原event_id、消息revision、original_parts及其digest冻结；迟到附件按原part/source建立独立补充修订和理解覆盖版本，不能改原正文后重新ingest或伪造一条新消息。
- 撤回输入在各异步边界正常取消派生工作，不新建 turn
- BOT自动归档回填使用切换下界，不因游标清空回灌全部旧聊天；用户主动引用旧内容仍按授权的新输入处理。
- 附件补全通知仅更新依赖该片段的理解、记忆与学习投影；补取本身不重复接收/回复，不生成独立的重复事实。确有补充表达需求时沿既有表达命令建立与原turn关联的单独决策。
- 临时下载失败有界补取；路径映射缺失等待配置变化；来源撤回后晚到附件不能复活原消息或派生材料。

删除或替换：

- 删除任一 media=None 直接拒绝整条消息；不把全部媒体强制压成 OCR 文本

最小有效验收（本轮未运行）：

- UF-03 的混合输入通过，依赖不可用视频的具体能力如实失败
- 接收重复事件和附件迟到不重复入站/回复；处理中撤回不冒充系统异常
- 混合文字/引用/不可用图片、纯不可用图片和缺路径文件均能留下正确接收与缺项状态；同一附件重复通知、乱序到达及处理中撤回不重复turn、发送或记忆，且原消息修订哈希不变。

## RB-07 Memory 当前事实、修订与搜索投影

- 状态：planned；职责：M；优先级：P1。
- 领域协作：C、D；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-02。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：正文、历史和索引分工明确，消除第二份有效行为账本。
- 功能清单：FI-04、FI-05。
- 数据变化：新空库 typed head/revision 与 search_document；新产生历史仍保留，旧迁移链可压平。
- 历史行为：PF-10、PF-11。
- 当前问题：UF-05、SF-01。
- 本轮需求：承接原优化。
- 衔接补充：CP-04、CP-06。
- 原规划：SD-07、ML-04、ML-10、ML-11。
- 完整盘点补全：RI-03、RI-07（审计第16节）。
- 两窗口逐项结论：CW-11、CW-17（见审计第15节）。

复用/修改入口：

- [src/tianshu/memory/schema.sql](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/schema.sql)
- [src/tianshu/memory/types.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/types.py)
- [src/tianshu/memory/service.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/service.py)
- [src/tianshu/memory/revisions.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/revisions.py)
- [src/tianshu/memory/index_jobs.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/index_jobs.py)
- [src/tianshu/memory/indexing.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/indexing.py)
- [tests/memory](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/memory)

交付：

- 事实/事项/关系维持明确类型，以 head 指向语义 revision，进度与访问统计分离
- 搜索文档为可重建投影；学习规则索引只引用 Companion 记录和版本，当前状态须由所属域解析
- 当前与历史查询使用显式用途；撤回原件立即使派生材料不能被消费，索引可异步清理
- 原文正文、媒体对象和向量各归一处；保存具体时间、主体、否定、条件，不只留摘要
- 索引profile按嵌入空间区分，profile重建期间当前词法/结构化读取继续；不得将不同模型同维向量混存为同一有效profile。
- 承接ML-11：Episode仅在语义内容变化时追加历史，提取游标/索引状态不复制正文；current/valid_at/known_at分开。学习历史由Companion单一权威承接，不另建可编辑Reflection副本。
- 搜索投影按对象语义版本/profile写回并复核当前head；旧版本索引晚到不得覆盖新版本或让撤回内容复活。索引未就绪使用合法词法/结构化原域读并报告freshness。
- 关系/好感账本仍属Memory，角色—人物冻结不授予权限；短期情绪仍归Companion。作者明确冻结策略作为配置保留，新自动数值不从旧BOT业务回灌。

删除或替换：

- 删除多域复制学习正文/active 状态；删除业务 JSON 内重复嵌入的大证据正文和向量

最小有效验收（本轮未运行）：

- 更改/撤回事实后当前查询不回旧结论，历史查询仍能解释演变
- 暂未建向量不丢关键词/结构化召回；不同条件记录不会因为文字相似被合并
- 角色想象和第三方观点不写成用户客观事实；关系冻结与权限独立保持
- 经历语义编辑后各时间模式能读到真实已保存版本；历史/缓存/派生索引也服从遗忘。旧embedding晚于新版本返回不覆盖当前head，元数据变化不产生新语义历史。

## RB-08 共享整理、局部别名编译与覆盖闭环

- 状态：planned；职责：M；优先级：P1。
- 完成依赖：RB-03、RB-04、RB-07、RB-22。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：保留群聊一次共享理解，机械身份交给程序。
- 功能清单：FI-04。
- 数据变化：原逻辑批次保存一份材料集合、规范提案、别名映射、编译版本/摘要、逐目标覆盖与整批回执；引用Models原请求/结果。必要重组记录父子替代及覆盖归属。
- 历史行为：PF-02、PF-03、PF-11、PF-15。
- 当前问题：UF-01、UF-05、SF-04。
- 本轮需求：FR-01。
- 衔接补充：CP-04。
- 原规划：SD-08、ML-03、ML-04、ML-08。
- 完整盘点补全：RI-01、RI-04（审计第16节）。
- 两窗口逐项结论：CW-07、CW-08、CW-09、CW-15（见审计第15节）。

复用/修改入口：

- [src/tianshu/memory/materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/materials.py)
- [src/tianshu/memory/proposal.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/proposal.py)
- [src/tianshu/memory/extraction_contract.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/extraction_contract.py)
- [src/tianshu/memory/consolidation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/consolidation.py)
- [src/tianshu/memory/consolidation_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/consolidation_retry.py)
- [src/tianshu/memory/preparation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/preparation.py)
- [tests/core/test_consolidation_pipeline.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_consolidation_pipeline.py)

交付：

- 同一材料组共享一次多主体理解。两遍编译：先按主体+局部别名登记声明，再统一解析create、successor、关系端点和反例引用；跨主体引用显式限定，同作用域重名或歧义给出双方位置而不猜测。
- 按输入语义闭合和预计输出规模分段；保留原图、上下文范围及一次准备召回，不按每个人重复全套请求
- 完整校验后的canonical proposal、alias map、compiler/schema version、来源快照和摘要作为同一逻辑批次的冻结提交输入；引用只来自该批。编译后重启不重新猜映射，摘要不足时在冻结前有界lookup补齐原件。
- 每目标记录changed/no_change/unresolved与原因，但同一完整合法提案的操作、全部目标覆盖和提交回执在整批同一事务提交；协议非法或CAS失败不提交其中的成功目标。合法unresolved作为覆盖缺项，不等于协议损坏。
- 确定性编号/协议问题优先复用原完整模型结果本地编译，不额外请求模型；修编译器时新增可追溯编译尝试，保留旧映射且只选一个当前提交输入。语义错误有限纠正，来源/状态变化重新取材，取消/撤回停止，截断不猜补。
- 失败隔离位于任务/输入批边界。只对未提交父批的确定性材料/容量问题按已证明闭合的输入依赖有界重组；显式替代父批并禁止其迟到结果应用，子批各自产生完整提案和原子回执，共享原恢复预算及覆盖归属；不能按人物切割非法输出。429/上游故障不拆批放大请求。
- 承接ML-08：同事项/待修订记录与回复引用优先，人物间公平选材，保留低频发言、短句和表情的真实语义；输入/输出容量联合计量，不用每人固定条数截断换低成本。分项记录原件、必要上下文、候选范围、逐目标理解/提交和索引就绪。

删除或替换：

- 删除模型填写全局 UUID/CAS/任意恢复令牌；移除重复 create 后无限原样重试
- 收回分目标部分提交设计；保留整批事务，在独立工作之间隔离失败，不拆断同批关系、覆盖或来源一致性。

最小有效验收（本轮未运行）：

- PF-11/PF-15 与 UF-05 的范围、覆盖、maxItems、重复别名和错误纠正形状均有反例
- 多人归属不丢；共享材料不重复模型调用；不同语义记录不被模糊去重
- A/B各声明n1可稳定编译且跨人关系准确；同主体重复及歧义拒绝；相同输入/编译版本产生相同ID和摘要。整批中途失败零写入，已提交批重放返回原回执，重启和迟到结果不重复对象、不回退游标。
- 本地编号修复不新增模型调用；生成期间来源变化或用户取消仍阻止提交；父批被重组后不能迟到提交，子批覆盖不重复计算且预算不重置。
- 低频人物及短回复依赖不漏；optional候选省略与required上下文缺失不同，旧缺项不能靠改标签变完整；正常共享理解、必要补查及纠正的物理调用分别计数。

## RB-09 候选检索、重排与计算复用

- 状态：planned；职责：M；优先级：P1。
- 完成依赖：RB-04、RB-07。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：先缩小候选，再取正文和证明，复用不变计算。
- 功能清单：FI-04。
- 数据变化：向量、词法索引与轻量计算缓存作为派生数据；不新增第二事实存储。
- 历史行为：PF-03、PF-10。
- 当前问题：SF-08。
- 本轮需求：承接原优化。
- 衔接补充：CP-04。
- 原规划：SD-09、ML-09、ML-10。
- 完整盘点补全：RI-03、RI-04（审计第16节）。
- 两窗口逐项结论：CW-01（见审计第15节）。

复用/修改入口：

- [src/tianshu/memory/candidates.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/candidates.py)
- [src/tianshu/memory/query_plan.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/query_plan.py)
- [src/tianshu/memory/retrieval.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/retrieval.py)
- [src/tianshu/memory/retrieval_budget.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/retrieval_budget.py)
- [src/tianshu/memory/embedding.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/embedding.py)
- [tests/core/test_memory_evidence_retrieval.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/core/test_memory_evidence_retrieval.py)
- [tests/memory/test_retrieval_degradation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/memory/test_retrieval_degradation.py)

- [deploy/core/settings.example.json](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/settings.example.json)
- [src/tianshu/runtime/config.schema.json](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/config.schema.json)

交付：

- 结构化主体/场景/时间/当前状态约束优先，词法与向量返回小型 ID/分数集合，再批量证明并按需要补候选
- 有条件使用融合与多样性选择；只在候选接近且用途需要时重排，不能仅为缩时取消语义质量
- 向量复用键包含规范化输入、模型/维度/预处理版本；重排复用还需 query 和候选版本
- 同请求只准备一次图片、token 估计和所需材料；撤回/权限变化使缓存失效
- 将本地向量候选查询与来源校验窗口的代码fallback、新装配置种子、部署示例及配置文档统一为2.0秒；不得只依赖生产配置覆盖。既有显式用户设置继续生效，embedding/rerank各30秒独立。
- 承接ML-09/SD-09：中文整词/短语/姓名优先并保留合法单字回退；独立词法/向量支路在原总预算和连接预算内并行，成功支路保留。复用既有RRF/MMR、批量来源校验和有界补位，不新造检索系统。
- 精确对象/list走结构化查询，完整列表不等于top-k；诊断分开候选未召回、资格/预算丢弃和最终装入。跨请求成功数学结果缓存仅在代表负载证明净收益后采用小容量/短TTL，绝不缓存权限决定、失败或unknown。

删除或替换：

- 删除全授权域大正文扫描与逐候选独立来源往返；不按每个无语义变化重建向量

最小有效验收（本轮未运行）：

- 省略配置项和使用部署示例两种新装入口均实际加载2.0秒本地候选/来源窗口；显式覆盖仍可用，embedding/rerank各30秒及合法词法降级语义保持。
- 短预算仍有合法词法结果；大 AND/OR 支持组不超参数限额；source-filter 后有界补候选
- 记录同规模质量与端到端时间/调用数；ANN 命中不能证明完整清单
- 同维不同嵌入空间的备选不能查询旧索引；索引切换尚未完成时沿用可用profile或词法降级，并如实显示覆盖。
- 中文姓名/合法单字、否定/时间、多主体、前批失权后的补位与支路失败均保留质量；缓存冷/热/低命中分别有开销和省掉的实际调用证据，未证明收益时只保留请求内及相同索引文本复用。

## RB-10 唯一学习账本、偏好修订与 Growth 恢复

- 状态：planned；职责：C；优先级：P1。
- 完成依赖：RB-02、RB-03、RB-04、RB-07、RB-22。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：让学会、暂时采用、撤回和历史解释来自一个权威。
- 功能清单：FI-05、FI-06、FI-07。
- 数据变化：本域学习 head/revision 与索引通知；用户事实仍归 Memory，避免反向依赖。
- 历史行为：PF-02、PF-03、PF-04、PF-05、PF-11、PF-14、PF-16。
- 当前问题：UF-04、SF-01、SF-02、SF-04、SF-06。
- 本轮需求：FR-01。
- 衔接补充：CP-06。
- 原规划：SD-10、SD-15、ML-04、ML-05、ML-07、LE-01、LE-02。
- 完整盘点补全：RI-02、RI-03（审计第16节）。
- 两窗口逐项结论：CW-11、CW-12、CW-14、CW-15、CW-20（见审计第15节）。

复用/修改入口：

- [src/tianshu/companion/growth.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/growth.py)
- [src/tianshu/companion/growth_materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/growth_materials.py)
- [src/tianshu/companion/growth_observations.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/growth_observations.py)
- [src/tianshu/companion/reflection_output.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/reflection_output.py)
- [src/tianshu/companion/growth_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/growth_retry.py)
- [tests/companion](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/companion)

交付：

- Companion 拥有 learning record/revision/current status；有效习惯是本域读视图，Memory 仅索引引用
- 当前明确指令优先于旧推断；偏好有角色/对象/群/场景/有效期/条件/反例；明确纠正修订同一规则
- 运行候选查询先 active/scope/validity 再排序/limit；管理分页和 retry 统计分离
- Growth 支持子集与生成依赖分开；过滤技术噪声，低信息想象事件汇总，保留有意义体验
- JSON/schema/引用错误一次受限纠正；按批持久失败且后续材料公平推进
- 失败材料进入其独立恢复集合；后续输入推进但不虚报学习覆盖。已知来源撤回/用户取消不交Agent绕过。
- 稳定rule_id承载支持、修订、撤回与恢复；已解析到同一被撤回规则的重述不能换ID自行恢复，未知语义相似保留待辨别。持续明确偏好不因话题相似度低而漏选；本轮/今日要求按范围到期。
- 支持次数按实际独立事件去重；完整生成依赖继续用于来源与授权复核，不缩成模型所选support子集。低频仅影响选择排序，不按天自动删除、不永久保护高频；关系冻结暂停对应自动更新，用户明确纠正仍生效。
- 规则语义/范围/状态变化与support_only/统计变化分开；向各消费者提供批量当前资格与语义兼容性，不因任意revision增加使正常回复失效。
- 明确界面命令/已解析原生命令在原事务修订；自然语言纠正先在本轮遵守，再由原事件形成持久修订，归属未明时不猜删规则，也不宣称永久偏好已保存。

删除或替换：

- 删除有效行为双账本及管理列表前 40 条充当运行候选；移除自我重复增信

最小有效验收（本轮未运行）：

- 撤回规则从普通检索和习惯入口均不再采用；第 41 条合法规则可被选中
- ‘今天’不固化为永久偏好；群内词义不泄露到其他群；用户当前要求不被习惯盖过
- UF-04 原始错误及 source/dependency/迟到结果反例通过；静态人格不被自动重写
- 同源多派生产物与跨批重述不重复增信；并发纠正CAS可解释；换话题仍遵守持续偏好，同一撤回规则换ID不能复活；历史查询仍可读取带当时状态和当前失效说明的授权依据。
- 新增支持不取消回答；真正撤回/适用范围变化使依赖该规则的未提交表达失效；本轮已遵守、持久已提交和仍待整理分别可见。

## RB-11 Life 对象解耦与统一执行资格

- 状态：planned；职责：L；优先级：P1。
- 完成依赖：RB-02、RB-03。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：角色当前生活状态与后台工作分别建模，资格只实现一处。
- 功能清单：FI-08、FI-11。
- 数据变化：职责明确的 Life 数据对象；不为所有领域引入通用工作流表。
- 历史行为：PF-04、PF-06、PF-08、PF-09、PF-22。
- 当前问题：UF-02、UF-06。
- 本轮需求：承接原优化。
- 衔接补充：CP-06。
- 原规划：SD-12。
- 两窗口逐项结论：CW-03（见审计第15节）。

复用/修改入口：

- [src/tianshu/life/schema.sql](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/schema.sql)
- [src/tianshu/life/types.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/types.py)
- [src/tianshu/life/state.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/state.py)
- [src/tianshu/life/day_plan.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/day_plan.py)
- [src/tianshu/life/continuation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/continuation.py)
- [tests/life](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life)

交付：

- goal 表达目的，intent 表达待做工作，step/checkpoint 表达执行，role focus/timeline 只表达生活与承诺
- 提案生成、提交和执行复用 eligibility，输出明确候选键/原因/版本
- 读模型只包含页面所需摘要和原因，检查点正文按需读取；PF-08 的暂停与保护行为有新归属

删除或替换：

- 删除把所有 running 工作都限制为每角色一个的总锁；真实独占设备或同一动作保持资源级排他
- 保护期只保护已有承诺/专注，不限制无冲突的记忆和学习

最小有效验收（本轮未运行）：

- 同角色聊天、后台阅读、Growth 可以并行；真实时间冲突仍被正确处理
- 已完成目标没有继续候选；scope 更新不重造活动身份；暂停不造成版本抖动
- UI能分清‘角色在休息’与‘后台任务仍在执行’

## RB-12 对话依赖优先、分用途取材与预算

- 状态：planned；职责：D；优先级：P1。
- 领域协作：M、C、L、I；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-06、RB-09、RB-10、RB-11。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：先理解眼前对话，再选有用记忆，减少重复 token 工作。
- 功能清单：FI-02、FI-06、FI-07。
- 数据变化：冻结取材身份与版本的小清单；正文复用原权威记录和 Models 请求。
- 历史行为：PF-05、PF-10、PF-14、PF-15、PF-18。
- 当前问题：SF-01、SF-03、SF-05。
- 本轮需求：承接原优化。
- 衔接补充：CP-04、CP-06。
- 原规划：SD-11、ML-01、ML-06、LE-01、LE-05。
- 完整盘点补全：RI-01、RI-02（审计第16节）。
- 两窗口逐项结论：CW-11、CW-12、CW-13、CW-14、CW-17、CW-18、CW-19、CW-20（见审计第15节）。

复用/修改入口：

- [src/tianshu/conversation/context](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/context)
- [src/tianshu/types/context_materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/types/context_materials.py)
- [tests/context](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/context)

交付：

- 保留完整人格和当前输入所需的引用对话/工具结果，再分配可选记忆、规则和样例；用途区分聊天、规划、整理、日记
- 各域读材料可并行，使用同一总 deadline；可选域失败只移除完整可选单元
- actual selection manifest 按记录身份+语义版本保存，复核有效性时不被相同 kind 覆盖
- 单元 token 估计按内容/模型版本复用，最终包做必要总量检查；没有材料不主动填满预算
- 聊天、Life规划使用各自用途的完整材料单元；当前对话已有同一依据时不重复展开。候选选择后、请求冻结前按稳定ID/revision复核当前状态，清单记录最终装入项及实际省略原因；不引入前台辅助LLM或新的远程重排。
- 近期消息保留event/turn/reply/source/revision直到选定完整单元再汇总直接来源；仅从未参与模型或衍生计算且最终未选的候选可排除，已用材料与重排/生成完整依赖不得缩小。
- 尚未长期整理的新消息可通过原会话/原件参与本轮理解；明确纠正与旧事实按可证明的修订关系处理，无法证明覆盖时并列冲突，不新建短期事实库。
- 原生memory工具、聊天、Life、Growth、日记和历史读取共用用途/行为资格解释；Memory不直查Companion私表。当前事实、历史依据、动态风格和明确约束保持类型。

删除或替换：

- 删除先塞长期材料再截断当前指代链的顺序；不重复每个候选全包 tokenize
- 删除规则作为静态 system 人格正文不断改写的做法

最小有效验收（本轮未运行）：

- ‘刚才第二种’保留必要上文；同类多条修订不互相覆盖；撤回学习规则无复活入口
- 背景输入 200000；普通聊天 16000/输出预留 2048 保持，embedding/rerank 不套此预算
- 完整人格保留；估计 token、字节上限、provider usage 分开呈现
- 可选Memory/Growth服务不可达时保留当前对话继续；必需输入缺失准确说明，不能凭空补全用户意思。
- 零记忆、零习惯、零向量的首次对话可正常取材；备选模型装不下完整必需材料时明确局部不可用，不默默截断人格和当前指代链。
- 聊天选style、规划选interest的代表场景正确；不需个性化时零规则/样例合法；选材后撤回不进入最终请求，多个同kind材料版本均保留，预算舍弃项不显示为已注入。
- 未选且未参与衍生计算的候选撤回不无故取消回复；已采用来源撤回仍生效。新消息未整理也不被旧事实压掉，原生memory工具不能绕过被撤回习惯。

## RB-13 事件驱动规划与轻量时间安排

- 状态：planned；职责：L；优先级：P1。
- 完成依赖：RB-04、RB-11、RB-12、RB-22。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：日程由有效变化和真实到期触发，模型负责选择，程序负责排时间。
- 功能清单：FI-08。
- 数据变化：独立意图、版本化计划和 due；一次建议最多几个未来候选仅为输出大小，不是每日限额。
- 历史行为：PF-06、PF-07、PF-08、PF-09、PF-17、PF-22、PF-23。
- 当前问题：UF-02、SF-07。
- 本轮需求：承接原优化。
- 衔接补充：CP-06。
- 原规划：SD-13。
- 完整盘点补全：RI-04（审计第16节）。
- 两窗口逐项结论：CW-03、CW-04、CW-09、CW-12（见审计第15节）。

复用/修改入口：

- [src/tianshu/life/planning_engine.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/planning_engine.py)
- [src/tianshu/life/planner.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/planner.py)
- [src/tianshu/life/day_plan.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/day_plan.py)
- [src/tianshu/life/opportunities.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/opportunities.py)
- [src/tianshu/life/directions.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/directions.py)
- [tests/life](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life)

交付：

- 新目标/当前活动完成/结果到达/材料语义变化/用户干预触发重算；next_due 取真实边界，clock 仅兜底
- 模型选择合法候选和意图，程序解析 continuation key 并计算 next gap；真实预约/时区与日界线继续约束
- 有效空计划保存绝对 next_review；失败不写有效指纹；已知完整错误按原有最多两次纠正，传输重试独立
- 可执行独立片段仅在完整可解析且各自验证后应用，goal 与依赖活动成组；保留旧合法计划
- 应用返回逐候选结果：scheduled、intentional_idle、deferred_no_slot、stale_state、invalid_proposal及原因/对象/版本/下次触发。零创建数不能决定主动留白；有效意图但全跳过时保留未完成结果。
- 调度按应用结果选择等待真实窗口、刷新失效候选或有限语义纠正；仅明确留白才保存其有效空白指纹与绝对复查时点。混合成功与等待逐项保留，已完成的目标修改/联系动作不被重放。
- 规划读取本地能力声明与后台刷新、带验证时间的可用性快照；不每次规划串行探测全部外部连接。实际执行对所选能力当前授权/配置/必要可用性复检，未知不能伪造可执行。

删除或替换：

- 删除普遍套用的 15 分钟最短、20 分钟间隔、90 分钟等下一次再说等对后台工作的限制
- 不恢复全天四项、固定占用比例和固定每日目标数；真实承诺/休息偏好仍是产品语义

最小有效验收（本轮未运行）：

- 两个 UF-02 样本都能沿合法候选继续；完整无效与截断提案不伪造成功
- 第 5/6 项活动仍可创建；无关 tick 不推迟空计划绝对复查时间；不因分钟变化反复调用模型
- 已有预约、日界线与首次夜间启动的正确行为保持
- 生成时合法、应用前revision变化；全部候选因无时间窗跳过；明确选择留白；部分安排成功部分过期，四类结果可区分。前三者不能都被标为no_meaningful_activity，重复结果不重复排程、目标变更或联系。
- 一个未选外部能力探测慢/失败不拖住整轮规划；所选能力配置变化仍在派发前正确拒绝或刷新，不用缓存掩盖失权。

## RB-14 后台执行、阅读续接与局部失败恢复

- 状态：planned；职责：L；优先级：P1。
- 完成依赖：RB-03、RB-04、RB-05、RB-11、RB-13、RB-22。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：执行按一个有进展的步骤推进，外部等待不占角色和 worker。
- 功能清单：FI-08、FI-09。
- 数据变化：步骤、游标、原动作引用和实际结果；不另开内容任务账本。
- 历史行为：PF-01、PF-06、PF-07、PF-08、PF-09、PF-17、PF-21、PF-23。
- 当前问题：UF-02。
- 本轮需求：FR-01。
- 衔接补充：按原合同。
- 原规划：SD-14。
- 完整盘点补全：RI-07（审计第16节）。
- 两窗口逐项结论：CW-03（见审计第15节）。

复用/修改入口：

- [src/tianshu/life/clock.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/clock.py)
- [src/tianshu/life/events.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/events.py)
- [src/tianshu/life/worker.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/worker.py)
- [src/tianshu/life/generation_control.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/generation_control.py)
- [src/tianshu/life/continuation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/continuation.py)
- [tests/life](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life)

交付：

- 每任务执行一个有界步骤并保存 cursor/checkpoint，结果通知续接，独立任务可并行
- 原活动身份与明确继续授权保留；已知阅读失败只跳过失败候选，未知结果先观察原 handle
- 按内容去重、cursor 前进及无进展签名防循环；真业务 deadline/取消仍生效
- 作息限制角色主动表现与打扰，后台整理/索引/学习按资源和意图运行，必要结果等可表达时再说
- 保留实际共读会话、共享授权、游标与暂停/续读入口；提醒、真实约定、主动联系和后台工作分别用各自到期/执行条件，时间推进不因后台活动暂停停止。

删除或替换：

- 删除固定 12 步当整段活动终止条件，改为每次工作有界+真实进度判据
- 保留已有外部等待释放注意力行为，并推广到所有合适纯计算等待；不声称此前完全没有

最小有效验收（本轮未运行）：

- 页发现→读取→下一页在同活动内正确推进；重复同页会停止，无漏读成功部分
- 休息期间学习可完成，QQ仍尊重不打扰；用户取消后结果只能按取消语义归档
- 重复唤醒不重复 Action；暂停恢复不刷新原期限、不抖动语义版本

## RB-15 语义经历与独立日记闭环

- 状态：planned；职责：L；优先级：P1。
- 完成依赖：RB-03、RB-04、RB-07、RB-10、RB-11、RB-14、RB-22。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：经历真实分型，日记按日独立生成并准确落状态。
- 功能清单：FI-10。
- 数据变化：独立日级对象、语义经历引用、草稿/发布版本；正文和模型请求不多处复制。
- 历史行为：PF-02、PF-03、PF-04、PF-05、PF-11、PF-14、PF-16、PF-21、PF-22。
- 当前问题：SF-06。
- 本轮需求：承接原优化。
- 衔接补充：CP-06。
- 原规划：SD-15、ML-04。
- 完整盘点补全：RI-07（审计第16节）。

复用/修改入口：

- [src/tianshu/life/diaries.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/diaries.py)
- [src/tianshu/life/diary_automation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/diary_automation.py)
- [src/tianshu/life/diary_materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/diary_materials.py)
- [src/tianshu/life/diary_retry.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/diary_retry.py)
- [src/tianshu/life/imagined.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/imagined.py)
- [tests/life/test_diary_automation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_diary_automation.py)
- [tests/life/test_diary_materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/life/test_diary_materials.py)

交付：

- 实际 action result 与想象生活分型；discovery/read/失败/未知图片区分，技术补取不生生活经历
- 角色+当地日期+稳定作者场景标识 diary day，不随 scope 哈希变化复制日期对象
- 从当天有效语义经历构造独立 200000 输入请求，无前日、聊天、失败半截上下文串入
- 只要正文已可靠保存，用原回执对账派生状态；实际无材料可以合法不写
- 日记与创作作品/章节各保留手动请求、生成、取消、对账、修订、审阅、发布及已发布读者授权；共用可靠生成/回执能力，按对象/版本分开，不能为独立日记删除创作链路。

删除或替换：

- 删除每个微小虚构休息事件都当独立高价值学习材料的全量重复处理
- 取消对旧数据库歧义日的永久兼容迁移；新数据的来源版本/正文与回执必须保真

最小有效验收（本轮未运行）：

- 同日唯一已保存正文只更新正确派生状态；多候选场景不能猜作者或覆盖正文
- 旧日期失败不阻塞后一天；首次深夜启动不误闭日
- 日记/反思不递归喂给自身成长；25 条想象时段不会显示为读了 25 次内容

## RB-16 对话、工具与发送结果收敛

- 状态：planned；职责：D；优先级：P1。
- 完成依赖：RB-03、RB-04、RB-05、RB-06、RB-12、RB-22。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：聊天过程可以并行取材，同一会话表达与外部动作保持准确身份。
- 功能清单：FI-02、FI-03、FI-10、FI-12。
- 数据变化：表达意图、尝试与投递回执一条链；无第二外部发送账本。
- 历史行为：PF-13、PF-19、PF-20。
- 当前问题：UF-03、SF-03。
- 本轮需求：承接原优化。
- 衔接补充：CP-02。
- 原规划：SD-16、ML-05、ML-06、LE-01。
- 完整盘点补全：RI-02、RI-05（审计第16节）。
- 两窗口逐项结论：CW-05、CW-06、CW-13（见审计第15节）。

复用/修改入口：

- [src/tianshu/conversation/expression.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/expression.py)
- [src/tianshu/conversation/rendering.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/rendering.py)
- [src/tianshu/conversation/results.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/results.py)
- [src/tianshu/conversation/generation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/conversation/generation.py)
- [src/tianshu/channels/delivery.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/channels/delivery.py)
- [tests/conversation](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/conversation)
- [tests/channels](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/channels)

交付：

- 会话内有关联的消息顺序短串行，模型/工具等待释放资源；新消息按现有合并/取消语义更新
- 工具机械字段与权限由程序提供；结果回流原 intent，表达只陈述实际已知结果
- 发送前在外部边界复核当前目标/来源与权限；成功、失败、部分和 unknown 分开
- 同内容 chunk 可合并存储，但保留 role/tool/thought 与实际 completion 顺序和原诊断
- 实际装入规则的语义依赖随expression/action传到模型结果、渲染release和最后未提交发送边界；support_only不取消，已确认撤回/实质变化只停止受影响未提交部分。已发送/部分送达/unknown保留原回执，不自动重发。
- 一般可选风格状态读取故障不伪称已撤回或持续取消；当前明确禁止要求无法复核时保留未决处理，不假装已经遵守。

删除或替换：

- 删除内存长锁包住模型调用与全角色工作；不把 RPC 包装器成功当业务成功

最小有效验收（本轮未运行）：

- 断线且提交不明不重复发送；新独立意图不因旧 unknown 永久停住
- 工具成功但表达取消保留实际产物，不能说已经发给用户
- 无法保证供应商端 exactly-once 时保留 unknown，不用清空账本推定未执行
- 等待语音、分段发送和排队期间撤回能影响尚未提交部分；仅新增支持不影响正常发送，多段已有一段提交不能改写为全未发生。

## RB-17 按需表达样例与群词义

- 状态：planned；职责：C；优先级：P2。
- 领域协作：M、D、F、I；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-07、RB-08、RB-10、RB-12。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：增强理解与表达细节，避免自我模仿循环。
- 功能清单：FI-07。
- 数据变化：独立样例/词义类型及来源，当前规则只引用学习权威。
- 历史行为：PF-11。
- 当前问题：SF-04。
- 本轮需求：承接原优化。
- 衔接补充：按原合同。
- 原规划：SD-17、ML-12、ML-13、LE-03、LE-04。
- 完整盘点补全：RI-10（审计第16节）。
- 两窗口逐项结论：CW-15、CW-16、CW-17、CW-19、CW-20（见审计第15节）。

复用/修改入口：

- [src/tianshu/companion](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion)
- [src/tianshu/memory/materials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/materials.py)
- [src/tianshu/memory/revisions.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/revisions.py)
- [src/tianshu/memory/service.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/memory/service.py)
- [tests/companion](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/companion)

交付：

- 样例按真实 reply/turn 关联抽取，保留条件/否定/场景；只在用途匹配时少量选用
- 群词义复用 group:<conversation_id> 和当前 Memory 主体；非新建身份系统
- 使用记录只作曝光/采用统计，真实纠正、来源支持和任务反馈才改变规则可信度
- 样例区分生成、真实表达及送达；unknown/failed不冒称成功交流，送达也不等于用户认可。保留多消息回复关联、条件、否定与原件定位；不复制样例人名/地点/数字为当前事实，近期已充分表达的规则不重复补例。
- 群词义保留真实group主体、实际speaker、assertion、适用语境与不同解释；单人解释不得升格全群共识。人物别名归Identity、任务约定归Matter，词项未命中时不额外调用模型。

删除或替换：

- 不以相邻时间冒充问答；不把模型自说自话、好感分、情绪分当事实证据或奖励

最小有效验收（本轮未运行）：

- 撤回来源样例立即不可用；不同群同词不同义不串用
- 无相关样例时普通聊天继续，群词用于理解，不强迫模仿粗俗表达
- 自学习不是模型权重训练，固定人格不自动改写
- A/B交错及合并回复不配错、私聊样例不越域、否定不丢；明确负反馈后停用；同群分歧和单人解释不变群体self_confirmed；样例事实不污染当前人物。

## RB-18 独立模块边界与 BOT 调用接线

- 状态：planned；职责：X；优先级：P1。
- 领域协作：R、I；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-02、RB-03、RB-04、RB-05。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：外围能力按自身任务和资源运行，BOT 使用它们但不成为全部任务前提。
- 功能清单：FI-09、FI-11、FI-12、FI-13、FI-14、FI-15、FI-16、FI-17、FI-18。
- 数据变化：复用已有 action/receipt 与各能力原产物记录，保留外部账号/资源边界。
- 历史行为：PF-09、PF-12、PF-16、PF-18、PF-19。
- 当前问题：UF-03、NF-07。
- 本轮需求：FR-06。
- 衔接补充：CP-01。
- 原规划：SD-18。
- 完整盘点补全：RI-04、RI-07（审计第16节）。
- 两窗口逐项结论：CW-10（见审计第15节）。
- 范围补充：视频下载、音乐下载、图书订阅属于独立媒体业务。本卡只核验边界、适配 BOT 调用及必要共享接口；不重写下载/订阅业务，不重建其数据库或队列。

复用/修改入口：

- [src/tianshu/capabilities](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/capabilities)
- [src/tianshu/runtime/media.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/media.py)
- [src/tianshu/runtime/comics.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/comics.py)
- [tests/capabilities](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/capabilities)

交付：

- 书籍/漫画/音乐/视频订阅、导入、图像、语音和插件保持原入口与配置归属
- 全局作者的订阅独立于 Life/Memory 与角色是否在线；任务到期直接调用所属能力
- ComfyUI/下载引擎未知任务以原 handle 观察；角色工作流固定部分与显式动态段分开
- 外围完成以实际产物和回执为准，再由主动表达决定是否展示
- 完整保留独立项目资料/经验、知识与资产连接、家庭设备/基础设施/游戏、天气/位置、插件/技能/MCP及原生模型代理既有入口和数据；仅改共享调用/任务适配。独立管理任务不依赖BOT角色、作息或学习就绪。
- 能力目录声明/后台可用性刷新与真实调用分开；避免每次聊天/规划遍历外部探针。保留当前权限与配置版本，更新仅唤醒相关工作。

删除或替换：

- 删除无实际需求的 BOT 角色前置条件、同源无效阻塞所有源、旧场景隐式提示继承

最小有效验收（本轮未运行）：

- 授权 epoch 变化不影响仍有效独立书源；真失权只停对应源
- 图像规划不把 Base64 算数百万 token；未知一致性仍是 unknown，保存成功不冒充发送成功
- 插件/媒体原配置契约保持；没有绑定语音不得冒称真实语音验证
- 不创建 BOT 角色也能使用管理员媒体入口及其定时任务；BOT 作息、学习失败不成为媒体任务前置条件。
- BOT后台进程退出或未配置QQ时，媒体worker保持原订阅进度；公共设施升级保留媒体原回执和队列任务身份。
- 保留媒体的主体、密文凭据、来源和原队列任务在BOT重建后仍可解析，不靠重新订阅或重建任务恢复。
- 22类功能清单的独立入口在无BOT历史/未配置QQ时仍能按原条件工作；无法实调的设备、外部服务或声音保持未验证，不用空实现替代。

## RB-23 指定模型的运维 Agent 与验证闭环

- 状态：planned；职责：O；优先级：P1。
- 完成依赖：RB-02、RB-03、RB-04、RB-05、RB-22、RB-08、RB-10、RB-15、RB-16、RB-18。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：由指定模型诊断聚合故障、选择登记恢复动作，执行后按真实结果确认。
- 功能清单：FI-22。
- 数据变化：最少incident与recovery_attempt元数据，引用既有业务回执；Ops不拥有事实或业务完成权威。
- 历史行为：PF-02、PF-03、PF-04、PF-12、PF-13、PF-16。
- 当前问题：NF-04。
- 本轮需求：FR-02、FR-03。
- 衔接补充：CP-04、CP-05。
- 原规划：本轮新增。

复用/修改入口：

- [src/tianshu/runtime/commands.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/commands.py)
- [src/tianshu/runtime/diagnostics.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/diagnostics.py)
- [src/tianshu/models/types.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/types.py)
- [src/tianshu/companion/growth.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/growth.py)
- [src/tianshu/life/diary_automation.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/diary_automation.py)

拟新增路径（尚未创建）：

- `src/tianshu/operations`
- `tests/operations`

交付：

- 独立Operations进程消费事件与低频进展检查，按依赖/错误族/版本聚合incident；确定性常见修复先于模型。
- ops.diagnose由用户指定主模型及可选备用；默认只提供去敏错误、版本、进度、栈帧和回执摘要，不发送整库/聊天正文。
- 只暴露inspect、invoke_registered_recovery、observe_result、escalate等窄工具；恢复注册映射到既有领域命令与当前版本检查。
- 允许自动执行安全重试、保存结果对账、派生索引重建、原授权刷新、兼容备用路由及局部进程恢复；每种动作定义前置事实、效果与验证。
- Agent执行有独立尝试/时间/费用记录；默认一次诊断，必要时一次反馈修正；自身模型失败直接标明并走确定性通知，不递归唤醒自己。
- 代码/schema改写、凭据替换、业务数据删除、已提交效果不明的重放属于复杂处置，整理建议发送管理员，不靠模型自信分授予动作。
- 无进展诊断先确认存在已到期且具备执行条件的工作；正常休息、无材料、用户暂停、等待未来due或正在有效执行预算内不触发故障恢复。心跳、可用性和实际进展分别观察。

删除或替换：

- 不让模型生成shell/SQL直接执行；不建立第二份任务成功状态；不为正常用户操作新增审批流程。
- 不每行错误调用一次模型，不把模型说已修好当恢复完成。

最小有效验收（本轮未运行）：

- 简单错误用领域命令完成并读到预期业务状态；失败/过期命令不能覆盖新结果。
- 同一故障重复100条日志只形成聚合事件，模型调用次数受该事件预算约束。
- 错误正文中的指令只作为数据；越界动作被执行器拒绝并转人工，合法自动恢复无需逐次用户确认。
- 诊断模型不可用时业务继续，事件可见并升级；运维行为不进入BOT学习材料。
- 登记动作只有在对应领域端口已装配且验证成立时可用；缺入口不能返回占位成功，也不能调用全量恢复绕过范围。
- 修复产生子任务或改变行政revision仍继承故障链预算；用户暂停/取消/配置变更使旧决策失效，不覆盖人工结果。

## RB-24 独立日志页与不阻塞业务的日志链

- 状态：planned；职责：R；优先级：P1。
- 领域协作：F、I；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-03、RB-05。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：保留完整日志要求，消除日志对业务的硬依赖，提供截图所示可用体验。
- 功能清单：FI-20、FI-21。
- 数据变化：沿用原日志持久层，增加必要索引/游标元数据与公共查询字段；不复制整个日志数据库。
- 历史行为：公共合同。
- 当前问题：NF-03、NF-05、NF-06。
- 本轮需求：FR-04。
- 衔接补充：CP-03。
- 原规划：SD-19、SD-20。
- 完整盘点补全：RI-06、RI-08、RI-10（审计第16节）。

复用/修改入口：

- [src/tianshu/runtime/events.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/events.py)
- [src/tianshu/runtime/logging.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/logging.py)
- [src/tianshu/runtime/supervisor.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/supervisor.py)
- [src/tianshu/runtime/diagnostics.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/diagnostics.py)
- [web/src/features/flow-graph/RuntimeView.tsx](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/web/src/features/flow-graph/RuntimeView.tsx)
- [web/src/features/settings/TasksPanel.tsx](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/web/src/features/settings/TasksPanel.tsx)
- [deploy/core/observability/vector.toml](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/observability/vector.toml)
- [tests/runtime/test_events.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/runtime/test_events.py)

拟新增路径（尚未创建）：

- `web/src/features/system-logs`

交付：

- 后端复用本地日志/Vector/Loki；业务事实与外部回执先在领域事务保存，日志收集批量异步，不由Loki/监控查询可达性决定业务准入。
- 完整日志不采样不静默丢弃；本地缓冲、补传、磁盘容量、覆盖缺口和采集状态明示；资源真正耗尽按受影响写域处理。
- 保留原始level、module、role、trace/job/attempt、error_family与依赖引用；敏感值在存储/导出/送Agent之前脱敏。
- 前端增加独立日志导航：时间/级别/模块/角色/关联ID检索、排除对话事件、自动跟随/暂停、关键词、详情与脱敏导出；虚拟列表保持大日志可用。
- opaque分页游标处理同时间戳边界、去重和迟到事件；无新日志、断连、未配置、查询不完整分别显示。
- 错误详情可跳转原任务、模型尝试和运维事件；日志页不负责直接改数据库。
- 日志续页不能只包装opaque cursor：固定查询范围/过滤条件和原event_id、instance_id、sequence，验证上游同时间戳超过limit的可枚举性；必要时按来源有界分片或从现有完整持久日志补取，不能凭timestamp加减或无限扩大limit宣布完整。
- 复用原追加日志的sequence与采集checkpoint，区分已生成/已持久/已采集。正常退出排空；进程强退未落盘范围标为可能缺口，业务结果以事务回执为准。trace/job/attempt保留在日志字段或兼容的structured metadata，不作为无限增长的索引标签。

删除或替换：

- 替换stdout所有级别均INFO、只按timestamp减1翻页、每次业务开始必须等待日志fsync的路径。
- 不新建第二套日志平台，不把聊天正文、完整提示词或密钥默认复制到平台日志。

最小有效验收（本轮未运行）：

- 原WARN/ERROR在筛选和统计中正确；超页数量同时间戳日志不漏不重。
- Loki断开、writer慢、可恢复日志写入故障不会导致无关业务入口全503；采集恢复能补传并声明缺口。
- 并发大量日志时页面不无限增长；暂停不丢查询位置；管理员看到明确数据覆盖和采样时点。
- 完整日志要求与容量边界一致，不能以偷偷采样/删除未采集片段获得测试通过。
- 同时间戳超过服务端页限、跨来源、迟到补传与断线续页在实际支持的查询后端不漏不重；不能只mock一个理想cursor。强退后不伪称完整日志已持久。

## RB-25 系统统计页与准确的增量计量

- 状态：planned；职责：O；优先级：P1。
- 领域协作：F、R、B、M、C、L；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-03、RB-04、RB-05、RB-07、RB-10、RB-11、RB-22、RB-13、RB-23。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：统一展示系统资源、消息、模型用量、业务积压与恢复效果。
- 功能清单：FI-20、FI-21。
- 数据变化：小型可重建时间桶与维度聚合，原Models/业务账本仍是权威；记录量限制来自必要粒度而非丢结果。
- 历史行为：公共合同。
- 当前问题：NF-05、SF-07。
- 本轮需求：FR-05。
- 衔接补充：CP-03。
- 原规划：SD-19、SD-20。
- 完整盘点补全：RI-08、RI-09、RI-10（审计第16节）。
- 两窗口逐项结论：CW-02、CW-04、CW-10、CW-18（见审计第15节）。

复用/修改入口：

- [src/tianshu/runtime/diagnostics.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/diagnostics.py)
- [src/tianshu/models/management.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/management.py)
- [src/tianshu/models/ledger.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/ledger.py)
- [web/src/features/settings/TasksPanel.tsx](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/web/src/features/settings/TasksPanel.tsx)
- [deploy/core/observability/prometheus.yaml](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/observability/prometheus.yaml)

拟新增路径（尚未创建）：

- `src/tianshu/operations/statistics.py`
- `web/src/features/system-statistics`

交付：

- 资源数据复用现有运行观测与Prometheus；业务统计从权威账本/领域变更增量汇总，页面不反复全表扫描。
- 截图式总览卡、1天/3天/7天趋势、模块/角色/路由切换；分开显示调用次数与Token、累计与所选时间范围。
- 消息按原事件去重，明确接收/已处理/需回复/已投递；模型按物理attempt记录输入输出、cache/reasoning子项、队列/执行/TTFT和实际/估算/未知费用。
- 业务统计覆盖Memory目标结果、Growth两处阻塞、Life有效/空白/拒绝、未覆盖材料年龄；独立媒体任务独立分组。
- 以需要统计的有效状态转换/版本去重更新minute/hour汇总；迟到usage和撤回可以纠正；不采用全局max-ID跳过晚提交事务。
- 显示freshness、样本数、未知用量覆盖和故障影响范围；实际修复率以验证后的eligible事件为分母。
- 时延读取RB-04的分段记录，分别显示连接池/DNS/建连/发送/等待响应/读取与有效输出；不重复累加嵌套阶段。Life统计消费RB-13逐项应用结果，明确区分主动留白、暂排不下和状态过期。
- 统计投影保持可重建、按受控有限维度汇总，trace/job/attempt ID仅作明细关联，不为每任务生成永久时序。复用现有保留/容量策略，不新增默认删除原回执或未完成工作证据。

删除或替换：

- 不用任务完成数代替模型调用数，不把token叫调用次数，不把NULL费用当0。
- 不把reasoning/cache子项再次加到已包含它们的total；不对小样本伪报长期p95。

最小有效验收（本轮未运行）：

- 同一任务重试三次：业务成功一次、物理调用三次；unknown usage仍显示缺口。
- 晚到usage只修正一次，汇总与明细对得上；时间范围与卡片/图表一致。
- 未调用模型的媒体下载仍有正常业务计数；BOT错误不把媒体判为停止。
- 统计服务暂时失败只让统计页显示不可用，不阻止业务。
- 相同总耗时但不同瓶颈的HTTP夹具显示不同阶段；供应商内部耗时未知时不标纯推理。日程全跳过不增加主动留白成功数，部分成功不掩盖剩余待办。

## RB-26 独立守护、升级通知与恢复验证

- 状态：planned；职责：O；优先级：P1。
- 完成依赖：RB-03、RB-05、RB-22、RB-23。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：业务进程、模型或数据库不可用时仍能给出确定性通知与有界恢复。
- 功能清单：FI-22。
- 数据变化：incident引用+通知outbox；宿主兜底只存最少状态，恢复后并回，不另建完整业务数据库。
- 历史行为：PF-12、PF-13、PF-16、PF-17。
- 当前问题：NF-01、NF-03、NF-07。
- 本轮需求：FR-02、FR-03。
- 衔接补充：CP-03、CP-05。
- 原规划：本轮新增。
- 完整盘点补全：RI-06（审计第16节）。

复用/修改入口：

- [src/tianshu/runtime/supervisor.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/supervisor.py)
- [deploy/core/freeze_guard.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/freeze_guard.py)
- [deploy/core/observability/alerts.yaml](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/observability/alerts.yaml)
- [src/tianshu/channels/delivery.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/channels/delivery.py)

拟新增路径（尚未创建）：

- `src/tianshu/operations/notifications.py`
- `deploy/core/operations-watchdog`

交付：

- 复用宿主已有守护的边界，增加本产品角色心跳/实际进度/容器状态检查；不依赖出错的BOT队列或诊断模型才能告警。
- 管理员QQ或SMTP邮箱由用户配置；恢复通知走运维来源，不经角色人格/Life日程，QQ不可用可用邮件；不猜收件人。
- 按incident聚合、依赖根因抑制下游重复、严重升级、恢复通知；管理员可看完整受影响清单。
- 通知有outbox、稳定ID、重试与发送回执；返回未知保留可能重复/未达语义，SMTP接受不等于收件人阅读。
- DB/模型服务故障走宿主最小持久spool和确定性通知，恢复后按同一incident身份导回；Agent本身不成为单点。
- 有限重启只作用于登记的本产品角色；共享host/NAS断电需外部探测才可即时通知，此能力不冒称已具备。

删除或替换：

- 不向每条报错发一封邮件，不让通知失败再次触发无限Agent调用。
- 不以运维Agent身份授予任意主机shell、docker socket或跨产品重启权限。

最小有效验收（本轮未运行）：

- 主业务容器停止时独立检测仍能形成告警；邮件/QQ一条通道失败不阻塞其他业务。
- 同故障重复事件不刷屏；复杂故障包含影响、已试动作、未完成项和建议，恢复由实际业务观察确认。
- DB不可用时不丢通知身份；恢复后不重复创建incident；通知缺配置明确显示。
- 不触碰独立媒体数据和其他NAS服务；运维调用失败不会阻塞常规工作。
- 守护重启确认实际owner与部署代际，迟到旧owner不能提交；故障链聚合不因重复重启变成无限新事件。
- 聚合health降级不自动等同整容器需重启；守护依据具体角色/依赖和登记动作处理，后台或日志失败不使可用API/QQ/独立模块被连带重启。

## RB-19 现有页面装配、运维配置与业务解释

- 状态：planned；职责：F；优先级：P1。
- 领域协作：M、C、D、L、X、B、O；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-07、RB-09、RB-10、RB-11、RB-12、RB-13、RB-14、RB-15、RB-16、RB-17、RB-18、RB-23、RB-24、RB-25、RB-26。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：让用户直接看懂正在做什么、为什么等待和是否真的完成。
- 功能清单：FI-01、FI-05、FI-06、FI-07、FI-08、FI-09、FI-10、FI-11、FI-12、FI-14、FI-15、FI-16、FI-17、FI-18、FI-19、FI-20、FI-21、FI-22。
- 数据变化：轻量只读投影、计数和实际取材清单；JSON完整错误详情按需读。
- 历史行为：PF-04。
- 当前问题：UF-01、UF-04、UF-06、UF-07、SF-05、SF-07。
- 本轮需求：FR-03、FR-04、FR-05。
- 衔接补充：CP-02、CP-06。
- 原规划：SD-19、SD-20、ML-04、ML-15、ML-16、LE-05、LE-06。
- 完整盘点补全：RI-01、RI-02、RI-07、RI-10（审计第16节）。
- 两窗口逐项结论：CW-04、CW-10、CW-14、CW-16、CW-18（见审计第15节）。

复用/修改入口：

- [web/src](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/web/src)
- [web/tests](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/web/tests)
- [src/tianshu/life/module.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/life/module.py)
- [src/tianshu/companion/module.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/companion/module.py)
- [src/tianshu/models/management.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/models/management.py)
- [src/tianshu/runtime/diagnostics.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/diagnostics.py)
- [deploy/core/observability/alerts.yaml](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy/core/observability/alerts.yaml)

交付：

- 保留 807 页面体验，分别显示角色状态、待做事情、后台进度、日历承诺和实际产物
- Memory/Growth 显示具体来源、当前规则、撤回/修订与实际选中材料；复用既有操作入口
- 计量进入各域实现，本卡整合：accepted→parsed→applied→expression→delivered，不把模型/队列成功混用
- 日程查询加阶段计时，据实际瓶颈批量证明、投影和分页，不在读请求里运行模型
- 把独立日志页和统计页接入现有导航；显示运维事件、指定诊断模型/备用路由、管理员或邮箱配置、最近恢复结果。
- 首日空态、部分就绪与旧数据代际提示明确；旧浏览器保留的对象/命令不能被自动重发成新动作。
- 沿用成长/聊天/群组页面展示最终取材清单及省略原因、规则历史/修订/纠正/撤回/恢复；区分入上下文、可观察回复行为、明确用户反馈，历史未记录不等于零选择，不把无投诉当好评。
- 语音保持用户未绑定状态，不擅自选择模型/声线；NAS采集partial逐项显示缺失命令/权限/连接原因，受影响采集路径修复或明确外部前提，不以核心ready掩盖。
- 对feature_inventory全部现有入口完成新版字段装配，包含创作/共读、小屋服装、工作台、群画像、资料/项目/家庭等；旧书签、窄屏、空态和原编辑操作保留。列表读取使用薄投影/分页，详情按需读正文。
- 显示已接收未整理、必要上下文缺失、候选省略、已应用未索引、当前规则已遵守但持久修订未完成等不同状态，避免以一个complete覆盖所有含义。

删除或替换：

- 删除只展示后台 running 不解释等待原因的状态；不建第二监控面板或强制逐轮提醒

最小有效验收（本轮未运行）：

- UF-06 能提供可解释前后对照，展示当前读取结果而非旧缓存掩盖问题
- UF-07 两个 Growth 阻塞位置均可见；重试/未知/失败年龄和无样本明确
- 页面操作调用原领域命令；不存在 UI 看似撤回、运行仍注入的分裂
- 规则细节及来源按当前权限读取；历史无清单显示未记录，零个性化显示实际空选择；未绑定语音与NAS partial分别呈现，不强行配置。

## RB-20 空库基线、交付包与切换步骤

- 状态：planned；职责：I；优先级：P1。
- 领域协作：R、S、X、B；路径按共享文件归属单写，owner为牵头者。
- 完成依赖：RB-02、RB-03、RB-04、RB-05、RB-06、RB-07、RB-10、RB-11、RB-15、RB-16、RB-17、RB-18、RB-19、RB-22、RB-23、RB-24、RB-25、RB-26。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：利用可丢弃旧业务数据删除兼容负担，形成可执行的新安装路径。
- 功能清单：FI-01、FI-05、FI-06、FI-11、FI-13、FI-14、FI-15、FI-16、FI-17、FI-18、FI-19。
- 数据变化：新基线 schema 与安装纪元；最少防重放元数据属于外部效果边界，不恢复旧业务历史。
- 历史行为：PF-13。
- 当前问题：SF-08。
- 本轮需求：FR-06。
- 衔接补充：CP-01、CP-02、CP-06。
- 原规划：SD-21、ML-16。
- 完整盘点补全：RI-06、RI-07（审计第16节）。
- 两窗口逐项结论：CW-01、CW-09（见审计第15节）。

复用/修改入口：

- [deploy](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/deploy)
- [src/tianshu/runtime/migrations.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/migrations.py)
- [tests/deployment](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/deployment)
- [docs/development](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development)
- [src/tianshu/runtime/credentials.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/credentials.py)
- [src/tianshu/capabilities/schema.sql](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/capabilities/schema.sql)
- [src/tianshu/identity/schema.sql](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/identity/schema.sql)

交付：

- 新空库压平 schema；替代路径随所属卡清理，最终删除不再有调用方的旧迁移/适配/一次性修补
- 配置种子按明确白名单保存作者人格、QQ绑定、供应商、工作流等；支持用户选择全部新建
- 切换步骤记录旧写者停用、外部消息 cutover cursor/epoch、防重放信息与未完成外部任务句柄处理
- 发布后新产生数据要有兼容回退；回退不直接用旧备份覆盖新版合法数据，不动其他 NAS 服务
- BOT/媒体/运维/日志的持久目录与共享schema引用有明确保留表；六类角色的启动参数及宿主兜底按同一交付包装配。
- 基线重建限定BOT业务域；保留共享引用闭包、密文凭据及对应部署主密钥、作者配置、媒体文件。文件处置按实际归属和引用列明，不整目录删除。
- 新版以后使用正常增量迁移保留新业务数据；升级兼容任务payload和应用接口，发布代际、数据代际和业务revision分别处理。
- BOT派生缓存和当前数据视图纳入数据代际；保留独立媒体缓存/订阅进度。旧上游会话、过时浏览器命令和自动历史回填不能重新污染新BOT。
- 配置种子具体列出人格/角色绑定、作息/联系偏好、冻结策略、小屋/服装作者选择及其仍被引用的文件、声音/图像工作流、权限与连接；自动习惯/旧日程/旧情绪和旧数值不冒充作者配置。
- 保留独立项目/知识/资产、家庭设备/基础设施/游戏、天气、插件技能、外部模型代理及其真实共享依赖；BOT数据按业务归属清理，不能只保护媒体几张表。

删除或替换：

- 取消 ML-14 旧缺口补偿、旧库变形迁移和永久兼容层；没有删除其他服务/共享媒体的授权

最小有效验收（本轮未运行）：

- 空库启动能走通有效新输入；导入配置不重放旧消息/发送/图像任务
- 空库仅覆盖 BOT 业务域；视频/音乐/图书模块的配置、订阅、任务、内容及其引用的共享身份/来源/回执/队列记录保留。共用物理库时不得整库清除。
- 旧程序不能与新程序共同写生产；无法取得可靠渠道切换游标时必须给出等价的最小已接收身份方案
- 回退和保留清单具体到该产品资源；这张卡本轮仍不执行删除或部署
- 混合BOT/媒体数据的切换样例中BOT业务域为空、媒体任务仍以原身份推进、保留凭据可解密；不得整体清理共享schema/Procrastinate队列。
- 首次重建后产生合法新数据，再做受影响域普通升级与兼容回退，数据不被清零或旧快照覆盖。
- BOT新装未提供vector_sql_seconds及使用示例种子时均沿RB-09加载2.0秒；旧生产已调2秒不算新装默认已修。

## RB-21 完整装配、旧错验证与同质量对照

- 状态：planned；职责：I；优先级：P1。
- 完成依赖：RB-01、RB-02、RB-03、RB-04、RB-05、RB-06、RB-07、RB-08、RB-09、RB-10、RB-11、RB-12、RB-13、RB-14、RB-15、RB-16、RB-17、RB-18、RB-19、RB-20、RB-22、RB-23、RB-24、RB-25、RB-26。
- 可准备依赖：RB-01；不等于提前完成。
- 目标：证明新链路在保留功能和语义质量下收敛，按实际证据结束。
- 数据变化：集中装配与交付记录；新生产操作须进入实际授权范围，本轮全部为计划。
- 历史行为：公共合同。
- 当前问题：UF-06、UF-07、NF-01、NF-02、NF-06。
- 本轮需求：FR-01、FR-06。
- 衔接补充：CP-01、CP-02、CP-06。
- 原规划：SD-20、SD-22、ML-04、ML-15、ML-16、LE-05、LE-06。
- 完整盘点补全：RI-09（审计第16节）。
- 两窗口逐项结论：CW-08、CW-09、CW-10、CW-13、CW-16、CW-19、CW-20（见审计第15节）。

复用/修改入口：

- [src/tianshu/runtime/product.py](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/src/tianshu/runtime/product.py)
- [tests/integration](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/integration)
- [tests/deployment](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/tests/deployment)
- [docs/development](C:/Users/Administrator/.codex/worktrees/frontend-807/tianshu-peiban-bot/docs/development)

交付：

- 逐项接线并审核23条历史行为、22组问题及两窗口结论的卡级结果；已通过且输入未变的定向结果直接复用，不把计划映射当验证通过。
- 补装配才出现的代表性跨域场景：混合媒体入站、一次撤回、一次重启/迟到结果、同角色并行阅读与聊天
- 同材料规模测非模型耗时、SQL往返、候选质量、输入用量、重试开销和业务覆盖；不得只报变快
- 交付清楚区分本地、已集成、部署健康和真实自然使用；未配置、无样本、未知成本如实列出
- 集中验证材料隔离、依赖冷却、备用路由不兼容、单consumer退出、Loki不可达、Agent自身失败、通知去重、同时间戳分页与统计迟到修正；不用生产注入故障。
- 承接LE-06：用相同人格/输入/模型和相近预算，先比较原实现与核心选择修正，再在相关场景单独比较有无表达样例、有无群词义；不默认跑全组合或设样本量门槛。保留错误套用、口癖、指代和零个性化反例，分别报告贡献与未证明项。
- 记录取材耗时、查询数、最终材料估计token、实际usage、额外辅助调用及失败重试。机械边界用确定性用例，表达收益用对应代表样例；未经实际模型/自然样本验证只声明功能实现，不能宣称学习效果改善。
- 承接ML-16/SD-20：原24故事仅为已知回归；新增必要场景按故事及改写家族隔离，使用过的材料不再称盲保留集。按实际适用机会计必要证据遗漏、无关个性化与纠正生效，保留原文补答和未决，不以总消息数稀释失败。
- 性能对照固定实际版本/模型/数据规模/权限/质量，纳入失败、取消、重试、unknown与全量长尾；分别评估核心选择、样例/词义，以及实际启用的重排/缓存优化，不预设节省率、不重复全组合。

删除或替换：

- 不按旧目录全部跑一遍；不新增 SQLite/PG 双矩阵、固定20次探针、长期观察天数门禁或新审批链

最小有效验收（本轮未运行）：

- 所有必要行为有当前实现证据；旧用例不能因新结构不适配而删掉其语义断言
- 已确认模块缺陷关闭后停止该范围反复测试；无证据的供应商内部原因和长期质量不冒称解决
- 真实发送/媒体/学习效果仅凭对应实际样本验收，不用健康和空队列代替
- 六项CP衔接要求逐项有负责卡证据：保留引用闭包、一次性清空与未来升级、六角色资源、嵌入空间、恢复链预算、空库首日；只补装配新增差异。
- 两来源窗口的主张均有对应实现/必要检查或明确替代原因；旧BOT存量恢复因本次弃旧数据取消，但旧故障形状以脱敏合成夹具保留，不能把清库当成修复通过。
- 完整来源盘点的10组修订及22类功能均有当前实现证据或明确外部未验范围；作用于同代码状态的既有有效检查直接复用，仅补受影响跨域断点。

