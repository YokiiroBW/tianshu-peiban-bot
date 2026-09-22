# DEP-F：真实合成进程停写、备份恢复与更新生命周期

状态：**R1 目录枚举失败关闭返修完成，ready_for_review；未集成，Linux 容器/四真实产品/NAS 未验收。**

## 2026-09-22 R1 返修：枚举错误不得发布不完整备份

返修基线 `4598bb902f5c23b0da31698c773bb669c5a9e426`。已只读协调的 `docs/development/reviews/DEP-F-review-2026-09-22.md` 与 `.runtime/review-dep-f/probe_unreadable.py`。协调原 56 项通过，但独立九进程反例确认 `os.walk` 默认吞掉不可读 Loki 子目录，导致源文件存在而备份缺失仍 complete；原提交未通过验收、未合并。**下方首轮 56 项及其机器报告为历史，当前证据以本节为准。**

运行时只改 `ops/recovery/safety.py`、`snapshot.py`：新增共享 `walk_tree` 显式使用 `os.scandir`，文件集合、完整卷目录集合、发布前 `sync_tree` 三个消费者全部使用。目录打开、迭代和 `DirEntry.is_dir` 的 PermissionError/OSError 都转为固定 `directory_enumeration_failed`，不输出异常正文、路径或内容。不仅添加 `os.walk(onerror=...)`，因为其条目类型查询仍会自行吞错。每层迭代器在交还结果前关闭，以显式栈保留 topdown/bottom-up 行为；不改变备份/恢复合同、owner 及门禁逻辑，不删除锁或恢复 writer。

新增 `test_enumeration_failures.py` 七项：

- 源嵌套卷 PermissionError/OSError 在可预检时先拒绝，九 writer 继续正常运行，无新门禁、备份或完成收据。
- 九个真实 writer 全部退出后才出现源枚举故障，备份不发布，源 must-retain 原字节、维护门禁及原锁 inode/大小保持。
- 条目元数据查询错误不能把目录误判为文件而略过其子树。
- payload 原空子目录加入未登记文件，再隐藏该目录的枚举；包验证和恢复都拒绝，不发布目标/新增完成收据。
- 迭代中途 PermissionError/OSError 对 files、volume_directories、sync_tree 三条路径均失败关闭。
- 备份及恢复各自在停写、复制之后的暂存目录枚举故障，不发布备份/目标/完成收据；未完成 staging 标为 ABORTED 并保留。

测试仅注入文件系统枚举故障，没有替换真实停止、封包或恢复。原 `test_lifecycle.py` helper 新增等待登记后 action lease 真正释放，避免启动下一真实 writer 与前一登记释放的小窗口竞争；本轮第一次故障套件也发现 Windows 活动 byte lock 不允许读内容，检查改为 inode/大小。首次修前包枚举/部分迭代反例已失败，测试环境错误修正后才计通过。

验证：新增七项专项 **7/0错误/0失败/0skip，14.578 秒**；完整受影响回归 **63/0错误/0失败/0skip，59.886 秒**，退出 0。命令仍为同一 `run_verification.py`，本次输出 [verification-depf-r1-2026-09-22.json](../../tests/deployment/recovery/verification-depf-r1-2026-09-22.json)。Ruff 0.14.0 检查及格式通过（20 Python 文件），完整返修 diff 和空白检查通过；20 个源文件 SHA256 与当前工作树及 Git 暂存 blob 逐项一致。Python3.12.14/SQLite3.53.1/Windows，原 DEP-E 固定接口未变化、未重跑产品或扩大范围。

本次增量共七文件：两个运行时文件、`LIFECYCLE.md`、两个测试文件、新机器报告、本交接。返修提交为包含本节的 Git 提交，完整 HEAD 另发总协调；固定后停写。旧版本在枚举失败时生成的包可能已漏数据，无法仅由该包证明完整，应从可读且停写的当前权威重备份。Linux 容器、四真实产品恢复、NAS、权威丢失灾难恢复及自动放行的所有既有缺口保持，未增加任何实机通过声明。

## 目标、范围与固定版本

