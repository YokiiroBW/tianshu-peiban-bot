# 四核心与日志发布组合（DEP-A / DEP-E）

这是固定版本的**候选包生成器**；正式发布仍需 Linux 镜像/四服务验收以及协调者审核。当前完整产品提交见发布清单。
本包不连接 NAS、不替换已有服务、不调用模型、不自动启动或迁移数据库。源码只从清单固定 Git 提交导出。
默认网页对话关闭；自动长期记忆、Chat Audit、渠道、媒体、设备、主动发送没有假实现。

## 入口与产物

Python 3.12+；在独立环境安装 `requirements.txt`。运行以下命令时，`python` 指这个解释器。
所有目标必须是操作者显式给出的**新目录**，父目录已存在；不覆盖、不合并已有部署。

```text
python deploy/tianshu/release.py validate-manifest --manifest deploy/tianshu/release-manifest.example.json
python deploy/tianshu/release.py init --manifest <release.json> --inputs <private-inputs/deployment-input.json> --contracts-root <原字节contracts目录> --target <新包绝对路径>
python <新包>/tools/release.py preflight --bundle <新包>
python <新包>/tools/release.py preflight --bundle <新包> --check-permissions
python <新包>/tools/release.py preflight --bundle <新包> --release
```

`init` 先验证全部输入、合同原字节、真实 TLS 链与 SAN，再创建目标；创建期间 IO 失败保留 `INCOMPLETE`，预检拒绝。
输出 `compose.json`、四套私有设置/TLS/凭据文件、原字节合同、发布清单、校验清单、公开工具和命令清单。
`compose.json` 是 Compose 支持的 JSON/YAML 文档，兼容目标 Compose 2.20.1；已实际用官方 2.20.1 的 `config` 解析。
四核心生成路径为包内相对主机路径或容器 POSIX 路径。日志公开生成器使用绝对主机路径：
先将核心包放到最终目标，再运行 `configure-observability`。配置日志后的组合不可直接搬运；新地址须重新生成新包。

退出码：0 仅表示本命令范围有效，2 表示拒绝输入，`runtime-check` 的 3 表示无可用运行环境。
`initialized_candidate` / `package_valid` 始终 `release_ready=false`；空状态目录不会当作业务就绪。
`--release` 附加检查真实 Linux 权限、已验证清单、证据 hash/版本绑定、证书来源声明和发布阻断。
证据文件本身不证明执行真实性；协调者独立复核及现场端口/网段/资源/认证 readiness 仍是必要步骤。

## 配置输入

将 `templates/` 复制到自己的私有输入目录，编辑四产品 JSON 和 `deployment-input.example.json`。
例子中的公开源 `.invalid` 故意不可用。必须提供准确的 HTTPS Origin、显式绑定 IPv4/端口、私有网段与四个静态 IP，
确认与现有网络/端口不冲突。网关采用已审阅 IP 白名单，不能依赖动态 DNS 地址替代 `targets[].addresses`。
默认只发布平台 HTTPS 一个端口，内部服务不发布端口。内部名称/端口固定如下：

| 产品 | 容器名称 / SAN / Host | 入口 | 权威状态目录 |
| --- | --- | --- | --- |
| platform | platform.internal:8443 | `python -m services.platform --settings … serve --host 0.0.0.0 --port 8443` | `/var/lib/tianshu` |
| companion | companion.internal:8765 | `python -m tianshu_companion.runtime_cli --config … --contracts /contracts/text-dialogue/v1 …` | `/data` |
| memory | memory.internal:8130 | `python -m tianshu_memory.cli --config … serve --host … --port … --allowed-host memory.internal:8130 …` | `/srv/tianshu` |
| gateway | gateway.internal:8443 | `python -m tianshu_gateway --settings … --host … --port …` | `/var/lib/tianshu` |

