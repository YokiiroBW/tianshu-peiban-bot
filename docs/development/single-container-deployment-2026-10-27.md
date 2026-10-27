# 单容器 Linux 构建与 230 实机发布

用户本轮明确要求：先修复日志批量写入退步，再构建 Linux 镜像，在实际机器验证功能、异步联动与资源占用；保留现有配置和数据，验证通过后再切换旧服务。目标明确为 **192.168.31.230**。这覆盖本批先前仅本地、不部署的阶段限制。

协调者为唯一远端写者和生产切换负责人。复用现有严格 host key/专属 SSH 密钥及 `/srv/tianshu-tooling` 的维护、冷备份和恢复能力，不改变其他服务、QQ 账号配置或模型额度。原根工作区的混合修改和暂存保持。

| 卡 | 责任 | 范围和完成条件 |
| --- | --- | --- |
| TS-347 | consolidation_observability | 日志批摘要写入；保留去重、完整内容、事务、汇总、补传与保留期；旧版/退步版/修复版同条件 bulk 和真实 scan 对照，相关迁移和回归通过后交协调者验收。 |
| TS-348 | service_rationale | Linux 构建兼容；固定现有基础镜像和共享 wheel；修正本地 config ID 引用、离线 Dockerfile 与二进制兼容检查；不自行远端构建或部署。 |
| TS-349 | 协调者统筹 | 复用原配置/备份/守护，准备隔离验收、异步链路和资源实测及切换/回退；实际远端动作只由协调者串行执行。 |

## 当前状态：Linux 候选通过，生产尚未切换

截至本次 2026-10-28 文档更新，已在230完成真实 Linux R6 镜像构建、隔离完整候选及受影响日志功能验收。候选已正常退出，旧13个容器仍运行，配置、业务数据和已有冷备保留；**尚未清空生产运行日志、尚未切换生产**。正式切换结果由协调者后补，不能把隔离副本 ready 写成生产已完成。

R6 镜像为 `sha256:b10d4e38f5cd32cf159309857705f9a7b3d3e2ab45816444e01377903b32b784`，linux/amd64，实际大小 **3376075371 bytes（3.376 GB）**。application 为 `a76f268a3bf22785f971120cafa70ea82618f592`，共享环境使用66个冻结 Linux wheel；完整产品提交和字节哈希以该上下文 receipt 为准，不再把早期 TS-340 的“仅准备上下文”当当前构建状态。本地 image ID 仍不是可拉取的 registry manifest digest。

只读盘点已确认：230 为 Docker 26.1.4 / Compose 2.28.1 / linux amd64，13 个旧容器仍运行，容量守护管理十核心/观测服务且 ready。五个现有基础镜像 ID 与前期快照一致。机器无可用 buildx；使用现有 Docker legacy builder 并明确记录，不安装新 builder 或调用外部 Dockerfile frontend。

## 已有真实验收证据

| 项目 | 已验证范围 |
| --- | --- |
| Linux 构建 | Docker26.1.4 legacy builder、build network none；R6实际镜像 ID/架构/大小已读取，不是仅执行 binary --version。 |
| 完整候选 | 隔离副本中25库检查通过；Platform/Gateway/Memory/Knowledge/Companion/OneBot六 owner 全部 ready，业务宿主、五观测进程及漫画 inputs/publisher 均 ready。 |
| 观测原功能 | 原生 Vector→Loki→guard持久账本→Prometheus/Grafana查询及原15条告警真实运行。既有成功结果仅在代码/配置未受影响范围复用，未把每轮计数相加或冒称所有功能均已逐项重验。 |
| 本次日志策略 | R6真实五引擎 `log-retention` 全场景通过：五源合计10000、30天配置、实际ACK/原字节查询、窗口内聚合、自动淘汰、writer手动清空含pending、重启后旧raw/Vector缓冲不回灌及新日志可查；原生子进程均正常收口。 |
| 写入故障恢复 | R6 Companion/Memory真实chmod拒绝及清段后原生容量恢复通过（3.624s）；Knowledge复用同原生sink并验证真实持久记录，不冒称另做了一套独立压力测试。 |
| 候选退出 | 候选exit0、所属验收helper关闭，旧13仍运行，`production_logs_deleted=false`。 |

