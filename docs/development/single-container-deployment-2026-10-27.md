# 单容器 Linux 构建与 .230 正式发布

2026-10-28，天枢已在 **192.168.31.230** 切换到 R7 正式单容器，01:00:14（UTC+8）启动。镜像为 `sha256:d72f5febd4945ca5db8e51e2869f5720ab1ee86250ba76ede62b953bc3e1069b`，linux/amd64，实际3376079257 bytes（3.376GB），66个冻结Linux wheel、一个共享Python环境。原13容器已正常停机并关闭自动重启，保留用于有备份支撑的恢复；当前只有统一服务作为运行写者。

正式容器 healthy，runtime与business-host均ready；Platform/Gateway/Memory/Knowledge/Companion/OneBot六模块全部ready，`diagnostics.complete=true`。五个原生观测进程ready，漫画inputs/publisher按原插件与UID运行。新容量守护generation r2已armed、unit active/enabled。这些是正式机器回执，不再是“尚未提供Linux镜像”或“仅候选通过”。逐项验收结果见下表。

默认新安装网页HTTP18446、外部TLS API18443，OneBot启用时18765；保留原WebAccess的绑定/Origin及原token，观测鉴权入口仍仅主机环回发布。发行是一张镜像、一份安装目录、一个Compose服务；业务内部使用显式模块交换，保留的观测二进制及插件仍由统一生命周期管理，不把容器数量等同进程数量。

## 数据保留与按授权清旧日志

旧13停写后取得并核验最新完整冷备：25个逻辑存储源、3个guard，39个SQLite数据库及1个辅助SQLite均列入相应快照/验证记录；原配置、凭据引用、账号/人格、业务数据、媒体外部bind保持。冷备manifest SHA256为 `bf22d5ad6e4079f370e5bf412001e570978007cf1721388e13bbd4665c54ee8c`。

随后按用户“之前运行日志全部清空”的授权，于2026-10-28 **00:20:09（UTC+8）**精确清理10个已核独占运行日志目录：五业务raw、guard日志账本/告警历史、Vector本地位点/外部buffer、Loki本地日志状态/外部chunks。共清除3091文件、203目录、899542145 bytes；未删除业务数据、聊天、记忆、日程、配置或其他服务数据。冷备保留原日志，故该字节数不是NAS总盘即时净回收量，亦不计作业务数据精简。之后首次启动故障及修复过程的新诊断记录保留；R7成功不覆盖旧失败，也没有再清空测试期间的新故障证据。

默认五来源合计保有最多**10000个逻辑运行日志事件、30天**，数量或天数任一先到，自动退役最旧可安全退役前缀。主设置 `log_max_events` / `retention_days` 由原生成器一次投影；修改后重新导出并重启guard/Loki。原15条告警保留，正常满额不触发容量故障。

自动淘汰保护未补传、未确认和真实缺号。在线管理CLI复用writer凭据执行clear，明确销毁cutoff以前的旧日志含pending；query/metrics仍只读。持久cutoff保证旧raw、未扫位点与Vector旧buffer后续重放不回灌，之后新日志仍可入账检索。详情见[日志保留说明](../../deploy/observability/RETENTION.md)与[TS-363](../handoffs/TS-363.md)。

10000是逻辑窗口，活动原始分段、未补传内容、Vector持久buffer、Loki WAL/chunk/cache仍有各自字节用量。Loki删除API、取消窗口和Compactor异步删除不会使所有磁盘副本瞬时只剩10000条。本批真实五引擎验收验证过自动/手动/重启重放；受控synthetic age用于过期验证，没有30天实跑或物理ENOSPC结论。

## 正式首次启动的两项故障与修复

| 故障 | 已证原因与集中修复 |
| --- | --- |
| TS-378：新容量守护判部署树无效而停服务 | Linux Python3.12原生venv创建合法`lib64 -> lib`。旧metadata扫描把这一标准alias拒绝；仅unified-one对固定插件venv路径、64hex包目录、UID/GID10001、原文target=lib且真实lib目录作精确纳管，只计link自身，不follow/double scan，不放宽其他symlink。以新generation r2收口，保留r1故障/latch证据。 |
| TS-379：Gateway续期等平台线程而bootstrap超时 | Music maintenance计划长持原media事务，另三线程等同advisory lock，耗尽LocalWork四线程；是实际后台池饥饿，无死锁证据。媒体Background可信context标后台，单次同步操作先独立限3，再入原容量16；总线程仍4，给未标记交互留1。permit直到真实future完成才归还，取消/close/drain保持真实结算；不改3秒期限、不关后台、不弱化鉴权或Store锁，不夹带N+1 SQL优化。 |

