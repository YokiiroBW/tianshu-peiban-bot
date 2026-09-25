# NAS-A1 — 恢复读回与语义验收

## 目标与结论

对隔离合成 A1 scope 完成了产品支持路径上的事实准备和 source 语义读回。`forgotten`、`source_revoked`、`model_revoked` 与 `unknown_no_resend` 均在 A1 source deployment 上得到正向证据。随后五个 OBS owner 正常加入 scope，DEP-J 成功登记九 owner、正常停写、创建一致性快照，并恢复到新的 `restored_disabled` 目标。

仍未完成恢复副本上的公开功能读回和限时 `unknown_no_resend` 观察。当前 scope 没有独立 `drill-inputs` 或一次性 permit，也没有启动隔离克隆；source 保持停止，恢复目标保持 `restored_disabled`。因此本交接状态仍为 `needs_validation`，source 上的语义证据不能替代恢复副本验收。

## 变更

- 恢复断言默认保留 GET；新增 POST 时必须同时提供 `method: "POST"` 和 JSON 请求体。
- POST 仅访问明确列出的只读路径（包括 Companion web-snapshot）并绑定到对应服务，拒绝写接口、错误服务映射、查询字符串和超大请求体。TLS 请求全程共用总期限，响应摘要受 256 KiB 限制。
- 恢复副本的五个 observability owner 与四个 core owner 在功能读回时同时保持运行；只对 Compose 中实际配置 healthcheck 的服务报告 healthy，未配置 healthcheck 的 owner 只报告 running。
- 新增可选 A1 `runtime_observation`：独立 Companion diagnostics token 的 `/health/ready` 探针、1–300 秒窗口、窗口前后稳定的 unknown turn/reply API 读回，以及按 correlation ID 对比 Gateway accepted/started/finished 计数和成功控制组。Gateway 基线在任何克隆 owner 启动前读取。
- JSON 期望对象递归按子集比较，数组递归比较且长度必须精确相同；观察报告明确只计 API 可见 reply records，不声称未暴露的投递尝试计数，也不把有界观察写成永久保证。
- 增加 A1 专用验收适配器，通过 Platform/Companion/Memory 的既有 CLI 与 HTTPS API 建立、确认及读回合成状态。适配器不访问或直接写入产品数据库。
- DEP-J 将 Prometheus 上游镜像声明的匿名 `/prometheus` image volume 作为唯一窄例外：必须同时匹配固定 Compose tmpfs 选项，并在运行态从 mount namespace 确认只读 tmpfs 覆盖；该匿名卷不进入恢复清单。

## A1 source scope 的功能证据

输入 H/I 是明确标为虚构的测试消息，由 A1 Platform CLI 登记和 fanout；模型上游是包内离线合成服务。调用使用的版本为 v3；未使用真实模型、真实聊天或生产资料。脱敏结果保存在 NAS source deployment 的 `reports/a1-synthetic-inputs-r4/`：

- Companion source-facts API 读回了序列 8/9 的 `committed_event`，来源与输入回执一致，分类为 `fictional`。
- Memory 真实 select API 在动作前读回绿色标记、红色标记和阅读事实。
- `forgotten`：Memory 本地用户 CLI 确认完整遗忘请求，随后现有 revise API 返回 HTTP 200、`authoritative_state=tombstoned`。之后绿色记录为 `no_match`，不同来源的红色记录仍可读。与绿色记录来自同一原始输入的“喜欢阅读”记录也变成 `no_match`；这项同源影响已保留在报告中。
- `source_revoked`：仅撤销 `source-input:` 凭据不会使既有 Memory 记录失效，实际读回仍看见红色事实。随后经 Platform CLI 登记 revision 2 `retract` 并执行正常 fanout；Memory select 返回 HTTP 200、`no_match`，红色记录不再可读。正向证据对应产品的来源撤回流程，而不是前一次仅撤销输入凭据的尝试。
- `model_revoked`：Platform CLI 撤销 v3 后，Platform `view-config` 返回 `availability=revoked`；由 Gateway 容器发起的 Platform 配置 snapshot API 对 v3 返回 HTTP 410/`forbidden`。
- `unknown_no_resend`：Companion API 两次读回序列 8/9，间隔约 2 秒，turn/reply ID 未变化；两个 turn 均为 `closed_unknown`，delivery 和 reply state 均为 `unknown`。Gateway 脱敏日志中总计 4 个合成 upstream 调用组（2 unknown、2 succeeded）；读回期间未再发起模型上游调用。

关键机器报告：`forgotten-readback-r4.json`、`source-revoked-readback-r4.json`、`source-retraction-readback-r4.json`、`model-revoked-readback-r4.json`、`unknown-no-resend-readback-r4.json`、`source-facts-r4.json`。报告不含凭据或 origin 明文。

## 九 owner 登记、正常停写与恢复读回

