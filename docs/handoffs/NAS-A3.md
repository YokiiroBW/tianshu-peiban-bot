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

## dialogue5 合成对话与重新授权完整实测

- 独立一次性 scope：`cycle-20260925-dialogue5`，项目 `tianshu-accept-a3-dialogue5`。使用协调分配的 A3 `10.205.32.0/20` 内三个子网：core `10.205.36.0/26`、egress `10.205.36.64/26`、frontend `10.205.37.0/26`；回环端口 `127.0.0.1:19543`。四服务仍各限 1 GiB、cpuset `6,7`，无真实模型或 Dockge 写入。
- 源码 pin 与上文候选相同。A4-R1 修正提交 `0ad6165` 将重新授权的 120 秒余量在公开 CLI 实际签发后测量；Python 3.12 定向测试 7/7 通过。探针提交 `501eddd` 仅在合成对话成功时输出版本化 accepted/sent 证明；解析器独立提交 `20012f4` 严格读取该 JSON 行与原有 `container_probe_passed` 成功行，定向测试 3/3 及实际 Runner→hook 离线回归通过。
- 标准合成对话于 `2026-09-25T03:06:05.591480Z` 完成，早于初次签发回执 `03:08:35.443037Z` 过期。四服务 ID 和镜像在活动观察的两侧相同，旧 Gateway 未重建、未出现重新授权标记；初次过期后另一条新 client/message/turn/reply ID 的请求于 `03:09:08.135900Z` 得到 accepted 回执和最终 sent 回复。初次回执的时间仅用作观察边界，未当作活动续期后的当前授权有效期。
- 标准校验正常 SIGTERM 停止四服务后，再保守观察 345 秒，持续核实四个原 ID 均 exit 0、非 OOM、无重启。Platform 重启健康，旧 Gateway 原 ID `6a690e8b922d9f5ff567c11bc6bd67b808f98b1382ecda4048b9e0999ca567a9` 在停机自然过期后以 exit 1、非 OOM 退出。
- 随后只调用一次 Platform 公开 CLI 重新授权，无自动重试，报告不含 origin ref；正常移除已停止的旧 Gateway 并由 Compose 创建新 ID `e1d29fc76f429ce1258566e876deb2e6dee3ad257d0d92f4a113ec8d6393d709`，镜像 ID 仍为 `sha256:10746fed0f95f138501609620368e94d4764888dab08363124dfeccc2273a7be`。四服务鉴权 readiness 通过；第三条全新 ID 的合成请求在新 Gateway 上获得 accepted 与最终 sent。签发至最终正常停止耗时 112.9 秒。最终四服务均 exit 0、非 OOM、重启数 0；三个本项目网络 endpoint 数均为 0。采样的主机 MemAvailable 最低 `18,216,517,632` 字节，超过运行时 4 GiB 保留门槛。
- 本次合成模型发布窗口是 1800 秒，低于 Platform pin 的默认上限 3600 秒；Gateway origin 初次回执 TTL 仍为 300 秒。此调整仅属于一次性合成验收输入，不改变真实模型或发布配置。`release_ready=false`。详情和报告摘要见[合成周期证据](../development/nas-a3-dialogue-evidence-2026-09-25.json)。
- NAS 实际执行的一次性操作器、后续请求探针、证明解析器和阶段预算已按字节摘要归档在[dialogue5 验收目录](../../deploy/tianshu/acceptance/nas-a3-dialogue5/README.md)。证据 JSON 将每个源码 pin 和归档摘要关联至唯一镜像标签、构建结果、镜像 ID 和最终容器 ID。严格证明解析是该一次性操作器中的同进程 hook，并非默认 Runner 能力。
- 本次旧 Gateway 的自然过期归因依据停机时长、exit 1 与非 OOM 状态，没有专用过期错误码；周期报告可核验通过，但最外层操作器退出码未另存。失败清理在 Docker inspect 不可用时只能尽力而为；镜像链无签名 OCI provenance。上述边界不改变 `release_ready=false`。
- 归档基线为 `ddaced278211c9b7c2beccc290ac9fbd5be38e69`，归档版本即本交接所在提交。本地重算四份归档文件 SHA-256 与当轮记录一致；冻结源清单摘要一致，四条源码到最终容器的关联逐项核对通过，证明解析器定向测试 3/3 通过，JSON 解析与 diff 检查通过，凭据特征扫描未发现匹配。原始 bundle 归档不在当前本地，仅保留当轮记录的 SHA-256；本次未重跑 NAS。
- 归档后的协调 diff 审查发现通用 `project_name` 正则误限为 QA/A3，导致无 NAS profile 的仓库示例名称被拒。以 `e56eccc7681783195d5fdbb50e82957a181f1778` 为基线恢复原通用正则及长度边界；NAS profile 的 QA/A3 前缀门禁、保留名拒绝和 release 拒绝保持独立。四项定向回归通过，覆盖原模板、自定义名称、保留名、长度边界、非 QA/A3 profile 拒绝、A3 接受及 A3 release 拒绝；未触碰一次性验收归档文件，也未重跑 NAS。

