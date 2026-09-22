# DEP-J：正常停写、禁用恢复与一次性合成副本

状态：**本地实现/边界验证 ready_for_review；未集成。Linux 九真实容器、真实产品恢复功能、NAS 未执行。** 本交接所在最终提交完整 HEAD 单独发送协调，固定后停写。

## 范围与依据

根基线 `eced6de3ed0e2292d8c767d9f37fea36d0e04476`；独立 worktree `C:/Users/Administrator/.codex/worktrees/dd34/tianshu-peiban-bot`，分支 `codex/dep-j-recovery`。仅 ops/recovery、tests/deployment/recovery、本交接。读取协调 CURRENT/第四批精确 DEP-J 卡及后补 drill-clone 授权，Codex 本轮执行覆盖历史 DSH-only。没有子代理/其他窗口、项目协调检出修改、根板/合同修改、推送、合并、NAS/SSH/真实数据/真实模型。

许可窄接口先提交 `8a9462845bdca9b40d4d30210d316d1280076f46`，协调明确批准继续。最终 schema 增 C:/ 绝对路径仅用于 Windows 本地计划/边界验证，仍禁 UNC/相对路径/反斜杠；execute 始终强制 Linux flock。此差异已告协调。

DEP-G 固定接口 `880c2c33ffc4c12b9267d38267c2dacde074890d`，文档补充 `ceca645fbc60b68a28eb1fa4e942ecf8bab3ea1b` 只澄清 image/container 观测区分，schema 原字节不变。通过 git show 固定对象读取，不读 G 活动检出；schema/example/doc 私有快照和 SHA256 在 ops/recovery/interfaces/。这些是运行接口资料，**不是根业务合同快照**；未以旧 Git 合同冒充协调根发布合同。没有执行真实产品，测试合同均为隔离合成夹具；实际 Linux 输入仍必须由 G 从协调根核原字节发布合同装配。

只读核既定四产品 Git：platform `0a3cf65b8da19eabdd5d72f9f999281cbb52fdac`、gateway `ec20f95e3849ebee968c4f00d7a31d14eccaa40f`、companion `e94b609099365f75ca933d9fed03cdfbc83ec235`、memory `9a3b2bed6aebff9f0677f2c62e979859769e0c9c` 的 CLI/store。没有修改或运行产品工作检出。

## 交付行为

1. `linux-prepare`：只在显式 synthetic scope/deployments 的此前未登记直属首装目录登记 authority、全量库存及 J 侧证，绑定 G 原报告 hash，原 null/G 文件不改。准备需核九实际 owner，源码/配置/两项目/Compose 原字节及规范化内容、全挂载、UID/GID、本地镜像 ID/架构与 RepoDigests。qa 前缀只在 G 固定身份入口受控接受，旧 F synthetic 前缀/路径规则不放宽。完整扫描真实首装 SQLite、迁移备份、未知状态文件；四 runtime_guard 锁和 companion owner 锁明确登记，原锁不删，恢复只在新目标创建新锁。
2. `linux-rehearse`：整操作持有与 G/I 相同源 `.runtime-owner.lock` inode 的非阻塞 flock；绑定初次登记的九个确切容器身份，重核再禁重启、逐 owner SIGTERM，只接受 exited/0、非OOM/Dead/Error且无残留。不将143/137视正常。完整备份到新路径、恢复到新目标并保持 restored_disabled；所有事实/rowid/guard 当前权威复核；旧 authority 丢失或新撤销/遗忘/unknown事实与旧包不一致就拒绝。旧生命周期CLI消费G登记时也复核登记owner和共享租约，不能从旧命令绕过。
3. `drill-clone`：需外部独立准备且 hash 绑定的有期限一次性许可，工具不自发生成再执行。原目标保持禁用；源九owner全停、原恢复目标无任何容器挂载、恢复内容逐文件对原备份核验后，完整复制到新副本；保留日志内部 WAL/空目录。原状态/锁/权限不改，副本绝不成为authority。复制输入与目标再次核hash，漂移不启动；claim 在启动前持久化，失败未知不重放。
4. 副本两新项目、九固定本地image ID、禁pull/build/额外owner/原目录挂载、internal网络、显式loopback端口。命令/入口/环境/healthcheck及只读挂载布局沿固定G来源，代码/合同hash绑定，独立配置/TLS/合成秘密。服务字段白名单、禁Compose插值。只执行固定Compose动作和公开 HTTPS GET 断言，不执行许可任意argv/SQL；无代理/重定向，TLS、总体期限/体积限制，结果仅状态/摘要。health不能冒充功能，缺产品/语义显式 partial/nonzero。
5. 工作预算预留60秒正常关停；部分启动失败也只停止逐项核验的现存owner。取消/异常保留副本和claim；未确认关停是 stop_unconfirmed/nonzero。源/副本租约覆盖关停；原系统从未自动激活。版本选择与镜像运行分开，旧 update/rollback 不变成数据回退。

