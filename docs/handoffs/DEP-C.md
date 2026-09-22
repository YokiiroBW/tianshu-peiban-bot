# DEP-C 交接：一致备份、隔离恢复与代码回退

状态：**rowid 遮蔽返修完成，本地 38 项通过，待协调复验；尚未集成，不代表真实部署恢复能力完成。**

## 范围、基线与提交

- 精确任务卡：原协调目录 `C:/YOKI/Codex/tianshu-peiban-bot/docs/development/deployment-wave2-codex-2026-09-22.md`，以及同目录 gap plan、CURRENT、NAS 核对报告、四产品 AGENTS、diagnostics/v1。此次明确 Codex 直接开发授权覆盖旧 DSH 约定；未派子代理。
- 本号 worktree：`C:/Users/Administrator/.codex/worktrees/aa35/tianshu-peiban-bot`；分支 `codex/dep-c-recovery`；根基线 `21668e4300d7bedc8ac1ed819bdb7dad4a2bd58b`。
- 首轮实现/证据提交：`4bbad55bebb29a2620755ef32b31ca25da68f859`；首轮交接提交：`b24a430ee5bbe518e3fd13e1fa597914523e1c2c`。初验发现 rowid 遮蔽阻断，未合并；本轮在该基线上只修快照逻辑、对应测试和本交接，修复提交为包含下节的 Git 提交。
- DEP-A 固定接口：`e86d622a813f116b059b62b820cbd1342722042d` 的两个 Git blob；已核原始 LF hash。没有从其活跃代码构建输入；四产品固定提交与备份矩阵详见 [INTEGRATION](../../ops/recovery/INTEGRATION.md)。未读活跃 TS104–106 检出。
- 只写 `ops/recovery/`、`tests/deployment/recovery/` 和本交接。没有改原协调目录、根任务板/CURRENT/workspace/contracts/产品代码，没有 NAS、真实数据/账号/付费模型、生产部署、推送或自动合并。

## 2026-09-22 初验返修：SQLite 隐式行标识遮蔽

已读取协调原目录 `docs/development/reviews/DEP-C-review-2026-09-22.md` 和 `.runtime/review-dep-c/rowid-result.json`。原实现按 Python 大小写敏感规则筛选 `_rowid_/rowid/oid`，SQLite 却将 `_ROWID_` 等用户列视为同名遮蔽；只改变真实 rowid 时事实摘要未变，导致旧包恢复被错误允许。新增完整恢复测试先在旧实现上复现了该问题；不是只测一个 helper。

本次仅修改三个文件：`ops/recovery/snapshot.py`、`tests/deployment/recovery/test_recovery.py`、本交接。别名检测现在使用 `table_xinfo` 的全部列（含隐藏/生成列），按 ASCII 大小写折叠识别遮蔽；通过 `table_list.wr` 明确认定 WITHOUT ROWID，不再把任意查询错误当作“没有 rowid”。三个别名均被遮蔽时，只有单列、精确 INTEGER、没有独立 PK 索引的普通主键能证明其值就是 rowid，才允许由 SELECT * 保留；无法证明则返回 `sqlite_row_identity_unavailable`，不发布备份。`INT PRIMARY KEY`、复合主键和列内 `INTEGER PRIMARY KEY DESC` 不误作证明。

新增 4 项测试、15 个参数用例：4 种混合大小写遮蔽，经完整备份→只修改真实行标识（SELECT * 用户值保持不变）→恢复拒绝且目标不存在；5 种全部别名遮蔽而无法证明身份的快照拒绝；2 种 WITHOUT ROWID 和 4 种合法 INTEGER PRIMARY KEY（包括全部别名遮蔽、表级 DESC、主键自身名为 `_RoWiD_`）正常恢复后，再改主键拒绝旧恢复。

修复后执行：

```text
python -B tests/deployment/recovery/run_verification.py --output tests/deployment/recovery/.runtime/verification-rowid-revision-2026-09-22.json
ruff check --no-cache --select E4,E7,E9,F,I ops/recovery/snapshot.py tests/deployment/recovery/test_recovery.py
ruff format --check --no-cache ops/recovery/snapshot.py tests/deployment/recovery/test_recovery.py
git diff --check
```

实际结果：**38 tests，0 failures，0 errors，0 skipped，18.560 秒，退出 0**；Ruff 0.14.0 通过且 2 files already formatted，完整本次 diff 审查及空白检查通过。环境仍为 Windows / Python 3.12.14 / SQLite 3.53.1，既有 WAL/guard/遗忘/取消/失败切换/路径与恢复后合成 HTTP 重启测试均在本次 38 项中通过。没有重复未变化的 DEP-A/合同检查，本次报告对此明确 `not_run`。日志/机器报告保留本号测试 `.runtime/`，未扩大返修的版本控制文件范围。

