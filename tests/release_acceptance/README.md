# DEP-D 当前发布组合验收

这是独立验收入口，不修改 TS050、不初始化正式产品、不部署、不访问 NAS。运行器只用 Python 3.12 标准库；TLS 自测另使用本机已装的 `cryptography==50.0.1`。四产品版本来自 DEP-A 清单，不能把根协调仓库 HEAD 当产品版本。

## 输入与命令

所有命令从根仓库运行。示例中的路径和环境变量必须换成**隔离合成部署**的显式输入；没有默认账号、模型或生产地址。

```text
python -B tests/release_acceptance/run.py plan --manifest <DEP-A清单.json> --contracts-root <原字节contracts目录> --input <验收输入.json> --output <新报告目录>
python -B tests/release_acceptance/run.py snapshot --manifest <清单.json> --contracts-root <contracts目录> --repositories <仓库映射.json> --output <不存在的快照目录>
python -B tests/release_acceptance/run.py catalog --manifest <清单.json> --contracts-root <contracts目录> --repositories <仓库映射.json> --output <目录>
python -B tests/release_acceptance/run.py run --execute --manifest <清单.json> --contracts-root <contracts目录> --input <验收输入.json> --output <报告目录>
python -B tests/release_acceptance/run.py observe --execute --manifest <清单.json> --contracts-root <contracts目录> --input <验收输入.json> --output <新观察目录>
python -B tests/release_acceptance/run.py verify-report --output <报告目录>
```

`plan` 和未给 `--execute` 的 `run` 只校验本地输入、生成 not_run 报告，无网络或控制动作。`snapshot` 显式创建全新输出目录，用四个完整 40 位提交执行 Git archive，不读活跃工作树字节、不 checkout、不安装依赖。禁止归档 symlink/hardlink/设备或越界路径；已存在目标拒绝覆盖。合同使用清单记录的原始字节复制，包内旧合同明确声明的 CRLF→LF 哈希另按自身约定检查，diagnostics 全程不规范化。

仓库映射是 `{"platform":"<Git目录>","companion":"...","memory":"...","gateway":"..."}`。DEP-A 适配映射独立放在 [depa-mapping.json](depa-mapping.json)，不是另一个发布清单 schema。已消费 DEP-A `e86d622a813f116b059b62b820cbd1342722042d` 的固定 Git 对象；两个对象哈希见交接。

从 [input.example.json](input.example.json) 开始。必需说明 `scope=synthetic_isolated`、`mode=local|container`、`runtime_kind=product|synthetic`、`model_kind=recorded`。本轮入口拒绝真实/付费模型、container+synthetic 组合和非 loopback 连接。正式环境模式表示 Linux 容器中产品正式依赖/命令，仍须合成身份与录制模型；不意味着 NAS/生产授权。

`endpoints` 的四个键是 platform/companion/memory/gateway。每个值示例：

```json
{
  "url": "https://platform.internal:18443",
  "connect_host": "127.0.0.1",
  "ca_file": "/isolated/ca.pem",
  "diagnostics_token_env": "DEP_D_PLATFORM_DIAGNOSTICS",
  "timeout_seconds": 5
}
```

`connect_host` 可省略，但 URL 此时必须是 loopback IP。指定时只允许 loopback IP，TCP 固定连接该地址；SNI、证书 hostname 校验、Host、Cookie 和 Origin 仍使用 URL 的原主机名。这样支持容器端口本地映射与内部 SAN，不关闭 TLS、不改系统信任、不做 DNS 查询。不继承代理、不跟随重定向、不自动重试。支持可选客户端 `cert_file/key_file`。HTTP 只允许明确标 synthetic 的自测替身。

`web` 给出用户名/密码的环境变量名、合成 conversation 和 actor。`features` 分开声明 automatic_memory、chat_archive；产品模式会和清单对应开关核对。`skip` 是 case→原因映射，保留 skipped，不折算成功。不要把凭据/正文写入 JSON、报告或仓库。报告仅保存输入哈希、状态、计数和安全事实；原响应、Cookie、密码、对话正文不落报告。