入口、隔离 venv、实际命令参数、输入结构、界限见 [LINUX-RECOVERY.md](../../ops/recovery/LINUX-RECOVERY.md)、[DRILL-PERMIT.md](../../ops/recovery/DRILL-PERMIT.md)。没有生产命令默认值。

## 实际验证

Windows / Python3.12.14 / SQLite3.53.1。本机无 Docker/WSL。依赖只装 tests/deployment/recovery/.runtime/venv：jsonschema4.25.1、Ruff0.14.0；TLS测试另用现有部署同版本 cryptography50.0.1。未安装宿主全局包、未修改系统配置。

- 首轮完整 `python -B tests/deployment/recovery/run_verification.py --output tests/deployment/recovery/verification-depj-2026-09-22.json`：93项，92过/1 error/0failure/0skip，120.369秒。唯一error是新增测试SQLite连接未close导致Windows清理临时目录失败，已显式closing修复；原63项回归全部通过。原失败报告保留，不改写成成功。
- 最终受影响 `run_depj_verification.py --output .../verification-depj-final-2026-09-22.json`：**41项全过、0skip，67.348秒**。其中真实loopback TLS4项覆盖认证GET、拒重定向、持续慢传总期限、响应大小与严格类型；测试服务为合成HTTP，非产品。
- 真实Compose静态复核发现obs并非都有healthcheck，改为只等已配置探针；兼容原observability-input目录、保持新秘密/封闭清单；相关 `--pattern test_drill.py --pattern test_drill_http.py`：**14项全过、0skip，55.643秒**，见 verification-depj-drill-final-2026-09-22.json。第一次TLS慢传断链有服务端ConnectionAbortedError噪音，已在合成服务正常断链分支处理；后次无该噪音。
- 最后复制前后输入/目标hash复核与漂移拒启动增量，`--pattern test_drill.py`：**11项全过、0skip，59.692秒**，见 verification-depj-copy-final-2026-09-22.json。包括完整新副本、原字节不变、过期/未来/重叠、异常退出、部分启动失败、源authority后变、恢复日志被改、opaque WAL/空目录和复制配置漂移。
- `ruff check --select E4,E7,E9,F,I ops/recovery tests/deployment/recovery`、`ruff format --check`、CLI --help、完整差异/空白检查通过。Python源码报告与最终暂存字节关系见 verification-depj-artifacts-2026-09-22.json；明确区分原字节与Git LF规范化，不虚称CRLF原字节相同。

Docker stop/start/inspect、Linux权限和POSIX lease在Windows编排测试中有明确double/patch，不能算Linux通过。恢复基础旧九writer实进程仍是合成服务。累计98不同检查有通过记录，未重跑未变化的成功检查来凑新全量报告。全部真实产品功能/容器/NAS结论保持未执行。

## 未完成、限制与协调下一步

1. 正式Linux构建、九真实容器停写/退出码、UID/GID/目录fsync、全卷备份恢复、四产品公开功能读回与NAS均**未运行**。需先验收/集成G最终正式镜像输入及I正常guard关停修复，再在获准Linux执行本入口。J不消费未验收的I活动包，也不忙等或降低正常退出要求。
2. 实际drill需要协调准备符合许可的专属配置/合成凭据/TLS及真实公开端点断言。本交付提供严格可执行适配器与结构，**没有把fixture URL/任意expected_json当作真实业务语义验收**。新凭据若需平台公开CLI引导，须独立合成准备、有限授权，不SQL造身份/自动重签过期来源。现模板没有安全录制模型第十owner接口，不自行加owner；无模型语义按缺项报告，不接真实模型。
3. 固定CLI核查：gateway usage-report确是部分只读公开入口；platform preflight仅配置/数据可用性；memory jobs/outbox会先configured_app/Store事务初始化；companion persona_cli的read也先Store构造。不能把这些全当无副作用恢复审计，因此采用经协调批准的一次性副本，不新增四套产品CLI、不激活原目标。
4. 适用私有单运维合成scope；Docker管理员/宿主写者须遵守共享租约。普通文件系统调用/内核停顿不能被Python硬实时抢占，预算不能作为对恶意同权限写者或故障磁盘的保证。未确认退出保留现场，不强杀/删锁/全局清理。
5. `verified` release仍按旧F拒绝，没有生产放行合同；灾难恢复无当前authority仍拒绝。恢复事实证明保守数据一致，不自动证明公开业务语义，不等于上线/日用完成。

最终本地提交使用单命令 `git -c user.name=Codex -c user.email=codex@local.invalid commit`，不改全局身份、不推送/合并。协调按完整最终HEAD验收；本任务固定后停写。
