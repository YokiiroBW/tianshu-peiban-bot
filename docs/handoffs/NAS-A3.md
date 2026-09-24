# NAS-A3 验收交接

## 目标

在 Synology NAS 的隔离 A3 目录完成四服务 Linux 镜像与运行期存活验收，并观察自然过期后的 Gateway 行为及一次性重新签发恢复路径。该范围不代表生产部署或发布就绪。

## 变更

- tianshu-accept-a3-* 被加入 bundle、resource profile、runtime identity 和 synthetic initializer 的隔离命名空间校验，支持为两个验收 scope 分配独立网络。
- 新增 linux_reauthorize.py：确认准确的四容器归属及自然过期状态，通过 Platform 公开 CLI 的 docker exec -i 输入通道签发新 origin；不输出 ref，不自动重试，不重启服务。
- 补充重新授权单测及操作者恢复文档。Synology Compose v2.20.1 的 create 不支持 --no-deps，恢复命令应使用 create --no-build --pull never gateway。
- 基线：2e04eff5d0bf1283a321d9042d9b62f94a54dabb；分支：codex/nas-a3。

## NAS 实际结果

- Resident 与 recovery 两个隔离 scope 均完成镜像构建、依赖、Linux 文件权限、UID/GID、鉴权 readiness 和四服务 SIGTERM 停止检查；两份 linux-executed.json 的 liveness 结果均为 passed。
- 两次自然过期都按预期观察到 Gateway exit 1、非 OOM，Platform 健康且 Companion/Memory 保持停止；无自动重试。
- Resident scope 的第一次重新授权执行以 reauthorization_runtime_failed 结束，marker 保持 failed_or_unknown，未重放该 scope。
- Recovery scope 验证了修正后的 stdin 调用路径：公开 CLI 成功签发新 origin 并替换私有 Gateway env，报告不含 ref。随后 Synology Compose 2.20.1 拒绝了带 --no-deps 的 create 命令。下一次修正执行前，新 origin 已过期，守卫以 new_origin_budget_insufficient 拒绝操作；没有再签发 token，也没有创建 replacement Gateway。
- 结束检查覆盖两个项目中的全部七个现存容器：运行数为 0。Recovery 的三个剩余容器均 exit 0；Resident 中 Gateway 的自然过期 exit 1，其余 exit 0。已移除 Recovery 的旧 Gateway；未删除卷、网络、镜像或两处 scope 证据。
- nas-cpuset-qa-v1 每服务内存上限 1 GiB、总上限 4 GiB，CPU 绑定 6–7。NAS 的 MemoryLimit 与 CPUSet 可用，但 CFS quota 和 PID limit 不支持，因此 release_ready=false。

## 验证与证据

- 本地相关 Linux 测试：43 项通过；资源配置测试：14 项通过。最新实现使用 docker exec -i 的输入通道，不依赖容器内对临时 host 文件的读取权限。
- NAS 证据摘要及原始脱敏报告 SHA-256：[nas-a3-evidence-2026-09-25.json](../development/nas-a3-evidence-2026-09-25.json)。
- 所有未执行范围都保持明确：真实模型、合成对话、日志链、常规恢复、浏览器渲染与完整 Gateway 重新创建周期未验收；未写入 Dockge stack。

## 未完成、风险与下一步

- 重新授权路径的签发步骤已在 NAS 成功，但本次 Gateway 重新创建/鉴权恢复周期未完成。不要复用已到期 origin 或重放已消费的一次性 marker。
- 若继续验收，应启动新的显式隔离 scope，使用空闲网络和端口，执行一次完整的自然过期—重新授权—旧 Gateway 正常移除—新 Gateway 创建—鉴权 readiness—SIGTERM 停止周期。使用 Synology 已支持的 Compose create --no-build --pull never gateway 语法，并在签发后立即继续。
- CPU CFS quota 与 PID limit 不可用前，不得把该 NAS QA profile 标为 release-ready。
- 保留目录：/volume2/tianshu-v2-validation-wave1/accept-20260925-a3。不合并、不推送；由协调者审查后集成。