## dialogue1–dialogue4 保留的失败 scope

- `dialogue1` 的标准合成对话及正常停止通过，但额外首轮探针在初次回执过期前仅剩约 16 秒时被预算门槛拒绝；无重新授权或重建。后续改用标准首轮作为对照，未重放该 scope。
- `dialogue2` 在标准 `create_core` 失败；同时 A1 创建了覆盖当时 A3 计划的 `10.204.83.0/24`、`10.204.84.0/24` 网络。Docker stderr 未保存，网段重叠足以解释失败；该项目最终零容器，但 Linux 报告的 `stop_confirmed=false` 保留原值。协调随后给 A3 分配独占 `10.205.32.0/20`。
- `dialogue3` 的四服务健康、标准合成对话成功，但 hook 将“JSON 证明 + 原有成功行”整体解析为单个 JSON，导致证据解析失败；四服务随后正常 exit 0。独立无网络工具容器在 NAS 上证实 Compose exec 可捕获 stdout，该问题不是 NAS 输出丢失。解析器修正后另起新 scope，未重放原 scope。
- `dialogue4` 证实首轮和越过初次回执后的两条合成请求均 accepted/sent、活动期间四 ID 固定、停止 345 秒后的旧 Gateway exit 1 非 OOM；其 15 分钟合成模型发布到期前只剩约 174 秒，未达到恢复前 180 秒门槛，故未尝试重新授权。四服务按预期正常收尾。`dialogue5` 使用 30 分钟合成发布窗口完成后续恢复。

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

## cycle7 验证与证据

- NAS `reports/linux-executed.json` SHA-256：`65a8d1d2c1cc7cca4b03d12f1ab0af19256ca3daafa366520ae77253be857705`。
- NAS `reports/reauthorization-recovery-cycle.json` SHA-256：`66a3bec54d870f0dc0c27c20f90e2b8621bbc75ed105abb0ab9fec86c57c5e73`；报告与重新授权回执均未包含 origin ref。
- NAS `reports/bootstrap/reauthorization-result.json` SHA-256：`60cde83d58b97dc8bdfecb24754dd7071dc9d44e0581543956905d53d632b042`。
- 操作脚本 SHA-256：`a192b4d0b64e6383c83e2ca03eb24ea14060b4d009e39547cfe17169cdb5245a`。完整摘要在[nas-a3-evidence-2026-09-25.json](../development/nas-a3-evidence-2026-09-25.json)。
- cycle7 当轮没有重跑单元测试。只核验 NAS 的 liveness、完整生命周期脚本结果、最终容器/网络状态，并将候选资源计划写成文档。

## 尚未验收

真实模型调用、日志链、常规恢复、浏览器渲染和代表性持续负载仍未验收。标准校验的配置超时上限总和可能超过 300 秒初次 origin TTL；本次实际首轮及时完成，不证明所有最坏配置时长均可成功。产品化时应另行调整签发位置或启动预算。NAS 缺 CPU quota 与 PID controller 时，不将该 profile 标记为 release-ready。