`expected_config_sha256` 给四产品预期配置文件原字节摘要。控制适配器的 configuration 操作必须从**实际装配/进程**确认已加载配置，未能观测就返回 unsupported，不按 CLI 参数反射成成功。特别是当前 Memory configured_app 读取 `TIANSHU_MEMORY_CONFIG`；仅给 `--config` 不是已加载的证据，部署应绑定同一路径且验证不一致边界。报告仍将这份证据标为适配器运行观测，协调者独立核验。

## 已实现的场景

| 场景 | 通过条件与证据范围 |
|---|---|
| runtime_binding | 适配器身份与输入提交一致；container 必须四镜像非空 digest 一致且 OS=linux。此项仍为适配器陈述，协调需复核实际启动来源 |
| configuration_loading | 核四产品实际加载配置摘要与显式预期相等；未配置/未观测为依赖缺失，禁止开启后续对话场景 |
| health_and_auth | 四公开存活、无/错凭据拒绝就绪、独立诊断凭据；按四产品**各自**封闭检查键判断必要项，不把可选依赖 not_verified 当可达 |
| web_login_csrf | 实际 HTTP Cookie 会话、错密码、缺 CSRF、异 Origin 拒绝、登录后 CSRF 轮换、HttpOnly/SameSite/Secure |
| dialogue_model_reply | `/api/web/messages` 受理→实际网页 snapshot 中匹配 message_id 的轮次 sent/正文可见→模型与 sender 计数增长 |
| memory_candidate | 对应 turn 的候选回执；只证明 queued/accepted/consumed 候选阶段 |
| memory_finalized / chat_archive | 分开要求 committed memory ID/revision/读回、archived ID/读回；候选入队无法通过 |
| memory_backlog_boundary | 禁用自动记忆时仍检查 pending 增长；持续积压明确 fail。启用时必须提炼通过且排空本次增量 |
| unknown_no_resend | 接受后断连→closed_unknown/隐藏正文；重放同 client_id 两次仍相同 message_id，模型与发送计数不得增长 |
| restart_recovery | 四服务实例变更、旧网页会话失效、已发送历史不丢、unknown 回执仍在且重放无副作用 |
| source_revocation / model_revocation | 已撤来源历史隐藏及 recall 排除；已撤模型拒绝且无上游/发送增量 |
| timeout_cancel | 超时故障下用公开 cancel + version；轮次保守终结，不展示迟到正文，适配器需给超时/迟到/重复观测 |
| log_causality / failure_truthfulness | diagnostics 原 12 字段、UUID/时间/有限数值/静态词汇、event/sequence 重放一致；同 correlation 跨四服务指定事件；失败必须有非成功终态、禁止假成功并扫描秘密 canary |
| abnormal_readiness | 逐产品注入日志不可写；其实际 `logging/logs/log` 项失败、ready=503，发起新合成消息不得产生模型/发送副作用 |

故障均在 `finally` 撤销；撤销不确认即 fail，并停止后续变更场景。适配器缺失或声明 unsupported 为 dependency_missing；不可执行的后续用例仍显示，不生成“全通过”。来源撤销使用专属合成来源，不自动复活被撤来源。

`logs.catalog_file` 使用 `catalog` 命令产物，`catalog_sha256` 是文件原字节哈希。该命令通过 Git show + 静态 AST 提取固定产品注册表，无 import/执行产品模块。产品模式核对 catalog 的四产品绑定。`logs.success.required`、`logs.failure.required/forbidden` 是 `[service,event,outcome]` 数组，必须从当前注册表选择**同一业务阶段**。success 必须覆盖四服务。不要用上游某一步真实成功来禁止整个失败请求中所有 succeeded 事件。日志由适配器返回原 JSONL，报告不存原行。此入口验证因果及失败真实性，不代替 DEP-B 全量采集、回补/轮转/容量对账。

## 测试控制适配器

[ADAPTER.md](ADAPTER.md) 定义本目录私有的测试驱动接口，**不是产品 HTTP 合同**。现提供两种传输：本机独立 HTTPS `/control` 测试侧车，或显式 argv 命令（无 shell、30 秒上限、只转发列出的环境变量）。由隔离部署负责人将操作接到各包公开命令/端口、录制模型与日志查询；不得通过跨产品 SQL/修改生产配置伪造回执。

