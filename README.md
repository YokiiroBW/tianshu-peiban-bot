# 天枢

统一产品代码在 `src/tianshu/`，网页在 `web/`，部署材料在 `deploy/`。
后端使用 Python 3.12+、aiohttp、原生 PostgreSQL/pgvector 和一个 Procrastinate。
业务领域直接调用明确端口；实际供应商容量由唯一 Model Broker 拥有。

本轮施工与各领域交付状态见 [执行说明](docs/development/coherent-refactor-execution-2026-10-31.md)
和 [CR01–03 交接](docs/handoffs/CR01-03.md)。开发提交的 `implemented_not_tested` 不代表已经验收或部署。

## 后端安装和启动

依赖由根 `pyproject.toml`/`uv.lock` 统一管理：

```powershell
uv sync --locked --extra dev --python 3.12
uv run --no-sync tianshu migrate --config C:/Tianshu/settings.json
uv run --no-sync tianshu model-broker --config C:/Tianshu/settings.json
uv run --no-sync tianshu worker --config C:/Tianshu/settings.json
uv run --no-sync tianshu interactive --config C:/Tianshu/settings.json
```

后三条是三个独立的进程角色。Linux 单容器使用 `tianshu serve --config /config/settings.json`
由 PID 1 监督器完成迁移、启动、排空和停止；镜像内还保留完整 Vector/Loki 观测与受管插件子进程。
当前两宿主的构建、独立 PG/vector、新容量守护与备份恢复见 [部署说明](deploy/core/README.md)。
手动启动三个角色时，首次安装先运行一次 `migrate`；
它在天枢专属空 database 安装原生基线、vector 扩展和固定版本 Procrastinate schema，不导入旧库。
PG 实例必须已安装 pgvector 二进制；缺少扩展时安装明确失败，不回退到 JSON/字节向量。

凭据使用环境引用。设置 `TIANSHU_DATABASE_URL`、`TIANSHU_BROKER_TOKEN` 和 `TIANSHU_MASTER_KEY`；
master key 是部署生成并保管的 32 字节随机密钥的 base64url 编码。供应商 API Key 可在模型设置中只写保存，
数据库仅保存 AES-GCM 密封值；读取配置只返回引用。部署环境的供应商 Key 只允许
`broker.provider_credential_refs` 显式配置的 provider 专属 `TIANSHU_PROVIDER_*` 引用。
不要把实际密钥写进配置样例、命令行参数或 Git。

配置最小结构：

```json
{
  "schema_version": "tianshu.core.v1",
  "database": {
    "dsn_ref": "TIANSHU_DATABASE_URL",
    "connection_budget": 72,
    "roles": {
      "interactive": {"max_connections": 4, "control_connections": 2, "sync_connections": 2},
      "model-broker": {"max_connections": 4, "control_connections": 2, "sync_connections": 1},
      "worker": {"max_connections": 4, "control_connections": 2, "sync_connections": 2}
    }
  },
  "http": {"host": "127.0.0.1", "port": 8070, "max_body_bytes": 536870912},
  "broker": {"host": "127.0.0.1", "port": 8071, "url": "http://127.0.0.1:8071", "token_ref": "TIANSHU_BROKER_TOKEN",
    "transport": {"foreground_connections": 8, "due_connections": 4, "background_connections": 24, "control_connections": 4}},
  "worker": {"queue_connections": 8, "lanes": {}},
  "artifacts": "data/artifacts",
  "credentials": {"master_key_ref": "TIANSHU_MASTER_KEY"},
  "identity": {"installation_seal": "C:/Tianshu/data/state/installed", "setup_token_ref": "TIANSHU_SETUP_TOKEN"},
  "channels": {"owner_url": "http://127.0.0.1:8070", "owner_token_ref": "TIANSHU_CHANNEL_OWNER_TOKEN"},
  "capabilities": {},
  "observability": {}
}
```

领域由 `runtime/product.py` 直接构造、注册，配置不能动态导入旧产品或空模块。
`worker.lanes` 未覆盖的业务族使用固定默认值：35 个 lane、40 个执行槽，共享 8 个队列 SQL 连接，另有 35 个原生监听连接。
默认数据库角色池加监听、队列池和 Broker owner 最多需要 68 条连接，配置预算为 72；HTTP 传输池不计入 PG 连接。
完整 lane 配置见 `deploy/core/settings.example.json`；不再使用旧三类队列并发配置。模型服务优先级与执行 lane 分别选择。
`migrate` 从唯一总注册安装所有领域 schema，之后才启动任一角色；不在领域启动过程中临时迁移。
设置 `TIANSHU_SETUP_TOKEN` 和 `TIANSHU_CHANNEL_OWNER_TOKEN` 两个独立随机值；前者保护首次创建管理员，
后者只供固定回环频道 owner RPC，不代替网页会话鉴权。
数据库总预算包括各角色 async/control/sync 池、队列连接和 Broker 所有者连接。
此外包含三个实际 Procrastinate 消费者各自的 LISTEN 连接。interactive/due/elastic 消费份额分别有界；
长下载、听书和生成进入 elastic，短前台工具进入 interactive，due 仅处理实际到期工作。

