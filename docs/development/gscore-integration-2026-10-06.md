# GSCore 接入核查（实机只读）

结论：该实例拥有可复用的普通攻略/配队命令，但当前 HTTP 与 MCP 均未开启；现役 HTTP 实现也存在队列消费者和完整输出终态缺口。Companion 可先实现语义参数到白名单命令的 HTTP 协议适配，现场状态必须保持未联通。不能以“开启 ENABLE_HTTP”代替后续修复与实机验收。本轮未改 GSCore、未执行插件。

## 精确实例与版本

用户入口 `http://192.168.31.210:28765/app/#/login` 对应容器 `gsuid_core`，28765→8765/tcp，配置引用镜像 `docker.cnb.cool/gscore-mirror/gsuid_core:latest`。实际 image ID `sha256:8f0dde4bab13beaf9264215dd4aa35cd7db12f448993cad1ad8d4dccfa8dd0ae`，core git HEAD `87c06f11ae10c12b3bb8e76b3c6f420c831282a8`。`gsuid_core/version.py` 运行版常量为 0.10.7，pyproject版本为 0.11.0；镜像 OCI 标签描述继承的 uv 基础镜像，不能当 GSCore 版本。

八个磁盘插件为 XutheringWavesUID、RoverSign、ScoreEcho、StarRailUID、NTEUID、ZZZeroUID、DeltaUID、EndUID，精确 SHA 见本机协调根目录 `.runtime/skills-v1-20261005/gscore-adapter-evidence.json`。只读选定插件配置显示 enabled=true；下表攻略 SV 为 enabled/pm6/ALL。`/api/plugins/list` 匿名 GET 401，所以未声称完成已加载 runtime registry 验证。

## 当前开关及目录能力

| 项目 | 当前实读 | 只读 HTTP 证据 |
| --- | --- | --- |
| `ENABLE_HTTP` | false | `/api/send_msg` GET 404 |
| AI 总开关 `enable` | false | `/api/ai/tools/list` GET 404 |
| `enable_mcp_server` | false | `/api/mcp` 与 `/api/mcp/` GET 404 |
| MCP path/transport | `/api/mcp` / 保存 sse；源码归一 HTTP | `/mcp` 404不能单独判定MCP |
| `WS_TOKEN` | 已配置，仅核对存在，值未读出 | 用途见下 |
| MCP静态key | 空 | 不能据此假定接口开放 |
| 全局命令起始 | `command_start=[]`、`enable_empty_start=true` | 下表无需额外 `#` 或 `/` |

代码支持 `GET /api/plugins/list` 的 require_auth目录、`GET /api/plugins/{name}` 的require_admin_header详情（详情含配置和权限，不宜整体读取）。`to_ai`/native `ai_tools` 与标准 FastMCP `tools/list` JSON Schema、`tools/call` 在源码存在，当前 AI/MCP 关闭；不启 GSCore 自带第二层推理来接普通命令。当前安装 ScoreEcho（611addb...）21个Python文件定向查找无FastAPI/APIRouter、HTTP/MCP注册或公开工具入口，不能凭MCP注释认定该插件提供可用结构化API。

## HTTP协议候选

声明接口 `POST http://192.168.31.210:28765/api/send_msg`（core.py:213）。鉴权为 `X-WS-Token`，或 `Authorization: Bearer`，均匹配现有 WS_TOKEN。其权威来源为 NAS `/volume2/Dockers/qqbot/gscore/data/config.json` 的该单字段；未来由受信部署端选取该字段纳入 Companion 专属 secret/env 引用，不回显值、不把整个 GSCore config 挂给产品、不伪造网页登录session。

```json
{"bot_id":"TianshuSkills","bot_self_id":"","msg_id":"<call UUID>","user_type":"direct","group_id":null,"user_id":"<confirmed caller/service identity>","sender":{},"user_pm":6,"content":[{"type":"text","data":"ww长离攻略"}]}
```

`MessageReceive` 精确定义在 models.py:194。user_pm会由 handler.py:382 的 get_user_pml重新计算，不能靠自报管理员跳过插件权限。使用确认过的调用身份，图片/攻略权限与账户UID查询、签到、绑定、管理指令分开；本轮白名单只含下表。模型输入只暴露 game、character、operation 等语义参数，不提供 raw_command。