scope `b3f59a3a-0fd4-4d47-8ff9-2c7a00d8b7a5` 为本轮唯一 NAS 目标。OBS Compose 已加入五个 owner，Prometheus 的 `/prometheus` 由固定只读 tmpfs 覆盖；外层镜像 volume 仍存在但被 mount namespace 覆盖。运行身份报告记录四个 core healthcheck 均 healthy、五个 OBS owner 均 running（未配置 healthcheck，不报告 healthy），UID/GID 为 `10001:10001`。

- runtime identity SHA256：`79809f34bff2e7d57b0d3ebd7bf88407373de098781eb6a3e604ec7203ab6b34`。
- `linux-prepare` plan 通过；execute 返回 `registered`，authority UUID 为 `61e7cc33-8ece-44f5-80b0-a3ea22e04e60`，registration SHA256 为 `f6b46d5595d90d0c0c058ee0e924b335ae00afc9fe3c247405101379a07a58e3`，activation 保持 disabled。
- `linux-rehearse` plan 通过；execute 按 `platform → companion → memory → gateway → obs-vector → obs-guard → obs-grafana → obs-prometheus → obs-loki` 发 SIGTERM。九个容器均退出码 0；无强制终止。
- 新快照 `a1-r1-backup-20260925` 包含 204 个文件，snapshot SHA256 为 `208e4da028e8ebc43dc44889232acd38d18ae18a2da4b2654b97fbee91f5129f`。独立 `verify-backup` 返回 `integrity_verified`，磁盘 snapshot 文件原字节 hash 与收据相同。
- 新目标 `a1-r1-restored-20260925` 标记为 `role=restored,status=restored_disabled`；restore 内部返回 `state_verified`，`verification_sha256=b5525653daaa359255dadf4c1df204d10f4458215f1feb3a9daa734e8ef72cd3`。source 和恢复目标均未启动。

## 隔离克隆的未完成项

只读检查未发现 `drill-inputs/`、`inputs.json` 或一次性 permit，因此未创建 drill claim、克隆目录或容器。DEP-J 要求独立配置、私有凭据、TLS 与真实只读 API 断言；所有私有输入必须与 source 不同。A1 当前 Gateway config-origin 是绑定已快照 Platform 状态的短时引用，旧 `issued-origin-summary.json` 的 expiry/hash 已不能代表当前 `private/gateway.env`；其当前有效期没有可核的签发收据。source 已登记并停止，恢复目标已禁用，不能为续期而直接改写任一输入，否则会破坏 runtime identity、registration 或 restore verification 绑定。

未触碰较早 scope、A2/A3、Dockge；没有使用 SQL、任意写接口、真实模型或生产数据。source 九 owner 保持 exited(0)，恢复目标保持 disabled。要完成克隆，需要在新的、明确授权的 A1 scope 中先准备新的 config-origin、专属凭据/TLS、固定 API 断言和一次性 permit，再做登记和快照；不要试图复用本次 r1 的 source/restore 绑定。

## 验证

- bundled CPython 3.12.14、临时隔离依赖 `jsonschema 4.26.0`：`test_drill_http*.py` 22 项通过，`test_drill_observation.py` 3 项通过，`test_drill.py` 全套 19 项通过；最后修改后重跑 A1 定向用例 4 项通过。HTTP 与事件扫描使用合成 loopback/TLS、Docker 和日志夹具。
- 新增事件夹具覆盖源日志的 9 个 correlation groups（2 unknown、2 成功 upstream、5 accepted-only）及一个成功控制组的重复 accepted；额外 upstream 事件会使观察失败。
- 修改文件的 Python `compileall` 与 `git diff --check` 通过。
- 新增 Prometheus image-volume 覆盖回归：`test_compose_lifecycle.py` 8 项通过；针对本次变更的 `compileall` 与 `git diff --check` 通过。
- 完整 packaging suite 共 103 项，其中 4 项报错（99 项通过）：1 项 TLS round-trip 和 3 项 synthetic-init 用例均在 TLS handshake 报 `Missing Authority Key Identifier`。这些失败发生在本次恢复适配器覆盖之外，仍作为未通过项记录。
- Ruff 检查和格式检查在最终工作树未重跑：可用环境没有 Ruff 可执行文件；此前已记录的检查通过结果早于本次 A1 适配器和 handoff 更新。
- NAS 上 A1 source 功能证据来自真实产品 CLI/API；九 owner 停写、备份和禁用恢复已在 NAS 执行。恢复副本上的功能断言、readiness 与限时 no-resend 逻辑尚未运行，不能标记完整恢复验收通过。

## 下一步

下一步需先确认是否创建新的隔离 A1 scope。新 scope 应在首次登记前准备可验证的 config-origin 收据、clone 专属 TLS/凭据和网络/端口计划；source 语义准备完成后，再按 DEP-G/DEP-J 登记九 owner、正常停写、生成新快照和禁用恢复目标，最后签发最多 900 秒的一次性 permit，运行九 owner 克隆与五类公开 API 断言及有界 `unknown_no_resend` 观察。不得通过 SQL、伪造 owner 状态、修改已绑定 r1 source/restore 或重放 permit 绕过 authority。完成前继续保持 `needs_validation`。