R7只更换Platform为 `d9c1452c01f30350b2861afd3458e157a6a863af`；其余65个wheel与application `a76f268a3bf22785f971120cafa70ea82618f592`保持。原始诊断source SHA256 `f70f15522063c92ad82cc99361a43e159d5eebd94e57de194d09363f87ecd74d`未变，vocabulary按新commit更新。发行前再次续期沿原owner/entry/scope，没有新增权限。

R7已安装wheel的32项线程/后台/真实TLS鉴权续期与snapshot测试通过（6.812s），没有源码叠加；旧失败记录保留，未把修改前后多轮节点相加。根负责所有远端动作，本说明根据其回执整理，未另行执行生产命令。

## 正式验收与资源记录

| 项目 | 当前证据/结果 |
| --- | --- |
| 正式进程/模块 | healthy、runtime/host ready、六模块ready、完整五UUID、观测五引擎ready。 |
| TS361公开/内部入口 | 正式现存app只读检查及host外层入口补证通过：HTTP18446=200、TLS18443匿名401（严格CA/hostname核验）、OneBot18765=200、Platform原鉴权及漫画固定alias通过；没有另启第二套app。工具原状态名仍含candidate，实际目标为正式R7，不能据此称已验证登录浏览器全流程。 |
| 正式日志 | 原生guard/query已核10000/30天；初始样本2000逻辑事件均在清旧后入账，用户小时聚合2000，当时pending4。后续边界采样3231事件全部verified>0、pending0，覆盖五来源；两次时点分列，不写成最初2000已全部确认或永久无积压。 |
| Actor namespace | TS361原24逻辑库加独立actor-only核查共25，actor checkpoint matches；没有修改actor权限或数据来凑通过。 |
| 最终readiness/守护 | 启动约500秒后Gateway仍ready、diagcomplete，跨过原300秒租期；旧13cold/restart=no，新unit active/enabled、legacy unit disabled，新guard heartbeat healthy。 |
| 正式60秒资源 | 无合成流量，working set中位985919488B（0.9182GiB）、anonymous中位922439680B（0.8591GiB）、pids中位269，单核口径CPU79.5667%。 |
| 最终收口 | R7正式启动及上述只读/边界/actor/日志/生命周期验收通过；未将healthy替代未执行的真实QQ/模型或带session浏览器业务。 |

先前两个各60秒的独立窗口，旧13总working set中位1.737GiB、R6候选0.923GiB；本次R7正式为0.9182GiB。三者时间/业务负载不同，均未发送同一套合成对照流量，不能作同负载性能承诺或固定节省率。memory.current减inactive_file与anonymous分列，不能把10GiB限额当使用量；CPU只按计数差与核数口径报告。TS-347真实Linuxraw SQL bulk较旧版中位+3.79%的不利记录仍保留，不能声称所有路径均提速。

实际摘要位于协调工作树 `.runtime/unified-deploy-20261027/`：`linux-build-r7.json`、`production-start-r7.json`、`production-status-r7-r1.json`、`cold-phase-r19.json`、`production-log-reset-r2.json`、`production-ts361-r7.json`、`production-retention-query-r7.json`、`installed-work-linux-r7-r2.json`、`production-actor-r7-r2.json`、`production-boundary-r7-r3.json`、`production-lifecycle-r7.json`（内含正式`resource-production-r1`）、最终`production-release-r7.json`；完整安全摘要由协调者归档，不在文档写入凭据或业务正文。

补证harness的失败原样保留：actor-r1因rglob超时，未改产品；boundary-r1/r2误把verified时间戳按等于1判定，纠正为大于0后通过，未改日志/runtime来凑结果。这些不计作新的产品故障或新功能通过数。


本次验收副本清理已完成：现场核对全部容器bind及在途进程引用后，仅删除14个独占构建/克隆/合成目录或归档文件及R1–R5五张中间镜像；原失败报告、R6复现上下文、R7发行上下文、当前安装、旧13和完整冷备保留。该窗口`/srv`文件系统可用空间从35324542976B增至42860298240B（约增加7.54GB，包含并行系统写入影响），不以共享镜像虚拟大小相加推算释放。清理后正式容器仍healthy，记录为`scratch-cleanup-r7.json`。

## 范围与恢复边界

健康/只读验收不等于已发送真实QQ消息、调用真实模型、完成下载/导入/发布或走通带用户会话的跨模块浏览器代理。匿名Memory/Knowledge/Companion读者仍要求session；OneBot外部QQ离线可以是未连接状态，不能把listener alive写成真实账号已收发。其他设备、AssetLibrary/Chat等独立服务不因本次打包获得新权限或被改动。

旧13容器保持cold及restart=no，旧镜像、配置和最新冷备保留；**不能承诺无损一键重启旧服务**。回退须先停新写者，核验并保全切换后新增事实，再按匹配冷备恢复数据、配置、镜像和守护generation；冷备只覆盖备份时点，之后新增数据的处理需据实际状态决定。不能让旧/新实例同时写共享数据，也不能把仍留旧容器当作已完成回退。
