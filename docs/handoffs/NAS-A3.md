# NAS-A3 验收交接

## 目标

在隔离的 Synology A3 scope 完成四服务 Linux liveness，并验证 Gateway 在活动续期、停机自然过期、Platform CLI 重新授权、Compose 重建和正常停止之间的完整恢复周期。本验收不代表生产部署或发布就绪。

## cycle7 完整周期

- 项目：`tianshu-accept-a3-renewal7`；scope：`/volume2/tianshu-v2-validation-wave1/accept-20260925-a3/cycle-20260925-renewal7`。网络为 `10.204.76.0/26`、`10.204.76.64/26`、`10.204.77.0/26`，回环端口 `127.0.0.1:19538`。
- Compose 2.20.1 帮助确认 `create` 支持 `--no-recreate`、`--no-build`、`--pull`，不支持 `--no-deps`。恢复前核实 Platform 为原固定 ID 且 healthy、Memory/Companion 原 ID 已停止、Gateway 仅依赖 Platform。使用 `create --no-recreate --no-build --pull never gateway`，没有启动或创建额外依赖。
- 配置为 `platform_origin_renewal=true`、`model_origin_renewal_http=true`；`config-entry.ttl_seconds=300`，没有绝对 `expires_at`。初次签发回执的过期时间是签发时快照，不代表运行中同一 ref 的当前有效期。
- 初次 Gateway 在原容器 ID 上鉴权 readiness 通过；再观察 345 秒，期间每 30 秒 Platform 与 Gateway 的健康和鉴权 readiness 均通过，观察越过初次签发回执的过期时刻。该 scope 证实活动期间自动续期维持了同一 origin。
- 用 `docker stop --time 30` 正常停止旧 Gateway，Platform 保持健康，等待 TTL 加 45 秒。旧 Gateway ID `f45f663a0b2f8ea46df795ebf2ce515c62b3dfd5d0cf8b20f3747f4af9bf48e7` 重启后以 exit 1、非 OOM 退出。此结果将停机超过 TTL 与活动续期区分开。
- 单个有界脚本通过 Platform 公开 CLI 签发新 origin；报告不含 ref 且没有自动重试。正常移除已停止的旧 Gateway（未使用 `-f`，未移除卷），随后 Compose 创建新 Gateway。新 ID 为 `b314afadb11a733122ef254748f8431565e3c4a81fe9df7440f70fc1142447cb`，镜像 ID `sha256:10746fed0f95f138501609620368e94d4764888dab08363124dfeccc2273a7be`；UID/GID `10001:10001`、内存 1 GiB、cpuset `6,7`、restart policy `no`，绑定路径和 project/config labels 均通过检查。
- 新 Gateway、Platform、Memory、Companion 的鉴权 readiness 均通过。四服务随后按 Companion、Gateway、Memory、Platform 顺序以 SIGTERM 正常停止，exit 0、OOM false、restart count 0。全 A3 检查共 19 个既有容器，运行数 0；15 个 A3 网络均无 endpoints，均保留未删除。
- `release_ready=false`。未调用真实模型，未写 Dockge，A1/A2 未操作。

## 先前 scope 与失败记录

- 之前两个自然过期观察发生在服务干净停止之后；当时没有覆盖“Gateway 活动期间越过初次签发回执有效期”的探针。cycle7 完成了该区分。
- `renewal` 因复用已存在镜像标签而在构建前被拒绝；没有签发或创建容器/网络。
- `renewal2` 的四服务 liveness 通过；后续脚本在启动前因源码工具目录路径错误退出，没有重新签发或改变容器。该 scope 保留且不重放。
- `renewal3` 和 `renewal4` 在本地合成 TLS 初始化时分别遇到 AKI 与 CA `keyCertSign` 扩展错误，没有上传 NAS。`renewal5` 在 bundle 完整性检查时因 Python `__pycache__` 退出，没有构建或启动服务。`renewal6` 的 liveness 通过，但恢复脚本的虚拟环境路径检查失败，未启动服务或重新签发。以上 scope 均保留；细节与状态见证据 JSON。

## 资源候选与限制

- 当前 `nas-cpuset-qa-v1` 是每服务 1 GiB 硬内存上限、总上限 4 GiB、cpuset `6,7`。这描述已配置的上限，不是已证明的最低需求。
- NAS 的 MemoryLimit、CPUSet、CPUShares 可用；CPU CFS period/quota 与 PIDsLimit 不可用。cpuset 只限制落在哪两个 CPU 上，不能单独限制四服务总 CPU；CPU shares 只是竞争时的相对权重。PID hard limit 缺失仍是发布阻断项。
- cycle7 只做无模型启动/闲置与 readiness 采样：每服务快照约 29–53 MiB，主机 MemAvailable 最低约 17.1 GiB；Docker stats 的 PIDs 样本为 0，不能作为真实 PID 用量。该观察不足以设定服务最低资源或长期峰值。
- steady NAS 候选与复核探针见[资源计划](../development/nas-a3-resident-resource-candidate.md)。这只是待评审方案，未写入 Compose/Dockge，也未改变 `release_ready`。

## 验证与证据

- NAS `reports/linux-executed.json` SHA-256：`65a8d1d2c1cc7cca4b03d12f1ab0af19256ca3daafa366520ae77253be857705`。
- NAS `reports/reauthorization-recovery-cycle.json` SHA-256：`66a3bec54d870f0dc0c27c20f90e2b8621bbc75ed105abb0ab9fec86c57c5e73`；报告与重新授权回执均未包含 origin ref。
- NAS `reports/bootstrap/reauthorization-result.json` SHA-256：`60cde83d58b97dc8bdfecb24754dd7071dc9d44e0581543956905d53d632b042`。
- 操作脚本 SHA-256：`a192b4d0b64e6383c83e2ca03eb24ea14060b4d009e39547cfe17169cdb5245a`。完整摘要在[nas-a3-evidence-2026-09-25.json](../development/nas-a3-evidence-2026-09-25.json)。
- 本轮没有重跑单元测试。只核验 NAS 的 liveness、完整生命周期脚本结果、最终容器/网络状态，并将候选资源计划写成文档。

## 尚未验收

真实模型调用、合成对话、日志链、常规恢复、浏览器渲染和代表性持续负载仍未验收。NAS 缺 CPU quota 与 PID controller 时，不将该 profile 标记为 release-ready。
