# NAS-A2 有界容量验收交接

## 目标与状态

在 NAS 上对 DEP-B 的日志保留、容量拒绝与告警做隔离验收。结果为 **partial**：NAS 侧探针与证据收集成功，若干条件通过；应用日志回收仍被阻断，Vector 磁盘 buffer、物理 ENOSPC 与生产 30 天保留未验证。`release_ready=false`、`application_reclamation_authorized=false`，未删除应用源日志。

## 基线与变更

- 基线提交：`2e04eff5d0bf1283a321d9042d9b62f94a54dabb`；分支：`codex/nas-a2`。
- `tests/deployment/observability/helpers.py` 支持为合成 Loki 服务证书增加内部 IP SAN，以 mTLS 访问隔离网络中的 Loki。
- `tests/deployment/observability/nas_a2_probe.py` 固定 NAS 路径、项目/容器前缀、镜像 digest、内部网段、loopback 端口、内存和 tmpfs 上限；TTL 样本使用每轮唯一 stream 标签，避免重跑共享固定标签。拒绝写入的 HTTP 状态也进入机器报告。
- `tests/deployment/observability/test_nas_a2_probe.py` 覆盖路径/容量边界、网段冲突、CPU 集合解析、报告回收门禁与合成 padding。
- 完整机器报告：[nas-a2-2026-09-25.json](../../tests/deployment/observability/evidence/nas-a2-2026-09-25.json)。另保留修正前的 TTL 拒绝尝试：[nas-a2-2026-09-25-ttl-rejection-attempt.json](../../tests/deployment/observability/evidence/nas-a2-2026-09-25-ttl-rejection-attempt.json)。

## 实际验收

- NAS 预检无路径挂载、子网或端口冲突。复用内部网络 `10.204.50.0/24`，选用 loopback `19522`；镜像按固定 digest 校验。CPU 绑核可用，但 NAS Docker 不支持 CFS quota 与 pids 限制。任务内存计划约 1.75 GiB，低于 4 GiB 上限；专用 tmpfs 为 128 MiB。
- 加速保留期通过：在 48h 策略写入 25h 合成记录并确认可查询，改为 24h 后约 56.6 秒观察到查询不可见且对应 chunk 文件删除。它走同一 store-only Loki 查询；未验证生产默认查询路径或实际经过 24 小时。
- 应用容量告警通过：合成平台日志超过 16 KiB 配额，告警触发；恢复 tmpfs 空间后应用告警仍保持，因为合成源仍超配额。测试前后源文件 SHA-256 一致。
- 磁盘水位拒绝部分通过：仅在隔离 tmpfs 填充 116,916,224 字节，剩余空间约 15.9 MiB 时 guard 返回 503 `storage_capacity`，拒绝计数为 1、最近成功时间未变化；磁盘告警触发并在释放填充文件后清除。恢复写入和 flush 均返回 204，但事件在 30 秒查询窗口内不可见，故未声称恢复事件已落库/可检索。
- Vector 满 buffer 未运行：Vector 0.58.0 要求至少 268,435,488 字节，超过本任务 128 MiB tmpfs。预检识别其既有 A2 容器以 exit 78 因该下限退出，并保留为停止状态；未扩大 tmpfs。
- 应用日志回收门禁 blocked：缺少冻结的回收合同；源日志未删除。物理 ENOSPC 与生产 30 天保留为 not_run。
- 停止状态：Loki exit 0；Vector exit 78、非 OOM；A2 内部网络 `internal=true` 且当前无容器连接；tmpfs 总用量 659,456 字节。没有重启 NAS、清理其他容器/网络或使用真实日志/模型调用。

## 本地检查

- `python -m unittest discover -s tests/deployment/observability -p "test_nas_a2*.py" -v`：15 项通过。
- Ruff `format --check` 通过；`ruff check --select E4,E7,E9,F,I` 通过；`git diff --check` 通过。
- Ruff 默认规则集此前报告了额外规则告警，因此只把上述明确选择的错误、导入与格式规则记录为通过。

## 2026-09-25 NAS 跟进 R2–R11

