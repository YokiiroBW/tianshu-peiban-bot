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

- `python -m unittest discover -s tests/deployment/observability -p "test_nas_a2_probe.py" -v`：7 项通过。
- AST 解析通过；`git diff --check` 通过。
- Ruff 未在当前 shell 找到，未记录为通过。

## 未完成与下一步

1. 协调者审查并集成本任务提交后，再决定是否补齐容量恢复后事件可查询性；不要把 HTTP 204 或 flush 204 当作完整恢复证明。
2. 在应用回收合同冻结前保持回收门禁关闭，不删源数据；物理 ENOSPC、Vector 满 buffer 与 30 天生产保留需另行获批和设计隔离条件。
3. 本任务未推送、合并或更新协调仓库任务板。
