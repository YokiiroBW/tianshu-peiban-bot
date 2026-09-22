# DEP-C：离线恢复工具

本包实现**本机合成部署**备份、隔离恢复、更新前备份及独立代码选择。DEP-F 新增 [生命周期入口](LIFECYCLE.md)：默认 plan，显式绑定核心/日志两项目、停止并确认全部 owner 后才备份，消费 DEP-E 1.1 五个日志状态卷。已验证真实合成进程；Docker 适配器仅完成命令契约测试，Linux/四真实产品/NAS 未运行。以下为保留的 DEP-C 离线核心接口；生产 `restore_drill` 和原权威丢失的灾难恢复仍不支持。

运行需要 Python 3.12+ 标准库，无第三方运行依赖。入口从协调仓库根运行：

```text
python -B -m ops.recovery --help
python -B -m unittest discover -s tests/deployment/recovery -v
```

## 作用域与命令

每条命令显式传入本机绝对 `--root` 和 `--scope-id`。写入命令默认只做计划；全局参数 `--execute` 必须放在子命令之前。`--dry-run` 显式选择默认行为，不能与 `--execute` 并用。

| 命令 | 已实现的行为 |
| --- | --- |
| `init-sandbox` | 只创建新的空合成作用域、所有者锁和输出目录；不是产品初始化器 |
| `backup --deployment NAME --backup NAME` | 校验注册资源；执行时整组停写锁定、SQLite Backup API、WAL 合入、guard 校验、完整性封包；输出独立保存的 `snapshot_sha256` |
| `verify-backup --backup NAME --snapshot-sha256 HEX` | 核对外部保存的收据、文件集合、原字节 SHA256、合同、DB 完整性与 guard；不代替当前撤销状态检查 |
| `restore --backup NAME --snapshot-sha256 HEX --target NEW --authority CURRENT --authority-id UUID` | 执行时对照**原当前权威部署**的完整状态；默认创建新目标，保持 `restored_disabled` |
| `verify-restored --target NAME --authority CURRENT --authority-id UUID` | 复核恢复库、guard、固定版本和当前权威事实；输出 `external_runner_required`，不伪报功能验收 |
| `prepare-update --deployment NAME --candidate-manifest ABS --compatibility ABS --update-id NAME --backup NAME` | 兼容性核验，先成功备份，再原子写入禁用的代码选择；没有服务切换或迁移 |
| `rollback-code --deployment NAME --candidate-manifest ABS --compatibility ABS --update-id NAME` | 只选择兼容的代码版本，不覆盖数据，不恢复旧 guard，不自动生成迁移 |

总容量默认 1 GiB、10,000 文件，可显式给全局 `--max-bytes` / `--max-files`，上限 64 GiB / 100,000。普通文件按 1 MiB 块复制；SQLite 分页备份上限 60 秒，文件/页/事实扫描间检查取消。操作系统阻塞 IO 本身没有硬实时取消保证。返回码：0 计划/本命令成功，2 安全拒绝/输入不可用，130 已观察到取消。

恢复覆盖只允许本工具之前生成的、仍禁用的合成恢复目标，须同时给 `--replace-target --expected-target-id UUID`。旧目标先移动到本作用域 `quarantine/` 保留；目录切换失败会恢复旧位置。原 authority、其他部署及其他服务永远不允许覆盖。异常和取消留下 `.pending-*`/`ABORTED.json`，没有自动清理命令；需人工核验后处理。强制终止发生在两次 rename 之间时可能留下被隔离的旧目录和未发布的新目录，二者都不能启用。

## 可重复合成演练

以下生成器**只属于测试**，只允许在本测试目录 `.runtime/` 创建新目录。它不包含产品初始化、真实账号或模型配置。

```text
python -B tests/deployment/recovery/fixtures.py --root <绝对仓库路径>/tests/deployment/recovery/.runtime/demo --execute
```

记录输出的 scope/authority UUID，然后：

```text
python -B -m ops.recovery --root <上述ROOT> --scope-id <SCOPE> backup --deployment source --backup before-update
python -B -m ops.recovery --root <上述ROOT> --scope-id <SCOPE> --execute backup --deployment source --backup before-update
python -B -m ops.recovery --root <上述ROOT> --scope-id <SCOPE> verify-backup --backup before-update --snapshot-sha256 <收据HASH>
python -B -m ops.recovery --root <上述ROOT> --scope-id <SCOPE> --execute restore --backup before-update --snapshot-sha256 <收据HASH> --target rehearsal --authority source --authority-id <AUTHORITY>
python -B -m ops.recovery --root <上述ROOT> --scope-id <SCOPE> verify-restored --target rehearsal --authority source --authority-id <AUTHORITY>
```

完整测试会启动并重启独立 loopback 合成 HTTP 进程，验证召回可读、模型已撤销时拒绝、sidecar 回执可读、unknown 重试返回拒绝且数据库无新增副作用。该合成应用只在测试目录，恢复工具本身不启动它，也不将恢复标记改为 ready。

带机器可读证据的验证入口（输出必须是本测试目录内的新文件）：

```text
python -B tests/deployment/recovery/run_verification.py --output tests/deployment/recovery/.runtime/new-report.json --dep-a-repo <DEP-A仓库绝对路径> --contracts-root <原协调目录>/contracts
```

两个可选输入一起提供时，读取 DEP-A **固定 Git blob** `e86d622a813f116b059b62b820cbd1342722042d`，核对其 schema/示例 hash、实际解析适配、六个合同包原字节；不读取 DEP-A 活跃代码。报告固定各 Python 文件 hash，明确 `real_product_restore/linux_container/nas/production_restore_drill_evidence=false`。

## 输入边界

