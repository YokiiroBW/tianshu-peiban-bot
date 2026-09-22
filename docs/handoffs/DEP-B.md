# DEP-B：日志栈、完整性和故障告警

## 目标及交付状态

按2026-09-22部署第二批任务卡，交付独立Vector/Loki/Grafana/Prometheus/完整性守卫候选包、初始化输入校验、采集/查询/故障验证入口与最小应用回收提案。执行者遵循本批Codex直接开发特批，未派子代理/DSH。仅本号三个允许路径有改动；无NAS、真实数据/账号/模型、生产容器、推送、部署或自行集成操作。

根基线`21668e4300d7bedc8ac1ed819bdb7dad4a2bd58b`；分支`codex/dep-b-observability`。交付提交为包含本handoff的本地提交（确切hash另向协调任务报告，避免自引用hash）。开发完成不等于Linux、最终组合或NAS验收。

| 固定产品 | 最终提交 |
|---|---|
| platform | fa85ee939a2affdda054ee7fa3155ec89ade6196 |
| companion | cf020fd338b9beefc9d7156a00158e915d41735a |
| memory | 2f4037620f47991f42a5daa10f471c34c9ba4fd4 |
| gateway | 51121e6c02ed60605be14f31b19b484bc117a746 |

已从固定Git对象重新提取词汇，未import产品或复制可变业务目录。与首次原生运行event/error集合一致，重新生成Vector配置亦相等，证据记录前后快照。只读消费DEP-A schema固定`e86d622a813f116b059b62b820cbd1342722042d`形状；diagnostics/v1严格12字段、原字节hash，未改contracts。TS104–106修复已按协调者验收后的提交重绑；本包没有复跑这些产品的业务验收。

## 变更及集成

- [包入口](../../deploy/observability/README.md)：显式DEP-A清单/部署根/证书/分角色凭据/新输出目录，生成完整独占Compose和运行配置；不带candidate时关闭的发布门槛仍拒绝。
- [集成说明](../../deploy/observability/INTEGRATION.md)：只读四产品日志、五个独占数据目录，UID10001、私有网络、两loopback端口、TLS/mTLS、凭据分权和3.5GiB候选内存限额；DEP-A引用入口，不复制服务定义。
- 无采样；静态去敏词汇校验、4GiB持久buffer/block、持久位点、历史运输时间分离、Loki WAL/TSDB/720h保留、Grafana数据源/面板/15条告警、Prometheus及本地持久告警。
- 编号事件三方集合/hash对账；有界查询二分分页，饱和或超预算失败。监测增量持久账本、公平重试、一小时后进入已确认复查队列、中央丢失重新待确认；正常rename轮转与同inode已消费字节改写区分。均无源日志删改权限。
- [回收最小提案](../../deploy/observability/RECLAMATION-PROPOSAL.md)：所有者、封段原字节/事件集合hash、产生水位、可恢复副本和中央收据、世代/撤销/乱序幂等、应用CAS回收及12组反例；未冻结、未实现。

## 实际验证

详细原始报告、实际输入、版本、命令与全部hash见[证据说明](../../tests/deployment/observability/evidence/2026-09-22/README.md)和[index.json](../../tests/deployment/observability/evidence/2026-09-22/index.json)。本证据是DEP-B开发验证，不是DEP-D发布组合报告。

| 验证 | 实际结果及边界 |
|---|---|
| 单元/真实TLS | 完整23项0skip通过2.577秒；最终相关TLS4项1.833秒、Ledger6项0.149秒通过；共25不同方法覆盖，未虚报最后整套25项重跑 |
| 配置编译 | 真实Vector0.58.0 validate及Loki3.7.8 verify-config成功；不是Linux镜像证明 |
| 真实Windows流水线 | 640产生/640落地/640检索，缺失、冲突、内容差异、序号缺口均0；停存储/强杀采集器/轮转补传/容量reserve拒绝恢复/canary与丢弃指标通过 |
| 最终guard/query真实冒烟 | 8条事件穿过最终guard→真实Loki→最终query，8/8/8差异0；源码运行前后hash一致，配置/输入/实际响应已封存。角色401、4MiB+1写入413、8并发占满后第9拒绝且恢复查询通过；8MiB响应边界另用真实HTTPS明确替身验证，不冒充Loki大响应 |
| 最终保留期探针 | 前后相同store-only路径和区间；115.80秒观测事件消失+chunk实际删除+ready，保留UTC/单调时间线、原字节配置/输入/hash；加速配置限定，不计生产默认查询路径、720h/2h配置、Linux或真实24h观察 |
| 保留期失败阶段 | 240.91秒未通过报告保留；另两次查询路径变更实验只作诊断记录，不计同路径验收 |
| Linux入口 | 已实际调用，docker_cli_missing → dependency_missing/not_run；无Docker/WSL环境，不假装通过 |
| 静态/范围 | Ruff通过，完整diff人工审查，git diff --check通过；源/凭据/数据库和运行二进制未入库 |

关键复验命令见`tests/deployment/observability/fault-probes.md`。原生实际输入为成功运行后的源段原字节归集，原始期望集当时在运行器内存中；报告保留逐场景三方计数，但不是逐条检索响应归档。该证据边界不得升级。

