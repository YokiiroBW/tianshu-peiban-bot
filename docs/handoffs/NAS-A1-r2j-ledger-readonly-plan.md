# NAS-A1 r2j 已停机 Gateway 账本只读补验方案

状态：**一次停机副本离线读回已完成并经 A4 独立复核通过**。实际结果见 [NAS-A1-r2j-ledger-readback-evidence.json](NAS-A1-r2j-ledger-readback-evidence.json)。r2j 唯一 source 尝试已经失败并停机；此补验只澄清当时 Gateway 四尝试账本，不能把 source、恢复或六项克隆 HTTPS 改判为通过。原始故障与九 owner 停机见 [NAS-A1-r2j-evidence.json](NAS-A1-r2j-evidence.json)。

## 固定边界

- 原 scope：`/volume2/tianshu-v2-validation-wave1/accept-20260926-a1-r2j/scope-a1-r2j`，UUID `72d01f54-fef9-4bfe-8e4c-50ec8263fef7`。source 九 owner 均已 `exited(0)`，五个 source 网络端点为 0；不启动任何容器，不执行 source/clone/permit/recovery，也不改变原 scope、原工具 venv、设置、数据库或失败收据。
- 新专属目录由协调者固定为同一 parent 的 `usage-readback`，先核不存在和非符号链接，执行时独占创建、权限 0700。wheelhouse、`--copies --without-pip` CPython 3.12.14 venv、数据库字节副本及原始私有回执仅写这里；所有产品原路径保持只读。
- 固定 Gateway commit `601974194042641c5a85cc3c061cbd1880d7daf1`；已组装的 Gateway 17 文件树 SHA256 `39355bc3de216f18c37ee8d2f9f70673ae5aeb2eeaee8b8beacff314e7915755`。该提交的 `uv.lock` 原字节 SHA256 `56de5d63a49b0f4544ca3926eadfab4ff6b8f5b7506f976cb9b8832002700e29`。
- 本地已取得下表 14 个 CPython 3.12/Linux x86_64 wheel，逐文件 SHA 与固定 `uv.lock` 对应 URL/hash 相等；完整文件名、来源 URL、兼容 tag 和路径见 [NAS-A1-r2j-ledger-wheel-lock.json](NAS-A1-r2j-ledger-wheel-lock.json)，该锁原字节 SHA256 `8d19ce53b3de99fec8455eb762963f5e531956a3b6fb8781ab16c949b7b7aa9c`。其中 6 个与已装 r2j 恢复依赖**版本及 wheel SHA 均相同**；Gateway 追加 8 个。固定锁中除这 14 项外仅有项目自身和开发工具 Ruff。不得在线求解、替换版本或临时升级。

| 包 | 固定版本 | wheel SHA256 | 与原恢复环境 |
| --- | --- | --- | --- |
| aiohappyeyeballs | 2.7.1 | 9243213661e29250eb41368e5daa826fc017156c3b8a11440826b2e3ed376472 | Gateway 追加 |
| aiohttp | 3.14.1 | 3e6fc1a85fa7194a1a7d19f44e8609180f4a8eb5fa4c7ed8b4355f080fad235c | Gateway 追加 |
| aiosignal | 1.4.0 | 053243f8b92b990551949e63930a839ff0cf0b0ebbe0597b0f3fb19e1a0fe82e | Gateway 追加 |
| attrs | 26.1.0 | c647aa4a12dfbad9333ca4e71fe62ddc36f4e63b2d260a37a8b83d2f043ac309 | 与 r2j 恢复环境同版 |
| frozenlist | 1.8.0 | 494a5952b1c597ba44e0e78113a7266e656b9794eec897b19ead706bd7074383 | Gateway 追加 |
| idna | 3.19 | 815e7be7a7806d54abb586dc943addc79e8b2ee16915059658cbeff4b1b43bf4 | Gateway 追加 |
| jsonschema | 4.26.0 | d489f15263b8d200f8387e64b4c3a75f06629559fb73deb8fdfb525f2dab50ce | 与 r2j 恢复环境同版 |
| jsonschema-specifications | 2025.9.1 | 98802fee3a11ee76ecaca44429fda8a41bff98b00a0f2838151b113f210cc6fe | 与 r2j 恢复环境同版 |
| multidict | 6.8.0 | 003a3bddb32915c3f67096ea41d24e53edf710edb65a1f5d0c70ab40b0e4d20b | Gateway 追加 |
| propcache | 0.5.2 | 6f328175a2cde1f0ff2c4ed8ce968b9dcfb55f3a7153f39e2957ed994da13476 | Gateway 追加 |
| referencing | 0.37.0 | 381329a9f99628c9069361716891d34ad94af76e461dcb0335825aecc7692231 | 与 r2j 恢复环境同版 |
| rpds-py | 2026.6.3 | ecabd69db66de867690f9797f2f8fa27ba501bbc24540cbdbdc649cd15888ba6 | 与 r2j 恢复环境同版 |
| typing-extensions | 4.16.0 | 481caa481374e813c1b176ada14e97f1f67a4539ce9cfeb3f350d78d6370c2e8 | 与 r2j 恢复环境同版 |
| yarl | 1.24.5 | f08c7513ecef5aad65687bfdf6bc601ae9fccd04a42904501f8f7141abad9eb9 | Gateway 追加 |