安全摘要保存在协调工作树 `.runtime/unified-deploy-20261027/`：`linux-build-r6.json`、`context-r7-final.json`、`full-candidate-status-r6.json`、`log-retention-status-r2.json`、`producer-recovery-r2.json`、`candidate-closed-r2.json`。它们为本次实际状态依据；私有配置、凭据和真实业务内容不入本文。原生观测历史失败（包括旧容量场景第六条查询缺失）保留在原记录，新的通过不覆盖失败证据。

## 用户的日志保留设置

默认五个业务来源**合计最多10000个逻辑运行日志事件、保留30天**，数量或天数任一先到，自动退役最旧的可安全退役前缀。主设置为guard的 `log_max_events` 与 `retention_days`，生成器同步账本、Loki及本地告警历史；改后重新导出并重启guard/Loki。原15条告警保留，正常满额不触发容量故障告警。

自动清理保护未补传、未确认和真实缺号；在线手动clear按明确授权销毁cutoff以前的旧运行日志，**包括旧pending**。持久cutoff使旧raw、未扫描数据及Vector旧缓冲之后重放也不会回到用户保有窗口。原query/metrics角色不获删除权；容器内管理CLI读取现guard配置引用的writer凭据。接口及命令见 [运行日志保留说明](../../deploy/observability/RETENTION.md) 和 [TS-363](../handoffs/TS-363.md)。聊天、记忆、日程、业务收据和配置不适用此清理策略。

10000是逻辑保有上限，不是磁盘所有副本的瞬时条数。原始活动分段、未补传内容、Vector持久buffer以及Loki WAL/chunk/cache另列字节用量；Loki删除请求、取消窗口和Compactor延迟意味着底层磁盘异步回收。只清已证明封段/退出实例且获确认或明确clear授权的原件，不为凑条数删除活动段。此次通过包含受控synthetic age调整，**没有30天实跑、物理ENOSPC或“磁盘立即只剩一万条”结论**。

## 资源实测与访问入口

默认新安装网页HTTP端口18446、外部TLS API端口18443；OneBot启用时18765。保留原WebAccess的安装继续以捕获的实际绑定/Origin为准，观测鉴权入口仍仅发布主机环回。隔离候选使用其独立端口，不代表原生产入口已切换。

两个各60秒的独立cgroup采样窗口中，旧13容器总working set中位为 **1.737 GiB**，R6单容器候选为 **0.923 GiB**；对应anonymous中位为1.280/0.802 GiB。working set口径为memory.current减inactive_file，不能与10GiB硬限额混同。原值分别见 `resource-baseline-status-r1.json`、`resource-candidate-r1.json`。

两窗口发生时间、业务状态和负载不同，未发送同一套对照业务流量；此结果是观测样本，不能据此承诺固定节省比例或吞吐提升。单核口径CPU样本分别183.04%/50.26%亦不能作同负载速度对比。TS-347真实Linux账本对照保留raw SQL bulk较旧版中位+3.79%的不利结果；真实scan/append恢复，不声称所有路径全面提速。

## 待生产切换记录

下一阶段沿原维护流程停写、取得**最新**完整冷备，再清已核独占旧运行日志目录并切换唯一新写者；保留旧容器、镜像、配置和冷备以便回退。用户已授权清空此前运行日志，不能扩大到聊天/记忆/日程/其他服务数据。

| 生产动作 | 当前记录 |
| --- | --- |
| 旧13停写及最终冷备 | 待协调者执行后填写时间、receipt和范围。 |
| 旧运行日志清空 | 尚未执行；仅已核独占日志状态目录，业务/config保持。 |
| 正式统一服务与容量守护启用 | 尚未切换；待填写正式image/config generation及ready证据。 |
| 正式HTTP/TLS/OneBot/漫画与日志核验 | 待切换后填真实结果，不以本候选记录替代。 |
| 正式资源与回退状态 | 待同实际运行状态采样并补充；旧资源未由本文授权删除。 |
