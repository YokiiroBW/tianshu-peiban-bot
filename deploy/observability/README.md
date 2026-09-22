# DEP-B 日志包

本包独占 Vector/Loki/Grafana/Prometheus/对账守卫的定义。只收四产品专属日志，不读取业务数据库、Docker socket 或宿主其他服务日志。运行代码仅依赖 Python 标准库；测试证书工具依赖 `cryptography==50.0.1`。

**当前是候选包。** Windows 原生 Vector 0.58.0、Loki 3.7.8 已完成真实 TLS 采集/中断补传验证；Linux Compose、Grafana实际装载、30天容量、物理满盘及NAS没有通过记录。应用没有安全回收合同，容量耗尽后仍拒绝新业务，不能宣布日常无限运行完成。另见 [集成接口](INTEGRATION.md)、[回收提案](RECLAMATION-PROPOSAL.md)、[故障验证](../../tests/deployment/observability/fault-probes.md)。

## 生成（不启动服务）

部署负责人准备独立部署根、四产品日志目录、合同**原字节**、TLS证书和四个独立随机凭据文件。设置文件只含路径引用，样例无默认密码。路径均为部署根内相对POSIX路径；输出必须是不存在的新目录。

```text
python deploy/observability/configure.py --deployment-root <显式部署根> --release-manifest <DEP-A清单> --settings <私有设置JSON> --output-relative observability --candidate
```

输出 `observability/compose.yaml`（JSON为YAML的合法子集）、配置、运行代码和五个独占持久目录。不会启动Docker、签生产证书、改变既有日志权限或操作NAS。`--candidate`明确允许未核digest的固定版本标签；不带该参数会因未验证镜像或未关闭发布门槛拒绝，不会升格成生产证明。`binding.json`清单hash是有标注的语义摘要，不冒充DEP-A原始文件hash。

五服务UID/GID均10001:10001，分别写自己的data子目录，TLS/凭据和产品日志只读可达。生成器不chown/chmod/提权。产品日志可保持0600，同UID读取；最终镜像与挂载权限由DEP-A联验。主机需给Loki、Vector和产品日志配置独占空间/配额；Compose内存限额不是磁盘配额。

固定版本官方来源及 `digest=null/unverified` 见 `versions.json`。原生二进制通过不等于Linux镜像digest/运行通过。更新TS104–106修复提交后必须重提词汇：

```text
python deploy/observability/snapshot.py --projects <只读Git项目根> --commits <四个完整提交JSON> --contract <diagnostics/v1> --output <新的vocabulary.json>
```

只从 `git show <commit>:<path>` 提取AST静态词汇，不import产品、不复制活跃工作区、不发布第二份共享schema。配置器要求发布清单repo/commit及合同hash与词汇快照完全一致。

本次最终快照已重绑协调验收后的platform fa85ee93、companion cf020fd3、memory 2f403762、gateway 51121e6c；完整提交见baseline-commits.json。与首轮日志测试词汇集合完全相同，合同原字节未变；这不表示本包运行过四产品的最终业务进程。

## 持久性和完整性

应用源头去敏持久文件 → Vector封闭校验 → 4GiB disk buffer（满时block）→ HTTPS分权守卫 → mTLS Loki单实例WAL/TSDB/filesystem → 经鉴权的Grafana。所有正常事件及DEBUG等注册级别均不采样；无sample/filter/dedupe变换。只索引固定stack/service/level标签；关联号、实例号、event_id留在JSON，不作高基数索引。

文件源从beginning读取、持久checkpoint，sink启用acknowledgements；当前文件与数字轮转段均覆盖。没有自动删文件、忽略老文件、截断或drop_newest。429/5xx/断线重试并回压。不可重试4xx、坏记录或过长行会拒绝并计数；源文件保留，对账发现缺口，修复后隔离重放，不能把拒绝称无损成功。Vector磁盘同步窗口及file source的best-effort边界不等于应用到检索绝对零丢失。

运输时间取采集时间，原始 `timestamp` 原样留在JSON，历史补传不直接受旧生产时间的乱序窗口限制。Loki保留720h从运输时间算，Grafana时间轴亦如此；按生产时间分析须解析JSON。超长停机或错误时钟仍需专门恢复，不能承诺任意积压自动恢复。

`monitor.py`增量读源，将event_id、服务/实例/序号及规范内容hash存本包SQLite，不存原始行。偏移、冲突、缺口、重复和未检索条数持久可见。每轮最多扫描20k行、核对128条，待确认事件按最近检查时间公平重试；保留窗口内已确认事件满一小时进入复查队列，中央数据丢失会重新变为待确认，不把历史查询成功永久当证明。队列调度和积压决定实际发现延迟，不承诺一小时内全量复核。正常换inode轮转计为替换观察，已消费同inode字节改写/截断才触发源改写告警。账本默认最多100万事件，接近上限告警，满后拒绝纳入且不删除旧证据。初始最大序号之前的缺口亦计入，监测应从新安装开始，残缺历史不能冒充全量。

