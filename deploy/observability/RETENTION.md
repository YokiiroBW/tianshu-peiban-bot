# 运行日志保留与清理（TS-363）

仅处理天枢运行日志。聊天、记忆、日程、业务收据、插件业务数据和配置不属于清理范围。

## 同一份设置

现有observability设置增加 `retention_days`（默认30，正整数）及 `log_max_events`（默认10000，正整数）。四产品和独立Knowledge**合计**使用一个上限，天数或数量任一先到即退役最旧的可安全退役日志。原 `configure.py --settings ...` 一次生成guard/Loki；单容器 `make_installation(..., retention_days=..., log_max_events=...)` / 导出将同值投影，唯一真值仍在guard设置，`ledger_max_events`与数量同步。

改设置后重新导出并重启guard/Loki；不宣称热重载。`retention.apply_retention`只改策略字段，保留捕获的TLS、路径、身份和缓冲。原15条规则保持；已捕获Grafana provisioning只更新 `LedgerCapacity` 的A表达式，不能重建其余自定义规则。

| 层 | 保留/清理行为 |
|---|---|
| 用户查询 | guard限制每个LogQL日志selector到统一保有窗口，原看板聚合也在窗口内执行 |
| 账本 | 当前事件最多10000，包括为待确认事件预留的位置；自动只退役已确认且有原字节证明的连续前缀 |
| 原始文件 | 原生产者64MiB分段；只回收已封段、完整内容已确认或被明确手动清理授权的文件 |
| Vector | 原4GiB持久buffer、满时block、checkpoint、ack和补传保持；没有buffer TTL |
| Loki | 按采集时间执行所选天数的Compactor保留；数量/手动淘汰通过实际delete API，磁盘异步回收 |
| alerts.sqlite | 同一天数及原10000条历史转换上限；当前告警状态不删 |
| Prometheus | 原独立30天/2GB指标策略，不混算成运行日志事件 |
| Docker stdout | 原local缓存10m×3；OneBot/comics业务收据和DB不套日志TTL |

10000指**逻辑事件保有窗口**，不是所有磁盘副本的瞬时条数。同event_id重放仍是同一逻辑事件，原重复/冲突告警不抹除。活动段、未补传内容、Vector buffer及Loki WAL/chunk/cache另属暂存/存储副本，由source/Vector/storage字节指标单列。正常每个活动源可保留当前一个64MiB段；未确认、坏记录或I/O故障会额外保留原件并报警，不能声称磁盘也瞬时只含10000条。

## 自动与手动

自动按全局最旧的可退役前缀排序，保护未补传、未确认和真实缺号；先释放旧确认记录，再按实际空位纳入新事件。全部pending或缺号阻挡时保留原件并报积压/容量异常。扫描入口随实际消费进度在各来源之间轮转、持久提交，繁忙Platform不能永久饿死其余来源。

正常满额不报警。`LedgerCapacity`原UID/持续时间保持，只对本轮无法腾位的拒绝、源回收I/O失败或Loki删除失败报警；历史累计异常继续保留。

手动`clear`明确销毁cutoff以前的运行日志，**包含旧pending**。原query/metrics仍无删除权限；容器内管理CLI用原配置引用的writer凭据：

```text
docker exec <天枢容器> /opt/tianshu-venv/bin/python -B /opt/tianshu/application/deploy/observability/manage_logs.py clear --settings /config/observability/guard.json
```

默认内部地址 `https://obs-guard:8445`；旧独立部署显式加 `--guard-url https://obs-guard:8443`。凭据不放命令或返回值。202/CLI返回持久cutoff及当时已知事件/pending数量，表示已接受，磁盘回收仍异步。

cutoff重启后有效：旧账本记录退役；原raw、未扫描位点或Vector旧buffer后来重放的旧时间事件只消费、不回到保有窗口；cutoff之后的新日志继续入账。cutoff使用生产者事件时间及当时已落账事实，错误/未来时钟不属于任意历史恢复保证。

首次“全部旧运行日志清空”由部署协调者停所有writer、最新冷备后操作已核独占目录，包括raw、guard、Loki日志状态、Vector队列/位点。在线API不冒充跨进程停写证明；旧冷备保留供明确回退，不再挂回新运行窗口。

## 数据证明与恢复

v3增加 `retired_streams`水位和`source_receipts`原字节前缀收据。收据绑定source/basename、文件身份、准确长度、完整SHA256及压缩序号范围。自动收据须逐事件匹配原hash且已确认；手动clear收据明确源自销毁授权。活动文件只证明已扫描前缀，**不unlink活动文件**；扩展时先验证旧前缀SHA，再核新尾部。原32字节SHA-256不截断。

封段证明复用实际append-only生产者的更高编号段；旧UUID尾段另需原business-host state：version1、status=ready、diagnostics.complete=true、五个真实UUID、存活PID、updated_at为0..120秒。partial/degraded/stopping/陈旧状态无效。Companion跨重启全目录编号递增，只删最高段之前的段；其他四源可由完整新实例表证明旧UUID已结束。生产者必须唯一实例且不重开旧封段；任意固定UUID的测试/维护writer不在此约定内。Windows本地不以PID证明尾段关闭；Linux目标使用真实进程存活检查。

先commit收据，再比较文件身份/长度/时间、unlink及同步目录。RO/I/O失败或中断保留原件/收据供重试。事件/位点/汇总/水位与轮转入口在原事务中更新，异常回滚。v0/v1/v2迁移保留原8字段，新增occurred/collected用于cutoff和精确Loki删除时间窗；不自动VACUUM，旧大文件释放的页只供后续复用，不立即缩小。

v4只为删除outbox增加`submitted_at`，升级事务保留v3的事件、hash、位点及退役水位。每个stream首批立即派发，后续退役意图持久合并五分钟再提交；正常以实际collection时间为本批下界，失败不推进submitted或时间，重启后继续重试。guard的逻辑保有窗口仍每轮更新，不等待物理派发。

新生成的Loki配置使用原生SQLite删除请求主库及BoltDB双写备库。首次空SQLite由Loki导入原BoltDB，请求完成并经过原索引传播等待后退出查询过滤；备库同步新增、撤销及完成状态，不替代不可变冷备。取消期设为1分钟，实际处理另有1分钟竞态等待、Compactor周期和2小时chunk删除延迟。30天完整核对按24小时拆分，原10秒期限、hash、重复及冲突检查保持。已有安装通过正常发布应用这些参数；不能只换回v3镜像来读取v4账本。

持久outbox只派发本包固定tenant/stream/prefix，不接收调用者自选删除query。guard逻辑窗口不等待磁盘收缩；`index/stats`仍表示物理索引，不能拿来证明逻辑上限。2026-10-28正式修复、原生迁移/双写回退及历史追赶证据见[完整链路验收](../../docs/development/chain-closure-validation-2026-10-28.md)。

真实验证与未覆盖见 [TS-363](../../docs/handoffs/TS-363.md)。历史[回收提案](RECLAMATION-PROPOSAL.md)涉及更强冗余归档/跨世代恢复协议；本次不伪称建立了独立可恢复归档或永久完整历史。