## 公共端口

SnowLuma 能力的内部调用、插件声明、参数目录和回执处理见
[SnowLuma 内部接口文档](docs/development/snowluma-native-interfaces.md)。

公开业务路由统一 `/api/v1/`，写入使用 `client_request_id` 和对象 `expected_revision`，返回 CommandResult。
读取分页使用 Page。未知状态保持 null，写请求不会因网络超时自动重发。
同源 Cookie/CSRF、身份与来源策略由 identity/sources 注册，领域通过真实 handler 注册自己的按钮操作。

模型设置入口是 `/api/v1/models/catalog`、providers、routes、quota-groups、bindings、embedding-profile；
`/api/v1/models/routes/{route_id}/probe` 仅在用户显式测试时发出真实文本/流式验证请求。
每条实际路线关联真实共享 `quota_group`，前后台默认明确配置，不根据模型名称推断独立配额或模态。
OpenCode 的精确 HTTPS Zen 端点保留其既有固定 User-Agent 与不透明会话头，生成和目录枚举共用
同一传输规则；会话身份只由可信场景/原意图派生，不接受任意用户 HTTP 头。
Embedding 使用一个固定的 1024 维 profile，模型/版本/归一化/指令及路线指纹一起固定。

Broker 进程提供原生 Chat Completions、Responses、Claude Messages、Gemini generate/stream 和 embeddings 入口。
对外 Key、模型别名和允许路线在 `broker.external_keys` 明确登记；不能用交互 Cookie 代替外部 API Key。
原生续轮、工具关联、思考签名、SSE 与 usage 缺失语义保留，不做跨协议透传转换。

长音视频使用 FileStore 的流式写入/读取和范围接口。2 MiB 上限只适用于命令 JSON；
媒体上传由领域流式路由和实际部署上传上限控制，不把完整视频读入前台内存。

## 运行观测与验证

三个角色把完整注册事件写入各自持久 JSONL 文件，记录安全路由模板、方法、响应码、任务/attempt 和耗时。
无正文、密钥或任意异常文本。Vector 负责检查点、磁盘缓冲及 Loki 补传；日志文件不由业务自动删除。
新事件 schema 为 2.0.0，最终部署必须同步 Vector 的角色/字段规则，不能直接交给旧版 1.0.0 parser。
`RuntimeDiagnostics` 从真实 Procrastinate、Broker 和 Loki 读取状态，不创建第二个日志数据库。

`/health/live` 只表示进程在运行；交互 `/health/ready` 综合真实 Broker/worker 心跳与领域/任务注册。
控制取消独立于模型长流，unknown attempt 重启后保留身份和保守容量，不自动重放。

依用户本批要求，完整集成前不运行测试、typecheck、build 或浏览器检查。集中验收使用根清单中的新领域测试目录，
不把历史协调测试混入新产品。PG 用例只接受显式 `TIANSHU_TEST_POSTGRES_ADMIN_DSN`，
自行创建并删除 `tianshu_test_*` 隔离数据库，不使用生产默认值。

访问设置由 `/api/v1/access/view`、`access/save` 管理实际 HTTP/HTTPS/反向代理外部地址和已安装证书，
保存使用 CAS 与实时权限复核，下一次 interactive 启动后生效。HTTPS 的证书/私钥/域名离线核验，
外部 HTTPS 决定安全 Cookie；网页清楚区分当前生效与待重启设置。

项目 CLI 与 MCP stdio 复用当前 Identity 会话及同一项目 HTTP 用例，官方 MCP SDK 已进入根锁文件。
用户显式提供 owner-only 会话文件（实际 Cookie/CSRF JSON，不入库），例如只读项目目录与 MCP：

```sh
python -m tianshu.capabilities.projects.cli --server http://192.168.31.230:18446 --session-file /PRIVATE/session.json --actor ACTOR_ID view
python -m tianshu.capabilities.projects.cli --server http://192.168.31.230:18446 --session-file /PRIVATE/session.json --actor ACTOR_ID --mcp
```

资料写入按其真实项目会话重新核授权，不用独立 project-token、本地数据库或旧 Core。
