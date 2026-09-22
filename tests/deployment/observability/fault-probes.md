# 隔离验证入口与准确限制

所有工具只使用合成记录。证书来自测试工具私有临时CA，绝不用于生产。依赖：Python3.12+及 `cryptography==50.0.1`；JSON配置无需PyYAML。配置校验必须以目标组件的真实版本再执行，不把文本断言称启动通过。

## 本地组件/真实TLS

```text
DEP_B_CONTRACT=<权威diagnostics/v1目录> python -B -m unittest discover -s tests/deployment/observability -p 'test_*.py' -v
python -B tests/deployment/observability/run_native.py --vector <官方Vector二进制> --loki <官方Loki二进制> --contract <合同目录> --output <native报告JSON>
python -B tests/deployment/observability/run_final_smoke.py --loki <官方Loki二进制> --contract <合同目录> --output <最终guard-query冒烟JSON>
python -B tests/deployment/observability/capture_regressions.py --output <最终Ledger实际输入与指标JSON>
```

Windows用PowerShell环境变量语法设置DEP_B_CONTRACT。前者包含真实HTTPS/mTLS网络、鉴权、TLS错误、源重启/轮转/重复/截断/坏行、缺口/尾部缺失/hash冲突、查询分页饱和及告警持久性。后者是真实Vector+Loki，分别核配置、160条正常事件、停Loki后追加160条并强杀Vector再启、停采集轮转追加160条、注入磁盘reserve拒绝后恢复再追加160条、canary拒绝与实际Vector丢弃指标。总640条唯一事件对账。reserve注入是保护分支验证，**不是实际磁盘ENOSPC**。

`run_final_smoke.py`专核最终guard→真实Loki→query的8条小样例，封存源码hash和实际输入输出，并测角色/写入限额/8连接并发拒绝恢复；响应8MiB边界使用真实TLS上的明确替身，不冒充Loki大响应。`capture_regressions.py`执行两个实际Ledger单测并捕获合成输入和metrics，查询侧mock；各自范围详见证据README。

## 总期限返修探针

```text
python -B tests/deployment/observability/test_deadlines.py --output <deadline报告JSON>
```

真实loopback TLS/明确替身，生产默认10秒未调小；0/6/12秒三字节滴流应在总期限结束，覆盖未鉴权慢头、认证慢体、入站/出站停滞握手、上游慢头/体、响应慢读者、6秒请求体+6秒后端共用预算、每页6秒但全部分页共用预算。另验证client/server关闭取消、8槽回收与后续正常请求。测试调度容差1.5秒单列，实际耗时写报告；DNS不可强杀反例用0.2秒显式父期限及单解析线程，不能冒充OS解析已被中断。`--case`可选择受影响单项，报告tests_run如实计数，不能称全套。

## Linux Compose

```text
python -B tests/deployment/observability/run_stack.py --run-local-containers --contract <合同目录> --output <linux报告JSON>
```

拒绝DOCKER_HOST/DOCKER_CONTEXT环境覆盖，context端点必须本机unix/npipe，daemon必须Linux。每次新建随机`depb-*`项目与带SYNTHETIC_ONLY标记的目录，清理仅该项目，不删持久目录。工具在自己的合成目录设置宽权限以适配非root容器；这不代替实际部署权限验收。该脚本从包真实配置生成、不使用Docker socket挂载、不启动产品/付费模型。

缺Docker输出dependency_missing与not_run，退出2；不是通过。可运行时包含三个原生配置验证器、起栈、编号三方对账、停存储/重建采集器/轮转/查询权限/canary。Grafana浏览器登录/Viewer与匿名权限、面板告警装载、通知到达、物理满盘、TTL删块仍分列not_run，本入口最终partial，不能单独将发布状态提为verified。

## Loki保留期与删块

```text
python -B tests/deployment/observability/run_retention.py --loki <官方二进制> --output <保留报告JSON> --timeout-seconds 240
```

独立新Loki目录、mTLS，先48h策略写25小时前的合成运输时间并等待实际可检索/落chunk，再改最小24h策略重启。前后均采用完全相同的store-only查询路径、selector和纳秒区间。夹具关闭query/chunk缓存、索引resync=5s、compactor=5s、delete_delay=1s、chunk_idle=5s/max_age=1m；这些都是相对生产配置的探针差异，生产仍720h保留/2h异步删除延迟。

必须同时看到该事件不可检索、已观测chunk文件真正删除、Loki仍ready才通过。从未可检索、未观察到chunk、期限内没删或查询失败均失败，不把检索空集或静态720h配置称物理清理成功。保留固定查询区间、UTC/单调耗时观察序列、前后配置和hash。该测试不是等待真实24小时，亦未验证生产默认查询路径；报告明确elapsed_24_hours=false及production_default_query_path_verified=false。2026-09-22同路径探针115.80秒观察到组合判据通过，仅是这次夹具的观测上界，见evidence/2026-09-22；Linux和生产配置仍需重验，失败阶段原报告亦保留。

## 必须在隔离Linux补的故障

1. 使用本次生成的SYNTHETIC_ONLY部署根和独立有界测试文件系统。不可往真实NAS盘写填充文件，不使用带生产挂载的Docker context。由环境负责人准备配额或测试文件系统，工具不提权创建loop设备。
2. Loki空间逐步逼近reserve：确认本包磁盘告警、writer得到503、Vector buffer增长、源文件仍在。恢复空间后对同一产生清单逐条对账；再在该有界文件系统内触发真实ENOSPC，记录有无拒绝/丢弃/WAL受损，绝不能省略这一项。
3. Vector buffer达到4GiB：观察block而非drop，停止/重建容器仍能补传；源容量保护需用最终四产品在实际日志预算内联合测试，合成写文件不能代替应用503/ready=false。
4. Grafana匿名不能查询，Viewer可读运维日志但不能变更数据源/用户/规则；writer不能查询、query不能push/delete。固定三个秘密canary覆盖无效字段、未知event/error及异常响应，检索/Vector缓冲/守卫报告不出现canary；源头去敏仍由最终产品测试证明。
5. 从Grafana读取实际已加载的15条规则，故障触发并恢复，观察本包alerts.sqlite转换；另配置受控测试通知接收器验证送达、去重和恢复。当前没有外部通知地址，不发送真实消息。停全部日志栈须由栈外探活报警。
6. 对最终固定版本保存24h真实持续观察起止、流量、IO/CPU/内存、磁盘增量、buffer峰值、最大积压和三方差异。短测不能冒充24h/30d。

恢复解释：Loki可能按同stream+时间+内容合并完全相同重传，查询重复数是可见重复下界；不同运输时间的重放可重复可解释。若丢弃事件或中央数据丢失，先修根因、保留源/旧buffer/checkpoints证据，再用独立重放采集器读源并新建位点，不删旧应用段、不直接清checkpoint假恢复。重放后用event_id/hash对账；已发生业务副作用不会因日志重放重发。
