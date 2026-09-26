# NAS-A2 首发日志边界只读审计交接

日期：2026-09-26。状态：**needs_validation**。本报告只评估已固定源码和既有 NAS-A2 证据，不构成部署、完整日志或长期留存验收。

## 目标与固定基线

审计受控短期候选运行的完整安全事件采集、持久队列、容量保护与实际停止路径；区分代码配置意图和 NAS 实机证据，并指出最小候选门槛。未实施功能、连接 NAS、运行测试或改动其他文件。

- 主协调提交 `a47b70c28e3d71dca80023a98e8b5360694b27e5` 的 `deploy/tianshu/release-manifest.example.json:744` 将 OBS 固定为 `65b88a6d1c2b5047ca6bfb2f7f7484749eb14154`。产品固定提交：Platform `c1c7547680911462439ee9c9a09f4e72f44f36a3`、Companion `e94b609099365f75ca933d9fed03cdfbc83ec235`、Memory `9a3b2bed6aebff9f0677f2c62e979859769e0c9c`、Gateway `601974194042641c5a85cc3c061cbd1880d7daf1`。
- **旧 A1 验收基线**：OBS `65b88a6:deploy/observability/compose.py:140` 只有 `observe`、`storage` 两张 `internal` 网络。该版本虽为 Grafana、Guard 配置回环发布端口（同文件 83、136 行），既有 A1 九 owner 运行及恢复断言不能证明这两个端口从宿主可达，也不能证明四源日志联合查询。
- **新 resident 候选**：A3 正在获授权范围内补最小 OBS 兼容；主线后续的 `access` 网络不能回填旧 A1 结果。新 OBS 提交、配置和镜像最终固定后，只核受影响差异及相应候选读回，不重复把旧 A1 结果当成新版本实测。

## 固定源码的能力及实际边界

| 能力或保护 | 固定源码事实 | 尚不能推出的结论 |
| --- | --- | --- |
| 安全事件采集 | `65b88:deploy/observability/configs.py:10-113` 从四源 `*.jsonl` 及轮转文件读取，保留 checkpoint；校验 `diagnostics/v1` 的 12 字段，写入 Guard/Loki。Vector 使用 4 GiB 磁盘 buffer、`when_full=block`、acknowledgements 与请求重试。 | 配置了队列不等于真实四产品全链路无缺口。校验错误会按 58-60 行丢弃，不能称任意原始日志都被完整收集。 |
| 中央持久化 | `65b88:deploy/observability/configs.py:129-200` 配置 Loki WAL、文件系统 TSDB 和 720 小时留存；`compose.py:34-38` 为容器引擎输出配置 `local`、`10m`、`max-file=3`。 | 30 天是配置目标，未有 30 个自然日运行证据；有界引擎输出不是完整应用日志副本。 |
| 中央入口余量 | `65b88:deploy/observability/guard.py:100-104,318-321` 在 OBS storage roots 任一可用空间不大于 1 GiB 时，于读取 push 正文前返回可重试 503 `storage_capacity`。 | 只阻止新的中央 push；不阻止源产品、Loki 自身或其他宿主进程继续写共享文件系统。 |
| 水位与对账 | `65b88:deploy/observability/guard.py:113-150` 周期扫描来源、空间和对账；`configs.py:205-252` 有应用额度 80%、磁盘水位 85%、buffer、拒绝和丢弃告警；`monitor.py:11,70` 的账本默认最多一百万事件。 | 指标和告警不会自动停业务。`configure.py:300-306` 明示 `alert_delivery_unconfigured`；账本上限是核对能力边界，不是源写入限流。 |
| 四源预算与拒绝 | 四产品固定版本均默认每源目录 1 GiB：Platform `services/platform/diagnostics.py:328-338`，Companion `src/tianshu_companion/observability.py:761-768,1013-1016`，Memory `src/tianshu_memory/diagnostics.py:489-510`，Gateway `src/tianshu_gateway/observability/sink.py:639`。Platform `server.py:194-207`、Companion `app.py:249-276`、Gateway `server.py:437-457` 的新业务在持久开始记录失败时拒绝；Memory 写故障锁存 `log_capacity`（`diagnostics.py:1110-1125`）。 | 这些是应用逻辑额度，不是宿主文件系统配额。Companion 的完成事件与 Gateway 部分观察事件仍为 best-effort；容量或写故障下不能保证所有在途尾事件都存在。运行时配置是否都取默认值仍须检查。 |
| 源日志回收 | `65b88:deploy/observability/RECLAMATION-PROPOSAL.md` 明示回收合同未冻结、未实现，不能据 Vector 位点、HTTP 204 或 Loki 查询命中删段。 | 不能认为源目录满后仍可长期日用；目前没有安全自动回收路径。 |