DEP-E新增真实四产品CLI接线，支持实际进程/配置/HTTPS/Web对话/公开停用事实和原始因果日志。
DEP-E默认适配器的故障仍保持原边界；DEP-H使用显式 `--faults` 接入下述真实故障场景。
DEP-F生命周期是独立入口，仍未接成四产品恢复闭环；最终记忆消费者/归档缺口保持发布阻断。

## 观察、报告与边界

`observe` 默认请求 86400 秒，每 30 秒读四服务就绪（间隔最大 60 秒）；每样本 JSONL flush+fsync、前向哈希链、实际单调时长、失败计数和最大空档。停止/中断/未满 24h 或采样空档过大不能通过；重复观察不自动接续/累计成 24h，已有 journal 拒绝复用。允许短 `--duration-seconds` 调试，但始终 incomplete。观察范围仅 readiness，不代替 24h 连续聊天、记忆、日志吞吐或恢复压力；报告另列 observation_workload 未执行。

每次生成 `report.json`、`summary.md`；观察另有 `observation.jsonl`。退出码 0 表示该次没有失败/缺失的有限结果，1 为失败，2 为不完整。`smoke.py` 自测即使产品验收仍 incomplete，也在自测断言无 fail 时返回 0；不要把自测退出 0 当发布通过。

[REPORT.md](REPORT.md) 给精确字段、哈希约定及消费者责任。报告哈希只证明内容完整性。candidate 不自动升级 verified；原始清单附报告后哈希会变，协调应保留被测试的不可变清单作为证据输入，不让新清单自指它自己的验收。静态 source 绑定、适配器返回身份、作者填写 passed 都不是独立运行真实性证明。

无真实模型效果、实际浏览器渲染、Linux镜像或NAS验收。HTTP登录与真实浏览器分别记录。
DEP-E报告绑定TS107/108/109/110集成版本；应用镜像digest仍未知，不能把源码进程验证当镜像验证。

## DEP-E四产品真实CLI与合成模型

先导出不可变快照，再显式运行（新输出目录，Python3.12及四产品所需依赖）：

```text
python -B tests/release_acceptance/run.py snapshot --manifest <被测清单> --contracts-root <原字节合同> --repositories <只读Git映射> --output <新快照>
python -B tests/release_acceptance/product_stack.py --execute --manifest <相同被测清单> --snapshots <新快照> --repositories <相同Git映射> --output <新结果目录> --turns 257 --minimum-duration-seconds 310
```

被测清单须显式开启仅合成场景的web_text_dialogue；保存这份原始清单，不能用默认关闭的例子冒充同一输入。
例子仍是candidate；命令不将其晋升verified。产品只从Git对象导出、每次核全部文件。
运行器生成本地CA/loopback TLS、随机合成账号及私有凭据；调用真实平台publish/issue、新库Memory迁移，
随后启动原产品CLI。共用测试venv不代表镜像正式依赖安装；网页静态资源是测试夹具，不做浏览器渲染声明。

product_worker.py在产品子进程被动观察成功JSON解析，记录实际读取配置摘要；它调用原解析器并原样返回。
另核父子PID、存活、四产品自身的独立鉴权readiness及源码全树摘要。报告称测试观察，协调者仍须独立复核。
启动和结束记录运行器/模板文件摘要，运行期间修改代码会导致失败。

20个DEP-D场景逐个输出状态。禁用长期记忆的三个场景读取真实公开capabilities，成功只代表disabled_verified，
不会证明写入长期记忆或Chat Audit。已有候选只报告布尔存在事实；本运行器从新库开始，
不据此声称已验证旧库迁移/旧候选保留，该部分由TS108专门产品测试覆盖。

257轮负载保持同一会话、每次只有一条在途，短上下文配置为0，以隔离全局候选队列边界。
首轮通过公开Web snapshot读回回复；负载逐轮走公开Web输入，按相同correlation的真实
turn.delivery.finished/succeeded事件和独立模型HTTPS调用计数确认，不反复读完整历史。
stress-delivery.jsonl保存实际事件，报告绑定其hash；最终公开capabilities须无任何待处理状态。
310秒节奏覆盖初始300秒来源寿命，期间不换ref、不重启、不给网关管理员凭据；续期由两产品公开接口执行。
此负载不代表短上下文质量、并发压测、24h使用或永久模型授权。