- 术语修正：R6–R11 原报告字段 `ingester_fallback_reconciliation` 对应 `query_store_only=false` 的 **ingester + store 混合查询**，不是 WAL-only 查询，也不能单独证明全部事件仍在 WAL。旧机器报告保持原样，以下历史描述按混合查询理解；R11 的 55,370 条差额只证实当次 store-only 查询缺口。
- R2 与 R4、R5 的恢复诊断一致：Guard push/flush 均为 HTTP 204，直接 Loki 与 Guard 查询能在 ingester 可用时读回精确合成事件；切换为 store-only 查询后找不到该事件，恢复 ingester 后又能读回。诊断为 `store_only_query_missing_while_ingester_wal_returns_event`，没有证据表明这是标签或时间戳错误。
- R3 在 Loki 启动前失败：网络为 `10.204.52.0/24`，容器仍请求旧地址 `10.204.51.10`，Docker 拒绝启动并留下 Created/exit 128 容器。容器和网络保留；见 [R3 报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r3.json)。
- R4 报告记录清理确认错误，后续只读检查确认 Vector/Loki 均已停止且 exit 0。报告曾记载一个停止态网络附件，当前网络无附件。R4 的 tmpfs 归档有 82 个成员，SHA-256 为 `03ce158c…1604dc`，已校验并卸载。
- R5 的前两次预检没有启动新服务：第二次报告暴露 R2 历史资源校验误用 R5 scope 的问题，已改为按 R2 自身标签检查；R4 网络校验也允许当前附件数较停机报告减少。相关尝试记录为 `nas-a2-followup-2026-09-25-r5-preflight-attempt-1.json` 与 `…attempt-2.json`。第一次失败的具体原因只在本地尝试记录中标为推断，未当作远端原始日志。
- R5 预检使用了过度保守的累计算法，把已停止 R2 计划和当前 R5 计划相加；`4,026,761,216` 字节不是当时并发/驻留资源。R6 前置资源门禁已改为仅计当前运行 A2 容器驻留内存、新运行计划及仍挂载 tmpfs 的实际用量。当前历史用量约为 R2 的 229,376 字节；R3 为 0，R4/R5 已卸载。
- R5 向唯一服务标签写入 120,001 行合成数据，源文件 39,729,227 字节，前后 SHA-256 一致：`7c2541c3…38d4b8`。原始执行因 Vector 指标请求打到未生效的 loopback 发布端口而报告 `vector_metrics_unavailable`；同时 Loki 曾因 4 MiB/s 限速返回 429。
- 在同一 R5 资源内继续排查时，直接访问 Vector bridge 地址可读指标。队列先稳定在 131,846,120 / 268,435,488 字节；恢复 Loki 后，Vector 报告队列清空，source/sink 计数均为 72,723，稳定观察 90 秒，checkpoint 到达源文件末尾。这里的队列峰值仅约 49.1%，因此 Vector 满 buffer/backpressure 仍未验收。
- **修正 R5 身份差异解释**：[收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r5-drain-reconciliation.json)中的原句把 `Counter(actual - expected)` 误读为新 ID；它实际统计的是多重集的额外出现次数。最终查询有 120,001 行、119,917 个不同身份且全部匹配源夹具；其中 84 次是匹配身份的重复出现，另有 84 个源 ID 缺失。不同 ID 中没有源外 ID，也没有同 ID 的载荷摘要差异。R5 报告未保存完整的查询差异 ID 列表。
- 离线证据将缺失批次高度关联到 Vector 丢弃的 84 条请求：Vector 在 `20:06:29.397946Z` 记录 HTTP 400 和 `count=84`；Loki 在同一时刻记录 `entry too far behind`，事件时间为 `20:01:20Z`、最早可接受时间为 `20:01:38Z`。源夹具中序列 `24434–24517` 恰有 84 条，时间从 `20:01:20.305747Z` 到 `20:01:20.513247Z`，先前保存的缺失样本落在该范围内。Loki 还记录了单流 2 MiB/s 和租户 4 MiB/s 限速导致的连续 429；R5 同时使用 3 秒请求超时和 adaptive 并发。该路径解释了迟到旧批被拒绝；由于查询差异 ID 未保存，尚不能声称这 84 条已逐 ID 完整 join。首个 84 行批次序列 `2–85` 在 Loki WAL 中各出现两次，与 3 秒超时后的孤儿请求重试相符，但请求体/ack 未归档，重复机制仍标为推断。完整离线分析见 [R5 离线身份审计](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r5-offline-identity-audit.json)。
- R5 源数据为合成 JSONL：120,001 个唯一 UUIDv4，`sequence` 是 1–120001 的 Python 整数，`duration_ms` 为 null，不存在大整数负载字段。所有 119,917 个 Loki 查询返回的不同身份及载荷摘要均与期望相同；没有证据指向 ID 生成、JSON/VRL 转换、Loki JSON 编码或大整数精度问题。源文件 SHA-256 未变，不能把这组测试差异称为应用日志损失。
- R6 探针已按此根因收窄：先停 Loki、再启动 Vector 填队列，排除预先发出的请求；恢复时固定单并发、每秒最多 4 个约 240 KiB 批次、60 秒请求超时，使吞吐低于原 Loki 限速。使用独立网络 `10.204.55.0/24`、唯一 R6 路径和 512 MiB tmpfs；队列目标先定为配置上限 268,435,488 字节，夹具为 120,000 条合成记录。新的查询核对会分开报告缺失 ID、重复出现、源外唯一身份和载荷不匹配。
- R6 完成后发现首轮 Vector metrics 抓取有启动时序竞态：远端日志显示 metrics 服务已绑定且后续直连有效，但首个探测返回 `vector_metrics_unavailable`，因此没有记录满 buffer 时的指标，也不能把 `peak_buffer_bytes=0` 解释为队列始终为空。Loki 恢复后 source checkpoint 到 EOF；回连快照显示 source 120,000、sink 117,684、队列 11,458,984 字节、丢弃计数 0。store-only 索引在 120 秒内未就绪，直接查询返回 0 行；ingester fallback 读回 120,000 行，120,000 个身份与载荷均匹配，无缺失、重复、源外身份或载荷差异。该结果支持合成数据在 ingester 查询路径上的无损恢复，但不等价于 store-only 查询验收。
- R6 的 Vector 与 Loki 均 exit 0、未被 OOM；内网 `10.204.55.0/24` 保留且无容器附件。停止后 tmpfs 实际使用 253,997,056 / 536,870,912 字节；快照 13,507,891 字节、73 个成员，SHA-256 `f01556c6…11f0174fd`，归档内源文件哈希与报告一致，校验后卸载。见 [R6 NAS 报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r6.json) 与 [R6 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r6-cleanup.json)。
- R7 使用协调仓库网络分配记录指定的 `10.205.16.0/24`。首轮启动因未预挂载所需 tmpfs 在 `bounded_tmpfs_required` 处停止，没有创建容器或网络；独立资源复核为 2,147,713,024 / 4,294,967,296 字节，历史 tmpfs 仅计仍挂载的实际使用。后续启动前按分配规则重新检查 Docker 子网、非默认主机路由和 loopback 端口；预检冲突为零，端口采用本任务 19520–19529 范围内当前未占用的 19526。
- R7 正式运行中 metrics 读取有效，但 240 秒填充窗口结束时 source 只推进到 49,042 条，队列峰值 131,316,136 / 268,435,488 字节（48.9%），所以 backpressure 未验收；源哈希保持不变。恢复后 checkpoint 到 EOF，丢弃和组件错误均为 0，队列降至 11,540,648 字节。store-only 索引 120 秒内未就绪、查询为 0 行；ingester fallback 返回 120,000 行，全部身份与载荷匹配。两个容器 exit 0、无 OOM；R7 网络保留且无附件。tmpfs 停止后使用 247,934,976 字节；快照 13,263,282 字节、71 个成员，SHA-256 `8ecf8838…c718bcdf`，归档内源哈希吻合，校验后卸载。见 [R7 NAS 报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r7.json) 与 [R7 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r7-cleanup.json)。
- R8 使用协调仓库分配段内的下一独立子网 `10.205.17.0/24`。启动前检查了 129 个 Docker 网段、非默认路由和 19527 端口，均无冲突；并发资源计划为 2,147,713,024 / 4,294,967,296 字节。相对 R7 保持 120,000 条夹具、512 MiB tmpfs 和请求配置不变，只把满队列观察窗口延长至 600 秒。
- R8 的 600 秒窗口仍只推进到 49,093 条；队列峰值 131,464,864 / 268,435,488 字节（48.97%），未达到满队列/backpressure 验收条件。R7 在 240 秒时为 49,042 条、131,316,136 字节（48.91%），延长等待没有实质改变平台值。R8 报告的 timeout `last_metrics.buffer_bytes` 为 0，与同一报告的 131,464,864 字节峰值和运行期间直接采样不一致；不能把这个零值解释为队列为空。源文件 39,728,900 字节，压力前、超时、恢复后、Vector 停止后及归档内 SHA-256 均为 `febd8d79…02624cfc8`。
- Loki 恢复后 Vector source 到达 120,000，sink 计数 117,768，队列剩 11,391,936 字节，丢弃和组件错误均为 0，`source_replay_complete=true`。回放期间观察到 Vector 重试 HTTP 500，Loki 日志出现 `empty ring`。store-only 索引在 120 秒内仍未就绪、查询返回 0 行；ingester fallback 返回 120,000 行，全部身份及载荷匹配，无缺失、重复、意外身份、载荷差异或查询错误。
- R8 的 Vector 与 Loki 均 exit 0、无 OOM；内网 `10.205.17.0/24` 保留且无容器附件。停机后 tmpfs 实际使用 250,753,024 / 536,870,912 字节；快照 13,400,142 字节、71 个成员，SHA-256 `b0181f5d…5d48b2b`，归档完整性及源文件哈希均已校验后卸载。详见 [R8 NAS 报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r8.json) 与 [R8 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r8-cleanup.json)。
- R5 两容器均 exit 0、未被 OOM；internal 网络当前无附件。停机后 tmpfs 使用 135,958,528 / 536,870,912 字节。完整快照已压缩为 14,802,592 字节，SHA-256 `63eab695…f14c7042`，107 个归档成员；归档内源文件哈希再次吻合，随后卸载 tmpfs。R5 机器报告与收尾核对分别见 [R5 主报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r5.json)、[R5 收尾报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r5-drain-reconciliation.json)。
- R9 使用 `10.205.18.0/24`、19529 端口与 1 GiB tmpfs，填充窗口 600 秒。增加 5250 字节合成 padding 后，实际 Loki 行长 5599 字节，超过其 4096 字节上限；Vector 记录 2713 次组件错误、丢弃 119372 条。source 最终读到 120000，但 store-only 与 ingester fallback 均查不到事件，因此 R9 不能作为有效的 buffer replay 验收。峰值 130653496 / 268435488 字节；timeout 的末次 buffer 指标为 0，与峰值及运行采样不一致。两个容器 exit 0、无 OOM；网络 `10.205.18.0/24` 内部且无附件，tmpfs 停止后使用 67543040 字节。快照 4004124 字节、46 个成员，SHA-256 `0d9687ea…740901bca`，归档源文件 SHA 已核对后卸载。详见 [R9 主报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r9.json)、[R9 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r9-cleanup.json) 与 [R9 预检计划](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r9-scope-plan.json)。
- R10 将 padding 恢复为 2500 字节，并在 4 req/s 下保持 Loki 在线。600 秒内队列峰值 134125696 / 268435488 字节（49.97%），source 与 sink 均到 120000，恢复后 buffer 为 0，错误和丢弃均为 0。store-only 索引在 120 秒内未就绪、查询 0 行；ingester fallback 返回 120000 行且全部身份和载荷匹配。两个容器 exit 0、无 OOM；`10.205.19.0/24` 内部网络无附件并保留。tmpfs 停止后使用 131432448 字节；归档 10555798 字节、64 个成员，SHA-256 `bbd5ec35…c8fd057d`，源文件 SHA 核对后卸载。见 [R10 主报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r10.json)、[R10 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r10-cleanup.json) 与 [R10 预检计划](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r10-scope-plan.json)。
- R11 将同一夹具的 sink 限速降到 1 req/s，复放上限增至 1800 秒。600 秒填充窗口的峰值为 134125880 / 268435488 字节（49.97%）；队列约 134.1 MB 时 source/sink 仍有推进，未见错误或丢弃，仍未验证满 buffer/backpressure。收尾保留的一个运行采样为 source 120000、sink 73836，而填充超时快照为 source 99091、sink 50316；两个计数快照不一致，缺少时间戳，无法确定先后及原因。恢复后报告 source 120000、sink 117096、buffer 11397760 字节；ingester fallback 查询得到 120000 个完全匹配的身份/载荷，而 store-only 查询 64630 行、缺 55370 个 ID（索引报告 1 个 series，但没有唯一 service 标签）。保留这组 counter 与查询结果，不据此宣称 store-only 或 buffer 满状态通过。两个容器 exit 0、无 OOM；`10.205.20.0/24` 内部网络无附件并保留。tmpfs 停止后使用 265916416 字节；归档 13764168 字节、70 个成员，SHA-256 `d0d7c960…203704b83`，源文件 SHA 核对后卸载。见 [R11 主报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r11.json)、[R11 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r11-cleanup.json) 与 [R11 预检计划](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r11-scope-plan.json)。
- 收尾审阅将 backpressure 判定收紧为：source 至少连续三个样本停滞、尚未读完源文件，且 buffer 连续三个样本都达到配置上限的 98%。R11 峰值约为上限 49.97%，这项收紧不改变 R11 结果。当前探针与边界用例共 21 项通过；全部 24 份跟进 JSON 可解析，handoff 内部文件链接均存在，`git diff --check` 通过。该收紧只在本地单测验证，没有重新运行 NAS 探针。
- 本轮仍未授权或执行源日志回收；应用回收门禁保持 blocked，生产 30 天保留和物理 ENOSPC 保持 `not_run`，`release_ready=false`、`application_reclamation_authorized=false`。不得复用 HTTP 204 或 flush 204 作为完整持久化证明。