报告全部 Python SHA256 与修复后文件逐项复核一致；本次改动文件 SHA256：snapshot.py `8db945d34b2ccb6b987c4674f829f9db0009f3436969fc1de466ae76186847bd`，test_recovery.py `7b1ebc1a8f680730279bf8dbfcc0105744f98ab83ece50b1d4cac001fbb558c2`。原 `verification-final-2026-09-22.json` 保留为**首轮历史证据**，不代表本次修复或当前源码；以本节和本次 runtime 报告为准。

边界：已遮蔽 rowid 的旧错误快照可能被新事实校验拒绝，应在当前合成权威状态上重新备份，不能修改旧收据绕过。新检查依赖 SQLite 3.37.0 起的 table_list 元数据，不可获取时明确拒绝，未推定更旧 SQLite 可用。真实服务停写、灾难恢复、生产证据、Linux/NAS 等原限制全部保留。

语义按官方 [Rowid Tables](https://www.sqlite.org/rowidtable.html)、[CREATE TABLE 的 ROWID/INTEGER PRIMARY KEY](https://www.sqlite.org/lang_createtable.html#rowid)、[table_list/table_xinfo/index_list](https://www.sqlite.org/pragma.html) 核对；未改产品或新建协议。

## 已实现

默认 plan/dry-run CLI；每次显式指定本机合成 scope/部署/目标身份。完整资源库存覆盖四产品 DB、每库 WAL 合入、sidecar、guard、合同原字节、版本、日志与配置引用。当前写者通过测试专用 scope owner 锁排除，整组 SQLite 写入预留后用 Backup API 快照，不裸拷活 DB；同时拒绝 SQLite 被误标为普通文件。新包独占创建、外部收据 hash、精确文件集合、结构/事实/implicit rowid、完整性与 guard 校验。

恢复默认新空目标、一直 `restored_disabled`。执行时用另行指定的**原当前权威部署** UUID/版本/完整 DB 事实/guard 核对；不信任备份内自己的旧 guard。遗忘/撤销/owner/send 水位或普通事实任何变化均拒绝，不能回填旧授权。覆盖仅限显式指定 UUID 的既有禁用恢复目标，旧目录隔离保留，切换失败回原位置。包损坏、路径逃逸、junction/hardlink、未注册状态、错误版本/身份、owner 活跃、guard 不一致、取消或中途失败均失败关闭。

`prepare-update` 必须先完成备份再写禁用代码选择；`rollback-code` 单独选择经 schema 声明核对的代码，不触碰数据库或 guard、不迁移、不控制容器。代码选择异常恢复原指针，事务记录保留。完整用法、输入形状和边界在 [README](../../ops/recovery/README.md)。

## 首轮历史验证（本次返修结果见上节）

本机 Windows，Python **3.12.14**、SQLite **3.53.1**。无 Docker 可执行程序，Linux/容器/NAS 未运行。

首轮执行（仓库根；PowerShell 中 Python 为 `C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`）：

```text
python -B tests/deployment/recovery/run_verification.py --output tests/deployment/recovery/verification-final-2026-09-22.json --dep-a-repo C:/Users/Administrator/.codex/worktrees/1e1e/tianshu-peiban-bot --contracts-root C:/YOKI/Codex/tianshu-peiban-bot/contracts
```

首轮结果：**34 tests，0 failures，0 errors，0 skipped，13.311 秒，退出 0**，随后协调独立发现本次返修缺陷，故首轮通过不代表已验收。历史机器报告：[verification-final-2026-09-22.json](../../tests/deployment/recovery/verification-final-2026-09-22.json)。报告 Python SHA256 在首轮提交时复核一致，不作为返修后源码的绑定。

覆盖：WAL-only 已提交数据、sidecar/guard/CRLF 原字节、缺失/损坏/额外文件、隐式 rowid 漂移、旧备份与旧 guard 自洽仍不能复活遗忘、三产品新事实、无可信 authority、取消、快照/发布/隔离/代码切换失败注入、锁释放后重试、覆盖身份与隔离保留、预算/坏 SQLite、活动 writer、路径/UNC/ADS/Windows junction/hardlink、重复 JSON/跨产品卷重叠、默认 dry-run 文件不变及固定错误码去敏。

恢复后真实启动、停止并重启了**测试专用 loopback HTTP 应用**：召回可读、撤销模型返回 403、恢复 sidecar 回执可读、unknown 重试返回 409 且无新增副作用；运行期间备份被 owner 锁拒绝。该应用不是四产品，不把此结果扩大为真实产品恢复验收。

DEP-A `e86d622a` schema/示例 Git blob hash 与消费者解析通过；六个实际合同包 **57 个原始文件** hash 通过，diagnostics manifest 原字节 hash `5d89f7a21637fd57cea4a236e17f8d8c4917799497ff44f87ee68ea614d4805f`。这里只读核对接口/合同，没有执行产品。

其他完成检查：`python -B -m ops.recovery --help` 退出 0；Ruff **0.14.0** `check --no-cache --select E4,E7,E9,F,I ops/recovery tests/deployment/recovery` 通过，`format --check --no-cache` **10 files already formatted**；完整 diff 审查及 `git diff --check` 通过。Ruff 仅位于本号 `ops/recovery/.runtime/tools` 忽略目录，未修改运行依赖。

首轮发现 Windows 对只读描述符 fsync 不兼容，已改为 SQLite 关闭后可写描述符同步；随后修复测试连接未关闭。补充边界后的最终测试曾因测试生成器遗漏 Path 导入失败，已修正并完整重跑，以上只引用最终成功证据。旧失败/中间报告保留于本测试 `.runtime/`，不算通过。

## 全变更清单

| 路径 | 内容 |
| --- | --- |
| `ops/recovery/__init__.py` | 包入口 |
| `ops/recovery/__main__.py` | 显式目标、默认计划、预算、取消和固定错误码 CLI |
| `ops/recovery/engine.py` | 备份封包、当前权威核对、禁用恢复、更新前备份、独立代码选择 |
| `ops/recovery/manifest.py` | DEP-A 固定清单消费与私有恢复库存验证 |
| `ops/recovery/safety.py` | 路径、受限 IO、互斥锁、文件/目录同步 |
| `ops/recovery/snapshot.py` | 整组 SQLite 写入预留、Backup API、完整事实与 guard 核验 |
| `ops/recovery/README.md` | 命令、输入、演练、官方依据、限制 |
| `ops/recovery/INTEGRATION.md` | 固定产品矩阵及最小待接接口提案 |
| `ops/recovery/.gitignore` | 本号工具运行产物忽略 |
| `tests/deployment/recovery/fixtures.py` | 明确合成资源及测试专用生成器 |
| `tests/deployment/recovery/fixture_app.py` | 恢复功能/重启用合成 HTTP 应用 |
| `tests/deployment/recovery/test_recovery.py` | 38 项实际测试（首轮 34 + rowid 返修 4） |
| `tests/deployment/recovery/run_verification.py` | 测试证据与固定 DEP-A/合同核对入口 |
| `tests/deployment/recovery/verification-final-2026-09-22.json` | 首轮历史机器结果/当时源码绑定，非本次返修证据 |
| `tests/deployment/recovery/.gitignore` | 合成 DB/临时报告等忽略 |
| `docs/handoffs/DEP-C.md` | 本交接 |

## 未完成、风险和下一步

1. **真实部署停写适配尚缺。** `cooperative-offline-v1` 是测试用锁，不是产品协议。建议由部署生命周期层阻止准入/自动重启，停全部已登记服务及相关写者，并核不可变进程/容器身份、挂载和 owner 退出，在整个快照临界区维持停写。优先避免要求四产品增加合作锁；本轮未执行 Docker。
2. **原权威丢失的灾难恢复不可用。** 无公开批准/撤销重建接口时，缺 current authority 即拒绝；完整事实不等即拒绝。本工具不创建第二权威水位，不让旧 guard 自证。恢复批准/独立故障域保管及禁止集合对账由产品责任方另定。
3. **verified 证据适配待接。** 当前给出 `verified_release_evidence_adapter_required`，candidate 可合成演练。后续按 DEP-D 固定样例/内容与身份绑定实现，不能只看五个证据标签或长期用拒绝代替验证。合成报告不能当正式 `restore_drill`。
4. **实际代码切换/产品功能验收/激活未做。** 本号只输出禁用选择，schema 声明不证明任意旧代码兼容；配置引用未重新注入，真实四产品恢复后功能由 DEP-D 接线。TS104–106 合入后重新绑定版本/资源，再做 Linux 隔离组合测试，最后由协调安排 NAS。
5. Windows 无标准库目录 fsync，明确 `directory_fsync=false`；未验断电、强杀与 Linux 权限/挂载。scope 需私有且无恶意并发写者，未实现 openat 对抗同用户目录替换；不支持网络挂载。失败 staging/quarantine 不自动清理，预算需按日志与 DB 体量配置。备份未加密，真实敏感数据不在本轮范围。

上述接口缺口已向协调任务 `01a09fb9-931d-7152-9067-b980d8d83a2b` 报告并获其确认按合成边界继续；无自行扩权。官方 SQLite/Python/Ruff 依据和精确链接见 README。开发完成不等于集成，更不等于 NAS 已部署。
