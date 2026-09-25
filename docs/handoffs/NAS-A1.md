# NAS-A1 — 恢复读回与语义验收

## 目标与结论

最新隔离合成 A1 r2g scope 已完成 source 语义读回、九 owner 登记与正常停写、一致性快照和 `restored_disabled` 恢复。独立九 owner 克隆上的 `data_readback`、`forgotten`、`source_revoked`、`model_revoked`、`unknown_no_resend` 五项真实 API 断言全部通过，10 秒有界 no-resend 观察通过。恢复工具仍返回 `partial_functional_coverage`、`release_ready=false`：许可中没有独立的 Gateway 功能 API 断言，`missing_products=["gateway"]`。source 与克隆各九个 owner 均正常退出，恢复目标保持禁用，许可已消耗且未重放。状态保持 `needs_validation`，不能称完整恢复验收通过。旧轮次作为历史保留。

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

## 变更

- A1 接续修复：Memory POST 的 HTTP Host 从已验证的克隆 Compose `--allowed-host memory.internal:8130` 读取并严格固定；TCP/TLS 仍只连既定 `127.0.0.1` 回环端口。签发许可前检查该配置，拒绝任意 Host。
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

## 下一步

如需把整体状态推进至完整通过，应先为克隆 Gateway 明确一个受支持的只读功能 API 断言及预期响应，审查它与离线合成配置的真实关系，再在另行授权的新隔离 A1 scope 中重新完成 authority、快照和一次性许可。r2g 的五项语义与限时观察结果已通过，不能靠重写结果、把源端控制调用算作克隆断言或重放 r2g 许可消除 `missing_products=["gateway"]`。完成前保持 `needs_validation`。