`a47b70c:deploy/tianshu/bundle.py:453-460` 的新装计划空间检查计算四源日志预算、状态迁移余量及 256 MiB；它没有给 Vector buffer、Loki/WAL、Prometheus、Grafana、Guard 或 Docker 写入施加运行时硬额度。OBS `log_budgets` 是 Guard 监测输入（`65b88:deploy/observability/configure.py:173-178,258-264`），必须与四产品实际 `settings` 核对，不能把示例值视为已部署值。

## NAS-A2 已观测证据

固定证据为 `a47b70c:docs/handoffs/NAS-A2.md` 与 `tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r12*.json`、`r13*.json`。这些是隔离合成试验，不是五服务正式 OBS 栈与四产品的联合验收。

- R12：512 MiB 隔离 tmpfs，60,000 个唯一合成事件；Loki 离线时 Vector 来源停滞且源文件不变，恢复后来源/下游 60,000、混合查询身份精确，功能性背压与无损复放通过。离 disk-v2 内部上限尚有 2,726,768 字节，下一条序列化记录长度未测，故**精确满 buffer 容量未测**。R12 store-only 查询的零结果落在 Loki 1,635 秒近期排除窗口，属于无效持久化读回测试，不能当作数据丢失证据。
- R13：另一组同量合成事件经 flush、Loki 停止及 store-only 重启后，在非空 store 查询区间精确读回 60,000/60,000；本次隔离数据的持久化读取通过。报告仍为 `partial`、`release_ready=false`。两轮均未验证物理 ENOSPC、生产 30 天留存、应用安全回收或四产品实接。
- 较早 A2 隔离容量探针曾观测到源预算告警以及 Guard 在填充 tmpfs 后返回 503，证明指定隔离条件下的告警/中央拒绝路径；不能推导共享宿主盘已被保护。HTTP 204 或 flush 204 也不能单独证明日志持久可检索。

## 受控短期候选的最小闭环

1. **可用性及数据**：在最终固定的 resident 镜像、Compose、网络和端口上，核 Grafana/Guard 宿主回环可达。用四产品实际安全事件各做源文件→Vector→Guard→Loki 查询与身份/序号对账；覆盖轮转、重启和 Guard 503 后恢复，检查丢弃、组件错误、缺口及冲突为零。旧 A1 或 A2 合成结果不能替代。
2. **宿主容量**：固定四产品实际 1 GiB 配置与 Guard 四项 `log_budgets` 一致；给四源、Vector buffer、Loki/WAL 和其余 OBS/引擎输出明确卷/配额或可核验的总空间及保留余量。制定有执行人的停止新业务阈值、最晚截止时间和流量上限，确保在应用 80%、磁盘 85% 告警前有足够处置余量；演练源目录满或不可写时拒绝新业务而宿主仍留有安全空间。单纯的 Guard 1 GiB push reserve 或新装计划空间检查不足以覆盖此风险。
3. **运行方式**：若试运行有人值守，阈值读取、通知和停机责任必须可实际执行并留下收据；若无人值守，则需接通告警投递及自动停业务路径并验收。仍禁用源段回收。有限时长、有限流量的首试不必等满 30 天，但不能标记为无人值守完成、长期留存通过或无限可靠。

长期日用再冻结跨产品回收合同及归档/确认/删除协议，并验物理 ENOSPC、精确满 buffer、账本规模、多日容量预测及真实 30 天留存。这些尚未完成，但不应仅以“30 天尚未自然经过”阻断前述受控首试。

## 本次交付、验证与下一步

仅新增本报告。只读核对固定 Git 对象、源码和已存 A2 报告；未连接 NAS、运行测试或改动产品/部署代码。向 A3 resident 任务 `01a0d427-cf5e-7230-b08e-a71a617f75b6` 转达了容量保护边界及候选配置字段建议。下一步由总控固定新 OBS 候选提交、收取 A3 差异，再安排上述有界容量保护和四源联合查询验收；本报告不授权 NAS 写入或额外实现。
