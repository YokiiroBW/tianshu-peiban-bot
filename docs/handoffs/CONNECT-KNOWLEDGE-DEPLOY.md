# CONNECT-KNOWLEDGE-DEPLOY · 第十个 Knowledge 服务的本地部署准备

## 范围与基线

基线根提交 `2792108471361d9e8707931da8aefcfbe7337fc6`。本任务只改根独立 worktree 的 `ops/resident_capacity/`、`tests/resident_capacity/`、`deploy/observability/monitor.py`、对应定向测试和本交接，未接触 NAS、真实凭据/数据、产品代码、现有发布导出或根共享入口。Knowledge 领域与 HTTP 接口来源为 Memory CONNECT-M 固定提交 `bd0123bc7e242dc5a767347602f23957af9b2c33`；本任务仅读其 CLI、运行绑定与接口文档。

## 已完成：容量守护的白名单扩展

原守护在 `guard.py` 固定核心四服务加观测五服务，并在 `assess`、armed 标记与服务数量中要求恰好九个。直接添加容器会触发 `resident_identity_conflict`，不能把 Knowledge 作为裸服务运行。

新增可选 `service_profile: "knowledge-ten"`，只额外允许核心项目 `tianshu-v2-resident/knowledge`。缺省配置及原九服务标记保持兼容，显式 `resident-nine` 也只允许原九个；不接纳任意服务或通配项目。十服务配置必须精确包含 `images.knowledge` 和全部 `binds.knowledge`，镜像须 digest 固定，Compose project/workdir/file、bind source/target/RW、`unless-stopped`、运行状态与容器 ID 同其他九服务一起核对。`arm` 原子锁十个 ID；`status` 按相同十个 ID 重验；`fail-close` 只对标记中十个精确 ID 禁止重启、读回、SIGTERM、确认退出。容器缺失、替换、额外容器、错误镜像/挂载或容量测量失败仍拒绝；不因名字相同停止替代 ID。部署根预算仍至多 20 GiB，各受检文件系统剩余须至少 20 GiB，不提高阈值。

十容器最坏 Docker 等待与 TERM 轮询为 570 秒，另留 60 秒状态写入/调度，`MAX_FAIL_CLOSE_SECONDS=630`；systemd `TimeoutStopSec` 从 600 调到 700 秒，额外留 70 秒。挂起的内核文件系统操作仍无 Python 截止保证，须以真实 systemd 停止收据为准。九服务版本的代码和标记仍可解析；**在已 armed 的九服务运行中不可直接改配置加第十个**。需按审核过的维护流程停止并读回旧九 ID、保留旧状态目录与收据，再以新的空状态目录对完整十服务新导出 `arm`，启动 unit 后读回 `status=ready`；配置 digest 漂移会锁闭而不会自动纳入新 ID。

本地隔离 `python -m unittest discover -s tests/resident_capacity -v`：21 通过。覆盖原九服务、十服务缺失拒绝、十服务 arm/sample/status、知识容器镜像/挂载错误拒绝、十 ID 全量停机、旧配置拒绝额外知识容器及 700 秒上界。测试使用假 Docker 和临时目录，未运行 NAS systemd/Docker。guard 固定提交 `7e8274821b7b6a3e966b4763f2fa95733c92c3c3`。

另一个独立小提交 `baabeae9bf08e08d56c91f8196d76e11a189ffb0` 修正 `deploy/observability/monitor.py`：第五个 `knowledge` 日志根只接纳已发布事件服务名 `memory-knowledge`，旧 `memory` 根允许 `memory`/`memory-knowledge` 的行为不变。真实诊断格式行正反样例检查 ledger 落地服务和拒绝计数；`test_observability.py` 18 通过、4 因未提供 `DEP_B_CONTRACT` 跳过。这个修正**只**改 ledger 身份匹配，不自行接入第五个 Vector 源或放宽发布校验。

## 总控待接线：同镜像独立进程

