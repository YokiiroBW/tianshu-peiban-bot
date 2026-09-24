# NAS-A3 常驻资源候选方案

## 范围

这是供评审的四服务 NAS 常驻资源候选方案。它不修改 Compose、Dockge、控制器或 `release_ready`，也不表示生产资源需求已经确定。评估使用的 NAS scope 没有调用真实模型。

## 已测事实

| 资源项 | NAS / cycle7 事实 | 解释 |
| --- | --- | --- |
| 主机 | 8 CPU；MemTotal 50,106,032,128 bytes（约 46.7 GiB）；Cgroup v1 | cycle7 观察期间 MemAvailable 最低约 17.1 GiB |
| 内存控制 | Docker `MemoryLimit=true` | 每个核心服务有可执行的 1 GiB 上限，总上限 4 GiB；这只是上限，不能当作最低需求 |
| CPU 放置 | `CPUSet=true`；四服务绑定 `6,7` | 服务共同争用两个 CPU；cpuset 不等于 CPU 时间配额 |
| CPU 配额 | `CPUCfsPeriod`、`CPUCfsQuota` 未提供 | 不能给每个服务或整个 stack 设硬 CPU 上限 |
| CPU shares | `CPUShares=true` | 可作争用时的相对权重，不能作硬上限 |
| PID 控制 | `PidsLimit=false`，新 Gateway `PidsLimit=null` | NAS 未执行 PID hard limit。cycle7 的 `docker stats` PIDs 样本显示 0，不可视为真实进程数 |
| cycle7 闲置快照 | Platform 约 39.6 MiB、Gateway 约 43.9 MiB、Memory 约 53.3 MiB、Companion 约 53.2 MiB；均低于各自 1 GiB 上限 | 仅为 readiness/闲置周期的少量快照，不是峰值、常驻基线或最低需求测量 |

配置来源为 `deploy/tianshu/compose.py` 与 `deploy/tianshu/resource_profile.py`；现场能力及容器参数摘要在 `docs/development/nas-a3-evidence-2026-09-25.json`。资源 profile 会移除通用 `cpus` 与 `pids_limit`，保留内存上限并设置 `cpuset=6,7`。

## 候选配置

| 项 | 候选值 | 评审说明 |
| --- | --- | --- |
| 服务集合 | platform、gateway、memory、companion 各 1 个 | 不额外引入资源或容器 |
| 内存 | 每服务 `mem_limit=1g`，总上限 4 GiB | 暂时保留已验证硬上限。通过代表性负载前，不把 1 GiB 记为最低需求；持续峰值若逼近上限，应先测量再决定是否调整 |
| CPU 放置 | cpuset `6,7` | 暂时保留 NAS QA 布局；四服务会共享这两个 CPU |
| CPU shares | 暂拟每服务 `1024` | 等权相对权重的评审建议，不是当前配置，也不提供硬上限；若业务优先级不同，先由负责人决定权重关系 |
| CPU quota | 不设定可执行值 | 当前 NAS 未暴露 CFS quota；不能声称有 per-service CPU ceiling |
| PID 上限 | 目标可评审为每服务 `128`，但只在内核/Docker 真正启用 pids controller 后应用 | 当前 NAS 不支持，`docker stats` 的 PID 样本也不可用；不能用应用并发设置替代 hard limit |
| 重启与故障处理 | 沿用隔离验收中的显式启停方式，单独验收生产恢复策略 | 本轮只验证 SIGTERM 正常停止与固定容器的受控重建 |

在 PID 控制不可用时，该 NAS profile 保持 `release_ready=false`。不得因为观察到的闲置用量低而提高服务数量、放宽资源边界或推断最低规格。

## 建议探针与门槛

以下门槛是待评审的起点，不是已批准的容量承诺。

1. **控制器核对**：记录 Docker `MemoryLimit`、`CPUSet`、`CPUShares`、`CPUCfsQuota`、`PidsLimit` 与 Cgroup 版本；同时检查实际 cgroup v1 的 memory、cpu/cpuacct、cpuset、pids 挂载和每容器 HostConfig。每次验证都以容器实际值为准。
2. **启动和鉴权**：逐服务记录启动时延、health、鉴权 readiness、OOM 与 restart count；验证 Gateway 在运行中跨过 300 秒初始签发回执期限后仍保持 readiness。
3. **无模型合成负载**：在新的隔离 scope 中先做至少 30 分钟稳定期，再做经评审的合成请求负载和更长时段 soak。不得连接真实模型或付费 provider。启动、续期空闲期与合成负载分开记录。
4. **采样**：每 15 秒记录各容器 memory usage/high-water、CPU usage、restart/OOM；每分钟记录主机 MemAvailable、swap、memory pressure/PSI 和 cgroup `memory.events`。PID controller 可用时记录 `pids.current`/`pids.max`；不可用时用受限的只读进程计数作趋势监控，并明确它不具备硬限制效果。
5. **暂定验收线**：无 OOM、无意外重启；代表性负载下每服务内存峰值低于 1 GiB 上限的 75%，主机 MemAvailable 高于 8 GiB；readiness 延迟和错误率由服务负责人补充业务阈值。若未来启用 128 PID hard cap，负载期间 `pids.current` 峰值不超过上限的 80%。
6. **CPU 判断**：报告每服务 CPU p50/p95/p99、压力与业务延迟。CPU shares 只用于相互竞争时的比例；没有 quota 时，不能通过单一 CPU 百分比样本证明服务获得了硬隔离。

## cycle7 可支持的结论

cycle7 以新镜像标签、项目、网络和端口完成 345 秒活动续期观察、停机 345 秒自然过期、公开 CLI 重新签发、新 Gateway 创建、四服务鉴权 readiness 和 SIGTERM 正常停止。它支持 liveness 与续期恢复路径的结论，不覆盖合成对话、真实模型、持续负载、日志链、常规恢复或浏览器渲染。scope 报告及 SHA-256 见 NAS-A3 证据文件。
