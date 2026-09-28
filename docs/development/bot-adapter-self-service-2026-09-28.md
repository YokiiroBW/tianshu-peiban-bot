# 网页机器人适配器：本轮执行边界

用户要求：在网页选择平台、填写地址，使用内置默认协议连接机器人插件；不要用户编辑配置文件。沿用现有设置子页面与视觉。用户仍未指定真实群/联系人，不发送真实机器人测试消息。

## 产品流程

设置 → 机器人接入 → 添加适配器 → AstrBot / NoneBot → 插件地址、插件连接密钥 → 检测连接并读取实际机器人账号 → 选择账号、角色及允许的会话/作者 → 保存 → 启用。协议固定内置，协议名不是必填项；不让用户填 slot、binding、source、Core 地址或服务 token。提供清晰的未安装插件/地址不可达/密钥错误/版本不兼容提示。没有实际 host discovery 不显示已连接。

地址是插件连接端点，不是通用聊天 API；自动拼接固定 RPC 路径。插件的安装与一次连接密钥获取是必要前提，平台/核心内部登记由程序完成。首轮 QQ 纯文本，媒体/TG/其他智能体不在本轮。旧已配置 pull 插件能力保留，但同一连接只能运行一种 transport，不能双重发送。

## 架构决策

天枢主动调用插件固定 RPC，插件不再要求用户填写天枢内部 HTTPS/CA/服务身份。平台保存独立的适配器配置，凭据使用服务端加密私有存储；不把密钥写入日志、前端回显或 Git。连接探测、配置持久化、平台/Core 自动绑定、后台传输各自独立模块；网页只调用管理 API，不访问 Docker、不编辑部署 JSON。

复用原 Platform Bots 的入站去重、唯一回复者、send/claim/ACK 台账和 Core PlatformBotSender；插件端的 SDK 发送仍先持久 intent，再调用 SDK 一次。平台 worker 读取插件队列，提交既有入站管道；取得本地回复 claim 后调用插件 send/status，再 ACK。本轮不得建立绕过 Sources/Origin/Memory 权限的聊天捷径，不把无法判断是否发送的结果直接重发。

平台与 Core 的动态注册要持久、限于机器人命名空间且与静态配置无冲突。平台只接受管理员解锁后的显式对象；从可信角色模板构造最小 Sources/Origin entries，不能允许浏览器上传任意 principal/entry。Core 通过已认证的平台管理端口接收有限 bot binding，验证真实已配置角色；不得读对方数据库。创建/启停是有版本和幂等键的过程，步骤不明或失败则保持不可收发；所有参与方一致确认后才能显示 enabled。重启恢复状态，不要求改文件或重启容器。部署初次启用管理能力由总控统一打包，不由用户手工接线。

## 插件 RPC v1（P/H 共同基线）

统一 JSON POST 路径前缀 `/tianshu/adapter/v1`，`Authorization: Bearer <插件连接密钥>`，固定协议 `tianshu.bot-adapter/v1`；禁用重定向，限响应/请求大小、并发、超时。首轮安全边界复用现有受控外部连接：HTTPS 默认保留证书验证；局域网 HTTP 需显式允许且仅私有/loopback 合法目标，禁止公共 HTTP、metadata/link-local、URL userinfo、危险协议与任意重定向。DNS/IP 在连接时核验；浏览器永不直连插件。

- `/capabilities`：请求 `{}`；返回 `{protocol,adapter,instance_id,accounts:[{id,platform:"qq",label}],capabilities:["text"],max_outbound_utf8_bytes:32768}`。账号必须来自实际 SDK，离线时列表可空并准确提示；不能从用户输入伪造 SDK 在线。
- `/bindings/apply`：请求 `{request_id,connection_id,revision,account_id,conversation:{kind:"group"|"private",id},allowed_authors:[string],enabled}`；返回 `{connection_id,revision,enabled}`。原子持久、同 request/语义幂等、冲突拒绝、低版本拒绝。先保存 disabled；启用时校验目标账号和白名单。`account_id` 是机器人 self_id；作者是独立白名单，不允许隐含所有群。
- `/bindings/status`：请求 `{connection_id}`；返回 `{found,binding:null|{connection_id,revision,enabled}}`，用于管理写入结果不明的恢复。
- `/events/poll`：请求 `{connection_id,limit}`（1–20）；返回 `{events:[{id,event}]}`，`id` 是本连接持久队列 ID，`event` 使用既有 Platform Bots event_request（平台 ID 为 capabilities.instance_id，真实 SDK event/self/author/时间，namespace qq，null thread）；未 ACK 会重复读出，不能破坏原身份。只有显式启用、目标会话/作者白名单和持久入队成功才接管消息。
- `/events/ack`：请求 `{connection_id,event_ids:[id]}`；返回 `{acknowledged:[id]}`。平台确认原入站台账结果后再清队列；未知结果先 status，不创造新 event ID。ACK 幂等且不可确认别的连接事件。
- `/messages/send`：请求 `{connection_id,delivery}`，delivery 复用 bot-connection v1（reply_id/attempt_id/namespace/conversation_id/thread_id/text/turn_id/segment_sequence）；返回 `{reply_id,attempt_id,state:"sent"|"failed"|"unknown",channel_message_ids:[string]}`。同 connection/reply/attempt 语义重放仅返回 journal，不重复 SDK。已知发送前拒绝为 failed；SDK 已调用但结果不明为 unknown；sent 只接受真实 SDK ID。
- `/messages/status`：请求 `{connection_id,reply_id,attempt_id}`；返回 `{found,receipt:null|上述回执}`。不存在也不能由网络不明自动推断可安全重发。入站 <=8000 字符；出站 <=32768 UTF-8 字节，超界且未调用 SDK 为 failed；不私自拆成多次发送。