| 边界 | 已核事实与所需改动 |
| --- | --- |
| 发布与 Compose | `deploy/tianshu/manifest.py::PRODUCTS`、`configuration.py`、`compose.py`、`resident_export.py` 现只固定 platform/companion/memory/gateway。现有导出会拒绝第五个核心服务，不可手工附加后绕过锁。总控需生成并锁定十服务 Compose/镜像/挂载清单，再将实际 `.Config.Image` 和全部 bind 精确写进 guard 的十服务配置。Knowledge 可用与 Memory **同一固定镜像**，但须独立 `python -m tianshu_memory.knowledge_cli --config ... serve --client ... --port ...` 进程；使用专门的配置、独立 Knowledge SQLite 状态目录、独立 TLS 证书与密钥、独立日志目录，不挂 Memory 的可变聊天 DB。不得重复使用 Memory 聊天配置文件或 `data/memory` 可写挂载。需沿用受约束资源、非 root、只读 rootfs、tmpfs、digest、镜像无 pull/build 的发布规则。 |
| 网络与 TLS | Knowledge CLI 的非 loopback `serve` 必须显式 `--host` IP、`--tls-certfile`、`--tls-keyfile`、`--allowed-host` 精确 authority，内层 Host 检查继承同一绑定。只入核心内部网络，由 Platform 服务端调用；不新增公网上端口。端口、IP、SAN/CA、诊断 token、Knowledge client Bearer 都需真实发布配置与回读，不能在此文档虚构值。浏览器不可直连。 |
| 就绪 | `/health` 仅说明知识入口在监听，不证明数据库已迁移或项目可读。共享运行框架还提供 `/health/live` 与受诊断授权的 `/health/ready`；总控须验证 readiness 报告及固定客户端/配置句柄，再用一个已授权、隔离的真实只读 Knowledge 操作验证 SQLite schema/项目权限/文件可读。新独立库按下文显式顺序建库迁移；请求路径不迁移。 |
| 日志全量 | `deploy/observability/configs.py::PRODUCTS`、`configure.py::manifest_logs`、guard `log_roots/log_budgets`、`runtime_binding.py` 目前恰好四源；Vector sink 只接四个 `_safe` 输入。虽然 Memory 诊断词表已允许 `memory-knowledge` 服务字段，独立 `logs/knowledge` **不会被现有 Vector/guard 收集或计入预算**。现修正 ledger 的 `knowledge` 根服务校验，但总控仍须给新目录独占只读 mount 至 obs-vector 与 obs-guard，增加 `knowledge` file source + 严格 `memory-knowledge` 安全变换 + sink 输入、对应 guard log root 与预算；验证旋转历史 JSONL 与新文件从头至尾被消费，非法行拒绝，ledger 事件数确有增量且 Loki 可按服务查询，回压/容量覆盖新增目录。不能把独立日志目录遗漏在导出或备份外。预算须在既有 20 GiB 根上限内重新实算。 |
| 备份与恢复 | `ops/recovery/manifest.py` 固定四产品/四状态和四日志，`product_inventory.py` 要求四状态根；原恢复路径不能覆盖新的 Knowledge DB、WAL、配置和日志。本轮总控拟采用整个 R 树的停写冷备，须核完整 Knowledge DB/WAL/source guard/配置/日志的字节、所有权、恢复读回与新副本隔离；不能借旧四服务清单宣称已覆盖。后续若改走现有恢复工具，必须先扩展合同、库存与验收。不得共享 Memory 聊天 DB 来套用旧备份。Knowledge checkout 引用也必须保持服务端登记的别名与已授权项目，恢复时不能用旧快照复活后来撤销的权限或冒充当前 Git 状态。 |
| 容量守护与启停 | 完整十服务导出/观测五服务、全部 bind 和两个 Compose label 必须与新 guard 配置逐项一致。总控在受控启停序列中安装新 `TimeoutStopSec=700s` unit，以新 state generation arm 十服务并读回 heartbeat、`status.services=10`、容量阈值，再做真实异常退出、容量越界和十 ID 停机读回；拒绝/未确认时不得继续服务。Dockge 自动重建仍须关闭。 |

## Knowledge 独立新库的确定顺序

以下命令结构来自当前 Memory 的 `Store`、`source_migration` 与 `knowledge_migration`。只在**新且独占** Knowledge 状态卷、停写条件下运行；每个 `--backup` 指向不同的、此前不存在的本地文件，备份与 source guard 一并纳入新部署冷备。实际路径/项目 ID/凭据由总控的真实私有配置决定，本文不代填。新库不能跳过 schema 1→2→3：Knowledge 主迁移要求 `schema=3` 且此前没有 `knowledge_schema`。`Store` 在首条显式 CLI 操作中建 schema 1，之后：

```text
python -m tianshu_memory.cli --config <独立knowledge配置> migrate-profiles --backup <未存在的backup-1>
python -m tianshu_memory.cli --config <同一配置> migrate-sources --backup <未存在的backup-2>
python -m tianshu_memory.knowledge_cli --config <同一配置> migrate --backup <未存在的backup-3>
```

第二步必须能从 `contract_directory` 找到并验证已发布 `text-dialogue/v1`、`profile-memory/v1` 和 `source-sync/v1`；第三步一次安装 Knowledge、Lessons、Directory、Research Notes、Catalog 索引与版本标记，**新库不再单独重复运行** `migrate-lessons` / `migrate-directories` / `migrate-research-notes` / `migrate-catalog`。迁移后核对 DB、WAL、`.source-guard.json` 与元数据 `schema=3`、`knowledge_schema=1`、`lessons_schema=1`、`knowledge_directories_schema=1`、`research_notes_schema=1`、`knowledge_catalog_schema=1`，再启动服务。不要把任何旧 Memory 聊天库复制进此卷充当新库；也不要删除或重建 guard 绕过检查点。

