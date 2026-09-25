# NAS-A1 — 恢复读回与语义验收

## 目标与结论

最新隔离合成 A1 r2h scope 已完成 source 语义准备、九 owner 登记与正常停写、一致性快照和 `restored_disabled` 恢复。克隆入口因来源和 unknown 请求截止组成的最短预算低于固定 180 秒入场阈值，返回 `drill_origin_lifetime_insufficient`；没有创建 claim、克隆目录或克隆容器，六项克隆 API 断言均未运行。许可签发时最短预算为 198 秒，本次是入场阈值拒绝，不能称产品来源已经过期。状态保持 `needs_validation`、`release_ready=false`。前一轮 r2g 的五项克隆语义与有界观察已通过，但缺独立 Gateway API 断言，其 `partial_functional_coverage` 结论不变。

## A1 r2h 单次 source、恢复与许可入场（2026-09-25）

固定代码提交 `07dfbb81bffd814834f169b6b7d061b9c7308512`，NAS 执行包 tar SHA256 `0a891b771a52d781a54f640c01f908d29f36dcda971a949b09ce424df3b7d2b5`。唯一新 scope 为 `/volume2/tianshu-v2-validation-wave1/accept-20260925-a1/scope-a1-r2h`，UUID `b9e36ccb-45df-4333-be96-28ddcafc6f29`。原始候选产品镜像和最终 release manifest SHA256 `ce68ed58f4dbbaac6b9a09fc3632fd2522afc97501a63b790e1447b0af9fc4e2` 未变；初始化 manifest 仅将 `web_text_dialogue.enabled` 从 true 设为 false，SHA256 `de3526cea7a20a56051b1a6db92fe9c40f8a3a204a816a61625c1dcdfb57c932`。同 scope 的两次初始化前受控拒绝分别来自误用最终 manifest 和空目录 guard 逻辑，均留存原收据；经 A4 复核的修正 helper 后初始化成功，没有另开 scope。执行仅使用 NAS 已验证的 CPython 3.12.14 绝对路径及新 `tooling-r2h`，没有把隔离 wheel 目录加入执行路径。

source 五段、clone 七段均在协调者分配的 `10.205.48.0/24` 中，各为互不重叠的 `/28`；尾部 `10.205.48.192/26` 未分配。12 个专属回环端口与 9 owner 的 7.5 GiB 内存上限通过初次和启动前预检。source 的四 core 全部 healthy、五 OBS running。两条虚构消息各为 `closed_unknown`、各一条 unknown 回复；Companion committed event、Memory 三条候选记录和 Gateway 账本均经固定产品路径读回。遗忘绿色记录后绿色及同源阅读为 `no_match`，红色仍可选；单独撤销输入凭据后红色仍可选，经 Platform revision 2 retract/fanout 后为 `no_match`。模型 v3 撤销后 Gateway→Platform snapshot 为 HTTP 410/`forbidden`。v4 离线上游两组 unknown 与两组成功控制，重启前后两条 unknown turn/reply 相同。源端 Gateway 基线为四个关联组、两组 unknown、两组成功控制；克隆的独立 Gateway HTTP 断言尚未执行。

source 准备期间另有两处受控续接：合成 v2 发布脚本在完成发布后因分类/Memory handoff 尚未绑定而停止，绑定后继续到 v3；遗忘脚本首次因正在运行的 Memory 尚未载入新本地凭据返回 401。只重载本 scope 的 Memory，用**同一个**已写入的遗忘请求经产品 CLI 确认，并由 API 返回 HTTP 200、`authoritative_state=tombstoned`；之后的绿色、红色与阅读读回如上。Memory 重载在最终九 owner 身份采集与 authority 登记**之前**。这次续接的原始失败与成功输出从任务会话提取至忽略目录，文件明确标为事后提取；失败脚本本应写的 `forget-approval-result.json`、`forget-revision-result.json` 未在冻结 source 补写，交接不冒充原始现场文件。

九 owner runtime identity SHA256 `e74697ae84fa0fe6dc3f1fa31330bab2104071fc93af9fcf99fa615df7cebb80`；authority `84d542a8-1d4e-46c1-a907-2fe723a9ecad`，registration SHA256 `63161b4bddc81d707d0bc4a8aa8c25f50510ee189344f974c8741146c2508166`。`linux-prepare` 首次 execute 漏传 NAS Docker 绝对路径，被受控拒绝且没有登记；带绝对路径的同 authority execute 成功。正常 `linux-rehearse` 停止九个 source owner，全部退出码 0；备份 `a1-r2h-backup-20260925` 的 snapshot SHA256 `286fb6e4c8f0bf2c641cc70296e0c4f4bbb81a56ad529a56b0b9956ff98f3b01`，恢复目标 `a1-r2h-restored-20260925` 为 `restored_disabled`，verification SHA256 `5cfb011e6d7120237464bc03fde6a6257d428743a24c272627325185b283f5eb`。

clone 占位输入共 184 文件、六项断言（原五项加独立 `gateway_usage_readback`），独立 Gateway 源端 CLI 预探针已记录，七段 clone 网络和六个端口在 source 运行时、source 停止后两次复核均无冲突。最终签发的 config/actor 来源分别在 14:36:43.159696Z、14:36:44.262127Z 到期，unknown 请求截止 14:36:29Z；最短预算取 unknown 截止。`inputs.json` SHA256 `8215df7b571890c5a2336a0fc3bd04a7d823efc34c7fdafce569c116fbd8c8db`。单次许可 ID `ca0cb455-dff9-4ef8-a64d-a6c34cdbb326`、SHA256 `2ca8a2251d9932291bf624bc05c25d7a25232410c80f90c79e20794d651aaad9`，14:33:10Z 签发时入口检查返回剩余 198 秒，超过 180 秒阈值；许可自身的 600 秒有效期至 14:43:10Z，不能延长更短的产品来源与 unknown 截止。

| 阶段（UTC） | 实测结果 |
| --- | --- |
| 14:31:40.274–14:31:44.960 | v5 与双来源密封，工具单调计时 4.686 秒 |
| 14:31:55.744–14:32:52.467 | `linux-rehearse`，工具单调计时 56.724 秒；九 owner 在 14:32:00–16Z 退出，14:32:22Z 写入 snapshot，14:32:45Z 写入禁用态恢复标记 |
| 14:33:09.136–14:33:11.037 | 许可签发，工具单调计时 1.902 秒；最短剩余 198 秒 |
| 14:33:36.550–14:33:37.347 | `drill-clone` plan 通过，工具单调计时 0.797 秒 |
| 14:33:47.045–14:33:52.120 | 唯一一次 `drill-clone --execute` 返回 `drill_origin_lifetime_insufficient`、退出码 1，工具单调计时 5.075 秒 |

签发完成到 execute 开始相隔约 36 秒，其中约 25.5 秒在查找已知命令格式、约 9.7 秒在 plan 之后；这些步骤可以在密封前准备或紧接许可执行。14:33:47Z 时最短截止还约 162 秒，执行内实际复核发生在 5.075 秒调用窗口中，约剩 157–162 秒；工具没有输出该次精确秒数，故这里只给时间窗推算。**来源当时尚未到期**，但已低于已审定的 180 秒 claim 闸门。`drill-clone` 在写 claim 前拒绝：`drill-claims/<permit-id>.json` 不存在、克隆目录和两个克隆项目容器均不存在。没有重试、第二许可或第二 scope，也没有降低 180 秒阈值。source 九 owner 均 exited(0)，五个新网络端点数均为 0，恢复目标仍禁用；r2h 没有克隆功能读回结论。

本轮可在当前工具内改进的是把命令、静态克隆预检和待签发 plan 的可检查部分提前准备，并用一个有硬超时的顺序协调器连续执行密封→停写/恢复→许可→plan→一次 execute；任一阶段预算不足即停止，不重复 claim。它能减少本轮约 36 秒的人为间隔，但 300 秒产品来源寿命仍可能被主机负载和恢复时长耗尽，不能据此保证验收。产品化方案应由 Platform/恢复边界提供**快照绑定、仅限克隆只读端点、用途及租期受限、一次性消费**的恢复授权，在禁用态恢复与输入哈希核验之后由独立受信发行方签发；它不能修改冻结 source/备份、不能复用旧许可，且须把来源失效、撤销、审计与九 owner 正常清理纳入双方契约。该方案仅为后续架构建议，本次没有改产品或恢复工具代码。

机器证据索引见 [`NAS-A1-r2h-evidence.json`](NAS-A1-r2h-evidence.json)。NAS 私有输入、来源原文及完整会话仍只在隔离现场与本工作树忽略目录；索引仅存路径、哈希、状态和脱敏时间线。最终 A4 独立复核尚待协调者安排。

## A1 r2 配置失败的确切原因与 r2b 接续（2026-09-25）

r2 scope 位于 `/volume2/tianshu-v2-validation-wave1/accept-20260925-a1/scope-a1-r2`，UUID `0e141711-16e9-44cc-a0d3-af32f3297ae8`。首次公开 `release.py configure-observability` 省略了 `--projects`，包装器只返回 `observability_public_cli_failed`。对相同合成输入在隔离本地副本记录子命令的白名单错误码后，确定固定 OBS 词表被 `configure.py` 以 `vocabulary_source_commit_mismatch` 拒绝。加上 `--projects` 指向四个固定产品 Git 对象后，公开 `snapshot.py` 与 `configure.py` 均返回 0；本地 Windows 包装器只在最终 Linux 权限步骤返回 `linux_permissions_not_verified`。NAS r1 的 OBS 输入原有 `commits.json` 和 `vocabulary.json`，与这一诊断吻合。旧 NAS-01 资源说明早于已集成适配，不能用它判定当前 QA profile 不支持 OBS。

r2 的 `INCOMPLETE` 标记、部分 OBS 输入和固定工具导出保持原样，`observability/` 输出未产生；再次只读检查没有 r2 容器或 Docker 网络。没有清标记或重放未完成的 r2 包。接续 scope `scope-a1-r2b` 使用新 UUID `9aaffd16-009c-4617-99e1-802ad21759a8`，保留相同固定 candidate、A1 专属网络/回环端口、合成 TLS 和 `nas-cpuset-qa-v1`；公开 OBS 配置增加 `--projects /volume2/Dockers/tianshu-v2-validation/wave1-20260923a/repos`，返回 `observability_configured`。四个产品的 manifest 固定提交对象已逐一核实存在，NAS 上 OBS 包装器、恢复资源适配和 drill 的源码哈希与本工作树一致。

