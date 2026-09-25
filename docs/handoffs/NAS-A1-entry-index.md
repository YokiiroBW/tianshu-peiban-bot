# NAS-A1 新范围入口与证据索引

## 输入与固定文件

这份索引记录协调者分配的单次 r2i 测试入口；实际尝试在源端停止，结果见 [`NAS-A1.md`](NAS-A1.md) 的 r2i 实机交接。当前代码提供静态 source、参数化源端业务入口、克隆占位输入和单次短时驱动。短时许可尚不存在，不能用 r2h/r2g 的值代填。

后续**新范围**须额外在准备 JSON 写入 `gateway_runtime_lock`（固定为 `code_root/ops/recovery/a1_gateway_runtime.lock.json`）及 `gateway_runtime_lock_sha256`，并在同一 `python` 工具 venv 离线安装该锁规定的 Gateway 全部 14 个运行包以及原恢复工具依赖。操作方需在创建新 scope 前逐 wheel 核锁定 SHA、记录安装回执；通用 `ops/recovery/requirements.txt` 的 `jsonschema==4.25.1` 不能代替 A1 锁定的 `4.26.0`。`a1_prepare --execute` 在创建 scope 前检查完整版本与导入来源；`a1_source_flow --execute` 在 attempt 标记与九 owner 启动前，使用临时合成 SQLite 和同一 `python` 执行真实 `_usage`/Gateway CLI。此要求来自 r2j 宿主缺 `aiohttp` 的故障复盘；旧 r2i/r2j 输入与结果仍按当时固定代码解释，不可在旧 scope 补包重试。副本补验与局限见 [`NAS-A1-r2j-ledger-readback-evidence.json`](NAS-A1-r2j-ledger-readback-evidence.json)。

本次固定分配是 `scope-a1-r2i`、execution ID `7a8aeaf2-80af-4cc6-a109-18d2c249552d`，以及 `10.205.49.0/24` 内 source 五段、clone 七段互不重叠的 `/28`；尾段 `10.205.49.192/26` 不分配。source/clone 回环端口各六个，以 `scope_parent/preparations/nas-a1-network-allocation-r2i.json` 为准，其原始字节 SHA256 必须是 `8dd4844321d0b396adc09451d8c6cea26abab7ef08f2bc21d70164fa87aa3d55`。配置须引用该文件并带同一哈希与 execution ID；准备、源端、克隆及单次驱动分别核对。创建前仍须在 NAS 实时预检所有网络、路由、端口、至少 11.5 GiB 可用内存和九 owner 资源约束。固定产品要求 source Companion/Memory/Gateway 回环 API 端口依次为 `19512/19513/19514`。准备 JSON 还需指定规范化绝对 `scope_parent`、新 scope UUID、两个专属 Compose 项目名、固定代码根 `scope_parent/tooling`、绝对 Python/Docker 可执行文件、原始固定候选 manifest、合同根、资源 profile、固定 OBS Git 仓库及其 manifest 提交对象 SHA256、四产品固定 Git 仓库的 `projects_root`、Gateway CLI 导入树及其逐树 SHA、`run_label`、恢复/克隆/备份/许可/收据名称。所有值都在 `scope_parent/preparations/scope-a1-r2i.json`，严格符合 `a1-preparation/1`。

代码锁由以下只读命令输出 `a1-code-lock/1` JSON，原样置于相邻 `scope_name.code-lock.json`；准备 JSON 中的 `code_tree_sha256` 与 `code_lock_sha256` 分别来自内容字段和锁文件原始字节。原 manifest 原始字节 SHA256 必须为 `ce68ed58f4dbbaac6b9a09fc3632fd2522afc97501a63b790e1447b0af9fc4e2`，派生仅改 `web_text_dialogue.enabled: true → false`。OBS 固定提交对象、四产品固定提交对象、Gateway 导入树、资源 profile 也必须锁定。打包后任何 `ops/` 或 `deploy/` 字节变化都使配置失效，应先审新包并重新锁定。所有公开 `-m` 命令须在锁定的 `scope_parent/tooling` 工作目录启动；程序仍会校验实际加载模块的来源，不以工作目录声明代替校验。

```text
ABS_PYTHON -B -m ops.recovery.a1_once --emit-code-lock ABS_CODE_ROOT
cd ABS_CODE_ROOT
ABS_PYTHON -B -m ops.recovery.a1_prepare --config ABS_PREPARATION_JSON
ABS_PYTHON -B -m ops.recovery.a1_prepare --config ABS_PREPARATION_JSON --execute
```

默认第一项准备命令是只读计划；`--execute` 才在全新范围写 source、OBS、独立 TLS/凭据、模型发布模板及九个虚构产品输入。部分失败保留新范围，不能在同一范围重试。创建前须检查原 manifest、合同字节、所有分配网络/路由、回环端口、宿主资源、镜像/产品提交及两份 Compose 的实际配置。

## 源端业务事实：参数化入口

静态 source 包之后、克隆占位之前，`a1_source_flow` 使用准备配置中的同一 scope、项目、Docker/Python 路径，并经公开产品 CLI/HTTPS 完成下列**真实操作和收据**。默认是只读计划；`--execute` 先实时检查 12 网络、12 回环端口、四个项目名和可用内存，再写一次性 attempt 标记。仅对全新空数据/日志目录设置运行 UID/GID，随后调用打包的 Linux 权限 preflight，成功后才启动服务。任何写入后失败都保留现场，禁止同范围重放。旧 `a1_r2h_*` helper 含硬编码路径及顶层副作用，不能用于新范围。