每个服务的证书 SAN 覆盖其 `.internal` 主机名；平台还覆盖对外 Origin 主机名。四服务用同一份显式 CA bundle，
需要公网模型时将相应公有信任根也纳入审阅的 bundle。网关通过 `SSL_CERT_FILE` 使用它；其他服务使用真实配置的 `ca_file`。
生产材料由操作者提供，工具**不签证书、不安装信任、不调用测试 issuer**。测试证书只在 `tests/deployment/packaging` 临时生成；
`isolated_test` 标记禁止作为发布证据。`operator_supplied` 是声明，还需真实环境证书授权复核。

所有服务必须有独立诊断 token。四个诊断环境变量名由 `diagnostics_env` 指定，再映射到各容器自己的 `TIANSHU_DIAGNOSTICS_TOKEN`。
业务连接复用同一条边两端的变量名；不同凭据名禁止使用相同值，诊断凭据也不得等于业务凭据。

| 引用 | 用途 |
| --- | --- |
| TS_ADMIN_TOKEN / TS_ADMIN_PASSWORD | 平台管理身份 / 网页口令；口令转换为产品 `scrypt-v1` 格式，仅 hash 落盘 |
| TS_PLATFORM_CORE | 平台 → 陪伴 |
| TS_CORE_PLATFORM / TS_CORE_PLATFORM_SENDER | 陪伴 → 平台来源解析 / 网页投递 |
| TS_CORE_MEMORY / TS_MEMORY_CORE | 陪伴 → 记忆 / 记忆 → 陪伴来源事实 |
| TS_MEMORY_PLATFORM | 记忆 → 平台来源与 origin 解析 |
| TS_CORE_GATEWAY / TS_GATEWAY_PLATFORM | 陪伴 → 网关 / 网关 → 平台配置快照 |
| TS_GATEWAY_ORIGIN | 真实平台首次签发且仍有效的配置来源；受限续期由平台/网关产品执行，部署工具不伪造 |
| TS_DIAG_PLATFORM / TS_DIAG_COMPANION / TS_DIAG_MEMORY / TS_DIAG_GATEWAY | 独立 readiness 身份 |

普通 `init` 从当前进程环境读取这些值，不生成管理员/来源/模型授权。网页管理员账号、actor、binding、input entry 必须一致登记。
模型 `providers` 默认空；启用前要完成平台发布的真实模型版本、网关 client/provider/targets/凭据与 Core config_version 对应。
首次引导及来源到期处理见 [BOOTSTRAP.md](BOOTSTRAP.md)。全新合成执行入口实际调用平台公开 `local publish/issue`；
网关仅获得已有 `config.snapshot` 服务身份，不能持有管理员凭据或执行 `origin.issue`。

Memory 当前产品需要 JSON 字面凭据。输入模板仅含 `{"$env":"变量名"}`；工具将其解析到
`config/memory/settings.json`，权限 0640。其它产品凭据进入 `private/<product>.env`，权限 0600；
不将密码、token、TLS 私钥写进发布清单、Compose 或报告。不要运行会打印解析后凭据的 `compose config`，日常用 `config --quiet`。

## 初始化、身份与所有者

四服务使用 UID/GID 10001:10001、只读根文件系统、无 Linux capabilities、no-new-privileges、有界资源和临时目录。
不挂 Docker socket；platform 另接前端网络承载发布端口，gateway 另接出站网络。平台 → memory/gateway → companion 按活性健康有序启动，无依赖环；
活性不能代替认证 readiness，也不会因日志/远端依赖不就绪触发重启循环。

Linux `runtime_guard.py` 在各产品专属状态目录持有 `flock`，保留锁 FD 后 exec 产品进程；每服务固定单实例。
它防止另一进程误用同一目录；不删除产品 owner 锁、不绕过 source-guard。实际 Linux 锁/信号路径尚未运行。
初始化工具保留操作者属主，不提权/chown。Linux 权限按下表准备，由有权管理**本次新部署目录**的操作者调整；
只调整所列路径，私密目录不沿用公开合同的权限。