r2b 的四个 core owner 内存上限各 1 GiB；五个 OBS owner 上限合计 3.5 GiB，合计 7.5 GiB。两份 Compose 均通过 `config --quiet`，网络使用 `10.204.80.0/24` 至 `10.204.84.0/24`。OBS 配置后首次 Linux 权限预检指出新建 core `data/` 与 `logs/` 八个空目录仍归 root；只对这八个目录设为 `10001:10001`、`0700`。再次预检指出合成配置更新后的两个私有 env 文件为 `0644`，只将其设为 `0600`。公开 `preflight --check-permissions` 随后返回 `package_valid`，检查包含包完整性、合同、TLS、OBS Compose 和 Linux 权限；此时服务尚未启动。后续执行结果见下节。

## A1 r2b 源端、恢复与克隆执行结果

r2b source 通过首次 Memory 迁移，四个 core owner 达到健康、五个 OBS owner 同时运行。合成消息两轮均为 `closed_unknown`，各有一条 `unknown` 回复；Companion source-facts API 读回 `reality=fictional` 的 committed event。Memory 通过产品候选提交产生三条可选事实；本地用户确认遗忘和 revise API 将绿色事实置为 `tombstoned`，绿色与同源阅读事实返回 `no_match`。仅撤销输入凭据后，红色事实仍可读；经 Platform revision 2 `retract` 与正常 fanout 后红色事实返回 `no_match`。Platform 撤销模型 v3，Gateway→Platform snapshot 对 v3 返回 HTTP 410/`forbidden`。两次离线上游 unknown 调用后，Companion turn/reply 在重启前后保持相同；Gateway 日志有两组 unknown 和两组成功控制调用。上述是 source 证据，不计入恢复副本断言通过。

九 owner runtime identity SHA256 为 `4124e0468045589bc846e63757db6316b1e33986b83ffe748639ffc4d762dd21`。`linux-prepare` 登记 authority `89655ec6-d541-4630-8fa2-dfcd5b525cfd`，registration SHA256 `a1308307d552adb3de9d607734259ccbcad570f52880833569196ef97ae59530`。`linux-rehearse` 一次执行按正常 SIGTERM 停止全部九个 source owner，退出码均为 0；备份 `a1-r2b-backup-20260925` snapshot SHA256 `843edc0659f38e1826f7758bcbee44bf356048c190ba3eb1b0b31e651d5e6c9c`，恢复目标 `a1-r2b-restored-20260925` 为 `restored_disabled`，verification SHA256 `f89569eb32b85bdb9338a9c19117e82b7651bdbc191851512b8078482ec4ce6b`。source 与恢复目标保持停止/禁用。

克隆 `a1-r2b-clone-20260925` 使用 183 个独立输入文件、专属 TLS/凭据、`10.205.0.0/24` 至 `10.205.6.0/24` 网络及回环端口 19612–19616、19621。静态输入、来源、宿主资源和一次性许可计划通过；许可 ID `f9763c43-acd3-4320-a06c-4f15fc3b1efa`，SHA256 `6af82c9ab174fafa82d5851724d697d8dabafe151b0b0ae84529b4101bf4bbd1`。单次 execute 启动全部九个 owner，四个 core 健康、五个 OBS 同时运行；`data_readback` 通过，响应 SHA256 `59237c6db3f73ae5e97d1567079c0af3d89c77a8e9280a7a1d5296e560560937`。随后 `forgotten` 失败，`drill-result.json` 为 `drill_failed_or_cancelled`、`release_ready=false`，仅保留首项通过；其余三项和 10 秒观察未运行。九个克隆容器均正常退出码 0，claim 和现场保留，许可未重放。

只读定位发现 r2b 克隆输入有凭据映射错误。source `private/companion.env` 的 `TS_CORE_MEMORY` 去除 env 引号后，等于 source `config/memory/settings.json` 的 `callers.companion.token`；克隆准备脚本独立轮换前者，却未同步更新后者。克隆两者分别为 64 与 43 字符且不相等；`private/token-memory-select` 等于轮换后的 `TS_CORE_MEMORY`。这一差异会在请求进入 Memory 鉴权后导致 401；但后续定位确认回环验收请求发送的 Host 也与 Memory 允许列表不符，产品会在鉴权之前拒绝。r2b 未保存实际 HTTP 状态，不能把当时失败定为 401。同一准备脚本也未同步 `TS_MEMORY_CORE → memory.source_sync.core.token` 与 `TS_MEMORY_PLATFORM → memory.source_sync.platform.token / callers.companion.issuer_token`。另外 unknown 请求的截止时间为 03:17:38Z，克隆约 03:17:44Z 开始关闭，期限留得过短。上述是验收夹具问题，没有产品恢复数据丢失的证据。

## A1 r2c–r2e 准备记录与 r2f 完整执行（2026-09-25）

r2c 使用不匹配的后处理 manifest 初始化，固定候选检查以 `feature_configuration_mismatch` 拒绝，未启动容器。r2d 初始化后首次迁移时 Docker 默认地址池已耗尽；没有给所有辅助网络显式分配子网，迁移未完成。r2e 使用显式子网并启动九 owner，但漏做 A1 `prepare-synthetic`，Platform 没有合成 provider，模型发布检查拒绝；已停止并移除 r2e 容器与网络，保留现场。这三次均是范围内准备失败，未登记 authority、未备份、未签发克隆许可；r1/r2/r2b 证据没有被改写。

r2f scope `9de11d89-3149-4fab-86d8-fab1f065ebf1` 使用原始固定 candidate、预先完成的合成配置、专属 TLS/凭据、`10.204.90.0/24` 至 `10.204.94.0/24` 网络和四个 core、五个 OBS owner。公开 preflight 与两份 Compose 检查通过。Memory 两项首次迁移通过。两轮合成消息均为 `closed_unknown`，各保留一条 unknown 回复；Companion source-facts API 读回虚构 committed event。Memory 通过产品候选流程提交三条事实，遗忘绿色事实后绿色和同源阅读事实为 `no_match`；仅撤销红色事实的输入凭据后仍可读，经 Platform revision 2 retract 与正常 fanout 后为 `no_match`。模型 v3 撤销后的 Gateway→Platform snapshot 返回 HTTP 410/`forbidden`；新合成模型 v4 的 Gateway 日志满足两组 unknown 和两组成功控制调用，Companion turn/reply 重启前后稳定。上述仅为 source 语义证据。

首次 r2f `linux-prepare` execute 因 OBS 容器的 Compose 工作目录标签指向 source 根目录而以 `compose_directory_mismatch` 拒绝，尚未登记。只在 r2f 范围内按 `source/observability` 作为项目目录重建五个 OBS owner，原 runtime identity 移入 `runtime-identity-initial-rejected.json` 保留，重新采集九 owner。最终 runtime identity SHA256 `9f09b62ee6f19d9107a3cef7cf16051ab2ede69d4bea690a888cd85bbcd45f32`；authority `4896b5d0-252b-4a68-9c02-4481e64efb0a` 登记成功，registration SHA256 `5cee58aee5e54dbf0587039f4da5175d1567fffb4dcd2e270f5c18f11444b67a`。新合成模型 v5 和两个短时 origin 在停写前签发。`linux-rehearse` 九 owner 正常停写，备份 `a1-r2f-backup-20260925` snapshot SHA256 `d9b0bac2951be846c7a47f3058f2e27b91511b0be583b7eb40985f3eb58800f5`；`a1-r2f-restored-20260925` 为 `restored_disabled`，verification SHA256 `53d59da93081df3f9324313535f8552c2f50154441ca16b842a9483583bfead5`。

r2f 克隆输入共 183 个文件，独立网络 `10.205.7.0/24` 至 `10.205.13.0/24`，回环端口 19812–19816、19821。五项断言、输入来源和宿主资源的静态校验均通过；Memory 的 Companion bearer、Platform issuer 和两条 source-sync 凭据在克隆配置与 env 中逐项相等。许可 ID `4810fd1a-185f-4b2e-a3ca-fb754ddc54fd`，SHA256 `04e0383e3356abdb652121a222c4fe2cac5c75e2bede0933d33d1290e6059633`，仅执行一次。克隆九 owner 同时运行，四个 core 健康、五个 OBS 运行；Companion `data_readback` 通过，响应 SHA256 `e9b0f57046346bdaf6cc9a6b3954dd4a8029682b3141fc3672f4b7ec924c6f11`。Memory `forgotten` 未通过，drill 返回 `drill_failed_or_cancelled`、`release_ready=false`；其余三项与 10 秒观察未执行。结束后 source 和克隆各九个容器均为 exited(0)，恢复目标仍禁用，claim 为 `claimed`。

只读诊断确认 r2b 的凭据映射错误在 r2f 已消除：克隆 `private/token-memory-select`、Memory `callers.companion.token` 与 Companion `TS_CORE_MEMORY` 相等；对应 issuer/source-sync 凭据也相等。`forgotten` 请求通过 Memory 发布合同的静态校验，caller 允许 `select`；Memory TLS 证书包含 `127.0.0.1` 且由克隆 CA 验证通过，Compose 发布了目标端口 8130 到回环 19813。确定的验收边界错误是 HTTP Host：r2f 容器实际启动参数只允许 `memory.internal:8130`，原恢复工具发送 `Host: 127.0.0.1:19813`。固定产品的 `server_runtime.py` 在诊断中间件之前对该 Host 返回 HTTP 400/`invalid_host`，与克隆 Memory 实例只有启动、就绪、停止记录而无 `request.started` 一致。r2f drill 未记录实际 HTTP 状态，400 是由固定代码和容器参数确定的预期响应，并非已保存的现场 HTTP 收据。Platform 在该时间窗仅有周期性健康请求；没有产品遗忘语义失败或恢复数据丢失的证据。短时 origin 截止 04:39:47Z/04:39:48Z，unknown 请求截止 04:39:34Z；克隆在约 04:38:41Z 停止，本次失败不等于这些期限已过。