共享发布清单只消费 DEP-A 的 `release-manifest.json`，本包没有第二份共享 schema。固定接口 SHA256：schema blob `cf95607fa7252f806ae4ab4d919298c234d91f1007ce763ec12485091f4d0397`，示例 blob `7566375a763440d9408d1ae6a3502aab5521bd996cec3dc93c523ebcd660f922`。这些是 Git LF 字节；不能用 CRLF 工作区 hash 混比。合同始终保留自己的原字节，不做行尾转换。

合成部署位于 `ROOT/deployments/NAME/`，含：

- `.deployment.json`：本 scope UUID、独立 deployment UUID、`environment=synthetic`、`role=authority|restored`、`status=offline|restored_disabled`。
- `release-manifest.json`：DEP-A 发布清单，所有卷主机路径相对本 deployment。`mount=false` 的 guard/sidecar 是父卷里的精确路径，按路径去重，不重复覆盖。
- `recovery-inventory.json`：本工具私有输入，不是跨产品权威合同。含 `schema_version=1.0.0`、发布清单**原字节** `release_manifest_sha256`、`resources`、`guard_checks`、`config_references`。完整可执行形状由测试生成器输出。
- `resources` 每项只有 `id/volume_id/path/kind`；path 是相对 deployment 的完整路径；kind 是 `sqlite|guard|file|owner_lock`。四产品及已存在全部 sidecar SQLite 必须逐个登记。未登记的状态文件直接拒绝。SQLite 文件不能伪装为普通 file；伴随 WAL/SHM 自动关联，不作为恢复负载单独覆盖。
- `guard_checks` 当前只有 `memory-source-v3`，绑定一个数据库 resource 与一个 guard resource；只按固定基线公开恢复格式比较 `metadata` 与检查点，无产品内部类导入。新增产品格式必须明确适配，不能从旧 DB 重造 guard。
- `config_references` 每项只有 `id/reference/sha256`。只备份配置/密钥/证书引用和已核摘要，**不读取引用指向的配置、私钥、凭据文件或环境值**；配置重建与重新注入在后续生命周期接线中验收。

代码选择额外需要私有 `compatibility.json`：`candidate_manifest_sha256`、`current_manifest_sha256`、`migration: "none"`、`read_write_schema_sha256`（每个 SQLite 相对路径→schema 摘要）。声明必须由后续验收提供；本工具核对当前结构并保存声明，不推断旧代码能兼容新数据。测试内声明仅为合成值。选择只产出 `selected-code.json` 及 `updates/` 事务记录；原发布清单不改，服务不启动。缺 `COMMITTED.json` 或出现 `ABORTED.json` 的选择一律不可被后续部署层采用。

## 一致性与遗忘/撤销

测试专用 `cooperative-offline-v1` 是全作用域互斥锁，合成服务持锁期间维护拒绝。它**不是四产品已有协议，也不证明真实容器已停写**。取得该锁后，对所有注册 SQLite 同时持有 `BEGIN IMMEDIATE` 写入预留，再逐一通过 Backup API 制作已提交快照。物理 WAL 内容由 API 纳入 DB，SHM/运行 owner 锁不是恢复事实。原库/WAL 不删除、不覆盖、不手工 checkpoint。

完整性包含每个文件的大小/hash、精确文件集合、合同原字节、配置引用、SQLite integrity/foreign-key 检查、结构及全部事实摘要（包括隐式 rowid、回执、suppression、owner/source 水位与虚拟表影子表）。Memory guard 必须与库的 instance/revision/recovery 完全相同。

备份自身 guard 只能证明备份内部配对，**不能证明今天仍允许恢复**。执行恢复必须另行指定原 authority UUID，重新锁住原当前部署，核同一清单/资源版本，并比较所有 DB 事实与 guard。任何进展，包括遗忘、来源/模型撤销、发送水位、已消费批准或普通业务写入，都返回 `current_authority_diverged`。这是比仅比较撤销计数更保守的边界；不合并旧账本、不覆写当前 guard、不复制备份中的“当前水位”来冒充外部证明。authority 缺失、不可信、版本不同或 guard 不一致都拒绝。

这意味着**本版不能在原权威状态已丢失时完成灾难恢复**，也不能对已有新业务的当前库做时间点回滚。没有获批的公开恢复批准/对账接口时，这是有意失败关闭，不能靠手工修改 UUID/标记解除。

## 当前限制与后续接线

见 [集成边界与最小接口提案](INTEGRATION.md)。真实部署停写、真实产品功能恢复、最终 TS104–106 修复组合、Linux 容器及 NAS 均未验证。当前明确返回 `verified_release_evidence_adapter_required`，不把清单的 `verified` 标签当作事实；后续要接 DEP-D 固定证据适配，不永久拒绝合格发布来替代验收。

所有路径拒绝相对根、UNC、`..`、驱动/ADS 路径、符号链接、junction/reparse、硬链接、特殊文件和大小写别名；输出只在注册 scope 子树。scope 必须由操作者控制、保持私有，不能有不遵守维护锁的并发写者。此版不提供抵御同用户恶意替换文件的 openat 沙箱，不支持网络挂载。POSIX 路径包含目录 fsync；Windows 标准库无等价目录 fsync，报告 `directory_fsync=false`，未声称断电持久性已验收。备份包未加密，真实敏感数据处理仍不在本轮范围。

官方依据（2026-09-22 核对）：[SQLite Backup API](https://www.sqlite.org/backup.html)、[SQLite WAL](https://www.sqlite.org/wal.html)、[Python 3.12 sqlite3](https://docs.python.org/3.12/library/sqlite3.html)。运行实测 Python 3.12.14 / SQLite 3.53.1。开发格式检查用 [Ruff 0.14.0](https://pypi.org/project/ruff/0.14.0/)，仅装在本号忽略目录，无运行依赖变更。
