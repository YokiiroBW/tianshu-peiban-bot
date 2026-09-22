# DEP-B 开发者证据，2026-09-22

这些是本机隔离合成场景，不是DEP-D发布组合验收，不含真实产品数据、凭据、私钥、数据库或二进制。`index.json`列出逐文件SHA256、最终源码绑定、原生工具下载来源、实际测试与未通过项。上级`.gitattributes`禁止Git改写本目录字节；核hash时读取原字节。

## 640事件原生运行

实际命令（从根工作区；以下`python`是本机bundled Python3.12）：

```powershell
python -B tests/deployment/observability/run_native.py --vector tests/deployment/observability/.runtime/vector.exe --loki tests/deployment/observability/.runtime/loki.exe --contract C:/YOKI/Codex/tianshu-peiban-bot/contracts/diagnostics/v1 --output tests/deployment/observability/.runtime/native-report-final.json
```

执行器：真实Windows Vector0.58.0 + Loki3.7.8 +本包HTTPS守卫，四目录合成事件；未启动四产品业务进程。报告保留原始字节和运行目录标识。每批四产品各40条、每实例sequence1–40、共4批640条；事件值和event_id见`native-input.jsonl`，目录/批次/字节偏移/hash见`native-segments.json`，可按偏移原样重建16个段。

这份输入是成功运行后从16个源段原样归集；运行器当时在内存中先产生期望集再写源并作三方对账，未另行持久化写入前期望清单。不要把事后归集文件说成独立的前置产生清单，也不要把计数报告说成逐条Loki原始响应归档。独立复验可执行同一入口重新产生新UUID夹具，逐条内容hash/身份集合均会实际比较。

| 场景 | 产生 | 落地 | 检索 | 缺失/冲突/序号缺口 |
|---|---:|---:|---:|---:|
| 正常写入 |160|160|160|0|
| 停Loki、强制结束Vector、同buffer重启补传 |320|320|320|0|
| 停采集、新增数字轮转段、恢复 |480|480|480|0|
| 注入容量reserve拒绝、解除后补传 |640|640|640|0|

原始`native-report.json`包含各场景全部差异/重复/坏行计数，最后canary拒绝与Vector实际discard指标通过。`native-vector-metrics.txt`是原始指标响应。reserve模拟不等于物理ENOSPC，数字新段回放不等于四产品实进程轮转联验；同名rename轮转另有Ledger回归测试。`native-vector.json`和`native-loki.json`是实跑配置，包含合成路径引用而无凭据内容。

原生运行当时的词汇快照另存`vocabulary-at-native-run.json`。最终修复提交重绑后event/error集合完全相同，按相同本地路径/端口重生成的Vector配置与实跑JSON逐字段相等，见index；产品新提交的业务测试不由此继承通过。最后守卫并发/响应资源上限及Ledger审查修正由对应局部单测验证，未重复640运行。

**历史实现hash缺口：** 640运行未封存当时全部Python源码快照，不能证明一个完整历史实现hash，也没有事后补造。Vector/Loki实际配置、版本、归档hash是已保存事实；原报告仅代表当时运行，不能标为最终guard/query/Ledger实现的全链通过。最终实现的实跑工作字节hash见`final-ledger-regressions.json`，对应Git规范换行比较hash见index。

最终两项修改的真实合成输入、文件名及metrics前后序列由以下命令直接执行实际单测并封存（查询端明确为mock，Ledger/文件/SQLite是真实执行）：

```powershell
python -B tests/deployment/observability/capture_regressions.py --output tests/deployment/observability/evidence/2026-09-22/final-ledger-regressions.json
```

中央丢失场景：先源落地→检索成功，pending从1变0；把已确认项放入复查期，再返回中央空集，pending变1。正常轮转场景：消费active.jsonl后重命名为active.jsonl.1，新inode写sequence2；最终landed=2、source_replacements=1、source_changed=0。旧的同inode改写告警、重启位点、容量和公平重试另由6项局部回归覆盖。