```text
cd ABS_CODE_ROOT
ABS_PYTHON -B -m ops.recovery.a1_source_flow --config ABS_PREPARATION_JSON
ABS_PYTHON -B -m ops.recovery.a1_source_flow --config ABS_PREPARATION_JSON --execute
```

| 顺序 | 必须取得的实际状态/固定文件 |
| --- | --- |
| 1 | 使用明确的 source/OBS Compose 项目启动九个固定 owner；Memory 首次迁移；四 core healthy、五 OBS running、固定镜像、挂载与进程身份。Gateway 的短时 source origin 仅由 Platform 公开 CLI 签发并绑定；不直写产品库。 |
| 2 | 发布合成模型 v3；经 Platform 注册/分派两份虚构输入；Companion 产生两条 `closed_unknown`，各一 unknown 回复。API 收据与只读路由检查证明两条请求经固定合成 provider。 |
| 3 | `source-facts.json` 与 `web-snapshot-initial.json` 从 Companion 公开 API 读回；Memory 产品候选/信任提交形成绿色、同源阅读及红色三条虚构事实。 |
| 4 | 绿色基线可选；通过本地用户确认与 Memory 公共 revision 忘记绿色，绿色/同源阅读变成 `no_match`，红色仍可选。仅撤回红色输入凭据后仍可选；Platform retract 与 fanout 后红色 `no_match`。 |
| 5 | 撤销模型 v3 后经 Gateway→Platform snapshot 得到 HTTP 410/`forbidden`。发布并绑定 v4；离线上游两组 unknown、两组成功控制写入源端 Gateway 账本，官方 usage CLI 读回四组；Companion 两条 unknown turn/reply 重启前后稳定。 |
| 6 | 从当轮九 owner 采集 `reports/runtime-identity.json`，使用公开 `linux-prepare` 登记 authority 和 `.recovery-registration.json`，核实 scope ID、文件哈希、镜像与 owner。不要预签发克隆 v5/origin。 |

克隆占位生成器要求 `source/reports/RUN_LABEL/` 下 `source-facts.json`、`unknown-after-v4-readback.json`、`web-snapshot-initial.json`；源端 SQLite 只读 route receipts、Gateway 官方 usage CLI 四组，以及 `scope/inputs/model-publication-template.json`、`source/reports/runtime-identity.json` 和 `.recovery-registration.json`。入口在登记前也调用同一源证据消费者，写 `gateway-usage-readback.json`；收据必须来自当轮 source，而非旧范围复制。

## 克隆占位和短时单次链

完成源端并独立复核上述事实后，以下第一项只读，第二项只写本范围私有的初始 clone 输入、TLS、逐文件锁和 `inputs/a1-once.json`；真实 clone 目录尚不存在。任何占位生成失败应保留现场并换新范围，不原地覆盖。

```text
ABS_PYTHON -B -m ops.recovery.a1_clone_prepare --config ABS_PREPARATION_JSON
ABS_PYTHON -B -m ops.recovery.a1_clone_prepare --config ABS_PREPARATION_JSON --execute
ABS_PYTHON -B -m ops.recovery.a1_once --config ABS_SCOPE/inputs/a1-once.json --execute
```

最后一条命令串行执行 seal → `linux-rehearse --execute` → 实时宿主 preflight → 独立 permit → `drill-clone` plan → **一次** execute。seal 发布 v5 与两份新短时 origin，封印初始 6 断言输入；许可和 execute 各自重新检查九 owner、禁用恢复、冻结 SQLite/WAL、精确网络/端口、文件 SHA、当前 origin/unknown 到期和最少 180 秒入场门槛。许可 600 秒、克隆运行上限 540 秒均不延长更短的产品来源期限。r2h 旧运行里最终 seal 至 execute 入场被 180 秒拒绝；消除人工空档后的理论余量仅为估算，六断言实机总时长尚未测得。

每阶段有私有原始 stdout/stderr 与 SHA、结构化收据、UTC 与单调时间。CLI 超时仅给该 CLI 主进程 SIGTERM，再等 90 秒正常清理；存活状态不明即 `stop_unconfirmed`，不启动第二写入者。seal 子进程已退出但失败时只尝试一次精确已登记九 owner 的 `linux-rehearse` 正常停写/备份/禁用恢复；失败、许可拒绝、执行失败均不自动重试或重放 claim。任何结果后只读核对 source/clone 九 owner、恢复目标仍禁用、claim、四项目及网络端点，并保存现场。

离线定向验证为准备链 20 项、单次驱动 38 项、源端入口 8 项；真实 bundle initializer、固定 OBS Git 包与四产品词表、静态合成配置/九输入、克隆占位生成及同一封印包的 `drill_inputs.isolated_inputs` 消费者校验已运行。源端入口的顺序夹具逐步走到登记完成标记，全部产品行为为离线替身；另对实际 Memory 读回适配器使用新随机 person/conv ID 做绑定正反例，并检查资源 profile `[6,7]` 与两份 Compose/九 owner 观测一致。Windows 不支持的 OBS Linux 权限函数被单点替换；同步 Platform issue/publication、Docker、Linux 权限/资源、实际九 owner 及六项 HTTPS 均未在新范围执行。真实 NAS 新范围、许可和容器本轮均未创建。
