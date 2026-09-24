# NAS-A1 — 恢复读回与语义验收

## 目标与结论

为恢复演练补充严格受限的只读 POST 断言，支持后续验收四产品恢复读回，以及 `forgotten`、`source_revoked`、`model_revoked`、`unknown_no_resend`。本轮完成工具和本地验证；NAS 产品功能验收为 `not_run`，不能作为剩余恢复语义已通过的证据。

此前 m3 回执只通过 Gateway 的历史成功回执读回，报告状态是 `partial_functional_coverage`，缺 Companion、Memory、Platform 读回及四项目标语义。旧 m/source、`restore-disabled`、登记及一次性许可均未重做或启动。

## 变更

- 恢复断言仍默认使用 GET；新增 POST 时必须同时提供 `method: "POST"` 和 JSON 请求体。
- POST 只能访问固定的只读路径，并绑定到对应产品服务：Companion 的 source-facts/life-read，Memory 的 select/source-sync/check，Platform 的 source-access/read。写入、未知服务映射和带查询字符串的 POST 在连接前拒绝。
- 请求 JSON 上限 16 KiB；响应沿用 256 KiB 上限。TLS 建连、请求写入、状态行、响应头和响应体共用同一总期限，不跟随跳转、不重试；报告只保留响应摘要。
- 未改动四个产品、NAS 旧 scope、恢复登记或生产数据。

## 验证

- `test_drill_http*.py`：18 项通过，覆盖旧 GET、允许的 TLS POST、非法写路径、错误服务映射、查询字符串、超大请求体、响应上限和总期限。
- `test_drill.py`：14 项恢复演练回归通过；新增的只读 POST 输入白名单用例单独通过。
- Ruff 检查、格式检查和 `git diff --check` 通过。
- 本地 Python 3.12.14；测试使用合成 TLS 服务和合成响应，不代表真实产品或 NAS 读回通过。

## NAS 前置检查与未运行原因

独占目标 `/volume2/tianshu-v2-validation-wave1/accept-20260925-a1` 不存在；没有创建该目录或 A1 容器。候选网段 `10.204.40.0/24` 至 `10.204.47.0/24` 没有 Docker 子网或路由冲突；`127.0.0.1:19510–19519` 没有 TCP/TCP6 监听或 Docker 发布端口。对全部 Docker 容器挂载源核对后，没有与目标路径重叠的挂载祖先/后代。NAS 可用内存为 18,525,568 KiB，高于 4 GiB 预算；`/volume2` 可用空间约 475 GB。

从旧 `nas-first-snapshot` 备份和 `restore-disabled` 副本以 SQLite immutable/read-only 方式只读取聚合行数，没有读取正文或修改数据库。两份数据的关键行数一致，但缺少本任务要求的可恢复前置事实：

- Companion 有 1 条 turn，但 `life_access`、`life_diaries`、`life_actors` 均为 0。
- Memory 有 1 个 account/person，但 `records`、`groups`、`sources`、`source_admissions`、`suppression` 均为 0。
- Platform 的 `sources`、`revoked_entries`、`revoked_principals` 均为 0。
- Gateway 有 1 条请求/turn，`revoked` 与 `native_revoked` 均为 0；旧 m3 报告只验证了历史成功回执，没有验证未知结果或不重发。

因此本轮没有启动新的 NAS 恢复副本，也没有用 SQL 或任意写接口制造成功状态。四产品完整业务读回及四项语义在本轮均记为 `not_run`。机器证据见 [NAS-A1.json](NAS-A1.json)。

## 下一步

需要一个通过产品公开流程生成、并已纳入可恢复快照的隔离合成数据集，包含可读回业务事实及四项语义所需的既有状态。准备完成后，使用新的 A1 scope 和固定容器/image 身份执行 NAS 读回；不得直接篡改数据库或重放旧 scope 的初始化。