## 最终guard/query真实冒烟

```powershell
python -B tests/deployment/observability/run_final_smoke.py --loki tests/deployment/observability/.runtime/loki.exe --contract C:/YOKI/Codex/tianshu-peiban-bot/contracts/diagnostics/v1 --output tests/deployment/observability/evidence/2026-09-22/final-guard-query-smoke.json
```

最终源码固定后另启独立真实Loki，用本版guard接收8条唯一event_id合成事件，再用本版query读回；8/8/8、所有差异0。`final-guard-query-smoke.json`完整保存产生事件、实际检索(timestamp,line)响应、查询范围、源码hash、Loki二进制hash、配置和配置hash，并核源码运行前后未变化。`final-smoke-loki.json`保存实跑原始配置字节。此入口不包含Vector和Ledger后台线程，不替代640恢复场景或完整Linux栈。

同轮真实guard验证query写入401、writer查询401、申报4MiB+1写入413、8个TLS工作槽占满后第9连接被拒绝；释放连接后同范围再次读回同8条。最终query的8MiB响应上限由另一个明确合成HTTPS响应器验证：8MiB接受，8MiB+1抛`query_response_over_budget`；该大响应来自替身，**不是Loki大响应测试**。这四项均通过，不将最终少量冒烟扩大成所有历史故障在最终实现上的复验。

## 保留期证据分级

`retention-before-cache-isolation.json`保留失败报告：240.91秒预算内组合判据未通过；工具观察到chunk消失但查询仍可见，该旧报告的`actual_chunk_deletion=false`属于当时的组合断言，不单独冒充成功。

`retention-cache-isolation.json`（125.58秒）和`retention-changed-path-timeline.json`（115.45秒观察到组合）是诊断阶段记录。它们前后查询配置从可查ingester改为store-only，**不能计同一路径前后验收**。原始状态不改写，解释与配置一起保留。

最终有效探针是`retention-same-path.json`：从首次写入前即固定store-only，查询selector及纳秒区间全程不变。2026-09-22T13:04:12.167878Z实际先从store读到1条；43.06秒改48h→24h保留策略；2026-09-22T13:05:24.942373Z（115.80秒，改策略后72.74秒）观测查询为空、原始chunk从1→0，随后ready=200，整体115.83秒结束。前后配置除`limits_config.retention_period`外完全一致；配置与push输入原字节/hash均保留。没有清空结果或手工删除chunk。

```powershell
python -B tests/deployment/observability/run_retention.py --loki tests/deployment/observability/.runtime/loki.exe --output tests/deployment/observability/.runtime/retention-report-same-path.json --timeout-seconds 240
```

相对生产差异：25h旧合成运输时间、缓存关闭、store-only、索引resync/compactor 5s、delete_delay 1s、chunk_idle5s/max_age1m、48h→24h策略缩短。**上述耗时仅为该探针观测上界；生产720h/2h配置、默认查询路径、真实24h观察、Linux/NAS删块均未验收。** 单次成功亦不证明保留期稳定SLO。

## 其他实际验证及缺口

23项完整单测曾通过（2.577秒、0skip），最后按改动范围分别跑4项TLS测试（1.833秒）与6项Ledger测试（0.149秒）。当前共25个不同方法得到相关运行覆盖，未声称最后重新整套25项跑过。测试命令见`../../fault-probes.md`，完整套使用权威`DEP_B_CONTRACT`原字节。Ruff格式/检查通过。

`linux-report.json`来自实际调用`run_stack.py --run-local-containers`，原因`docker_cli_missing`，Linux状态not_run/dependency_missing，不能计通过。Grafana实际加载/Viewer/匿名权限、外部通知、物理满盘、满buffer、生产保留路径、30天容量、24h持续观察、NAS均没有通过记录。应用回收授权始终false。