| 主机路径（相对包根） | 建议属主/属组与模式 | 容器实际需要 |
| --- | --- | --- |
| `data/{product}`、`logs/{product}` | `10001:10001`，目录0750，无other权限 | 每个挂载根可读、写、遍历；既有业务文件另由产品启动验证 |
| `config/{product}` 完整子树（含TLS） | 操作者`:10001` 或 `10001:10001`，目录0750、普通文件0640；属主为10001时也可用0700/0600 | 挂载根及所有子目录可读/遍历，文件可读；无other权限、无组写；有组权限时组必须为10001 |
| `contracts` 完整子树 | 可保留操作者属主/组；目录0755、文件0644（新包默认） | 全树可读/遍历，无组/other写；也可用组10001的0750/0640限制公开读取 |
| `tools/runtime_guard.py` | 可保留操作者属主/组，文件0644；或组10001且0640 | 直接绑定的文件可读，无组/other写；Python读取，不要求执行位 |
| `private` 与其中四个 `.env` | 保留操作者属主；目录0700、文件0600 | 由主机Compose读取，不要求容器10001拥有；不得开放组/other权限 |

准备步骤：先将八个数据/日志挂载根属主和属组设为10001；再给四套config完整子树安排上表的属组和目录/文件模式，
单独确认TLS私钥未开放；检查contracts全树和guard文件的公开或组读取方案；最后保留private的操作者独占权限。
容器不会遍历直接文件绑定的主机`tools/`父目录，也不会遍历`config/{product}`之外的主机源父路径；主机Docker/Compose仍需合法访问源。
使用本次修正版生成新包后运行 `preflight --bundle <包> --check-permissions`。检查按UID/GID10001和POSIX模式的属主优先规则判定，
不是检查当前操作者能否读；不推断附加组、ACL或其他内核访问策略。Windows上的权限元数据反例测试与Linux容器实测分别记录，后者仍未运行。

所有四产品的实际启动入口都显式覆盖镜像默认配置。Memory 最终固定版本的实际 factory 仍读取环境变量：
Compose 同时将 `--config` 和 `TIANSHU_MEMORY_CONFIG` 绑定 `/etc/tianshu/settings.json`。
包预检重新计算固定 Compose，路径不一致即拒绝（即使重新计算文件 hash）；真实 factory 的读取行为已隔离截获验证。

`init` **只初始化配置和空目录**。首次数据库初始化另行执行：

1. 固定修复后的产品镜像、检查 Linux 权限和合成启动；不要对已有产品目录运行本包。
2. Memory 的新库由产品命令先 `migrate-profiles` 再 `migrate-sources`，各带独占新备份文件。
   `commands.json` 给出 Compose 命令的完整 argv；只允许已确认全停写的新安装，工具不自动执行。
   实际本地产品 CLI 已在新合成库验证 schema 1→2→3 与 source-guard 创建；容器路径未运行。
3. 平台、陪伴、网关按各自入口创建新库。首次平台 `preflight` 应为 `requires_initialization=true`，不是成功就绪。
4. 由 DEP-D 验证网页登录/CSRF、来源身份、模型与回复链、重启及异常 readiness。日志/恢复另消费 DEP-B/C 的实际证据。

2026-09-22 本组合进一步消费协调已验收的 TS107/108；完整提交与原字节合同 hash 见清单。
Companion 新增七个派生索引（schema 仍9），旧库缺索引时在任何启动 DDL 之前写 `.pre-work-index-<uuid>.bak`；
因此后续更新必须单 owner 停写并预留完整备份空间，不能因为 schema 版本未变就跳过备份判断。
readiness 还要求后台至少成功一轮，失败/停滞会降级；不能只以 socket/health-live 为成功。
本包预检检查本包卷元数据与链接，并保守要求空余空间覆盖四产品日志预算、两倍现有状态大小及256MiB余量；
这只是容量预检，不预分配配额、不证明长期吞吐，也不自动运行上述迁移。

