# NAS-A1 — 恢复读回与语义验收

## 目标与结论

对隔离合成 A1 scope 完成了产品支持路径上的事实准备和语义读回。`forgotten`、`source_revoked`、`model_revoked` 与 `unknown_no_resend` 均在当前 A1 source deployment 上得到正向证据；四个 A1 产品服务已正常停止，全部退出码为 0。

完整恢复验收仍未完成：DEP-J 备份计划返回 `path_missing`。当前部署没有 `.deployment.json`、`recovery-inventory.json` 或 observability Compose；五个 observability owner 均未登记/观察。因此没有生成新快照、恢复 `restored_disabled` 目标或启动九 owner 隔离克隆。当前结论为 `needs_validation`，source scope 的语义读回不能替代恢复副本读回。

## 变更

- 恢复断言默认保留 GET；新增 POST 时必须同时提供 `method: "POST"` 和 JSON 请求体。
- POST 仅访问明确列出的只读路径（包括 Companion web-snapshot）并绑定到对应服务，拒绝写接口、错误服务映射、查询字符串和超大请求体。TLS 请求全程共用总期限，响应摘要受 256 KiB 限制。
- 恢复副本的五个 observability owner 与四个 core owner 在功能读回时同时保持运行；只对 Compose 中实际配置 healthcheck 的服务报告 healthy，未配置 healthcheck 的 owner 只报告 running。
- 新增可选 A1 `runtime_observation`：独立 Companion diagnostics token 的 `/health/ready` 探针、1–300 秒窗口、窗口前后稳定的 unknown turn/reply API 读回，以及按 correlation ID 对比 Gateway accepted/started/finished 计数和成功控制组。Gateway 基线在任何克隆 owner 启动前读取。
- JSON 期望对象递归按子集比较，数组递归比较且长度必须精确相同；观察报告明确只计 API 可见 reply records，不声称未暴露的投递尝试计数，也不把有界观察写成永久保证。
- 增加 A1 专用验收适配器，通过 Platform/Companion/Memory 的既有 CLI 与 HTTPS API 建立、确认及读回合成状态。适配器不访问或直接写入产品数据库。

## A1 source scope 的功能证据

输入 H/I 是明确标为虚构的测试消息，由 A1 Platform CLI 登记和 fanout；模型上游是包内离线合成服务。调用使用的版本为 v3；未使用真实模型、真实聊天或生产资料。脱敏结果保存在 NAS source deployment 的 `reports/a1-synthetic-inputs-r4/`：

- Companion source-facts API 读回了序列 8/9 的 `committed_event`，来源与输入回执一致，分类为 `fictional`。
- Memory 真实 select API 在动作前读回绿色标记、红色标记和阅读事实。
- `forgotten`：Memory 本地用户 CLI 确认完整遗忘请求，随后现有 revise API 返回 HTTP 200、`authoritative_state=tombstoned`。之后绿色记录为 `no_match`，不同来源的红色记录仍可读。与绿色记录来自同一原始输入的“喜欢阅读”记录也变成 `no_match`；这项同源影响已保留在报告中。
- `source_revoked`：仅撤销 `source-input:` 凭据不会使既有 Memory 记录失效，实际读回仍看见红色事实。随后经 Platform CLI 登记 revision 2 `retract` 并执行正常 fanout；Memory select 返回 HTTP 200、`no_match`，红色记录不再可读。正向证据对应产品的来源撤回流程，而不是前一次仅撤销输入凭据的尝试。
- `model_revoked`：Platform CLI 撤销 v3 后，Platform `view-config` 返回 `availability=revoked`；由 Gateway 容器发起的 Platform 配置 snapshot API 对 v3 返回 HTTP 410/`forbidden`。
- `unknown_no_resend`：Companion API 两次读回序列 8/9，间隔约 2 秒，turn/reply ID 未变化；两个 turn 均为 `closed_unknown`，delivery 和 reply state 均为 `unknown`。Gateway 脱敏日志中总计 4 个合成 upstream 调用组（2 unknown、2 succeeded）；读回期间未再发起模型上游调用。

关键机器报告：`forgotten-readback-r4.json`、`source-revoked-readback-r4.json`、`source-retraction-readback-r4.json`、`model-revoked-readback-r4.json`、`unknown-no-resend-readback-r4.json`、`source-facts-r4.json`。报告不含凭据或 origin 明文。

## 正常停写与恢复阻断

执行 `docker compose stop --timeout 120` 停止 `companion`、`memory`、`gateway`、`platform`。Compose 读回四个容器均为 `exited`，退出码均为 0；没有对容器做强制终止。

DEP-J 的只读备份计划对 scope `b3f59a3a-0fd4-4d47-8ff9-2c7a00d8b7a5` 返回：`{"status":"rejected","reason":"path_missing","activation":"disabled"}`。核验到：

- A1 core Compose 只有四个产品服务，没有 `observability/compose.yaml`。
- `obs-vector`、`obs-loki`、`obs-grafana`、`obs-prometheus`、`obs-guard` 的 runtime identity 状态均为 `not_observed`。
- `deployments/source/.deployment.json` 与 `deployments/source/recovery-inventory.json` 不存在。
- 因此不能用当前四 owner 部署建立九 owner 一致性备份，也不能签发符合 DEP-J 的隔离克隆许可。

没有创建新的备份或恢复目标，也没有启动副本。没有触碰之前的 `scope-a1`、`next-m`；没有使用 SQL、任意写接口、真实模型或生产数据。

本轮继续只修改本地 DEP-J 工具、文档和合成测试；没有在 NAS 新启动容器、登记 owner、创建快照/恢复目标或运行克隆。下面的回归结果仅验证本地演练逻辑，不替代恢复副本的真实读回。

## 验证

- bundled CPython 3.12.14、临时隔离依赖 `jsonschema 4.26.0`：`test_drill_http*.py` 22 项通过，`test_drill_observation.py` 3 项通过，`test_drill.py` 全套 19 项通过；最后修改后重跑 A1 定向用例 4 项通过。HTTP 与事件扫描使用合成 loopback/TLS、Docker 和日志夹具。
- 新增事件夹具覆盖源日志的 9 个 correlation groups（2 unknown、2 成功 upstream、5 accepted-only）及一个成功控制组的重复 accepted；额外 upstream 事件会使观察失败。
- 修改文件的 Python `compileall` 与 `git diff --check` 通过。
- Ruff 检查和格式检查在最终工作树未重跑：可用环境没有 Ruff 可执行文件；此前已记录的检查通过结果早于本次 A1 适配器和 handoff 更新。
- NAS 上既有 A1 source 功能证据来自真实产品 CLI/API，但没有在恢复副本上执行；不能据此标记完整恢复验收通过。新增的 readiness 与限时 no-resend 逻辑尚无恢复副本运行证据。

## 下一步

按公开 DEP-B/DEP-G/DEP-J 流程为当前 A1 scope 配置并登记五个 observability owner、`observability/compose.yaml`、部署 marker 和 recovery inventory；随后正常停写、创建新快照、恢复到新的 `restored_disabled` 目录，最后用独立凭据/TLS、九 owner 和限时运行观察执行一次性隔离克隆。不得通过 SQL、伪造 owner 状态或复用旧 scope 绕过 authority。完成前继续保持 `needs_validation`。