## 一次性执行顺序

1. 只读复核固定 scope/config/code/Gateway 树、source 尝试失败标记、九个精确容器 ID 的 `exited(0)`、五网端点 0、无活动 source 流程。清点原 `data/gateway/diagnostics.sqlite`、`-wal`、`-shm` 及 Companion `data/companion/companion.db` 同名 sidecar 的**存在性、大小、SHA256、inode、mtime**，出具第一份私有基线；任何漂移即停。
2. 在新专属目录离线安装上述 14 wheels，核 `sys.executable`、`sys.base_prefix`、全部分布版本和导入来源；Gateway 模块仍从原固定 17 文件树导入。若导入失败或 ABI 不兼容，停在工具准备，不碰原账本。
3. source 全部 owner 已停且无写者时，逐字节复制 Gateway 与 Companion 的 DB/WAL/SHM **已存在文件**到新目录，副本文件名保持相同；不存在的 sidecar 继续记录为不存在，不生成到原目录。复制后核副本与第一份原字节 SHA 相同，再核原文件全套元数据与 SHA 未变。任何变化即停。此方式让 SQLite 即使为只读连接在 WAL 模式创建或更新 `-shm`，也只影响标为“副本”的目录。
4. 先在 Companion **副本**上以固定 `_source_routes` 校验两条 source-facts turn 的 route receipt，独立固定两个 `model:[a-f0-9]{32}` request ID。另两个预期 ID 已固定为 `a1-r2j-20260926-offline-control-5` 和 `a1-r2j-20260926-offline-control-6`。两条 model ID 在此步骤前未知，不从 Gateway 查询结果反推，也不伪称事先固定；若 route 读取失败即停。
5. 以新 venv 执行**原产品** `tianshu_gateway.usage_report`，参数与失败时相同，仅 `--database` 指向 Gateway 副本：`--settings <原source>/config/gateway/settings.json --database <新目录>/source-copy/data/gateway/diagnostics.sqlite --service companion --view attempts --since 2026-09-25T00:00:00Z --until 2026-09-26T00:00:00Z --compact`。在子进程环境内从原 `private/gateway.env` 读取凭据并设 `PYTHONPATH=<原parent>/tooling/gateway-pythonpath`，不打印值。固定产品 CLI 的 `open_ledger` 用 SQLite `mode=ro`，只执行读取；由于实际查询对象是副本，结论必须标为**停机一致性副本读回**。
6. 验收仅当 CLI exit 0、schema 1、`key_space=chat`、identity `service=companion`、total 4 / succeeded 2 / unknown 2 / failed 0 / cancelled 0、coverage matching/scanned 4、truncated false、unmetered 0，且四 ID 恰与第 4 步固定集合相等；两 model 的 reason/outcome 为 `completed/succeeded`，两个离线控制为 `transport_unknown/unknown`，所有 completed_at 落在固定窗口。原样保存子进程 stdout/stderr/退出码于 0600 私有回执，公开索引只写结构、SHA 和去敏摘要。任何不符标失败，不能改期望后重放。
7. 查询后重算原 Gateway 与 Companion DB/WAL/SHM 的存在性、大小、SHA、inode、mtime，并与第 1 步严格比较；核九 owner 仍 `exited(0)`、网络仍无端点、原恢复与 Gateway 固定代码树、venv 标记文件、settings/env、bundle 清单、配置锁及失败收据前后 SHA 不变。副本及新工具目录保留供复核，不删改原现场。

## 解释与限制

具体脚本已按固定 wheel 锁、精确写入路径、数据库副本边界及四 ID 固定方式审查。实际原库只有主 DB，无 WAL/SHM；Companion 副本读取与 Gateway 副本 CLI 各自在副本内新建 WAL/SHM，原数据目录完整清单、字节 SHA、inode 和 mtime 均未变。停机一致性副本查得四尝试 2 succeeded / 2 unknown，且与先从 Companion 副本固定的两条 route ID 和两个离线控制 ID 完全一致；原源端在当时因缺 `aiohttp` 未运行该 CLI。source complete、runtime identity、registration、恢复、许可和克隆六项 HTTPS 仍为未到达。
