# DEP-C 交接：一致备份、隔离恢复与代码回退

状态：**本号合成实现已完成并本地验证，待协调审查集成；不代表真实部署恢复能力完成。**

## 范围、基线与提交

- 精确任务卡：原协调目录 `C:/YOKI/Codex/tianshu-peiban-bot/docs/development/deployment-wave2-codex-2026-09-22.md`，以及同目录 gap plan、CURRENT、NAS 核对报告、四产品 AGENTS、diagnostics/v1。此次明确 Codex 直接开发授权覆盖旧 DSH 约定；未派子代理。
- 本号 worktree：`C:/Users/Administrator/.codex/worktrees/aa35/tianshu-peiban-bot`；分支 `codex/dep-c-recovery`；根基线 `21668e4300d7bedc8ac1ed819bdb7dad4a2bd58b`。
- 实现和最终测试证据提交：`4bbad55bebb29a2620755ef32b31ca25da68f859`。本交接单独追加文档提交，产品/实现未再变化。
- DEP-A 固定接口：`e86d622a813f116b059b62b820cbd1342722042d` 的两个 Git blob；已核原始 LF hash。没有从其活跃代码构建输入；四产品固定提交与备份矩阵详见 [INTEGRATION](../../ops/recovery/INTEGRATION.md)。未读活跃 TS104–106 检出。
- 只写 `ops/recovery/`、`tests/deployment/recovery/` 和本交接。没有改原协调目录、根任务板/CURRENT/workspace/contracts/产品代码，没有 NAS、真实数据/账号/付费模型、生产部署、推送或自动合并。

## 已实现

默认 plan/dry-run CLI；每次显式指定本机合成 scope/部署/目标身份。完整资源库存覆盖四产品 DB、每库 WAL 合入、sidecar、guard、合同原字节、版本、日志与配置引用。当前写者通过测试专用 scope owner 锁排除，整组 SQLite 写入预留后用 Backup API 快照，不裸拷活 DB；同时拒绝 SQLite 被误标为普通文件。新包独占创建、外部收据 hash、精确文件集合、结构/事实/implicit rowid、完整性与 guard 校验。

恢复默认新空目标、一直 `restored_disabled`。执行时用另行指定的**原当前权威部署** UUID/版本/完整 DB 事实/guard 核对；不信任备份内自己的旧 guard。遗忘/撤销/owner/send 水位或普通事实任何变化均拒绝，不能回填旧授权。覆盖仅限显式指定 UUID 的既有禁用恢复目标，旧目录隔离保留，切换失败回原位置。包损坏、路径逃逸、junction/hardlink、未注册状态、错误版本/身份、owner 活跃、guard 不一致、取消或中途失败均失败关闭。

`prepare-update` 必须先完成备份再写禁用代码选择；`rollback-code` 单独选择经 schema 声明核对的代码，不触碰数据库或 guard、不迁移、不控制容器。代码选择异常恢复原指针，事务记录保留。完整用法、输入形状和边界在 [README](../../ops/recovery/README.md)。

## 实际验证

本机 Windows，Python **3.12.14**、SQLite **3.53.1**。无 Docker 可执行程序，Linux/容器/NAS 未运行。

最终执行（仓库根；PowerShell 中 Python 为 `C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`）：

```text
python -B tests/deployment/recovery/run_verification.py --output tests/deployment/recovery/verification-final-2026-09-22.json --dep-a-repo C:/Users/Administrator/.codex/worktrees/1e1e/tianshu-peiban-bot --contracts-root C:/YOKI/Codex/tianshu-peiban-bot/contracts
```

结果：**34 tests，0 failures，0 errors，0 skipped，13.311 秒，退出 0**。机器报告：[verification-final-2026-09-22.json](../../tests/deployment/recovery/verification-final-2026-09-22.json)。报告中的全部 Python SHA256 与最终工作区及暂存 Git blob 均独立复核一致。

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
| `tests/deployment/recovery/test_recovery.py` | 34 项实际测试 |
| `tests/deployment/recovery/run_verification.py` | 测试证据与固定 DEP-A/合同核对入口 |
| `tests/deployment/recovery/verification-final-2026-09-22.json` | 最终机器可读真实结果/源码绑定 |
| `tests/deployment/recovery/.gitignore` | 合成 DB/临时报告等忽略 |
| `docs/handoffs/DEP-C.md` | 本交接 |

## 未完成、风险和下一步

1. **真实部署停写适配尚缺。** `cooperative-offline-v1` 是测试用锁，不是产品协议。建议由部署生命周期层阻止准入/自动重启，停全部已登记服务及相关写者，并核不可变进程/容器身份、挂载和 owner 退出，在整个快照临界区维持停写。优先避免要求四产品增加合作锁；本轮未执行 Docker。
2. **原权威丢失的灾难恢复不可用。** 无公开批准/撤销重建接口时，缺 current authority 即拒绝；完整事实不等即拒绝。本工具不创建第二权威水位，不让旧 guard 自证。恢复批准/独立故障域保管及禁止集合对账由产品责任方另定。
3. **verified 证据适配待接。** 当前给出 `verified_release_evidence_adapter_required`，candidate 可合成演练。后续按 DEP-D 固定样例/内容与身份绑定实现，不能只看五个证据标签或长期用拒绝代替验证。合成报告不能当正式 `restore_drill`。
4. **实际代码切换/产品功能验收/激活未做。** 本号只输出禁用选择，schema 声明不证明任意旧代码兼容；配置引用未重新注入，真实四产品恢复后功能由 DEP-D 接线。TS104–106 合入后重新绑定版本/资源，再做 Linux 隔离组合测试，最后由协调安排 NAS。
5. Windows 无标准库目录 fsync，明确 `directory_fsync=false`；未验断电、强杀与 Linux 权限/挂载。scope 需私有且无恶意并发写者，未实现 openat 对抗同用户目录替换；不支持网络挂载。失败 staging/quarantine 不自动清理，预算需按日志与 DB 体量配置。备份未加密，真实敏感数据不在本轮范围。

上述接口缺口已向协调任务 `01a09fb9-931d-7152-9067-b980d8d83a2b` 报告并获其确认按合成边界继续；无自行扩权。官方 SQLite/Python/Ruff 依据和精确链接见 README。开发完成不等于集成，更不等于 NAS 已部署。
