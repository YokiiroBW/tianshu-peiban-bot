# 网页 503 与网关续期恢复部署（2026-10-14）

TS-136 / TS-137 已推送两产品 main 并部署 NAS。Platform `9219d31`，Gateway `0a6b009`；网页在实际 Chrome 中显示登录页。

## 原因与修复

初次停机是容量守护一次 `docker_unavailable` 后锁定并停止整组服务。恢复容器后，平台又出现 `log_flush_timeout`；日志模块没有运行期恢复调用，导致盘写恢复后仍永久拒绝网页请求。另查到 Gateway 一次三秒续租失败后永久停止续期，300 秒授权随后过期，平台恢复不会使其重新续期。

平台复用现有 Diagnostics 恢复机制，增加一个有界退避工作者，使用同一恢复事件和冲刷屏障，只有真实 fsync 成功后恢复准入；停止先关闭准入，迟到恢复不重新接单。网关只在原确认授权仍有效时，使用同一来源和凭据重试瞬时传输/服务故障；故障期不读旧配置回退，过期、撤销、换来源及非法响应仍硬拒绝。两边均不重放业务。

## 验证与部署

- Platform 实际 Linux 候选安装代码 198 项通过；Gateway 与两产品 TLS 联合 Linux 68 项通过，均无失败/跳过。联合使用已安装 Platform 和与 Gateway 镜像一致的固定 Git 源码，上游为隔离替身。
- 本地 Platform 223 个唯一用例中 221 通过，1 个未修改的 Dockerfile/ffmpeg 静态断言既有失败，1 个旧 GatewaySubchain 缺输入而跳过；Gateway 最终 34 项通过。两份固定 diff 独立审阅无实质阻断。
- 37 个数据库冷备验证通过，未迁移；原设置、媒体配置、模型 Key、管理员及角色配置保留，其余 8 个容器 ID 保留。容量守护 ready，Dockge 与实际 Compose 同步。
- 网页连续 360.043 秒、13 次实际 HTML 读取均 HTTP 200；五核心实际 `/health/ready` 全部 200。Chrome 复查登录页成功。
- 同一个网关来源跨过原 300 秒到期点，观察 375.15 秒，新增 12 次成功续期，当前仍有效；未换来源、未调用真实模型或发送 QQ。

## 边界

未在生产磁盘注入故障。原容量守护的整组停机策略未修改；初次 Docker 探测的更细系统原因没有日志可确认。持续不可写仍如实拒绝；已过期或撤销的来源不能自动复活。此次生产恢复对既有失效 Gateway 来源做了同 entry/owner/route 的显式重新签发，没有新增权限。

实现交接：[TS-136](../../worktrees/TS-136/tianshu-platform/docs/handoffs/TS-136.md)、[TS-137](../../worktrees/TS-137/tianshu-model-gateway/docs/handoffs/TS-137.md)。脱敏数据见同名 JSON；私有证据保留在 `.runtime/platform-log-recovery-20261014` 与 `.runtime/web-outage-20261014`，不入库。