## A1 r2g 隔离合成恢复读回（2026-09-25）

新 scope `/volume2/tianshu-v2-validation-wave1/accept-20260925-a1/scope-a1-r2g`，UUID `2b1f206a-5794-492e-a005-aad9befb5e84`，固定候选与原始四产品提交不变，恢复工具使用独立 `tooling-r2g` 快照及 Host/失败收据修复提交 `a60bf049892180bf60c4a12284630c39a6c69d78`。准备前检查 155 个 Docker 网络、宿主路由、12 个回环端口与内存余量；源端使用 `10.204.95.0/24` 至 `10.204.99.0/24`，克隆在 A1 预留 `10.205.0.0/20` 内使用七个互不重叠的 /26 子网（`10.205.14.0/26` 至 `10.205.15.128/26`），每段至少 62 个可用地址。两份 Compose 与公开权限预检通过。OBS 首次启动采用默认项目名；核对五个实例的挂载全属 r2g 后，仅移除这五个新实例及其网络，并按 r2g 专属项目名重新启动，九 owner 身份登记前完成校正。Gateway 初次启动缺短时来源而退出；Platform 启动后通过公开 CLI 签发并绑定来源，只重建 r2g core 容器，随后四 core 均健康。

Memory schema 2/3 首次迁移通过。源端两条虚构消息均为 `closed_unknown`，各有一条 unknown 回复；Companion source-facts API 读回 committed event，Memory 产品候选流程提交三条事实。遗忘绿色事实后绿色及同源阅读事实均为 `no_match`，不同来源红色事实仍可选；仅撤销其输入凭据仍可选，Platform revision 2 retract/fanout 后为 `no_match`。模型 v3 撤销后 Gateway→Platform snapshot 为 HTTP 410/`forbidden`。发布 v4 后离线上游两组为 `result_unknown`，Gateway 共有两组 unknown 与两组成功控制；Companion 的两条 unknown turn/reply 在重启前后稳定。没有真实模型请求或产品数据库直写。

九 owner runtime identity SHA256 `3e124f81a211453ba3765fc802c15902fa012cb142dcbd7b37d4459c46fbed2e`；authority `0617ca53-071a-4fc4-9dd1-82a47b90d07e`，registration SHA256 `5e31a497078ca72fb2c08a2f0f5696f4a7c0ed19eec4801417a9ed21e4803e9b`。停写前发布合成模型 v5，并签发克隆专用 config/actor origin。`linux-rehearse` 正常停止九 owner，备份 `a1-r2g-backup-20260925` 的 snapshot SHA256 `9d88242dab67723d387b7b7df1cb7b1b9ad107420bab21c51ff2d946a65f793e`；新恢复目标 `a1-r2g-restored-20260925` 为 `restored_disabled`，verification SHA256 `18526ea304185d5f9617ca87c7317e558061d683939c0cfb77235c03276f7d6d`。

克隆使用 183 个独立输入文件、专属 CA/TLS 和轮换后相互匹配的跨服务凭据；七段 /26 子网和回环端口 19912–19916、19921 的 IPAM/路由/端口/容量检查通过。静态输入与来源验证通过，Memory `--allowed-host memory.internal:8130` 在许可前从克隆 Compose 核实；TCP/TLS 仍只连回环。许可 ID `e0c12ffc-181d-4dd4-8549-f3ba282817a7`，SHA256 `75acb564fee81436a09a27fda9c5d9809f0a1590bef1260f703750897f800a0a`，仅执行一次。克隆四 core 在断言期间 healthy，五 OBS owner 同时 running；五项断言依次均为 `passed`。10 秒观察实际持续 12.428 秒，窗口内九 owner 状态核对四次、Companion readiness 核对四次，API 可见的两条 unknown turn/reply 前后不变；Gateway 四个关联组（两 unknown、两成功控制）及 accepted/started/finished 计数前后不变。观察只证明本次有界窗口内无重发，不外推为永久保证。

最终 `drill-result.json` SHA256 `b31260604e67341f9bed1d01d460522725e69a5699b02f4ca77ed752221da733`，状态 `partial_functional_coverage`，五项语义均通过、`missing_semantics=[]`，但 `missing_products=["gateway"]`，`release_ready=false`。克隆没有独立 Gateway 功能 API 断言；已有 Gateway 日志计数和源端控制调用不能代替克隆的产品功能读回。claim 为 `claimed`；source 与克隆各九个容器均 exited(0)，四项目 running=0，恢复目标仍 `restored_disabled`。现场及旧 scope 均保留，不重放许可、不改写绑定快照。

## r2g 网络偏差与只读现场复核

创建 r2g 前的宿主预检检查了 155 个 Docker 网络、路由、12 个回环端口和 `MemAvailable=18,915,728 KiB`，对拟用的 12 个网段均未发现冲突。这只证明当时没有碰撞；我误将明确分配的 `10.205.0.0/20` 理解为仅限克隆，实际新建的五个 source 网络 `10.204.95.0/24`–`10.204.99.0/24` 均在该分配之外。这是地址分配偏差，不应把无碰撞预检写成合规批准，也不能称这五段是历史网络。创建时间为 2026-09-25 19:43–19:45 +08；七个克隆 /26 网络创建于 19:58 +08。网络与容器保持现场，不以清理旧范围释放空间。

只读 Docker 库存现有 167 个网络，其中 A1 相关 42 个，端点数均为 0；`10.205.0.0/20` 的 64 个 /26 槽位占用 63 个，仅 `10.205.15.192/26` 空闲。原先的预检结果没有在执行时另存原始文件；交接目录的 `r2g-precreation-preflight-transcript-copy.json` 是从当时工具输出逐字补存的副本，不能冒充同期原始收据。预检脚本 SHA256 `78decc060f128c0315a0c19c5cc3966b164c0c14c1dfcb88e602f5c9fc751e9c`，副本 SHA256 `f9f1c174fd9fbe26c2823054fb6154af139802f4bfc7e1a7251db43c8b604a8f`；当前原始 Docker inspect 库存 `r2g-network-inventory-20260925.json` SHA256 `548efeac238c92d61dc0a12a9e81256f95fbd692eb6afaeb3b5153c0b708f426`。这些文件在本 worktree 的忽略目录 `.runtime/nas-a1-20260925/reports/`，不入库。

## Gateway 独立只读断言的本地定义与验证

本次接续基线为 `e33e8ef28b681510f6273afdaee20a45cd6478dd`；改动限于恢复验收工具、定向用例及本交接夹具，没有修改固定产品仓库。

固定产品 Gateway 提交 `601974194042641c5a85cc3c061cbd1880d7daf1` 发布 `GET /internal/v1/model-usage`，按已认证的 Chat `service` 读取用量账本；它不调用上游，也不执行产品业务写入，但路由的 `observed` 包装会写一条 `request.accepted` 诊断日志。拟议断言见 [`NAS-A1-gateway-assertion.proposed.json`](NAS-A1-gateway-assertion.proposed.json)：Gateway 克隆 TLS 回环端口 19914、明确 UTC 半开窗口、`view=attempts`、身份 `companion`、4 条合成请求 ID/结果、2 成功及 2 未知，并核对 `coverage` 无截断/未计量。凭据文件 `private/token-gateway-usage` 应在**新的**独立输入中由轮换后的 `TS_CORE_GATEWAY` 写入；r2g Gateway 注册的 `companion` 凭据引用正映射至该环境变量。夹具是 r2g 冻结数据的设计样例，绝不是已消耗许可的补丁；新 scope 的请求 ID、时间窗、端口与凭据须从其合成数据重新固定。

只读产品 CLI 实际读取的是 **r2g 已停机克隆的持久 Gateway 账本** `/volume2/tianshu-v2-validation-wave1/accept-20260925-a1/scope-a1-r2g/deployments/a1-r2g-clone-20260925/data/gateway/diagnostics.sqlite`，不是源端、备份或 `restored_disabled` 目标；账本前后 SHA256 均为 `1e0f2a47c1b7c5286eb3b3904d5ab6be5b42739aa3e3bb494e0ded53eb57e959`。同一固定产品 CLI 的正例与夹具递归子集匹配；前一日空窗口得到 0，错误身份 `not-registered` 退出 3 且无报告。脱敏探针 `r2g-gateway-usage-probe-attempts-20260925.json` SHA256 `99e23c7bfab161669a7339e2969649dbb2c3009da2bbb5a6573b58cf79e76bc0`，位于上述忽略目录。固定产品的五项定向测试已通过，覆盖 HTTP 身份隔离、撤销/失效凭据拒绝、HTTP 与 CLI 对同一账本的读回及 CLI 身份拒绝。这些是固定产品行为与停机克隆 CLI 证据；**新的克隆 HTTP 断言尚未执行**，r2g 总结论不变。

本地恢复工具现接受独立 ID `gateway_usage_readback`，强制它最后执行、走上述 Gateway GET、指定 CA/独立凭据、固定 4 条尝试与计数，并要求原有 `runtime_observation`。签发许可前还核对 Gateway 的 `companion` 注册引用指向 `TS_CORE_GATEWAY`，Gateway/Companion 两份 env 与独立 token 文件三者一致。该 GET 带工具生成的 32 位关联 ID。读取前后 Gateway 账本及 WAL 哈希、Companion API 可见的两条 unknown turn/reply 必须不变；在**被跟踪的** accepted/started/finished 事件计数中，该关联 ID 只允许新增一条 `request.accepted`，旧关联组及其他新组均不得变化。产品仍可正常写 `request.finished` 等诊断记录，这里不声称总日志仅一条。待这条记录可见后才取观察窗基线，窗口结束仍要求每个关联组的被跟踪计数完全一致；额外上游事件、额外无关 accepted、账本写入或未知回复变化均失败。旧五项 ID 与旧观察门槛保持兼容。当前 `drill_passed` 只在新许可真实完成 Gateway HTTP、原五项、九 owner 与观察后才能产生；不能直接改 `missing_products`。

