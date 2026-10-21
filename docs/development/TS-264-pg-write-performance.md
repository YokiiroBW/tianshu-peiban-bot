# TS-264 / TS-265：PG短查询、来源同步及写入优化

用户2026-10-21继续授权优化PG短查询、来源同步及写入，争取至少持平SQLite，并实际确认SQLite写入没有退步。保留权限、来源、撤回、完整组与持久性；不影响生产数据和媒体库。本聊天已有子代理并行授权及gpt-6.1-sol/max配置继续适用，不创建新聊天，不恢复已完成旧卡。

Memory基线为本地已集成c4a75d648635b2833c7bf71a74a1100e40688461，共享0.1.2。未集成Q17候选7a33c01及其他产品候选不混入本批。TS264是唯一Memory写者，独立worktree；范围为src/tianshu_memory、相关tests、docs/pg-read-performance.md、本号handoff。优先使用现有Store、SourceAuthority、Contracts和写入路径，针对实际往返、规划及重复校验准备做优化。规则准备可复用，当前文档/权限/来源检查不能省略或跨操作复用；任何缓存须绑定不可变合同版本并验证更换后的失效行为。共享包/版本/wheel/lock/vendor和根记录由协调者单写，如确需共享变更另分新卡。

事务必须保留：后端绑定、全部六次新鲜来源revision检查、失效先提交→revision比较→业务事务、当前schema、锁后独立新语句快照、guard-before-commit、CAS、完整组和原BM25语义。不得降低fsync、synchronous_commit、FULL/WAL或跳过真实提交取得性能。SQLite SQL路径不因PG专属优化改变；通用校验准备优化也必须证明每次仍检查当前输入。

TS265拥有独立workspace工作树内scripts/benchmarks/pg-read-performance、docs/development/pg-write-performance-2026-10-21*及本号handoff。复用既有公开夹具/合同/安装proof，对当前基线和新候选分别运行PG及SQLite三轮交替四组；短field/关键词/零预算/撤回、完整组提交、来源→候选→提交和完整SourceAuthority均保留。原始样本、异常值、p50/p95/p99、吞吐及未达标结果全部记录，不混前批分母。额外设计SQLite写入配对控制：两边由相同物理初态克隆、同schema/guard与持久性，通过原生API进行真实提交并验证提交后及重开状态；同一对交替先后，恢复准备/校验不计时，不以共享可变库轮流写入造成状态偏差。

主协调者独占NAS、凭据、测试数据库、镜像打包和生产足迹，其他子代理仅读安全合成结果。仅创建新UUID测试库、低权限角色、合成目录和有界客户端；不挂生产配置、业务数据或媒体，不访问Redis/真实模型/QQ。以本批测试前生产足迹为比较基线，不覆盖Q17已记录的独立配置变化。前后核对十核心服务生命周期/镜像/挂载、五TLS、配置hash、guard及非目标容器快照；最后清理本批专属资源。仅本地集成，不推送、部署或迁移生产。

TS269另分独立workspace工作树，唯一写共享packages/tianshu-storage及相关tests/storage、本号handoff。实际新UUID同NAS驱动微基准支持将checkout三项设置合并为单个参数化SELECT set_config，但每次checkout仍重新设置，不能缓存会话/权限状态。新增明确的read_batch查询能力，供TS264在来源快照CAS后、首次DML前批量发出独立原生SELECT，全部确认后才返回结果；不能混入DML、commit、锁取得之前的元数据快照或跨来源提交。原setup-only pipeline合同不扩大。prepare=False默认及executemany/Row路径保持，不为未采用的prepared微实验增加闲置接口。版本/wheel由协调者冻结0.1.3并仅装入本批Memory候选；其他产品不换pin。TS266–268属于另一个Q18批次，其Memory卡已明确等待TS264释放；不接管、修改或恢复这些卡。