声明返回为 `{"status_code":200,"data":MessageSend}`；MessageSend 包含 bot_id/bot_self_id/msg_id/target_type/target_id/content/echo（models.py:293），保留请求 msg_id供调用关联。无匹配或拒绝等也可能返回 `{"status_code":-100,"data":null}`，鉴权失败401。text/markdown的data是文本；image.data通常 `base64://<标准base64>`，也支持 `link://<URL>` 或 `file://<适配器本机路径>`（segment.py:94）。file://不能假设是Companion可读NAS路径。结果产物进入Companion现有发送链路；GSCore文本“已发送”不证明QQ送达。

## 白名单映射

| 语义操作 | 插件 | 服务端合成命令 | 源码 |
| --- | --- | --- | --- |
| 鸣潮角色攻略 | XutheringWavesUID | `ww{character}攻略` | wutheringwaves_wiki/__init__.py:144 |
| 鸣潮矩阵队伍统计 | XutheringWavesUID | `ww矩阵高分队`，可附空格+角色 | wutheringwaves_query/__init__.py:126 |
| 异环角色攻略 | NTEUID | `nte{character}攻略` | nte_guide/__init__.py:11 |
| 异环角色配队 | NTEUID | `nte{character}配队` | nte_team/__init__.py:11 |
| 终末地角色攻略/攻略中配队 | EndUID | `end{character}攻略` | end_guide/__init__.py:13 |
| 星铁角色攻略 | StarRailUID | `sr角色攻略{character}` | starrailuid_wiki/__init__.py:48 |
| 绝区零角色攻略 | ZZZeroUID | `zzz{character}攻略` | zzzerouid_wiki/__init__.py:431 |

这里鸣潮配队是本期矩阵出场统计/热门高分队，不是任意个性化配队求解器；其它游戏独立配队API未核实。星铁攻略缺图时只写日志、不发响应；缺输出必须如实保留。

## 必须先处理的 HTTP 缺口

1. core.py:211 创建独立 `_Bot("HTTP")`；bot.py:113构造只初始化 PriorityQueue，不启动 `_process`。handler.py:629入队，631进入wait_task。核心源码唯一Bot._process消费者启动在core.py:184的WS局部process()，不消费该HTTP实例；没有发现HTTP startuphook补启动。
2. bot.py:518覆盖 `send_dict[task_id]`，519每次发送就设置task_event；wait_task（655—663）20秒等待首个send后取一个MessageSend，并非handler执行完成；多帧会覆盖。不能宣称完整攻略/全部图片/完成终态。
3. sv.py:38 modify_func对异常只日志并吞掉，无结构化失败终态。`-100/null`、超时、500与空产物不能归completed；200文本可能是“暂无攻略”，也不能当查到图。

隔离fixture从冻结源码AST仅提取_Bot构造/wait_task，缩短唯一20秒超时到0.02秒，未导入GSCore/插件，无网络。结果确认queue_size=1、auto_started_consumer=false、无send时TimeoutError。源码全核心引用、ScoreEcho检索结果见 `gscore-route-reference-evidence.json`；fixture见 `verify-http-queue-isolated.py` 与 `http-queue-isolated-result.json`。

恢复部署后的最小处理是：在GSCore拥有方修复HTTP消费者生命周期、调用终态和全部输出收集/错误报告，再按已有WS_TOKEN配置受信服务引用；保持AI/MCP关闭即可查询普通命令。之后只验专属身份下的目录可见性、白名单攻略与配队的真实text/base64产物、同msg_id关联、无图/异常/超时，不发送QQ。未获恢复授权前不做上述变更与真实调用。

## 验证边界

复用旧task/nas_release_access.py严格hostkey SSH，仅精确容器docker inspect、stdlib读取源与选定安全配置字段、匿名GET；无插件import、生产DB/聊天/日志读取、凭据值输出、真实模型/GPU/QQ、NAS写入或重启。冻结源码 SHA256 在 `gscore-source/source-index.json`。当前没有真实攻略返回或HTTP可用性验收通过记录，交付的是可实现协议与真实阻点。

文中未附前缀的源代码路径均指冻结的 GSCore 容器源码；JSON、fixture 与源码索引证据保存在本机协调根目录 `.runtime/skills-v1-20261005/`，不作为产品配置发布。