按已验证 r2g 拓扑，独立 source 与克隆分别需要 5 与 7 个不重叠网络；克隆七段 /26 每段 62 个可用地址，实际所需端点按 core、egress、frontend、access、observe、storage、OBS access 分别为 4、1、1、4、4、2、2。九 owner 的内存上限合计 7.5 GiB，既有宿主预检门槛为可用至少 11.5 GiB（含 4 GiB 余量）；r2g 克隆单次执行约 2.5 分钟，许可运行上限 540 秒/有效期 600 秒，短时来源约 5 分钟。拓扑原始报告 `r2g-clone-topology-20260925.json` SHA256 `cbcef04d65220e1afd54d16fbd78ff750f2c4605f16bcc239078ed579fcbf4b7`。当前预留范围只余一段 /26，无法支持相同独立拓扑；后续须由协调者重新明确分配 source 与克隆全部地址并完成新的碰撞/容量检查。r2g 的短时来源已过期，恢复工具的 `restoration_facts` 固定原 authority/restore 内容，独立输入文件集也不允许注入产品数据库，故不能从旧备份直接签新许可跳过新 source/快照。

## 新 scope 的本地准备与来源寿命闸门

协调者在主仓库文件 `docs/development/nas-a1-network-supplement-2026-09-25.json` 明确分配 `10.205.48.0/24` 内的 12 个 /28 网段：source 五段 `.0/.16/.32/.48/.64`，clone 七段 `.80/.96/.112/.128/.144/.160/.176`；`.192/26` 保持未分配。该文件 SHA256 `5667b859a8c7bb62cff8f9e88d580dce8620ff3960a68a9f78a959846ae6b893`，状态仍为 `allocated_pending_independent_code_review_and_execution_release`。本 worktree 忽略目录 `.runtime/nas-a1-20260925/next-a1-local/` 的 `project_networks.py`、`network-preparer-diff.patch`、`local-preflight.json` 分别为 SHA256 `451b91299b3e587d757fc25bebecb1ef654c0fb66bdea227c845909c452ee9f4`、`682517b5b05597a48102a934b58aafa06e28fee009dae3d696a908c583a9ba5f`、`9ec0ab4e56bdb1934319fb40cbd662ef96f87eeaed0fdd23eec18f7c27f09644`。本地投影确认 12 段互不重叠、最大每段 4 端点、固定回环端口不变，生产者网络校验与 OBS 消费者网络校验均通过；投影只覆盖网络与地址配置，不是可部署包，也没有 NAS 写入。创建前还需对当时宿主网络、路由、回环端口及内存重新预检，并由协调者放行。

本地投影中的 source core 固定 IP 为 Platform `10.205.48.10`、Companion `.11`、Memory `.12`、Gateway `.13`；clone 为 Platform `10.205.48.90`、Companion `.91`、Memory `.92`、Gateway `.93`。source 回环端口 Companion/Memory/Gateway/Platform/Grafana/Guard 为 `19512/19513/19514/20121/20115/20116`，clone 为 `19912/19913/19914/19921/19915/19916`。这些数字只来自协调分配与本地投影，执行前仍需用新 scope 的实际 Compose、IPAM、路由和端口占用重新核验。

新 scope 必须有新的 source、clone、项目名、专属 TLS/凭据、authority、备份、恢复目标和一次性许可。产品 Platform 在 source 运行时按 `config-entry` 与 `web-source-actor` 分别执行正式 `issue`；把**完整** `run_platform_cli(..., "issue", ...)` 返回对象及其各自的 `{"entry_id": ...}` 请求原样置入 clone 私有输入 `private/a1-{config,actor}-origin-{issue,request}.json`，加入 `inputs.json.files`，重新计算 `config_sha256`、`inputs_sha256` 和 permit 哈希。`inputs.json` 同时写 `"a1_origin_admission":{"minimum_remaining_seconds":180}`。Gateway env 的 `TS_GATEWAY_ORIGIN` 必须等于 config ref；`model_revoked` 请求用 config ref，`forgotten`、`source_revoked`、`unknown_no_resend` 请求用 actor ref，后者另有具体 UTC `deadline_at`。私有原文和引用不进 Git 或报告。收据与密封哈希只是本轮固定受控输入，不能称密码学产品认证；工具还只读核对已正常停机、已登记且与快照一致的 source Platform origin 行的同一 ref、entry、digest、未撤销状态和实际到期时间。Gateway 正常续期可使 source DB 的 config 到期时间晚于原始 `issue` 回执，故必须不早于回执，并以**当前冻结的 DB 时间**计入预算。非空 WAL 受控拒绝，不清理、不 checkpoint、不读活库。

claim 之前按当前时钟取 config DB、actor DB 与 unknown 请求期限的最短剩余，少于 180 秒即拒绝且不消耗 permit。180 秒由 r2g 约 150 秒实测加 30 秒余量得出，只是本轮入场阈值，不保证 540 秒许可全部可用，也不延长产品 300 秒 TTL。复制完成、每组启动及健康等待、worker readiness、每项断言、Gateway bridge 和 10 秒观察中继续检查实际到期；越界停止并走正常清理，许可一经 claim 不重放。入场报告仅保留阈值、取整剩余秒数与核对布尔值。新的 origin 必须临近 source 停写签发；如果新备份/恢复耗尽有效期，应新开独立 scope，不能修改冻结 source/备份或复用旧许可。

## 变更

- A1 接续修复：Memory POST 的 HTTP Host 从已验证的克隆 Compose `--allowed-host memory.internal:8130` 读取并严格固定；TCP/TLS 仍只连既定 `127.0.0.1` 回环端口。签发许可前检查该配置，拒绝任意 Host。
- Gateway 接续设计：新增有独立 ID 的正式用量 GET，精确限制身份、UTC 时间窗、四条合成请求、TLS/凭据路径；为此请求提供固定关联 ID，并在观察窗前只接受其一条 `request.accepted`。账本及 unknown API 状态在该 GET 前后不变，观察窗内继续严格逐组比较。
- drill 失败收据增加受控阶段、原因码、取得的 HTTP 状态和不含响应值的结构摘要；分别标识连接拒绝、TLS 失败、HTTP 401/403/400、JSON/业务断言失败，保留原失败原因并独立记录清理失败。
- 恢复断言默认保留 GET；新增 POST 时必须同时提供 `method: "POST"` 和 JSON 请求体。
- POST 仅访问明确列出的只读路径（包括 Companion web-snapshot）并绑定到对应服务，拒绝写接口、错误服务映射、查询字符串和超大请求体。TLS 请求全程共用总期限，响应摘要受 256 KiB 限制。
- 恢复副本的五个 observability owner 与四个 core owner 在功能读回时同时保持运行；只对 Compose 中实际配置 healthcheck 的服务报告 healthy，未配置 healthcheck 的 owner 只报告 running。
- 新增可选 A1 `runtime_observation`：独立 Companion diagnostics token 的 `/health/ready` 探针、1–300 秒窗口、窗口前后稳定的 unknown turn/reply API 读回，以及按 correlation ID 对比 Gateway accepted/started/finished 计数和成功控制组。Gateway 基线在任何克隆 owner 启动前读取。
- JSON 期望对象递归按子集比较，数组递归比较且长度必须精确相同；观察报告明确只计 API 可见 reply records，不声称未暴露的投递尝试计数，也不把有界观察写成永久保证。
- 增加 A1 专用验收适配器，通过 Platform/Companion/Memory 的既有 CLI 与 HTTPS API 建立、确认及读回合成状态。适配器不访问或直接写入产品数据库。
- DEP-J 将 Prometheus 上游镜像声明的匿名 `/prometheus` image volume 作为唯一窄例外：必须同时匹配固定 Compose tmpfs 选项，并在运行态从 mount namespace 确认只读 tmpfs 覆盖；该匿名卷不进入恢复清单。

## A1 r1 source scope 的功能证据（历史）

输入 H/I 是明确标为虚构的测试消息，由 A1 Platform CLI 登记和 fanout；模型上游是包内离线合成服务。调用使用的版本为 v3；未使用真实模型、真实聊天或生产资料。脱敏结果保存在 NAS source deployment 的 `reports/a1-synthetic-inputs-r4/`：

- Companion source-facts API 读回了序列 8/9 的 `committed_event`，来源与输入回执一致，分类为 `fictional`。
- Memory 真实 select API 在动作前读回绿色标记、红色标记和阅读事实。
- `forgotten`：Memory 本地用户 CLI 确认完整遗忘请求，随后现有 revise API 返回 HTTP 200、`authoritative_state=tombstoned`。之后绿色记录为 `no_match`，不同来源的红色记录仍可读。与绿色记录来自同一原始输入的“喜欢阅读”记录也变成 `no_match`；这项同源影响已保留在报告中。
- `source_revoked`：仅撤销 `source-input:` 凭据不会使既有 Memory 记录失效，实际读回仍看见红色事实。随后经 Platform CLI 登记 revision 2 `retract` 并执行正常 fanout；Memory select 返回 HTTP 200、`no_match`，红色记录不再可读。正向证据对应产品的来源撤回流程，而不是前一次仅撤销输入凭据的尝试。
- `model_revoked`：Platform CLI 撤销 v3 后，Platform `view-config` 返回 `availability=revoked`；由 Gateway 容器发起的 Platform 配置 snapshot API 对 v3 返回 HTTP 410/`forbidden`。
- `unknown_no_resend`：Companion API 两次读回序列 8/9，间隔约 2 秒，turn/reply ID 未变化；两个 turn 均为 `closed_unknown`，delivery 和 reply state 均为 `unknown`。Gateway 脱敏日志中总计 4 个合成 upstream 调用组（2 unknown、2 succeeded）；读回期间未再发起模型上游调用。

关键机器报告：`forgotten-readback-r4.json`、`source-revoked-readback-r4.json`、`source-retraction-readback-r4.json`、`model-revoked-readback-r4.json`、`unknown-no-resend-readback-r4.json`、`source-facts-r4.json`。报告不含凭据或 origin 明文。

## r1 九 owner 登记、正常停写与恢复读回（历史）