CLI对合成夹具比较产生/落地/检索集合，核内容hash、序号、冲突及重复。查询达limit会按时间二分；单纳秒桶仍超limit、预算用完或查询失败均报未验证，不把前N条当全集。运行监测能比较落地与检索；**缺少应用产生水位，不能发现连源文件都未形成的尾部事件**，由产品落盘测试及未来封段合同补齐。

```text
python deploy/observability/reconcile.py --generated <合成期望JSONL> --roots <产品到日志目录JSON> --loki-url https://<查询端点> --ca <CA> --token-file <只读token文件> --start-ns <采集下界> --end-ns <采集上界> --output <报告JSON>
```

此命令用于独立合成实例/租户的完整时间段，不可拿生产任意截片与完整源目录比较。exit0只表示本次对账通过；所有报告固定 `application_reclamation_authorized=false`。

## 检索和告警

仅两个显式端口绑定宿主127.0.0.1：Grafana HTTPS和守卫HTTPS。Loki无宿主端口，只有守卫加入storage网络并持Loki客户端私钥。Grafana只有query_token，Vector只有writer_token，Prometheus只有metrics_token。守卫拒绝删除/配置/管理API，不接收客户端租户选择。Grafana禁匿名/自助注册，新用户Viewer；日志授权独立于产品业务账号，有日志查询权限者可查询本套四产品安全事件。

守卫每个准入槽有**10秒单调总期限**，覆盖TLS握手、未鉴权请求头、请求体、后端调用与响应写出；滴流不能重置期限。查询客户端同样以10秒约束完整请求，`range`所有分页共用同一10秒父预算及128次请求上限，不会每页续期；更短父期限会继续下传。超期关闭连接、释放槽，客户端不自动重发；一次写入超期可能属于后端已经受理但响应未送达的不确定状态，保留源并按事件身份对账。

收发采用非阻塞TLS和最多50ms取消观察间隔，不创建后台读线程。`LokiClient.close()`及守卫关闭可取消正在进行的网络工作。DNS的系统`getaddrinfo`不能被Python强制中断：每进程最多一个daemon解析线程、一个排队项，相同域名合并等待，缓存30秒/最多64项；调用方仍按总期限退出，解析卡住时新的未缓存请求超时或容量拒绝，线程不会无限增长，IP字面量无需DNS。这里约束网络等待；OS调度、TLS/JSON的有限CPU操作，以及挂死的本地文件系统调用不能承诺硬实时中断。验证报告将配置10秒和测试调度容差分列，容差不是增加运行预算。

自动配两数据源、全量看板、15条告警，覆盖失联/陈旧/查询失败/积压/序号缺口/非法或冲突源/检索冲突/Vector拒绝与错误/存储拒绝/应用预算/磁盘水位与预测/账本容量/扫描落后/缓冲占用。空数据和执行错误进入告警态。守卫还将固定名告警状态/转换落自己的 `alerts.sqlite`，仅这些告警转换保留30天/1万条，不删应用日志。坏记录与冲突为持久计数，修复前不会自动洗绿。

外部通知未配置和验收，UI/本地持久告警不证明人员已收到通知；全栈停止亦需栈外探活。通知和探活由部署负责人配受控目的地后验收，本包不发送真实消息。

```logql
{stack="tianshu",service="tianshu_companion"} | json
{stack="tianshu"} | json | correlation_id="<32位关联号>"
{stack="tianshu"} | json | event_id="<UUID>"
{stack="tianshu"} | json | outcome=~"failed|unknown|rejected|degraded"
```

30天是测量后的容量目标。`capacity.py`用显式实测事件/日、平均行长、存储放大率、冗余系数和故障窗口算空间，并给出无回收时应用目录还能写多久。文件系统预测可能包含同卷其他写入，不能当日志独占吞吐。Loki满盘不会自动按空间回收：告警/拒绝/回压，最后由产品日志保护停止新业务。

## 官方依据（2026-09-22核对）

- [Vector文件源](https://vector.dev/docs/reference/configuration/sources/file/)：位点/轮转/最大行长及投递边界。
- [Vector Loki sink](https://vector.dev/docs/reference/configuration/sinks/loki/)：disk buffer、ack、重试与时间处理。
- [Vector0.58.0](https://vector.dev/releases/0.58.0/)与[秘密后端](https://vector.dev/docs/reference/configuration/secrets/)：采用文件后端，不开启危险环境插值；动态标签有固定前缀。
- [Loki retention](https://grafana.com/docs/loki/latest/operations/storage/retention/)：TSDB 24h index、Compactor、delete_request_store及异步删块。
- [Loki鉴权](https://grafana.com/docs/loki/latest/operations/authentication/)：自身没有用户认证，需前置分权入口。
- [Grafana provisioning](https://grafana.com/docs/grafana/latest/administration/provisioning/)及[密码文件](https://grafana.com/docs/grafana/latest/setup-grafana/configure-docker/)：数据源/面板、秘密引用。
- [Prometheus TLS](https://prometheus.io/docs/prometheus/latest/configuration/https/)：独立HTTPS指标服务。