## 2026-09-25 A5 后唯一补验 R12

- A5 固定源码分析确认 Vector 0.58.0 的 disk-v2 内部可用上限为 `268435488 - 134217728 = 134217760` 字节，公开 max gauge 仍为配置值。因此 R11 先前按公开 max 的 98% 判据不可达。R12 探针改记 UTC 与 monotonic 原始指标、至少三次相隔 5 秒的 source 停滞、内部上限与批次余量，并把预期离线连接重试与解析/磁盘/丢弃/HTTP 错误分开。先完成本地边界检查，然后只运行 `recovery-vector-20260925-r12` 一次；独立内网 `10.205.21.0/24`、512 MiB tmpfs、60,000 个唯一合成 ID、2500 字节 padding，实际 Loki 单行最长 2853 字节。
- 离线填充 300 秒中保存 52 个带双时钟时间戳的样本。source 在 49,102/60,000 停滞、未到 EOF，sink 离线、源 SHA 不变、无事件丢弃和非预期错误；Vector 日志中的 28 条连接失败按预期重试单列。buffer 峰值 `131490992` 字节，距内部上限 `2726768` 字节。首个可读指标已处于平台值，缺少正增长区间来测得实际编码事件大小；现有 262144 字节批次上限不能解释该差额。因此 **source 停滞有证据，但“队列接近内部上限且余量可解释”的满 buffer 判据未通过**，不能声称已验证 Vector 满 buffer。
- 恢复后 Vector source/sink 均到 60,000，队列为 0、丢弃为 0，源 SHA 仍一致；正常停止 Vector。`query_store_only=false` 的 ingester + store **混合查询**一次完整返回 60,000 行，所有唯一 ID 与载荷哈希精确匹配，无查询错误、重复、源外身份或超长行。此结果证明本次合成事件的混合查询可见性，不单独证明持久化 store 可见性。
- `/flush` 返回 204 后正常停止 Loki，并以 `query_store_only=true` 重启。06:41:52–06:46:22 UTC 每 15 秒发起的 19 次完整查询均返回 0/60,000，查询本身无错误；R12 自动探针记录 **store-only 未通过**。chunk、object-index、active-index、index-cache 的逐次时间戳与 SHA-256、flush/TSDB shipper/下载指标均在主报告。该次零结果的原因由后续 A5 固定源码与归档审计厘清：全部事件还处于 Loki `all`/filesystem 自动计算的 1635 秒近期排除窗口，store 区间为空，故 R12 并未实际测试持久化 store 读回；不能据此推断索引或数据丢失。
- 总运行 815.186 秒，未超 1500 秒期限；报告整体 `partial`，`release_ready=false`。Vector/Loki 均 exit 0、非 OOM；内部网络无附件。原合成源未删，tmpfs 归档 8,077,601 字节、62 个成员，SHA-256 `5c591348…81d7b94e`；归档内源 SHA 与运行源一致，独立 `gzip -t` 通过，随后确认 tmpfs 已卸载。见 [R12 主报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r12.json) 与 [R12 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r12-cleanup.json)。报告 NAS 与本地 SHA-256 同为 `3eb58cf6…47f09c`；旧 R2–R11 原始报告未修改。
- 本地针对性回归 28 项通过；R12 两份 JSON 可解析，`git diff --check` 通过。以上是探针判据与证据格式检查，不将它们替代 NAS 上未通过的两项验收。