scope `b3f59a3a-0fd4-4d47-8ff9-2c7a00d8b7a5` 为 r1 轮的唯一 NAS 目标。OBS Compose 已加入五个 owner，Prometheus 的 `/prometheus` 由固定只读 tmpfs 覆盖；外层镜像 volume 仍存在但被 mount namespace 覆盖。运行身份报告记录四个 core healthcheck 均 healthy、五个 OBS owner 均 running（未配置 healthcheck，不报告 healthy），UID/GID 为 `10001:10001`。

- runtime identity SHA256：`79809f34bff2e7d57b0d3ebd7bf88407373de098781eb6a3e604ec7203ab6b34`。
- `linux-prepare` plan 通过；execute 返回 `registered`，authority UUID 为 `61e7cc33-8ece-44f5-80b0-a3ea22e04e60`，registration SHA256 为 `f6b46d5595d90d0c0c058ee0e924b335ae00afc9fe3c247405101379a07a58e3`，activation 保持 disabled。
- `linux-rehearse` plan 通过；execute 按 `platform → companion → memory → gateway → obs-vector → obs-guard → obs-grafana → obs-prometheus → obs-loki` 发 SIGTERM。九个容器均退出码 0；无强制终止。
- 新快照 `a1-r1-backup-20260925` 包含 204 个文件，snapshot SHA256 为 `208e4da028e8ebc43dc44889232acd38d18ae18a2da4b2654b97fbee91f5129f`。独立 `verify-backup` 返回 `integrity_verified`，磁盘 snapshot 文件原字节 hash 与收据相同。
- 新目标 `a1-r1-restored-20260925` 标记为 `role=restored,status=restored_disabled`；restore 内部返回 `state_verified`，`verification_sha256=b5525653daaa359255dadf4c1df204d10f4458215f1feb3a9daa734e8ef72cd3`。source 和恢复目标均未启动。

## r1 隔离克隆的未完成项（历史）

只读检查未发现 `drill-inputs/`、`inputs.json` 或一次性 permit，因此未创建 drill claim、克隆目录或容器。DEP-J 要求独立配置、私有凭据、TLS 与真实只读 API 断言；所有私有输入必须与 source 不同。A1 当前 Gateway config-origin 是绑定已快照 Platform 状态的短时引用，旧 `issued-origin-summary.json` 的 expiry/hash 已不能代表当前 `private/gateway.env`；其当前有效期没有可核的签发收据。source 已登记并停止，恢复目标已禁用，不能为续期而直接改写任一输入，否则会破坏 runtime identity、registration 或 restore verification 绑定。

未触碰较早 scope、A2/A3、Dockge；没有使用 SQL、任意写接口、真实模型或生产数据。source 九 owner 保持 exited(0)，恢复目标保持 disabled。要完成克隆，需要在新的、明确授权的 A1 scope 中先准备新的 config-origin、专属凭据/TLS、固定 API 断言和一次性 permit，再做登记和快照；不要试图复用本次 r1 的 source/restore 绑定。

## 验证

- 本次 Host/失败收据修复的定向回归：`test_drill_http.py` 16 项通过；`test_drill.py` 20 项通过（本机初次缺 `jsonschema` 的运行未进入用例，隔离补齐 `jsonschema 4.26.0` 后全过）。最后增加许可前 Host 校验后，相关两项定向用例通过；完整 `test_drill.py` 未在最后一处测试修改后重跑。新增用例区分错误 Host 的 HTTP 400/`invalid_host`、鉴权 401/403、合同 400、正常 `no_match`、连接拒绝和 TLS 握手失败；`compileall` 与 `git diff --check` 通过。
- bundled CPython 3.12.14、临时隔离依赖 `jsonschema 4.26.0`：`test_drill_http*.py` 22 项通过，`test_drill_observation.py` 3 项通过，`test_drill.py` 全套 19 项通过；最后修改后重跑 A1 定向用例 4 项通过。HTTP 与事件扫描使用合成 loopback/TLS、Docker 和日志夹具。
- 新增事件夹具覆盖源日志的 9 个 correlation groups（2 unknown、2 成功 upstream、5 accepted-only）及一个成功控制组的重复 accepted；额外 upstream 事件会使观察失败。
- 修改文件的 Python `compileall` 与 `git diff --check` 通过。
- 新增 Prometheus image-volume 覆盖回归：`test_compose_lifecycle.py` 8 项通过；针对本次变更的 `compileall` 与 `git diff --check` 通过。
- 完整 packaging suite 共 103 项，其中 4 项报错（99 项通过）：1 项 TLS round-trip 和 3 项 synthetic-init 用例均在 TLS handshake 报 `Missing Authority Key Identifier`。这些失败发生在本次恢复适配器覆盖之外，仍作为未通过项记录。
- Ruff 检查和格式检查在最终工作树未重跑：可用环境没有 Ruff 可执行文件；此前已记录的检查通过结果早于本次 A1 适配器和 handoff 更新。
- NAS r2g 的 source 功能准备、九 owner 登记/停写、备份、禁用恢复与克隆五项 API 断言均真实执行；有界 no-resend 观察通过，最终源端与克隆各九个 owner 均 exited(0)，分属四个项目。总状态因缺少克隆 Gateway 独立功能断言仍为 `partial_functional_coverage`，不能标记完整恢复验收通过。旧 r2f 结论见上节；既有本地单测未因纯交接更新重跑。
- Gateway 接续本地定向验证：恢复演练 `test_drill.py` 24 项通过（完整一遍）；改为 `view=attempts` 和凭据静态核对后 4 项定向通过，另有 1 项证实缺 Gateway 独立读回仍为 `partial_functional_coverage`；回环 TLS `test_drill_http.py` 17 项通过；事件/账本 `test_drill_observation.py` 5 项通过。固定产品 Gateway 的五项用量接口定向测试在 NAS 隔离依赖环境通过，停机克隆的 CLI 探针正例、空窗口、错误身份及读前后哈希均符合预期；夹具对实际四条 CLI 尝试的递归子集比较通过。这里没有重新执行 NAS HTTP drill。
- A4 对 `124f06e` 的独立报告 `167421e` 未发现代码集成阻断，离线检查 `test_drill.py` 26 项、`test_drill_http.py` 17 项、`test_drill_observation.py` 5 项通过。其后 A4 对 origin 寿命闸门的独立报告 `a3780b65d2afb574ddeb467156562df7197bc970` 也未发现新的代码阻断；本地 `test_drill_origin_admission.py` 8 项及受影响的 `test_drill.py` 28 项通过，覆盖续期、回退、失配、非法期限、180 秒边界、运行时失效与非空 WAL 拒绝；Python 编译、JSON 解析与差异空白检查通过。没有重跑 NAS HTTP drill。
- NAS r2h 公开 source 预检、两份 Compose 配置、九 owner 身份、`linux-prepare` 登记、`linux-rehearse` 停写/快照/恢复及 clone 静态输入计划均实际通过；恢复工具执行入口以 `drill_origin_lifetime_insufficient` 拒绝，六项克隆 API 断言没有运行。source 九 owner 最终 exited(0)，恢复目标禁用，五个新网络端点为 0，claim 和 clone 均不存在。原始阶段输出、现场文件 SHA256、两次受控续接与时序见 r2h 证据索引。本次未改产品代码，也未为交接重复运行本地单测。

## 下一步

协调者先安排 A4 独立复核 r2h 的固定证据与入场拒绝，保持该 source、恢复目标和历史 scope 原样停止/禁用。若仍需完整恢复验收，须使用新的独立 scope、authority、快照、输入和单次许可，并在密封前准备命令及静态预检；不得重放 r2h/r2g 许可，也不能仅凭本轮时间窗把 180 秒阈值降低。单次驱动与新 scope 前预演边界见 [`NAS-A1-next-run-plan.md`](NAS-A1-next-run-plan.md)。产品化的快照绑定恢复授权需另立跨产品契约与验收。完成克隆六项 API 断言和有界观察前继续标为 `needs_validation`。

## 下一轮本地单次驱动交接（2026-09-25）

按本轮 Codex 执行授权，新增 `ops/recovery/a1_once.py` 与 `A1-ONCE.md`。驱动只接受事先准备的新 A1 source 和占位 clone 输入，锁定打包代码逐文件清单、源 manifest/registration/runtime/deployment、模型模板、初始输入索引及其每个文件的 SHA；12 段 `/28`、12 个回环端口与实际 Compose 双向核对。它按 seal → `linux-rehearse --execute` → 实时宿主预检 → 独立许可 → `drill-clone` plan → **一次** execute 串行调用公开 CLI，不修改恢复执行器原有的 owner、恢复事实、180 秒来源与 claim 检查。每阶段有 UTC/monotonic 收据、命令/结果/原始流 SHA 和 mode 0700/0600 的私有原始流；超时仅向 CLI 主进程发 SIGTERM 并等待其正常清理。若 seal 已结束却失败，尝试一次已登记九 owner 的正常停写/备份/禁用恢复；主进程仍存活或收束失败时标 `stop_unconfirmed`，不进入许可或克隆。

隔离临时目录预演运行实际驱动控制流、密封/许可参数生成和宿主预检算法；模拟的产品/Docker 回执不作为真实九 owner、Platform 来源、NAS 网络或六项 HTTPS 通过记录。新增驱动 23 项、实际来源闸门 8 项通过；恢复演练 28 项初次因本地缺 `jsonschema` 在 setUp 前全部报错，隔离安装该依赖后重跑 28 项通过。定向夹具覆盖正常六阶段仅一次 execute、配置/代码清单/manifest/模板/index/旧 scope/已有许可拒绝、非空目标、179/180/181 秒边界、错误来源/非空 WAL 造成 permit 非零退出后的阶段截断、坏回执/错 hash、seal 部分成功后的正常收束、退出与未退出两种超时以及无强杀行为。实际来源 SQLite/WAL 和真实克隆清理边界另由恢复核心既有定向测试覆盖；完整 NAS 六断言运行时间仍未测得，不能凭本地预演保证短时窗口足够。

本轮未新建 scope，未签发真实许可、启动容器或操作 NAS。旧 31 个 r2h helper 的固定路径和模块顶层副作用不可直接复用；必须填入经实时资源核对的实际分配，独立核验 source 的四 core、五 OBS、产品语义和 Gateway 四尝试，再决定是否进入短时链。r2h 原现场与证据结论不变。