- 精确卡：协调检出的 `docs/development/deployment-wave3-codex-2026-09-22.md`，仅共同边界与 DEP-F；本轮新窗口 Codex 明确授权覆盖旧 DSH/第二批名单。未创建子代理。
- worktree：`C:/Users/Administrator/.codex/worktrees/169f/tianshu-peiban-bot`；分支 `codex/dep-f-lifecycle`。
- 根基线：`668d69e2c52ea55d9b1f102426196a5286c57bc5`。
- 实现及机器证据提交：**`17b25ca5a6520181bf33d7d9c898747d0c6ad6de`**，18 文件；本交接为随后单独提交，最终完整 HEAD 由交付消息给出。
- 仅修改 `ops/recovery/`、`tests/deployment/recovery/` 和本交接。未改其他窗口、产品、清单、日志实现、协调 CURRENT/tasks/workspace/contracts；未合并/推送/发布，未操作 NAS、真实账号、真实数据或付费模型，未安装 Docker/WSL 或修改系统。
- Git 检出未配置作者；本次沿用既有 `Codex <codex@local.invalid>`，仅 `git -c` 单次覆盖，无全局配置写入。

## 固定接口

DEP-E `cb371ab453621841e4a498598529a4ddcc41d3b2`，仅用 `git show <完整commit>:<路径>`，未读其活动检出。

| Git blob（deploy/tianshu/ 下） | 原始字节 SHA256 |
| --- | --- |
| VOLUME-INTERFACE.md | `0f35a84c7ab6088f796d60c6311ca09cfc99089638f0c5e1f1192269caf3b2ba` |
| release-manifest.schema.json | `9f7382c0bdcaf049558c6c37e75738331d200c23a98d568b34c3b73ac72704fe` |
| release-manifest.example.json | `2d44a4e2e9fa17d51a8063ed8177614d5384e59ea75e3835defe238e509f7722` |

消费者兼容旧 1.0，完整接受 1.1 的五个 `observability_state` 卷、obs owner 与源码绑定；缺卷/误归属/改路径均拒绝。应用 products 保留四产品集合。日志项目严格 `<core>-obs`，工作目录独立为 `<deployment>/observability`。测试正式示例包含四产品固定版本 fa85ee93/cf020fd3/2f403762/51121e6c，日志源码 668d69e2；没有把未集成 TS107/108 或活动产品版本写入发布清单。

## 实现

新增 `lifecycle-init` 与 `lifecycle-{backup,restore,verify-restored,prepare-update,rollback-code}` 公开 CLI。所有操作默认 plan；执行只允许已有 synthetic scope 的直接子部署和 `tianshu-synthetic-*` 项目。独占创建生命周期登记，不覆盖旧目录；每次显式指定绝对部署目录、项目和另存的绑定 hash，核清单/库存/Compose 原字节、源码版本、镜像与所有挂载。

先检查操作输入及完整 owner 集合，再持久写维护门禁、禁重启、按生产者到消费者顺序停止、确认所有 owner 真退出，最后才调用一致快照/恢复/禁用代码选择。在封包和代码选择提交前重核停机与版本。共用总期限与取消检查；未确认退出/身份漂移/残留写者时失败关闭，无全局 kill、强杀退路或删除 owner 锁。

本地进程适配器以 Windows process handle/creation time（Linux 路径为 boot ID/starttime）识别确切进程；既要真实内核退出，也要正常退出收据和 owner lease 释放。测试服务有真正独立进程、SQLite/WAL 与连续日志写入，旧 DEP-C scope 合作锁未被当成产品支持。登记过生命周期的部署不能从旧离线 CLI 绕过停写。

Compose 适配器通过真实 Docker CLI 语义实现，但**本机无 Docker，只有契约替身测试通过**：仅显式本地 Unix socket，枚举完整容器集合，核两个项目标签、工作目录、配置文件、单 owner、不可变容器 ID/Created/Image、实际镜像和所有挂载，拒绝其他项目挂入本部署。先对精确 ID 写 `restart=no` 并读回，再只发 SIGTERM，不将超时升级为 SIGKILL；只有干净退出才可继续。

应用 SQLite 继续 Backup API/WAL 合入和完整事实/rowid/guard 校验。新增五个观测卷在全部 owner 停止后整目录按原字节备份，包括未知内部 SQLite/WAL/索引/缓冲/位点和空子目录。完整文件与目录集合封闭，源变化拒绝。不会把 Loki204、日志可查询或对账成功当作应用删段许可。

恢复仅到此前不存在的新目录，保持 restored_disabled，并立即对原当前 authority 的全部事实与 guard 复核；旧包不能复活后来撤销/遗忘/unknown 等状态。代码更新先备份；代码回退只选禁用版本，不还原数据。两者都不执行新镜像/迁移/重启。

