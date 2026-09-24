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

## 2026-09-25 NAS 跟进 R2–R5

- R2 与 R4、R5 的恢复诊断一致：Guard push/flush 均为 HTTP 204，直接 Loki 与 Guard 查询能在 ingester 可用时读回精确合成事件；切换为 store-only 查询后找不到该事件，恢复 ingester 后又能读回。诊断为 `store_only_query_missing_while_ingester_wal_returns_event`，没有证据表明这是标签或时间戳错误。
- R3 在 Loki 启动前失败：网络为 `10.204.52.0/24`，容器仍请求旧地址 `10.204.51.10`，Docker 拒绝启动并留下 Created/exit 128 容器。容器和网络保留；见 [R3 报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r3.json)。
- R4 报告记录清理确认错误，后续只读检查确认 Vector/Loki 均已停止且 exit 0。报告曾记载一个停止态网络附件，当前网络无附件。R4 的 tmpfs 归档有 82 个成员，SHA-256 为 `03ce158c…1604dc`，已校验并卸载。
- R5 的前两次预检没有启动新服务：第二次报告暴露 R2 历史资源校验误用 R5 scope 的问题，已改为按 R2 自身标签检查；R4 网络校验也允许当前附件数较停机报告减少。相关尝试记录为 `nas-a2-followup-2026-09-25-r5-preflight-attempt-1.json` 与 `…attempt-2.json`。第一次失败的具体原因只在本地尝试记录中标为推断，未当作远端原始日志。
- R5 预检通过：累计计划内存为 4,026,761,216 字节，低于 4 GiB；镜像 digest 固定。恢复诊断再次为 store-only 查询暂时漏读而 ingester 可读。
- R5 向唯一服务标签写入 120,001 行合成数据，源文件 39,729,227 字节，前后 SHA-256 一致：`7c2541c3…38d4b8`。原始执行因 Vector 指标请求打到未生效的 loopback 发布端口而报告 `vector_metrics_unavailable`；同时 Loki 曾因 4 MiB/s 限速返回 429。
- 在同一 R5 资源内继续排查时，直接访问 Vector bridge 地址可读指标。队列先稳定在 131,846,120 / 268,435,488 字节；恢复 Loki 并提高本次合成测试限速后，Vector 报告队列清空，source/sink 计数均为 72,723，稳定观察 90 秒，checkpoint 到达源文件末尾。完整 Loki 身份核对返回 120,001 行，其中 119,917 个身份与源夹具完全匹配；84 个源事件 ID 缺失，另有 84 个返回 ID 不在源文件中，二者没有交集，也没有相同 ID 的载荷摘要差异。回放身份核对因此仍为 **partial**；不能据此宣称 Vector 满 buffer/backpressure 验收通过。
- 为后续探针修正了 Vector 指标路径：不再发布宿主 loopback 端口，改用固定容器 IP `10.204.54.11:9598`。这项代码修正已通过本地单测和 Ruff 检查，尚未在新的 NAS 容器运行中复测；未因接近既定累计资源预算而新建 R6。
- R5 两容器均 exit 0、未被 OOM；internal 网络当前无附件。停机后 tmpfs 使用 135,958,528 / 536,870,912 字节。完整快照已压缩为 14,802,592 字节，SHA-256 `63eab695…f14c7042`，107 个归档成员；归档内源文件哈希再次吻合，随后卸载 tmpfs。R5 机器报告与收尾核对分别见 [R5 主报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r5.json)、[R5 收尾报告](../../tests/deployment/observability/evidence/nas-a2-followup-2026-09-25-r5-drain-reconciliation.json)。
- 本轮仍未授权或执行源日志回收；应用回收门禁保持 blocked，生产 30 天保留和物理 ENOSPC 保持 `not_run`，`release_ready=false`、`application_reclamation_authorized=false`。下一步应先由协调者审查 84 条身份差异与队列/限速行为，再决定是否设计新的隔离容量窗口；不得复用 204/flush 作为完整持久化证明。

## 未完成与下一步

1. 协调者审查并集成本任务提交后，再决定是否补齐容量恢复后事件可查询性；不要把 HTTP 204 或 flush 204 当作完整恢复证明。
2. 在应用回收合同冻结前保持回收门禁关闭，不删源数据；物理 ENOSPC、Vector 满 buffer 与 30 天生产保留需另行获批和设计隔离条件。
3. 本任务未推送、合并或更新协调仓库任务板。
