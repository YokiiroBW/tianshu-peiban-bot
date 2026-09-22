# DEP-D 联合验收运行器交接

2026-09-22。本号由用户明确授权新窗口 Codex 直接实现，覆盖旧 DSH 实现约定，仅在本 worktree 白名单写入。没有派子代理、改产品代码、操作 NAS/真实账号/真实数据/付费模型、部署、推送或合并。

## 目标、基线与交付

实现当前发布组合的可重复验收入口，区分产品/替身、本地/容器、候选受理/真实记忆/归档、未运行/依赖缺失/跳过/失败，提供机器报告与摘要。运行器与合成驱动已实现；**真实四产品组合验收尚未执行/通过**，不是全系统或发布完成。

- 本 worktree：`C:/Users/Administrator/.codex/worktrees/e478/tianshu-peiban-bot`。
- 分支：`codex/dep-d-release-acceptance`，从应用分配的 detached worktree 建立独立本地分支。
- 根基线：`21668e4300d7bedc8ac1ed819bdb7dad4a2bd58b`。
- 实现及测试证据提交：`5c89fc662b923b91e7f33a44c3dc0fc2101c9dce`，40 个新增文件。
- 本交接作为其后独立本地提交；最终提交号见 `git log -1` 与本任务最终回复。Git 未配置作者，本次仅使用单命令 `user.name=Codex/user.email=codex@local.invalid`，未改全局身份配置。

固定四产品来源（只从原协调仓库 Git 对象导出，未把 TS104–106 活跃目录或未提交字节当输入）：

| 产品 | 提交 |
|---|---|
| platform | `745ee9dd0b97ff1822a8fd4362e0626b7e3bb316` |
| companion | `ab7c58250961807a2016c5f9128ce34b0065e182` |
| memory | `9df2e9e2eb6c2c36778f215306e6b20c377e18c4` |
| gateway | `51121e6c02ed60605be14f31b19b484bc117a746` |

DEP-A 输入固定为 `e86d622a813f116b059b62b820cbd1342722042d`：schema Git blob SHA256 `cf95607fa7252f806ae4ab4d919298c234d91f1007ce763ec12485091f4d0397`；example `7566375a763440d9408d1ae6a3502aab5521bd996cec3dc93c523ebcd660f922`。此前协调给出的另外两个 hash 为 CRLF 工作区字节，已共同核对区别。本号使用 Git blob，不混比；所有合同依 DEP-A 原始字节 hash 核验，diagnostics 不规范化。旧合同包内部若明示 normalized hash，则另按其语义校验，但报告/快照仍保留原字节摘要。

## 实现内容

入口 `tests/release_acceptance/run.py` 支持 plan/run/observe/snapshot/catalog/verify-report。未给 execute 只计划；候选清单和缺镜像 digest 保留未验证。快照目标必须新建、四完整 commit、拒绝逃逸/链接，额外逐文件 hash 可防导出后篡改。日志词汇从固定 Git blob 静态 AST 提取，不 import 产品业务实现。

实际 HTTP 场景覆盖网页 Cookie/CSRF/Origin、对话到录制模型和发送、候选/记忆提交/归档分层、积压阻断、来源与模型撤销、unknown 重放和重启后不重发、独立超时与取消、四产品各自封闭就绪字段、日志因果/失败不假成功、秘密 canary。故障启用响应丢失也尝试撤销；撤销未确认则停止后续变更。TLS始终验链/hostname，可将TCP固定连接loopback而保留内部SNI/Host/Origin，不继承代理或重定向。

按协调最新 Memory 入口发现加 configuration_loading：显式预期配置 SHA256 与实际运行装配观测相符才允许对话；`TIANSHU_MEMORY_CONFIG`/`--config` 写对、CLI help可用、清单字段正确均不证明实际已加载。没有实际运行观测入口就报告依赖缺失。

报告 dep-d/1 的精确序列化、状态聚合、证据标签见 [REPORT.md](../../tests/release_acceptance/REPORT.md)。hash 是完整性，不是签名/执行真实性；没有写自填 passed 即 verified 的逻辑。DEP-A 追加 evidence 改变 manifest hash，必须保留被测试不可变输入，不能把改后清单倒填旧报告制造自引用。私有测试控制接口见 [ADAPTER.md](../../tests/release_acceptance/ADAPTER.md)，不发布到根 contracts。