## 新 scope 准备链本地补充（2026-09-25）

补充基线为已单独审查的驱动提交 `b4958f277269c7af3726191ca8d9ef049e299239`；此增量新增 `a1_prepare.py` 与 `a1_clone_prepare.py`。前者以精确原始 manifest 哈希派生唯一字段变更，锁定代码与 OBS/Gateway 输入，接受显式双项目、12 段已分配 `/28` 和 12 端口，创建新 scope 的 source、独立 TLS/凭据、OBS 静态配置与九份虚构产品输入。后者要求 source 注册、模型模板、两条 unknown 与 Companion 路由、Gateway 官方 usage CLI 四尝试及来源收据，才生成克隆占位输入、轮换凭据、六项断言、逐文件清单和单次驱动配置。两者默认只读 plan；`--execute` 的部分失败保留现场，原范围不可原地重试。操作说明见 [`A1-ONCE.md`](../../ops/recovery/A1-ONCE.md)。

隔离夹具运行新准备链 14 项与驱动 29 项：真实 bundle 初始化使用本机可用的固定合同字节，产品行为、OBS 运行、Docker 和驱动子进程回执使用注入替身。该夹具验证了准备到驱动入口的路径、一次 execute 调度以及原始 manifest、非空 scope、OBS 部分失败、缺模型模板/语义收据、错误 Gateway usage、凭据与地址错配的拒绝；不证明 NAS 上四 core/五 OBS、产品 source 语义、实时网络容量、短时来源或克隆六项 HTTPS。新分配仍须协调者明确给出；目前没有为这一轮创建实际新 scope 或许可。下一轮应先固定真实准备配置并由 A4 独立复核代码和输入，再通过公开 CLI 完成 source 语义与注册，之后才运行短时单次链。

后续集中复核修正：`scope_parent/tooling` 被设为唯一代码根，三处公开入口还核对实际加载的自有模块路径及逐文件代码锁；固定 source API 三端口须为 19512/19513/19514。OBS 配置现在明确传入 `projects_root` 并核对四产品固定 Git 提交，用固定 OBS Git 提交生成词表；模型发布模板从静态准备结果持久化。seal 的四个同步 Platform 调用外层上界为 240 秒；驱动生成本轮 attempt UUID，只有三份产品动作回执及带 scope/attempt/哈希的完成标记均有效、且 CLI 主进程确已退出，才允许进入九 owner 停写。缺失、半写、旧标记、错误 attempt 与父进程退出但产品动作未确认均不启动第二写入者。180 秒来源入场门槛不变。

本地定向准备 20 项、驱动 38 项通过。真实静态链在临时树跑了 bundle 初始化、固定 OBS Git 包/四产品词表、合成配置和九输入；因 Windows 无法验证 Linux chmod/chown，OBS 最终权限函数在这项测试里单点替换，不能宣称 Linux 权限已验。随后用明确的 fixture-only 产品事实和假同步短时签发，把真实生成的克隆输入送入 `drill_inputs.isolated_inputs`，同一份输入 SHA 通过消费者结构、Compose、TLS/凭据和六断言校验。**源端业务事实公开 CLI 编排与真正的九 owner/六 HTTPS 仍未完成**，此增量不能被称作完整实机链。逐阶段入口及缺口见 [`NAS-A1-entry-index.md`](NAS-A1-entry-index.md)。

## 新 scope 源端业务入口补充（2026-09-26）

基于固定准备修复提交 `8d640d10c443c7f03b66f6b479eb19cc123830a5`，新增 `ops/recovery/a1_source_flow.py`。它从同一 `a1-preparation/1` 配置绑定当轮 scope、代码锁、项目和绝对 Docker/Python；默认只读，执行前实时检查全部 12 网络/12 端口、四项目与可用内存。执行时先留下不可重用的 attempt 标记，仅调整新 scope 的空运行目录权限并经打包 Linux 权限预检，然后按公开 CLI/API 完成两项 Memory 迁移、source/OBS 启动、合成 v3 发布和两份虚构输入、Companion 两条 unknown 与 source facts、Memory 三条事实及遗忘/撤源、模型 v3 撤销、v4 离线控制和四组 Gateway 官方 usage 读回，最后采集九 owner runtime identity 并以 `linux-prepare` 登记 authority。回执只写本 scope 的私有报告目录；失败不自动重放。相关 `a1_acceptance` 入口现接受配置中的 Docker 路径，bundle 更新也保留原文件所有者/权限，不再由该流程继承固定 NAS 路径。运行命令和具体输入见 [`NAS-A1-entry-index.md`](NAS-A1-entry-index.md)。

定向离线检查：已有准备 20 项和驱动 38 项在此增量后仍通过；新增源端入口 8 项通过，覆盖整个动作序列的注入式产品替身、scope/单次门禁、unknown 精确结构、配置 Docker 路径和失败私有诊断。A4 对首个源端提交独立复核发现旧 r2h person/conv 常量和 CPU `0,1` 常量会阻断新范围；后续修复让 Memory 实际读回适配器核对当轮已绑定 scope，让资源 profile、两份 Compose 和运行观测核对同一动态 CPU 集，并有新随机 ID、`[6,7]` CPU 集与失配拒绝夹具。既有真实静态 bundle→clone 输入消费者夹具仍通过。新入口没有在 Linux/NAS 上实际运行：真实 Memory 迁移、Platform 同步写入、HTTPS、Docker 九 owner、Linux 权限/进程身份及源端 SQLite/Gateway 账本仍需在全新明确分配的 scope 中验证。真实许可、恢复与六项克隆 HTTPS 均未签发或执行；旧 r2h/r2g 证据与结论保持原样。下一步是 A4 对修复提交独立复核，然后由协调者提供新 scope 的实际分配并决定是否运行实机链。

## r2i 分配入口修复（2026-09-26）

A4 对源端修复提交 `05ebf930f042f6304cef12abe158f637edf51cad` 的独立复核未发现第三项源端代码阻断。协调者随后授权一次新的 r2i NAS 尝试，分配文件为主协调仓库 `docs/development/nas-a1-network-allocation-r2i-2026-09-26.json`，原始字节 SHA256 `8dd4844321d0b396adc09451d8c6cea26abab7ef08f2bc21d70164fa87aa3d55`。实机创建前静态审查发现旧网络校验仅接受 `10.205.48.0/24`，且 `"-r2"` 通配拒绝会错拒已授权的 `scope-a1-r2i`。本次只修改本地入口：将分配原文件作为精确字节夹具，准备与驱动配置显式绑定其路径、SHA、execution ID、scope 名称、十二个 purpose 顺序网段和十二个端口；网络范围从固定旧池改为该分配的 `10.205.49.0/24`，尾部 `10.205.49.192/26` 继续不可用。克隆输入把绑定传给驱动，源端和驱动的写前预检再次核对同一文件。仅移除宽泛的旧 `-r2` 子串拒绝，历史 scope 仍因精确分配身份不符而拒绝。

本地完整 `test_a1_*.py` 离线定向检查 69 项通过；测试使用主协调分配文件的字节副本，覆盖准备→真实静态 source→源端只读计划→克隆占位→单次驱动配置→封印输入消费者，以及旧池、未分配尾段、错端口、错文件路径/SHA、错 execution ID、错 scope 和篡改文件的拒绝。该文件原字节含 CRLF，已用专属 Git 属性保留；索引中原始 blob 与主协调文件 SHA 均为上述固定值，`git diff --cached --check` 通过。产品行为和 Linux/NAS 实际运行仍不由该夹具证明。当前修复待 A4 独立复核；截至本节记录，NAS 上未创建 r2i scope、工具包、网络、容器或许可，仅做过一次只读 SSH 系统识别。复核后还须先做实时宿主预检，再按已授权的单次范围执行；不能重用 r2h、原 `10.205.48.0/24` 分配或旧许可。

## r2i 单次实机结果与安全停机（2026-09-26）

A4 对分配入口提交 `104aa50e70784712d5ef39e215fd7bb70ad8687d` 的独立报告 `ab35da286e12ea92336172a9eba9cb290e58a42a` 未发现代码阻断后，按协调者的一次性授权创建独立 parent `/volume2/tianshu-v2-validation-wave1/accept-20260926-a1-r2i` 和唯一 `scope-a1-r2i`（UUID `9972bea7-b68c-40e9-9bee-6be1cc212c2e`）。先核实 NAS 的 706 条 IPv4 路由、172 个 Docker 网络、12 个回环端口及四个项目名均无冲突，可用内存 17.73 GiB、CPU 在线 0–7；原 manifest 和 63 个合同文件 SHA、四产品固定 Git 对象也通过。新包 `code-104aa50.tar` SHA256 `e48c9e544490ab54c898c7bfa4cf5163d2378c306af3c70ebfe4e54c83687171`，原分配文件 NAS 字节 SHA 与上述协调原件相同；代码树、配置和逐文件锁已由 NAS 公开只读准备计划校验。

公开 `a1_prepare --execute` 一次成功：新 source manifest SHA256 `49decd6b47e2fb7eb64735681b0754dc2ae015b0cffc46eb6bd8cab63e2d1f3d`，九份虚构输入已产生。随后公开 `a1_source_flow --execute` **仅调用一次**，139.22 秒后返回 `a1_source_unknown_turns_invalid`。Platform 两次 fanout 分别于 UTC 17:27:37.094、17:27:39.690 接受；Companion 第 1 条 turn 最终为 `closed_unknown`，一条 unknown 回复、模型调用 1 次。第 2 条在 UTC 17:27:40.232 创建、17:27:40.408 prepared，17:27:40.436 的 peer 调用记录 `connect_error`，随后以 `dependency_unavailable` 和模型调用 0 次终结；初始 web snapshot 两条 turn 均已终态、`active_turns=0`，故拒绝不是异步未完成或字段映射问题。