## A5 复核与唯一持久化读取补验 R13

- A5 在固定提交 `c0f07c1` 中复核 R12 的 52 个双时钟样本及 Vector 0.58.0 源码：source 49,102/60,000 在 sink 离线时持续停滞，源不变、无丢弃，恢复后 source/sink 60,000、buffer 0、混合查询全量精确。**功能性背压与无损复放为 passed**。但 R12 峰值距内部上限 2,726,768 字节，下一条 disk-v2 序列化记录长度没有采集；**精确满 buffer 容量为 not_measured**，不把 sink HTTP 的 262144 字节 batch 上限当磁盘记录长度。R12 原报告保持原样，纠正口径见 [R12 复核附注](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r12-review.json)。
- A5 同时确认 Loki 3.7.8 `all`/filesystem 自动导出 27 分 15 秒（1635 秒）store 近期排除窗口；R12 19 次查询的最老事件年龄只有 833.981–1103.993 秒。`query_store_only=true` 关闭 ingester 分支，而 store 分支区间为空，确定性返回 0。R12 的“store-only 失败”是无效测试结果，不作为存储丢失证据。
- 在此基础上只运行一次新 R13 scope：`10.205.22.0/24` 内网、512 MiB tmpfs、60,000 个唯一合成 ID、2500 字节 padding。预检子网冲突为 0，端口可用，A2 内存计划 2 GiB / 4 GiB、主机可用约 19.18 GB。事件基准时间固定为运行开始约 1 小时前，2.5 ms 递增，实际最长 Loki 行 2853 字节。运行前离线回归检查从现在到 1500 秒期限的完整查询窗口始终早于 1635 秒截点，且在 3 小时策略、48 小时保留、720 小时拒收界限内；运行中 store-only 查询再按实际 UTC 时刻逐桶检查。没有等待事件变老，也没有重复 R12 的离线填充/停滞试验。
- Loki 在线时 Vector 受限速发送后，source/sink 均 60,000、buffer 0、丢弃/组件错误 0，源 SHA 保持 `23d7bc9e…968811`。Vector 正常停止。混合查询第三次完整扫描返回 **60,000/60,000** 身份与载荷精确匹配；前两次结果不全，均保存在主报告。`/flush` 204 后正常停止 Loki 并切为 store-only；查询开始 07:23:40 UTC，最老/最新事件年龄分别 3844.237/3694.239 秒，完整查询窗口处于非空 store 区间。第一次完整 store-only 查询返回 **60,000/60,000**，无缺失、重复、额外身份、载荷差异、查询错误或超长行。**本次隔离合成数据的持久化 store 读取验收通过**，不外推到生产 30 天保留。
- R13 保存 flush 前后、停机后、store 重启后和查询当刻的 chunk/object-index/active-index/index-cache 时间戳与 SHA-256，以及 Loki flush/shipper/下载指标。总运行 283.467 秒，未超 1500 秒期限；主报告整体仍为 `partial`，因回收合同、物理 ENOSPC 和生产保留等独立门禁未完成，`release_ready=false`。Vector/Loki 均 exit 0、非 OOM；内网无附件。原合成源未删，tmpfs 归档 8,195,440 字节、SHA-256 `6037eb71…0ad6d4`，归档源哈希吻合、独立 `gzip -t` 通过后卸载。见 [R13 主报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r13.json) 与 [R13 收尾核对](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r13-cleanup.json)；NAS 与本地报告 SHA-256 同为 `37337b0c…19d8f5`。R12 原始证据未修改，未追加第二 scope。

## 未完成与下一步

1. A5 已厘清 R12 的容量与 Loki 查询区间口径；R12 功能性背压与无损回放通过，精确下一条序列化记录长度仍未测量。R13 在非空 store 分支完成 60,000 条合成事件的持久化读回；这不等于生产保留期或回收门禁通过。按本次授权不自动追加新 scope 或延长期限。
2. 在应用回收合同冻结前保持回收门禁关闭，不删源数据；精确满 buffer 容量、物理 ENOSPC 与 30 天生产保留仍需另行设计隔离验收条件。
3. 本任务未推送、合并或更新协调仓库任务板。