自动长期记忆消费者未完成；本版明确配置 `automatic_memory_candidates=false`，通过 Companion 公开鉴权
`/internal/v1/runtime/capabilities` 核 `disabled/paused/preserve` 和旧候选状态。Memory 空 `event_scopes` 不能单独证明停用。
默认网页入口仍关闭；显式隔离试验可在已审 TS108 固定提交上同时开启清单和平台配置，运行本次真实产品验证。
报告中 `disabled_verified` 只证明停用，`memory_write_proven=false`、`chat_archive_proven=false` 始终分开记录。
Chat Audit 未接入、应用日志无确认回收协议、真实恢复生命周期缺口都保留在清单 blockers。

## 固定源、构建与 Linux 入口

`repos.json` 仅为本地输入，键是 platform/companion/memory/gateway，值是明确只读 Git 仓库路径；不入发布包。

```text
python deploy/tianshu/release.py export-sources --manifest <release.json> --repos <private-repos.json> --contracts-root <contracts> --output <新的构建上下文绝对路径>
python <包>/tools/linux_validate.py --bundle <包> --contexts <固定上下文>
```

导出只读 `git archive <40位commit>`，不使用活动工作树；检查 tar 路径/链接；记录 archive hash、逐文件 hash、Dockerfile 和正式依赖文件原字节。
平台 Dockerfile 的 `COPY contracts` 所需内容由已核 hash 的合同输入补入构建上下文，产品源码/Dockerfile 一字节不改。
`source-inventory.json` 包含可执行构建 argv；Memory 使用 `docker build --file infra/container/Dockerfile`，
Docker 自动采用同目录 `Dockerfile.dockerignore`，**不用不受 Docker 支持的 `--ignorefile`**。

Linux 验证入口默认仅生成 `not_run` 计划。`--execute` 只允许本地 Linux daemon、新 `tianshu-qa-*` 项目、
`isolated_test` 证书、空 data/logs、关闭网页对话、空 providers、无外部模型 targets。它核固定上下文后构建四镜像、
用产品 CLI 初始化本次新合成 Memory 库，再运行 Compose 活性 smoke/平台 preflight，最后只关闭本次新建的 QA 项目，
保留合成数据；只输出退出码，绝不收集可能含输入的构建/服务日志。普通 `init` 不执行这些迁移。
这不是最终版本镜像 digest 证明，也不是四服务功能/模型/24h/NAS 验收。没有环境时 `runtime-check` 明确失败，不安装 Docker。
新续期组合的首次来源引导目前实际接在本地四CLI运行器；旧Linux smoke没有容器内签发及更新私有env/摘要的完整步骤。
不能拿静态占位ref启动开启续期的网关并预期成功。该容器引导接线需在后续Linux任务完成，此处保留构建/权限检查入口而不声称新组合容器启动已可通过。

构建历史与当前范围见 `version-review.md`。TS107/108 已处理各自构建输入，但本任务没有 Linux 镜像构建证据；
Platform/Gateway 构建输入仍需各自产品负责人确认，本号不修改 Dockerfile。

## 对接 DEP-B/C/D

共享清单唯一入口是本目录 schema/example。`host_path` 相对**部署根**，`container_path` 是容器绝对路径。
`mount=true` 才是物理挂载；`mount=false` 的 sidecar/guard 是父状态目录内成员，必须具有同 owner/product/backup_group，
不能再次独立挂载或重复恢复。状态目录备份涵盖**完整子树**，含所有 DB、WAL、SHM、guard、owner 和迁移前备份，文件清单不是排除其余文件的白名单。
Memory guard 与 DB 本版同一物理目录，不宣称独立故障域或旧备份防遗忘恢复已完成。