同一 UTC 时间线中，旧 Memory 实例于 17:27:40.341 开始正常停机、17:27:40.445 停止，新实例到 17:27:43.761 才 ready。固定源端编排当前顺序是 **两次 fanout → 绑定 Memory 精确 scope → 强制重建 Memory → 读取 turn**，而 fanout 接受并不等待 Companion 异步处理终结。Companion 第 2 条的依赖调用与 Memory 重建窗口重叠；产品事件中 peer 未带目标名，结合固定 `_preflight` 调用次序和 Memory 日志，Memory 连接竞态是当前最有力归因，待 A4 独立核对。两条 `committed_event` 已在 Companion 本轮 outbox 保留为 fictional；第 2 条 `failed` 不符合 source 继续要求，不能重发消息或续接该范围。

故障后未运行 source 注册、克隆占位、`a1_once`、恢复、许可或六项克隆 HTTPS。核对唯一项目标签、九个完整容器 ID、九服务集合及所有 bind 挂载均属本范围，确认 source CLI 已退出且无活动 turn 后，使用 `docker stop --time -1` 正常停止九个 owner：全部 `exited(0)`，五个本范围 `/28` 网络端点为 0，克隆网络未创建；保留容器、网络、数据和原始私有收据。去敏状态、精确时间线、文件 SHA、缺失后续产物与只读检查见 [`NAS-A1-r2i-evidence.json`](NAS-A1-r2i-evidence.json)。本次总体仍为 `needs_validation`，不宣称六断言恢复通过；历史范围未改动。下一步只在本地修正该编排竞态并做独立复核，是否另行授权新范围由协调者决定。

## r2i 竞态的本地编排修正（2026-09-26）

A4 只读复核报告 `b3c1e837928f68b35479f2132147c3306daa8b23` 确认第二条 turn 已以 `dependency_unavailable` 终结，`accepted` 仅保证 Companion 入站，Memory 连接竞态是最有力归因；peer 安全日志未记录目标名，故不把具体 RPC 目标写成直接观测事实。固定 Memory 产品代码中 `event_scopes` 约束 `consume/check_sources`，不约束普通选择查询；当轮第一条 turn 在绑定前已完成一次模型调用并得到 `closed_unknown`，第二条也在绑定前完成准备。精确 scope 仍在后续 trusted Memory commit 和来源检查之前必须绑定，不能删去。

在本地代码中，两次 fanout 后先用授权的 Companion web snapshot 有界轮询，要求同一会话恰好两条预期序号的 turn 均为 `closed_unknown`、各一条 unknown 回复，并且无活动 turn、无未完 collector；只有确认终态后才绑定观察到的 scope 并重建 Memory。每条 turn 的消息 ID/修订必须与本轮已注册输入及对应 fanout admission 相同，首次就绪读与最终读的 turn/reply 身份及终态必须精确一致。任一错误终态、输入关联错误、快照不一致或 90 秒期限届满立即拒绝，保留单次尝试与逐次私有读回，不重发输入。每次快照查询使用独立 request ID，最终确认读取不会与上一轮询同号。后续 source-facts 读取与 trusted Memory commit 原路径保持；Companion outbox 的 `unknown` 仅是本地持久事件，不能当作 Memory 已提交。

A4 对首版提交 `4b3ee2a` 的只读复核报告 `a32db0938ca0f2df9a188fba2fa82ab9b27b4d3e` 指出两次都满足终态但 turn/reply ID 被替换仍可通过；其阻断项已在修正版 `f56d63c` 补上身份比较，并加入 ID 替换、输入序号错配及 admission 与注册输入错配负例。修正版离线定向 77 项通过。A4 在固定 Companion 代码确认 fanout admission 的 `source.message_key` 来自已接受输入，turn bundle 与正式 web snapshot 保留相同 message ID/修订；独立顺序与负例 4 项及 diff 检查通过，结论是没有进一步确认的本地代码阻断，报告补充提交 `0f823d73ec7b5326ae6fd921c2a017c2520b1121`。这仍不等于真实产品异步 HTTP 验收。**未上传 NAS，未在 r2i 原范围重试，也未获新 scope 或许可授权**。

## r2j 新分配入场准备（2026-09-26）

协调者授权一个新的 `scope-a1-r2j` 实机范围，分配原件位于主仓 `docs/development/nas-a1-network-allocation-r2j-2026-09-26.json`，原始 SHA256 `5fc2ad22b78b109cb42aa78fbc32a02d67564cee342c95aa2c716e654d0535f1`，execution ID `b5ebf2e9-836d-4e9b-b94c-21b1ee10a0a6`。本地入口去掉 r2i 文件名和哈希常量，改由受控准备配置给出绝对分配路径与预期 SHA；同一代码静态接受 r2i/r2j 两份协调原件，按 scope/status/执行 ID、用途顺序网段及端口精确匹配，错原件、错哈希、跨实例、错用途及目标已存在均拒绝。准备、源端、克隆与单次驱动收据串接准备配置和分配文件 SHA，单次驱动还核对准备原件。此处记录的是跨阶段一致性；协调者须在上传前独立固定真实准备 JSON 原始 SHA，不能把运行时新算的哈希当作既有预期。

本地定向 `test_a1_*.py` 81 项通过。NAS 创建前只读预检原始收据保存在本工作树忽略目录 `.runtime/nas-a1-r2j/preflight-before-create.json`（SHA256 `66e0afa4ce0c24116193bcba3dab574b20afad34d1ee007bc674768e1214ea0c`）：当时 726 条 IPv4 路由、177 个 Docker 网络、12 个端口和四个项目名均无冲突，预计静态地址不冲突，MemAvailable 17.74 GiB，CPU 在线 0–7；新 parent 尚不存在。代码增量待 A4 复核，真实准备配置还未固定/上传，NAS 尚未创建 r2j parent、scope、网络、容器或许可；历史 r2i 现场保持停止。

## r2j 工具包跨平台代码树锁修正（2026-09-26）

协调者与 A4 已复核 r2j 参数化增量 `8bd62b1`，并独立固定第一版准备文件 SHA256 `f14f6bbb57fb3ca04cc1f4ae3a62f6328c6f8c4e25440671c30a93e78970e29a`。同一 parent `/volume2/tianshu-v2-validation-wave1/accept-20260926-a1-r2j` 已创建，并以独占创建、原字节读回上传分配、代码锁、准备配置与 `code-8bd62b1.tar`；没有创建 scope。第一次工具包组装时，Windows 锁定的 114 文件代码树 SHA256 `b802989f4e4cf62e5b7a74239045ec7105e8816e95d51b68a5320deb32974ea7` 与 NAS Linux 重算 `e8b0b05d8f4463ba67ce786df0106929c98877c19eecb5e83ef107a49e788138` 不符，包装器当即停止。逐文件 114 项的路径和 SHA 均相同；差异仅在整体清单排序：Windows `Path` 比较忽略大小写，Linux 比较区分大小写，97 个位置不同。原始收据保存在忽略目录 `.runtime/nas-a1-r2j/assembled-code-diff.json`，只读停止核查在 `setup-stop-readonly.json`：scope 和 venv 均不存在，四项目容器和 r2j 网络均未创建，许可和恢复链未启动。旧上传文件与失败收据保持原样。

本地修正统一使用相对根的 POSIX 路径 UTF-8 字节序，对代码锁和 Gateway 源树采用同一个排序键；生成者与消费者继续共用 `_code_files`。新增混合大小写夹具模拟 Windows 忽略大小写排序，证实规范树锁与旧排序不同，内容和路径增删改变哈希。当前固定 Gateway 17 文件源树的哈希仍为 `39355bc3de216f18c37ee8d2f9f70673ae5aeb2eeaee8b8beacff314e7915755`。完整 A1 定向离线测试 83 项通过，`git diff --check` 通过。此修正尚待 A4 独立审查及新版本执行包、代码锁、准备配置重新固定；在新输入获独立确认前，NAS 仅保持只读。

## r2j 唯一源端实机尝试及安全停机（2026-09-26）

A4 独立审查跨平台排序修复 `619355c` 通过；协调者与 A4 逐字节固定第二版原件。新 tar SHA256 `9234b1c922d69ce2f225b95bcb7d2e5e2a8bcd55f119025c2e33657b2e5dc36d`、114 文件代码树 `f7a4a769715f8b0c59ed9c862264f1683adafdf6ac2a1a32d148dbf82d390402`、代码锁 `5f0a78a756f9b6024843114047de07eddaffa66cbe3f457f9eee1d0fc438ec54`、准备配置 `4a68759370faf30bc85b26434f9f19cdf22a7feeeb13878faccd55c3418b7ac3`。配置仅代码树与代码锁字段变化，分配、execution ID 和 scope UUID 保持原值。新版文件先以上传独立名称读回；安装计划核旧 114 项哈希、旧 Linux 树、scope/venv/目标网络和项目容器不存在后，仅替换两份受审 Python 文件及相应锁、配置。新进程从本 parent 的模块重新计算 114 项与 Linux 代码树均吻合，Gateway 17 文件树未变；旧包和失败收据仍保留。A4 对安装脚本的超时与部分失败收据提出并确认修正，随后独立确认执行和读回证据。

依照旧 r2i `pyvenv.cfg` 记录的命令，用相同 CPython 3.12.14 创建本 parent 的非符号链接 `--copies --without-pip` 环境。按 r2i 实际九个运行依赖的精确版本，从官方 PyPI 获取兼容 wheel，原字节 SHA 与官方发布记录逐项比对后上传并离线安装；新解释器、base/version、依赖版本和导入来源均核对。公开 `a1_prepare` plan 与单次 execute 通过，创建唯一 `scope-a1-r2j`（UUID `72d01f54-fef9-4bfe-8e4c-50ec8263fef7`），source manifest SHA256 `49decd6b47e2fb7eb64735681b0754dc2ae015b0cffc46eb6bd8cab63e2d1f3d`，九份虚构输入。真实源端入场预检通过 12 网段、12 回环端口、四项目名、`6,7` CPU 集及内存门槛；公开 source plan 通过。