640实跑当时未封存所有Python源码，完整历史实现hash不可证，不事后补造。实际Vector/Loki配置hash及版本/词汇可核；之后guard/query资源边界和Ledger复查/轮转修正只计相关局部验证，**不把旧640标为最终实现全链通过**。`capture_regressions.py`再次执行两项真实回归并保存实际合成输入、metrics和当时源码工作字节hash到`final-ledger-regressions.json`；查询侧明确mock。index另列最终Git换行比较hash，区分Windows原字节与仓库blob。已确认后中央丢失pending恢复1、正常rename轮转landed2且无source_changed误告警。

另按协调要求执行`run_final_smoke.py`，最终源码少量真实guard/Loki/query链路和资源边界已验证，详见同目录`final-guard-query-smoke.json`；它不启动Vector或Ledger后台监测，不代替完整640故障恢复复验。未改旧报告来冒充最终版本。

## 未完成、风险与下一步

1. 协调者串行审查/集成本提交，DEP-A最终清单使用上述提交与合同原字节；观测五数据目录需由发布清单卷枚举扩展并让DEP-C明确备份归属。本号未改共享schema。
2. 补Linux固定digest构建/全栈启动、UID/挂载权限、Grafana实际面板/15规则/匿名与Viewer权限、物理ENOSPC、4GiB buffer满载、生产保留默认查询路径与配置、受控告警通知及栈外探活。短测不替代24h持续观察/30天容量测量。
3. 当前应用没有产生水位和安全封段回收：源文件都未生成的尾部事件无法由采集端发现；默认1GiB源目录终会容量拒绝新业务，不能称无限日常运行闭合。所有报告`application_reclamation_authorized=false`，不因Loki204/查询命中/位点推进删除应用段。
4. Loki单机WAL及查询曾成功不保证失盘/旧备份恢复后仍存在；需遵照回收提案落实恢复世代和可恢复副本。确认复查有预算/队列延迟，账本100万条上限不自动清证据；达限会告警并停纳入。CLI对账专用于独立合成租户/完整时间段，不能把任意生产截片当全集。
5. 记忆候选最终消费与Chat Audit归档缺口仍由协调者补产品卡；本包未修改、未以入队或日志可见替代功能闭环。

## 完整文件清单

以下由交付目录清点生成；证据文件逐项hash归index管理，二进制、数据库、TLS私钥及运行目录均被忽略。

<!-- DEP-B FILE INVENTORY -->

```text
deploy/observability/.gitattributes
deploy/observability/.gitignore
deploy/observability/INTEGRATION.md
deploy/observability/README.md
deploy/observability/RECLAMATION-PROPOSAL.md
deploy/observability/alerts.py
deploy/observability/baseline-commits.json
deploy/observability/capacity.py
deploy/observability/compose.py
deploy/observability/configs.py
deploy/observability/configure.py
deploy/observability/grafana-entrypoint.sh
deploy/observability/guard.py
deploy/observability/monitor.py
deploy/observability/policy.py
deploy/observability/query.py
deploy/observability/reconcile.py
deploy/observability/settings.example.json
deploy/observability/snapshot.py
deploy/observability/versions.json
deploy/observability/vocabulary.json
docs/handoffs/DEP-B.md
tests/deployment/observability/.gitignore
tests/deployment/observability/capture_regressions.py
tests/deployment/observability/evidence/.gitattributes
tests/deployment/observability/evidence/2026-09-22/README.md
tests/deployment/observability/evidence/2026-09-22/final-guard-query-smoke.json
tests/deployment/observability/evidence/2026-09-22/final-ledger-regressions.json
tests/deployment/observability/evidence/2026-09-22/final-smoke-loki.json
tests/deployment/observability/evidence/2026-09-22/index.json
tests/deployment/observability/evidence/2026-09-22/linux-report.json
tests/deployment/observability/evidence/2026-09-22/native-input.jsonl
tests/deployment/observability/evidence/2026-09-22/native-loki.json
tests/deployment/observability/evidence/2026-09-22/native-report.json
tests/deployment/observability/evidence/2026-09-22/native-segments.json
tests/deployment/observability/evidence/2026-09-22/native-vector-metrics.txt
tests/deployment/observability/evidence/2026-09-22/native-vector.json
tests/deployment/observability/evidence/2026-09-22/retention-before-cache-isolation-config.json
tests/deployment/observability/evidence/2026-09-22/retention-before-cache-isolation.json
tests/deployment/observability/evidence/2026-09-22/retention-cache-isolation-config.json
tests/deployment/observability/evidence/2026-09-22/retention-cache-isolation.json
tests/deployment/observability/evidence/2026-09-22/retention-changed-path-timeline-config-after.json
tests/deployment/observability/evidence/2026-09-22/retention-changed-path-timeline-config-before.json
tests/deployment/observability/evidence/2026-09-22/retention-changed-path-timeline-fixture-push.json
tests/deployment/observability/evidence/2026-09-22/retention-changed-path-timeline.json
tests/deployment/observability/evidence/2026-09-22/retention-same-path-config-after.json
tests/deployment/observability/evidence/2026-09-22/retention-same-path-config-before.json
tests/deployment/observability/evidence/2026-09-22/retention-same-path-fixture-push.json
tests/deployment/observability/evidence/2026-09-22/retention-same-path.json
tests/deployment/observability/evidence/2026-09-22/vocabulary-at-native-run.json
tests/deployment/observability/fault-probes.md
tests/deployment/observability/helpers.py
tests/deployment/observability/requirements.txt
tests/deployment/observability/run_final_smoke.py
tests/deployment/observability/run_native.py
tests/deployment/observability/run_retention.py
tests/deployment/observability/run_stack.py
tests/deployment/observability/test_guard_tls.py
tests/deployment/observability/test_observability.py
```