无法注入真正不确定投递时，unknown仍缺失。关闭录制模型TCP只能造成产品明确生成失败，
不能把它改写成closed_unknown通过。结束时只终止本次创建的子进程；强制清理会在报告列明，
不是正常停写/恢复证据。临时配置、密钥和数据库随测试清理，留下报告及封闭词汇内的原始因果事件。

## DEP-H四服务故障执行

`product_stack.py` 现在默认仅输出 plan；必须显式 `--execute` 才初始化全新合成状态。
沿用上面的固定Git快照、原字节合同及被测清单，执行：

```text
python -B tests/release_acceptance/product_stack.py --execute --faults --manifest <相同被测清单> --snapshots <新快照> --repositories <只读Git映射> --output <不存在的结果目录>
```

不可与257轮选项组合。四个产品CLI不变，进程生命周期移到 `product_lifecycle.py`，
故障控制、场景、发送代理和录制模型分别独立。所有故障仅作用于本次新建目录、随机凭据及自有PID。
正式模板、产品源码与数据库结构均不修改；不跨写产品SQL，不伪造诊断记录。

| 项目 | 实际接线与验证 |
|---|---|
| unknown/no resend | Core的platform_sender经过本地TLS代理，真实平台返回sent后丢失回包；Core实际closed_unknown，重放2次模型/发送计数不增加 |
| restart recovery | 四服务实例更换，同库重启后已发送历史/unknown回执保留、旧网页登录失效、无重发；重新核真实配置解析PID/hash与鉴权ready |
| source revocation | 已有操作员CLI register-input以相同message_id/revision2/retract登记，再dispatch-fanout至Core；历史正文隐藏，模型输入正对照包含旧文、撤回后排除 |
| model revocation | 已有操作员CLI revoke-config；网页立即503/model_not_configured，日志故障后冷网关直接请求拒绝且不调用模型；不证明暖缓存传播时延 |
| timeout/cancel | 合成网关上限2秒，录制端延迟8秒；真实超时事件、公开cancel、释放两条迟到响应后无投递/重试 |
| failure truthfulness | 模型TCP断开实际轮次failed、原始generation失败日志且无成功投递；不误称unknown |
| abnormal readiness | Windows OS强制字节锁阻断包括轮转段的日志追加；逐产品ready503，业务尝试无模型/发送增量；解锁后重启并复核配置/ready |

日志字节锁不改写原始日志。POSIX上该故障明确dependency_missing，尚无Linux文件权限/挂载故障证明。
重启是**崩溃恢复**：Windows退出码与Memory强制清理分别留证，`normal_stop_proven=false`。
没有删锁、重新签发来源或撤销后重新发布配置。来源撤回只证明已有操作员入口，不代表网页有撤回按钮。
记忆/归档相关pass仍仅为disabled_verified，未证明持久记忆写入或归档。

最终证据见 `evidence/wave4-h/final-fixed/`：17 pass、0 fail、0 dependency_missing、3 not_run，
整体incomplete；Linux、NAS、浏览器渲染、真实模型质量与24h未执行。此前失败尝试原样保留。
原wave3/wave3-r1及其他65份既有证据文件均未改字节，不重复旧257轮。

## 本目录验证

```text
python -B -m unittest discover -s tests/release_acceptance -p test_runner.py -v
python -B tests/release_acceptance/smoke.py --manifest <固定清单> --contracts-root <原字节合同> --output <新自测目录>
```

`product_login_probe.py --snapshots <snapshot输出> --output <目录>` 是额外 **Platform 单产品** 兼容探针。先在本目录忽略目录装该快照 `requirements-dev.txt` 的精确依赖，并用 PYTHONPATH 指向依赖目录；探针用快照中的明确测试身份配置、临时 TLS、自建 loopback listener，复用运行器的网页登录断言。它不调用外部 Core/模型，也不作为正式初始化工具。临时 CA/私钥/DB 自动删除，只有脱敏报告保留。

官方核对：Python 3.12 的 [SSL 客户端验证](https://docs.python.org/3.12/library/ssl.html)、[urllib 请求/代理/重定向](https://docs.python.org/3.12/library/urllib.request.html)，[Git archive 固定 tree-ish](https://git-scm.com/docs/git-archive)，[cryptography 50.0.1 X.509 测试证书](https://cryptography.io/en/50.0.1/x509/tutorial/)。未引入浮动镜像或 latest 依赖。