错误使用稳定 `{code,retryable}`，HTTP 400 invalid_input / 401 unauthorized / 403 forbidden / 404 not_found / 409 version_conflict或idempotency_conflict / 429 busy / 503 dependency_unavailable；不回显地址中凭据、token、异常栈和聊天正文。实际宿主挂载路径差异由对应插件处理，平台不能猜通用管理接口能收此协议。

## 网页 API（P/U 共同基线）

前缀 `/api/web/bot-adapters`，沿用真实 Cookie/CSRF/Origin 与现有 bot.manage 管理解锁，不新建浏览器认证体系。API 路径和字段如需更改必须总控同步双方，不各自猜。

- `/view {}` → `{available,unlocked,actors:[{id,label}],connections:[{id,name,adapter,address,account_id,conversation:{kind,id},allowed_authors,actor_id,enabled,revision,state,last_error,last_checked_at}]}`。state 仅 draft/disabled/ready/degraded/unknown；ready 表示配置、插件与Core确认，仍不等于真实消息发送验收。
- 解锁沿用 `/api/web/bots/unlock {password}`，再刷新 view；权限拒绝/未启用能力要清晰显示。
- `/probe {adapter,address,access_key,allow_private_http,ca_pem}` → `{draft_id,expires_at,protocol,instance_id,accounts:[{id,platform,label}]}`；密钥服务端保存短时加密草稿，不回传。ca_pem可null，UI高级项；仅成功真实探测才返回草稿。草稿绑定当前管理员会话并有限时，不用硬编码账号。
- `/create {draft_id,name,account_id,conversation:{kind,id},allowed_authors:[string],actor_id,client_id}` → `{connection:<view的连接项>}`。内部槽位/绑定/身份由服务端创建并核对，默认 disabled。私聊可用选定目标作为显式作者；群必须明确作者白名单。
- `/enable` 与 `/disable {id,expected_revision,client_id}` → `{connection:<view连接项>}`。结果不明显示 unknown/待恢复，不谎报成功，不自动重发写入。

### 联合审查补充：未决阶段的显式恢复

- `/reconcile {id,expected_revision,client_id}` → `{connection:<view连接项>}`，仅用于 `state=unknown` 且有服务端持久 pending 的连接。网页提供“核对并恢复”，不让浏览器选择或提交恢复目标。
- pending 固定保存原 `desired`、`request_id`，连接保留原 `revision`。先只读查询 Core/插件状态；两端已与原修订和目标一致则提交本地确认，否则仅以原 request_id/revision/语义幂等补齐配置阶段，不新建阶段、不发送消息。恢复原启用操作可能恢复后续正常消息处理，网页须明确说明。
- 恢复失败仍返回 unknown 和 last_error；旧修订或无 pending 返回 409 version_conflict。同 client_id 重放只读当前结果。create/enable/disable 不接续未决阶段，返回 409 result_unknown；view 只做远端状态查询与本地确认，不重放远端 apply。
- UI 对未知阶段保留普通启停禁用，单独提供恢复按钮；请求不自动重试，返回后核对 view。按钮仅对服务端 unknown 显示；仅前端读回不一致标记先要求刷新核对，不猜后台存在 pending。

## 并行写入责任

- ADAPTER-P（Sol/xhigh）：平台后台/API/worker/凭据目录、Core 动态 bot binding，两个专属产品 worktree，独占 services/platform、companion src、后端测试与交接。不得改插件/frontend。P需先把少量必要接口差异反馈总控。
- ADAPTER-H（Sol/xhigh）：两宿主插件 RPC、生命周期挂载、持久队列与SDK桥接、打包/安装说明；仅 companion integrations、插件测试、交接，不改Core src/pyproject/公共锁。共同代码放 integrations 下自包含包并提供明确打包方式，不能运行时依赖另一个未安装插件目录。实际 AstrBot4.27.3/NoneBot2.5.0 与 OneBot2.4.0 API 必须查源码，不猜框架钩子。
- ADAPTER-U（Sol/xhigh）：机器人独立页真实向导，按上方 API 实现；仅前端机器人模块/必要局部CSS/API与浏览器测试/交接。使用实际服务错误、表单校验、明确保存未启用与发送未验，不接入小屋/家庭/模型页面。
- 总控：合同与边界、精确整合、联测、NAS部署/旧插件兼容验证。新增依赖/安装端口/宿主挂载不能擅自生产操作。

## 必须验收

空安装到网页添加成功全过程不编辑配置文件；实际插件地址探测/错误凭据/错误协议；管理员创建后 Platform/Core/插件恢复一致；重启保留；限定会话/作者；重复事件/重复send/未知SDK结果不二次发送；断线与半成功恢复；停用后不新增收发；原网页聊天与旧机器人合同不回归。插件同宿主API注册/卸载与NoneBot真实driver挂载必须使用真实框架验证，不只模拟函数。初轮实机不发真实消息，真实对象待用户指定。成功 mock 不称真实SDK/NAS完成。

本文件是本轮草案执行合同，不是已发布、已实现或已部署的完成声明。正式schema与双方样例由总控在固定候选后归档。