**唯一一次** `a1_source_flow --execute` 终态返回 CLI exit 2、`a1_clone_gateway_usage_failed`。阶段证据表明初始异步屏障已成功：第五次 wait 与 final snapshot 的两条 `closed_unknown` turn、各一 unknown 回复及其消息身份一致，均无 active turn/collector；两次 fanout admission 与已注册 message key 相符。Memory 三条虚构事实的选择计数为基线绿/阅读/红 `1/1/1`，遗忘后 `0/0/1`，仅撤回 source 凭据后 `0/0/1`，正式 retract 后 `0/0/0`。v3/v4 发布成功，撤销 v3 的 API 读回 HTTP 410/`forbidden`，v4 后 unknown 保持，两个离线 Gateway 控制各返回 HTTP 502/`result_unknown`。这些真实步骤的原始报告 SHA、输入映射、状态和计数见 [`NAS-A1-r2j-evidence.json`](NAS-A1-r2j-evidence.json)；A4 独立分层报告提交为 `d7cf6a2c69cc9902ad668b06f781544647c338b4`。

失败发生在源端末段调用宿主只读 `python -B -m tianshu_gateway.usage_report`：实际子进程 exit 1、stdout 空，traceback 在固定 Gateway `usage_report.py → config.py` 导入处报 `ModuleNotFoundError: No module named 'aiohttp'`，尚未进入 SQLite 查询或四尝试值校验；外层错误名不能解释为克隆 HTTPS 失败。固定 Gateway commit `601974194042641c5a85cc3c061cbd1880d7daf1` 的 `uv.lock` SHA256 `56de5d63a49b0f4544ca3926eadfab4ff6b8f5b7506f976cb9b8832002700e29` 规定 `aiohttp==3.14.1` 及 `aiohappyeyeballs==2.7.1`、`aiosignal==1.4.0`、`frozenlist==1.8.0`、`idna==3.19`、`multidict==6.8.0`、`propcache==0.5.2`、`yarl==1.24.5` 等传递依赖；本轮宿主 venv 没有这组包。源端调用的其他宿主 Python 入口为打包 `release.py` 与 `ops.recovery`；Platform/Memory/Companion 产品 CLI 由相应容器执行。当前是工具环境打包遗漏，不归因为 Gateway 账本内容。隔离本地 CPython 3.12 环境按该固定锁完整版本安装后，真实 `_usage` 子进程读取合成只读 SQLite 的四尝试得到 2 成功、2 unknown，空窗为 0，错误身份退出码 3。独立复核发现，已存在的主数据库字节哈希不变，但 CLI 新建了 `diagnostics.sqlite-wal` 与 `diagnostics.sqlite-shm`；因此本地夹具**不能**证明 SQLite 整个文件集合无写入，NAS 原库不能直接运行 CLI。更正前的原始提交 `61a95e2` 保留，完整前后清单见本轮新增私有收据。此夹具也不证明 NAS 本轮四尝试账本已读回。

确认 source 流程进程已退出、九个容器完整 ID/项目标签/服务集合及所有挂载均属本 scope，且 unknown 终态后，以 `docker stop --time -1` 一次正常停止本轮九 owner；全部 `exited(0)`、无 OOM，五个 source `/28` 网络端点为 0。runtime identity、source registration、source complete、克隆占位、许可、恢复和六项 HTTPS 均未到达。原 scope 已消耗，不原地重试，也不在该范围补依赖后续跑。修复方向是新范围创建前按固定 Gateway 锁安装完整宿主只读 CLI 依赖，并在隔离环境先跑实际 `_usage` 入口；复用已停止 Gateway 容器执行需要重新启动/新建容器及额外挂载，会扩大当前安全边界。NAS 原现场与私有收据保留，当前总体仍为 `needs_validation`。

### r2j 停机账本副本补验

协调者另授权**一次**已停机账本一致性副本离线读回。专用 `usage-readback` 目录与原 scope 同 parent，但不修改原工具 venv、原配置、原数据库或失败收据。按固定 Gateway `uv.lock` 安装 14 个 Linux wheel 到专用 CPython 3.12.14 venv，核版本及导入来源；A4 独立审查准备脚本和副本读取脚本的精确 SHA 后才执行。原九 owner 均为 `exited(0)`，五 source 网络端点均为 0；原 Gateway、Companion 数据目录的完整文件清单、大小、SHA256、inode、mtime 在复制前后完全一致。原库只有两份主 DB，没有 WAL/SHM；`_source_routes` 只读 Companion **副本**，Gateway 产品 `usage_report` 只读 Gateway **副本**，新 WAL/SHM 只在两个副本目录出现。

先从 Companion 副本独立确定两条 `model:` request ID，再与两个固定离线控制 ID 组成四 ID 集。固定 UTC 日窗 `2026-09-25T00:00:00Z` 至 `2026-09-26T00:00:00Z` 的真实 Gateway CLI 副本读回 exit 0、stderr 空，精确返回 4 次尝试：2 `completed/succeeded`、2 `transport_unknown/unknown`；coverage matching/scanned `4/4`、无截断、unmetered `0`，四 ID、完成时间与预期完全一致。前后还核原 settings/env 与 bundle 清单一致，114 文件恢复树、17 文件 Gateway 树、原恢复 venv **标记文件**、配置锁与源失败收据不变，九 owner 和五网络状态未变。A4 对私有六文件逐个哈希并独立核对基线/查询后目录元数据、四 ID、结果与时间窗，结论为**停机源账本一致性副本离线读回通过**。详细公开索引见 [`NAS-A1-r2j-ledger-readback-evidence.json`](NAS-A1-r2j-ledger-readback-evidence.json)，固定方案与 wheel 锁见 [`NAS-A1-r2j-ledger-readonly-plan.md`](NAS-A1-r2j-ledger-readonly-plan.md) 和 [`NAS-A1-r2j-ledger-wheel-lock.json`](NAS-A1-r2j-ledger-wheel-lock.json)；原始私有回执仅存 `.runtime/nas-a1-r2j/usage-readback-private/` 和 NAS 专用目录。

此补验不回溯更改唯一源尝试 `a1_clone_gateway_usage_failed`；source complete、runtime identity、registration、恢复、许可、clone 及六项 HTTPS 仍未到达。后续须在新范围启动九 owner **之前**补固定 Gateway 宿主 CLI 依赖闭包与实际 `_usage` 启动检查，隔离合成副本正反例验证后再审查新的单次完整恢复尝试。当前不创建新 NAS scope，也不把 14 包补入旧 r2j 环境。

### 后续新范围的本地启动前预检交付

基线为副本证据提交 `b4b75dc20880be1a92d7a3de172ac745cf33bf78`；预检代码、锁、文档与测试固定于提交 `e57ba4892c97fa7da58972aafb246a31f7a72198`。新增便携锁 `ops/recovery/a1_gateway_runtime.lock.json` 原字节 SHA256 `20321ac2bbb40bd801ccbb5804c415af8144b226b3469516f8ffe92317f06280`，从固定 Gateway commit/`uv.lock` 导出完整 14 包版本、wheel 文件名、SHA 与来源 URL，并绑定 17 文件 Gateway 导入树。新准备配置增加 `gateway_runtime_lock` 与 `gateway_runtime_lock_sha256`，仍受同一代码树和相邻代码锁保护；旧 r2i/r2j 配置由当时旧代码解释，不追溯改写。

`a1_prepare --execute` 在创建 scope 前用配置中的同一个 `python` 核 CPython 3.12、Linux x86_64、14 包精确版本及每个导入来源，并加载固定 Gateway usage 模块。`a1_source_flow --execute` 的实时 host preflight 在 attempt 标记和任何 `compose up` 之前再次执行该核验，并在 source 私有报告目录的自动清理临时树内复制已按 bundle 清单核验的**合成** Gateway settings/env，创建一条合成成功尝试的 SQLite，调用后续源流程真正使用的 `_usage` 入口。原 `source/data/gateway` 账本文件集合、字节与元数据前后核相等；临时树在成功或异常时删除。`_usage` 与预检子进程使用同一配置解释器、固定 `PYTHONPATH`、禁用户 site 与 Python 环境覆盖。成功的静态准备回执记录 `gateway_runtime_preflight`；缺包或 CLI 不可用时在 scope 创建或九 owner 启动前失败。

本地 Windows CPython 3.12 隔离验收：固定完整 14 包环境运行真实 `_usage` 对临时合成账本返回 1 条 `completed/succeeded`；从相同环境复制并**仅剔除 `aiohttp`**的 13 包环境被预检拒绝，且未创建源账本/attempt。生产默认平台门拒绝 Windows，测试专用参数仅验证跨平台逻辑。两解释器版本/包清单私有回执 `.runtime/nas-a1-r2j/preflight-runtime-identities.json` SHA256 `1954acd50d4cd721e9416f8be18d1cc20d6ec9965d7cdc4a2840d9de1e634d31`；最终受影响 A1 测试 `88/88` 通过、无跳过，原始日志 `.runtime/nas-a1-r2j/preflight-a1-suite.txt` SHA256 `b8535e6aeb003e1f375d056cc857951dc6a7a08824c7a4bc27dc6aab8705ba91`。一次用裸本地基础解释器运行旧准备测试时因其未安装 `jsonschema` 出现 2 个环境错误；加入固定完整环境的 site-packages 后 `24/24` 通过，最终 `88/88` 也使用同一受控依赖环境。

其他宿主 Python 调用审查：新 A1 准备中的 `synthetic_init` 仍依赖原恢复环境的 cryptography/cffi；打包 `release.py` 的 host preflight 经 `manifest.py` 依赖 jsonschema，并与 Gateway `_usage` 使用配置中的同一工具 venv。Platform/Memory/Companion 公开产品 CLI 通过 Docker Compose 在各自容器内执行，不从宿主 venv 加载其产品包。通用 `ops/recovery/requirements.txt` 的 `jsonschema==4.25.1` 与本 A1 固定锁的 `4.26.0` 不一致，不能作新 A1 安装清单；新范围须保留原恢复专用包并按固定 Gateway 14 包 wheel SHA 离线补齐、记录安装回执，再运行现场预检。A4 对固定提交 `e57ba4892c97fa7da58972aafb246a31f7a72198` 的代码、锁、测试与安全边界集中窄审为 PASS。本轮只做本地预检代码交付；没有创建新 NAS scope、修改旧 r2j venv、启动容器或重新执行 source/clone。后续由协调者审核新的单次完整恢复尝试。