## 实际验证

实际解释器 `C:/Users/Administrator/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe`，Python 3.12.14；以下 `PY` 表示该完整路径，在 PowerShell 用 `& $taskPython` 调用。工作目录为本 worktree。

| 实际入口 | 结果 |
|---|---|
| `PY -B tests/release_acceptance/selftest.py --output tests/release_acceptance/evidence/selftest` | 最终 26 项通过、0 fail/error/skip，unittest 27.999 秒；报告墙钟约28.078秒 |
| `PY -B tests/release_acceptance/smoke.py --manifest tests/release_acceptance/evidence/inputs/tested-release.json --contracts-root C:/YOKI/Codex/tianshu-peiban-bot/contracts --output tests/release_acceptance/.runtime/final-smoke` | 实际 loopback TLS、合成驱动17 pass/3 not_run，整体incomplete；报告及journal原字节复制到 evidence/synthetic，完整性已核 |
| `PY -B tests/release_acceptance/product_login_probe.py --snapshots tests/release_acceptance/.runtime/pinned-products-v2 --output tests/release_acceptance/evidence/platform-login-final` | 固定真实Platform实际HTTPS网页登录/CSRF通过；四产品链not_run。仅使用明确合成测试配置/本地临时数据库与证书；其前一次报告另保留 |
| `PY -B tests/release_acceptance/run.py run --execute --manifest tests/release_acceptance/evidence/inputs/tested-release.json --contracts-root C:/YOKI/Codex/tianshu-peiban-bot/contracts --input tests/release_acceptance/input.example.json --output tests/release_acceptance/evidence/current-unwired` | 退出2，17 dependency_missing/3 not_run；无真实四服务控制接线，无网络副作用，未假报成功 |
| `run.py plan` 配合 `.runtime/inputs/container-input.json`（example改mode=container） | 容器报告20 not_run，退出2；没有容器实跑 |
| `run.py snapshot`，显式 repositories.json和完整四版本 | 原提交archive导出；第二版加逐文件核对，未读活跃修复目录；四archive摘要保存在忽略的snapshot.json中 |
| `run.py catalog`，同一固定清单与仓库映射 | 四产品真实静态词汇导出成功；catalog文件 SHA256 `8dd5d72e9e544c70d975825e34bccdc5a9034e56660def09e93af09af8ada092` |
| DEP-A schema/example 用已装 `jsonschema.Draft202012Validator` 实际验证 | 固定输入结构通过；不代表镜像/运行通过 |
| `tests/release_acceptance/.runtime/platform-deps/bin/ruff.exe check/format --check tests/release_acceptance --exclude .runtime` | 通过；依赖来自固定Platform requirements-dev.txt，ruff0.15.7，无锁文件改动 |
| 完整 diff、`git diff --cached --check`、逐报告content hash及观察journal哈希链独立核对 | 通过；新增文件均在白名单 |

Platform探针依赖装到本号 `.runtime/platform-deps`，使用固定导出 `requirements-dev.txt` 的精确版本，不改产品环境；运行时 PYTHONPATH 显式指该目录。TLS自测 cryptography50.0.1来自已有工具解释器。依赖安装目录、四源码快照、配置/证书/DB、仓库映射全部忽略，不入库。

首轮20项自测通过后，审查修复了实际就绪键差异、静态注册表集合union提取、故障启用丢响应清理；新指令又加实际配置加载门槛。最终只在对应变化后集中执行必要测试；未重复旧产品完整套件、未运行无关workspace全套。

## 准确限制和下一步