职责：`lifecycle_binding.py` 只做不可变部署解析；`process_identity.py` 只读内核身份；两 backend 只做 owner 状态与控制；`lifecycle.py` 编排门禁/停止/确认并调用原 Recovery；`engine/snapshot/manifest` 仍负责封包/事实/清单消费，互不导入产品业务。使用与安全边界详见 [LIFECYCLE.md](../../ops/recovery/LIFECYCLE.md)。

## 实际验证

环境 Windows / Python **3.12.14** / SQLite **3.53.1**。最后执行：

```text
python -B tests/deployment/recovery/run_verification.py --output tests/deployment/recovery/verification-depf-2026-09-22.json
ruff check --no-cache --select E4,E7,E9,F,I ops/recovery tests/deployment/recovery
ruff format --check --no-cache ops/recovery tests/deployment/recovery
git diff --check
```

**56 tests / 0 errors / 0 failures / 0 skipped，37.582 秒，退出 0**。其中旧 DEP-C 38 项、DEP-F 本地生命周期 11 项、Docker CLI 契约 7 项。Ruff **0.14.0** 静态检查通过，19 个 Python 文件均已格式化。证据：[verification-depf-2026-09-22.json](../../tests/deployment/recovery/verification-depf-2026-09-22.json)。报告 19 个 Python SHA256 与测试后工作树及 Git 暂存 blob 逐项相等，0 mismatch。已审查完整实现差异并核全部路径在白名单。

新增覆盖：默认计划没有文件变更；九个真实合成 writer 全部正常退出→完整备份→新目录恢复→原权威复核，所有日志保留 shutdown 终态与正序列、五观测卷逐字节一致且空子目录保留；维护期间重启被拒；不退出/提前写收据仍活着/中途取消均不发布备份；PID 创建时间错、额外 owner、版本漂移在停写前拒绝；既有备份名不停止健康进程；后来撤销阻止旧恢复；既有目标原文保留；代码选择/回退不恢复数据。容器契约覆盖两项目、精确 SIGTERM、重启禁用、外部挂载、镜像/配置/目录/owner漂移、OOM/异常退出/残留进程、远程 endpoint 等。

首轮 10 项中有 2 个测试清理错误：测试 `with sqlite3.connect` 未实际 close，Windows 占用文件。已改显式 closing；随后 55 项全部通过，最终增加“既有备份名先拒绝”及输入预检后为本次 56 项。只引用最终证据；原失败/中间报告保留测试 `.runtime/`。没有运行未变产品/全工作区测试，没有伪造 skipped 为通过。

## 未完成、风险与下一步

1. **Linux 容器及四真实产品恢复尚未运行。** 本轮允许无 Docker 时用本地合成进程，故只完成此边界。协调接最终 DEP-E 包后需在获准 Linux 环境验证真实镜像身份、权限、SIGTERM、目录 fsync、四产品恢复功能和重启；本机 `directory_fsync=false`。不代表 NAS 日用就绪。
2. **没有自动激活命令。** 当前权威可用且所有事实一致只证明保守状态复核。缺绑定恢复目标/世代/配置的四产品功能报告与当前批准后的公共放行合同，因此两个项目继续停止；不能把合成结果当生产 restore_drill。新配置/秘密/TLS 只保留引用和 hash，仍需重新注入核验。
3. **原权威丢失的灾难恢复仍拒绝。** 初装备份不能替代独立保管的新批准/撤销禁止集合；本层不会制造第二权威或用旧 guard 自证。代码选择不等于镜像切换，跨 schema 迁移/数据回退不在范围。
4. 仅私有、单运维控制的合成 scope。Docker 没有阻止外部管理员手动启动的全局排他锁，维护期间不得另用 Dockge/宿主 writer 绕过门禁。未实现对抗同用户恶意路径替换或未登记宿主写者。普通本地文件系统调用不能被 Python 预算强制抢占，故不宣称磁盘故障硬实时期限。
5. 测试固定读 DEP-E Git 对象；协调集成必须保留该提交可达性。若正式清单的卷结构改变，需新固定接口后适配，不能静默忽略。Compose 执行要求有 digest；当前正式 candidate 的空镜像 digest 会被准确拒绝。

完整 19 文件交付（含此交接）供协调审查与串行集成。固定后停写，等待精确返修卡，不自行修改 root main 或宣布全系统完成。