日志保持独立栈。Schema 1.1 加入五个 `observability_state` 卷，旧 1.0 例子仍可读取。
固定源从清单的根 Git 提交导出到包内工具目录，通过原公开入口生成配置，不修改日志实现：

```text
python deploy/tianshu/release.py configure-observability --bundle <包根> --settings <日志栈私有引用JSON> --root-repository <含固定Git对象的协调仓库> --projects <四产品Git仓库父目录>
python deploy/tianshu/release.py reconcile-logs --bundle <包根> --generated <独立合成实例完整JSONL> --url <显式loopback HTTPS查询地址> --ca <CA文件> --token-file <私有查询凭据文件> --start-ns <采集起点> --end-ns <采集终点> --report <新报告路径>
```

DEP-B 仅读 `logs/{product}`，Vector/审计服务需 10001:10001 读取权限；自己的数据/网络/TLS/凭据/查询端口由其包管理。
日志设置形状沿用固定 `deploy/observability` 公开文档，文件引用相对设置文件目录；本组合要求 `grafana_hostname=logs.internal`。
`--projects` 只读固定 Git 提交并重新生成词表；产品提交有变时不能沿用旧词表。旧日志 CLI 只接收私有 1.0 投影，
权威清单仍是完整 1.1。`observability-release.json` 同时记录两份摘要与固定代码/词表，给出显式 `-p <核心项目>-obs` 的 argv。
启动时必须使用该独立项目名，不使用日志 Compose 文件内与核心相同的默认名称。
五个数据根同样按 10001:10001、0700/0750 准备；配置/code/TLS/secret 目录需10001可遍历/读，不开放other或组写。
`preflight --check-permissions` 包含这些目录；Windows 不能替代 Linux 权限实测。
未取得最终 DEP-B 产物与实际全链路验证前，central_logging=false。禁止用 Loki 保留期代替应用段持久确认/安全回收。

DEP-F 已集成根提交 `543340ac5489545e32772eb28849b4a5c1038eed`，消费完整1.1清单及两个Compose项目；
初始化后的 SQLite/guard/sidecar 仍须登记其私有 recovery-inventory。不得把1.0投影交给恢复器漏掉五卷。
其 lifecycle 默认plan，合成停写/恢复演练不能作为四真实产品恢复、灾难恢复或 restore_drill 发布证据；不授予启动恢复目标资格。
DEP-D 的 `dep-d/1` 报告已有只读核验入口；清单 evidence 是引用层，不等于报告正文 schema，也不自动晋升 `verified`。
报告自述 `static_input_only` 不作为镜像/进程身份实测。任何未识别或不完整证据一律拒绝。

```text
python deploy/tianshu/release.py inspect-acceptance --report <DEP-D报告> --subject-manifest <实际被测试的原始不可变清单>
```

这个命令核 dep-d/1 的内容摘要、文件摘要、全部20用例、产品/合同/功能绑定；输出各状态数量和未满足门槛，始终不提升发布状态。
追加 evidence 会改变清单 hash，所以保存原被测清单，绝不把最终清单 hash 反填旧报告。DEP-D 固定报告的实际适配已验证；
统一 verified 的证据正文封装与独立执行证明仍需协调将五类证据接齐。本包不提供自动 promote 命令。

## 本地验证

```text
python -B -m unittest discover -s tests/deployment/packaging -v
python -B tests/deployment/packaging/verify_compose.py --compose <官方Compose-2.20.1可执行文件>
python -B tests/deployment/packaging/verify_product_entrypoints.py --contexts <固定导出上下文> --contracts-root <原合同> --report <本任务临时报表>
```

单测使用合成合同/凭据/TLS；产品入口检查显式读取固定原产品与真实合同、只运行 help、只读预检、Store 边界截获及新合成库迁移。
这些本地证明与未运行的 Linux 容器检查分别记录于 `verification.json`，不将未运行当跳过成功。