1. **未实现真实四产品部署控制适配器**。本号交付了公开HTTP断言、两种控制传输、精确输入接口和完整合成驱动；当前缺专属部署的实际进程配置/身份、重启、故障/撤销、录制上游计数、公开记忆/归档读回、DEP-B日志查询组合接线。缺失是机器报告中的dependency_missing，不是pass。需协调在各包公开命令/端口基础上接线；不得通过跨产品SQL、常量自报或改产品源码伪造闭环。
2. 无Docker命令入口被发现且未运行daemon/容器，不将Compose静态解析或本地TLS当Linux验收。内部接线需用 `/contracts/text-dialogue/v1` 叶目录；Memory `--config` 和 `TIANSHU_MEMORY_CONFIG` 应一致并实际确认加载，host/port/TLS/allowed-host按产品CLI真实位置传递。
3. TS104–106仍在修复，本组合是修复前固定基线；最终需重新绑定验收后的提交、镜像digest、合同与有效配置。root CURRENT/tasks/workspace、原协调目录、contracts、产品仓库及修复窗口均未修改或接管。
4. 候选消费者和Chat Audit闭环缺口未由本号补产品。候选受理单独过不能证明长期记忆；未启用自动消费仍积压时明确fail。当前发布不可称日常可用。
5. 所有模型为录制替身，real_model_quality始终not_run。HTTP Cookie验证不代表实际Chromium页面；browser_rendering单列not_run。没有NAS、真实账号/数据、独立服务覆盖或生产验证。
6. observe已实现真实时长、逐样本持久、空档/中断门槛；仅实际运行约0.25秒短测，24h未执行。即使未来24h探针通过，也仅readiness观察，不是全天真实聊天/日志吞吐/备份恢复工作负载。
7. 运行身份和已加载配置的控制适配器陈述均标明来源，协调仍需独立核实际启动记录。此报告不能替代linux_images/log_recovery/restore_drill等证据，也不自动提升DEP-A verified或DEP-C恢复安全性。

建议串行集成：协调审查本号runner和机器报告→接A/B/C公开控制与只读观测→重新固定修复后组合→本地正式Linux容器联合→另获授权后实机/真实模型/24h。不要先把所有not_run改成pass来满足清单标签。

## 全变更清单

新增以下40文件，加本交接 `docs/handoffs/DEP-D.md`，合计41；无修改或删除既存项目文件。

```text
tests/release_acceptance/.gitattributes
tests/release_acceptance/.gitignore
tests/release_acceptance/ADAPTER.md
tests/release_acceptance/README.md
tests/release_acceptance/REPORT.md
tests/release_acceptance/acceptance/__init__.py
tests/release_acceptance/acceptance/catalog.py
tests/release_acceptance/acceptance/diagnostics.py
tests/release_acceptance/acceptance/evidence.py
tests/release_acceptance/acceptance/health.py
tests/release_acceptance/acceptance/inputs.py
tests/release_acceptance/acceptance/observation.py
tests/release_acceptance/acceptance/suite.py
tests/release_acceptance/acceptance/transport.py
tests/release_acceptance/depa-mapping.json
tests/release_acceptance/evidence-index.json
tests/release_acceptance/evidence/catalog/catalog.json
tests/release_acceptance/evidence/container-not-run/report.json
tests/release_acceptance/evidence/container-not-run/summary.md
tests/release_acceptance/evidence/current-unwired/report.json
tests/release_acceptance/evidence/current-unwired/summary.md
tests/release_acceptance/evidence/inputs/tested-release.json
tests/release_acceptance/evidence/platform-login-final/report.json
tests/release_acceptance/evidence/platform-login-final/summary.md
tests/release_acceptance/evidence/platform-login/report.json
tests/release_acceptance/evidence/platform-login/summary.md
tests/release_acceptance/evidence/selftest/report.json
tests/release_acceptance/evidence/selftest/summary.md
tests/release_acceptance/evidence/synthetic/short-observation/observation.jsonl
tests/release_acceptance/evidence/synthetic/short-observation/report.json
tests/release_acceptance/evidence/synthetic/short-observation/summary.md
tests/release_acceptance/evidence/synthetic/suite/report.json
tests/release_acceptance/evidence/synthetic/suite/summary.md
tests/release_acceptance/fixtures.py
tests/release_acceptance/input.example.json
tests/release_acceptance/product_login_probe.py
tests/release_acceptance/run.py
tests/release_acceptance/selftest.py
tests/release_acceptance/smoke.py
tests/release_acceptance/test_runner.py
```

## 引用

精确任务卡及授权：原协调目录 `docs/development/deployment-wave2-codex-2026-09-22.md`；差距计划 `first-nas-deployment-gap-plan-2026-09-22.md`；CURRENT与 `nas-environment-check-2026-09-21.md`；四产品AGENTS；`contracts/diagnostics/v1` 实际12字段及各产品就绪源码。官方Python/Git/cryptography版本配置资料链接已收录 [README](../../tests/release_acceptance/README.md)。所有提交证据原字节hash见 [evidence-index.json](../../tests/release_acceptance/evidence-index.json)。