独立私有配置的最小结构为：`database_path` 指向 Knowledge 自己的 SQLite；`source_sync.recovery_path` 指向同卷配套 source guard；`contract_directory` 指向发布的 `text-dialogue/v1`；`log_directory` 指向独立、可写、持久且受限的 Knowledge JSONL 目录；`diagnostics.token_env` 命名**单独**就绪凭据变量；`knowledge.projects.<真实项目ID>` 精确登记现有绝对 `root`、`host`、`default_branch`、`urls` 四项，项目根必须存在并让 Knowledge 容器以审核过的只读 bind 看见。容量 guard 只接受 R 部署根内的 bind source，因此外部项目路径不能临时挂载绕过守护；若真实项目根尚不可用，就没有可验的真实项目查询。`knowledge.clients.<固定服务端client>` 提供独立 Bearer 的 SHA-256 摘要、实际项目 ID 列表和逐操作权限。日志和探针凭据与业务 Bearer 相互独立。若此阶段只有错题检索，权限和 `http_read_operations` 均只给 `lesson_query`；经验检索还需同名 `experience_query` **及独立 `review`** 权限，并在 HTTP 额外授权中列 `experience_query`。没有已登记 checkout 时不写 `knowledge.worktrees`，也不授 `continuation_recover/check` 或对应 HTTP 额外授权；不得接受浏览器路径代替登记别名。当前同镜像 Dockerfile 没有安装 Git，若以后启用 continuation，还需单独验证可执行 Git 与受限 checkout，不得把 `git_unavailable` 当成功。旧十二 HTTP 操作仍按原规则授权，Platform 连接器不可把全操作代理给网页。

由新进程显式启动 `python -m tianshu_memory.knowledge_cli --config <同一配置> serve --client <上述固定client> --port <审核端口> --host <容器IP> --tls-certfile <Knowledge证书> --tls-keyfile <Knowledge私钥> --allowed-host <精确DNS名:端口> --diagnostics-contract <发布的diagnostics/v1>`。TLS 证书 SAN 必须覆盖该 authority，Platform 使用其 CA 与独立 Bearer。容器 `TIANSHU_HEALTHCHECK_HOST` 设置为相同 `DNS名:端口`，`TIANSHU_HEALTHCHECK_CA` 指向信任锚；当前镜像的 `python /opt/tianshu/container_healthcheck.py` 仅探 `/health/live`，不可代替 ready 或业务读回。

容器内就绪探针可用下列**无明文参数**读回，前提是上述 host/CA 与 `diagnostics.token_env` 所命名的变量已在受信环境设置。此处用默认变量名；若配置改名，脚本也应改为读取那个名字。`remote=not_verified` 是代码如实报告的未测远端，不应伪装成 `ok`。当前 `runtime_probes.BLOCKING_STATES` 对 `extensions:not_configured` 允许 HTTP ready，因此验收必须额外要求 `checks.extensions == ok`，不能只看 200：

```sh
python - <<'PY'
import json, os, ssl, urllib.request
host = os.environ['TIANSHU_HEALTHCHECK_HOST']
ca = os.environ['TIANSHU_HEALTHCHECK_CA']
token = os.environ['TIANSHU_DIAGNOSTICS_TOKEN']
request = urllib.request.Request('https://' + host + '/health/ready', headers={'Authorization': 'Bearer ' + token})
with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=ca), timeout=5) as response:
    assert response.status == 200
    body = json.load(response)
assert body['status'] == 'ready' and body['service'] == 'memory-knowledge'
assert all(body['checks'][key] == 'ok' for key in ('configuration', 'contract', 'database', 'client', 'extensions', 'log', 'assembled', 'owner'))
assert body['checks']['remote'] == 'not_verified'
print('knowledge readiness verified')
PY
```

最后需由 Platform 服务端携带该固定 client 的独立 Bearer 调 `POST /local/v1/project-knowledge/action`，使用真实已授权项目与 `lesson_query` 或 `experience_query` 的有限文本检索，检查响应、权限拒绝、当前引用和日志事件。**新空库没有项目内容**：只读操作可能返回 `project_uninitialized`，不能把它改写成通过，更不能为了验收造一条假课程/经验。实际项目注册与内容写入须走领域已审查的显式真实操作，未完成前网页展示真实空态/不可用态。只有真实已登记 checkout 和用户明示“读取交接”时才验 continuation，并检查完整包不流向网页。

## 阻断与交付

当前根发布、观测和恢复工具尚未扩为十服务，因此 **guard 本地通过不代表 NAS 可部署**。总控需先完成表中独占接线、固定 SHA 与隔离验证，联同 Platform-B 实际知识读回和独立审查再作安装决策。部署、迁移生产数据、操作真实设备均不在本任务范围。
