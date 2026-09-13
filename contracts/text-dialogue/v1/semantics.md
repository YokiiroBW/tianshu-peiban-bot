# TS-001 文字链路边界

本文件与同目录 schema/实例共同构成候选；JSON Schema 负责形状，以下约束及 `validate.py` 负责字段关系与合成轨迹。实现者仍须执行授权、事务、超时及恢复。本文路径是拟议内部 HTTP 路径；尚无运行端点。

## 1. 传输、来源、版本、幂等

内部 JSON 操作通过 TLS 与每调用服务独立的 Bearer 服务凭据认证（凭据仅服务端持有、可撤销；本地夹具不需要真实凭据）。浏览器只调用平台同源 API，使用已登录会话和 CSRF 防护；平台重新构建内部请求。模型原生正文不套业务信封。

`command` 带版本、request_id、origin.assertion_ref、幂等键和 UTC `deadline_at`；query 不发起写入。变更现有对象还带 `expected_version`。ID 是不透明、区分大小写的字符串；人、角色、管理员、会话、轮次各自编号，不能相互替代。请求 ID 用于关联，不能替代幂等键。

`assertion_ref` 是服务端产生的短期来源引用，不是用户授权声明。接收者依认证服务固定的 issuer 配置，通过该 issuer 的 `POST /internal/v1/origins/resolve` 解析（common.origin_resolve_*）；URL 不取自 payload。issuer 校验调用方和引用的 audience、有效期、撤销状态，返回已验证账号、可选 principal 与 scope。接收者与自己的当前授权求交集，核对命令目标；引用过期/无权/无法解析一律拒绝。NoneBot issuer 必须来自已登记绑定的真实入站事件；platform issuer 来自当前登录会话。账号、person_id、actor_id 或 scope 在普通 payload 中出现都不授予权限。网页身份是服务端签发的稳定 web 账号，不能用昵称当 ID。

凭据只认证服务，**不能单凭平台服务账号扩大最终用户权限**。identity 注册要求 assertion 已验证该 account；查询他人身份/记忆需要服务端明确授权。首次登记前 origin_scope.person_id 可为 null，memory 仅凭已验证账号进行登记，不能要求先有 person_id 才能首次注册；登记后 companion 采用 memory 返回的权威 ID，再装配需要人物的 scope。已有 ID 仍须与 memory 当前绑定核对。common.trusted_context 是离线测试夹具中的接收者解析结果，不是客户端可提交的管理字段。来源解析的实际持久库、凭据发放和撤销测试是 L0 前置工作，静态样例不能证明认证。

写操作按 `(authenticated_service, operation, idempotency_key)` 持久记录规范化 JSON 输入及原结果；request_id 可因重试改变，deadline/origin_ref 可更新，但目标、语义 payload、expected_version 均须相同；新来源仍须通过授权。相同键异 payload 返回 409 `idempotency_conflict`，不能重做动作。超时后用原键核对原结果；已开始而结果未知返回 `result_unknown`，不能声称未执行。deadline 过期且未执行为 408 `timeout/not_started`。版本冲突为 409；鉴权失败 401，越权 403（隐藏对象统一 404），校验/未知主版本 400，容量/预算 429，依赖不可用 503，游标过期 410。错误不附私密记录存在性、凭据或原始上游正文。

事件事务 outbox，至少一次投递；消费者去重 event_id，并核对 `(owner, aggregate_id, aggregate_version)`。相同 ID 异内容须隔离报警；版本缺口重取责任方快照，不能假造中间状态。事件游标是投递位置，不是对象版本。

## 2. 消息与逻辑会话（I01）

`POST /internal/v1/conversation/ingest`：conversation.ingest_request → ingest_response 或 common.error。NoneBot/platform 发送 channel、不可变 author、原始时间、内容段、引用、提及及逐消息目标；companion 签发 conversation_id、collection_id、turn_id 和受理序号，memory 签发 person_id。客户端不能自行指定权威 conversation_id/person_id。

- 去重键：`(namespace, binding_id, channel_conversation_id, thread_id, message_id, revision)`。相同键不同正文拒绝；重投原事件不重置窗口。不同 message_id 的相同文字保留。
- 分人收集键：`(channel_key, author.namespace, author.immutable_account_id)`。同一人跨渠道、群/私聊或线程分别收集；群内 B 不重置 A。群内允许每个发送者一个 collector，不将全群强塞为一个 collector。
- 逻辑会话键：服务器登记的 `channel_key → conversation_id`。同一 channel_key 下所有人物/角色共享两个活跃轮次与发送序列。网页查看 QQ 会话是投影，不重新映射会话；网页原生聊天使用自身 web binding。跨绑定桥接若以后需要显式迁移，不能由相同 person_id 自动合并。
- 接收先可靠存储短期原消息和受理序号，再回执。`pending/unavailable` 不表示永久归档；只有真实 Chat Audit 回执才为 archived 并给 locator。不虚造 I06 写端点。
- 群背景插话放 context_refs，保留真实作者与公共 ingest_sequence，不能变为当前作者的新发言。引用原文仍需权限，不因有 locator 就允许读。

W=5000ms 是实例，配置可设 W=0；max_wait_ms 默认 null。只有受理时间严格 `< deadline` 才追加原组；等于时先封存。去重先于计时；受理序号决定同刻顺序。旧 timer 核对 collection_id/revision/deadline。恢复按持久 UTC deadline 换算等待，不复用旧进程单调时钟。edit/retract 修订号递增；旧修订只归档、不覆盖新修订、不重置窗口；同修订异内容冲突。撤回全组取消，窗口内部分撤回更新成员与计时；封存后编辑/撤回走对应轮次取消/失效路径。

bundle 固定消息边界、成员修订、作者、引用与目标，turn_sequence 在封存时由会话所有者递增（同刻按最早 ingest_sequence 破同序）。组内保留条件、否定、转题，不能只留末句或串成无作者长字符串。媒体就绪不重置窗口；首切片不实现媒体生成/传输。

## 3. 两轮、容量、发送、取消（I02/I18）

活跃阶段是 `preparing/generating/waiting_dependency/ready_to_send/sending/reconciling`，均占一个会话槽；`queued` 不占。终态 `sent/failed/cancelled/observed/closed_unknown` 持久封账才释放槽。模型计算配额独立：模型请求结束即释放，待发送不能继续占模型配额。T2 在 T1 未发完前可做独立准备与生成；T3 排队，不能在生成结束时偷偷变为第三个活跃轮次。dependencies 仅阻挡实际依赖步骤，绑定稳定 result_version，不把未发送草稿当用户已见内容。

容量为部署配置，实例为 max_collectors=32、max_queued_turns=8、每组256条/262144字节；不是所有产品默认常量。新 collector 在受理首条前预约一个封存队列位置：`queued + reserved_collectors <= max_queued_turns`，并检查 collector/message/byte 上限；满时 429 queue_full，未受理，不给成功 receipt。已受理内容不因后来满载丢弃。封存将预约转为 queued，开始处理释放排队容量。超过组资源上限前拒收新增内容，已有组可封存为 resource_limit/possibly_incomplete=true；必须保留 continuation_of 关联，等待后续完整输入或显式确认再执行动作，不能把半句当完整命令。渠道适配器必须保留重试队列并明确告知背压，不能丢弃未受理消息。最长等待触发也标 possibly_incomplete。

NoneBot：`POST /internal/v1/conversation/send`，send_request → send_receipt。每次是一个明确的段落，按全会话 `(turn_sequence, segment_sequence)` 串行提交，多角色不能互相超车或交错段落。reply_id 是稳定段落 ID；重试必须保留 key/reply_id/序号，adapter 持久记录 attempt。destination 必须等于核心登记目标，不能采用模型提供的任意地址。失败只在证实未执行时 retry_safe=true；unknown 必须 false。

未知回执先进入 reconciling。`delivery_reconcile_timeout_ms` 到期且仍无法查证，持久 `closed_unknown`、delivery_state=unknown、unresolved_delivery=true 后释放轮次槽，保留待核对记录和发送序列上的不确定标记，后轮可继续但不能假定前轮已达或再次重放。发送顺序保证本地提交顺序，不承诺渠道实际到达顺序。迟到回执更新该记录和对象版本，不重开已释放槽、不再次发送、不重复提交记忆；发 delivery_changed 投影。失败/取消/无回复也明确封账，后轮不会永久挂起。

`POST /internal/v1/conversation/cancel`：cancel_request → cancel_response。必须明确 turn_id、expected_version、经验证来源；普通续句、“等等”和被引用的取消词不调用该端点。生成中停止尚未执行部分；ready_to_send 停止待发；sending/reconciling 报 partially_cancelled 或 unknown，已发送列表与未知列表分别记录；已完成为 too_late。不能用 cancelled 覆盖外部既有事实，external_actions_rolled_back 永远 false。外部长任务的细粒度取消不在本包冻结。

## 4. 网页回复下行（I02 补充 / I17 文字投影）

`POST /internal/v1/conversations/snapshot`：web.snapshot_request → snapshot_response。平台对浏览器提供同源 `GET /api/v1/conversations/{id}?actor_id=…` 和 `GET /api/v1/conversations/{id}/events`（SSE）；它们转发并裁剪核心权威投影，每次校验登录者对该会话/角色的读取范围。浏览器不能直接使用 origin_ref、服务令牌或网关凭据。

核心保存 reply_id、reply_sequence、turn_sequence、text 和 delivery_state。网页目标以持久回复进入可授权读取日志为 sent，不等于用户读过；网页订阅连接不是一次发送动作。QQ/TG 投影显示原目标和真实回执状态，打开网页或重连绝不重跑模型/再发 QQ。

快照的 snapshot_token 固定一个授权范围与 object_version；分页 next_page_token 必须绑定同一快照、主体和会话，不能跨页混不同版本。resume_cursor 是该快照边界之后的订阅位置，与数据在同一一致读中取得。SSE `id:` 为 cursor，`event:` 为 conversation.projection_changed，`data:` 为 projection_event；Last-Event-ID 恢复只重放有权事件。event_id 去重，aggregate_version 连续检查；版本缺口或 cursor_expired 重取快照。收回权限终止流并清掉不可继续显示的缓存；观测超期显示 stale/unknown 与 observed_at，不能假装在线。当前只发布一个会话/角色文字状态，不覆盖完整任务中心或世界状态写入。

## 5. 身份与记忆（I03/I04/I05）

`POST /internal/v1/identity/resolve` 为只读，未登记返回 unregistered/null/0；`POST /internal/v1/identity/register` 在已验证账号下幂等首次登记。同 namespace/immutable_account_id 的群私聊返回同一 person_id；昵称仅展示。跨平台同昵称不同人。

`POST /internal/v1/identity/link` 必须明确两账号、两 binding_version、memory 签发且未消费的 verification_ref；证明同时绑定两个当前账号及授权主体、用途、有效期。接收者验证后原子合并并消费 proof，重投相同幂等请求返回原结果。未知、过期、目标不符的 proof 拒绝。请求不接受任意 person_id 或管理员声明。账号证明的实际获取/挑战流程尚待 memory/platform 实现，禁止以夹具 proof 当真实认证。并发版本冲突不自动合并。

`POST /internal/v1/memory/select` 一次批量选择。requested_scope 只是上限，memory 按验证来源/当前权限收紧为 effective_scope，**在检索前**过滤，再检查权威 record_version、scope_version 和来源。scope 版本变化返回 scope_changed，不能以旧候选重新授予访问。群请求只返回 shared_projection，不能取私聊原始来源；哪怕 statement 已脱敏，source locator 也必须是可共享投影引用。过滤数量和私密 record_id 不返回。

selected_units 与 dependency_groups 同取同舍；主语、条件、否定、时间、不确定性、现实/虚构标志和来源齐全。预算不够整组省略，零项合法，budget_used 不超请求。valid_until 仅有效期上限，**不是离线授权**；记忆服务不可用时不使用无法验证当前范围/版本的私密缓存。缓存键至少包含 actor/person/audience/conversation/scope_version/record_versions，查询文本不够。

`POST /internal/v1/memory/revise` 是已确认更正/遗忘，需 confirmation_ref 与当前来源权限，模型提议不能伪造确认。先原子更新权威记录/墓碑、版本、scope_version，再异步索引；pending 不等于旧内容仍可召回。旧缓存/向量在读取时必须核对当前权威版本与墓碑。correct 要 replacement_statement，forget 必须 null。

## 6. 首次记忆写入与提交事件

companion 在轮次封账事务中写 `conversation.turn_committed` outbox，aggregate_id=turn_id，带 input_revision、turn_sequence、范围版本、每条原文 source、reality、真实发送状态。不能把“候选生成完”或“已收件”当成已表达/已归档。confirmed_user_correction 恒 false，事件是后台整理依据，不是任意模型写记忆授权。

memory 通过 `POST /internal/v1/memory/turn-commits` 接收 committed_event，返回 consume_receipt：原子去重 event_id 以及 `(turn_id,input_revision)`，创建内部 candidate_job_ref；accepted 表示候选工作已可靠接收，confirmed_memory_written 恒 false。内部提炼验证当前来源修订/范围，再决定新建首条整理记录或不写；写入 ledger 对来源版本幂等，关系累计同样去重。相同 turn/revision 换 event_id 仍 duplicate；重放不得重复记账。stale_source/scope_changed 不安排候选。已撤回、权限撤销、用户明确更正优先于晚到事件和旧候选；混合虚构逐来源分开，不能写为现实经历。发送未知时只保留真实输入与未知状态，不能宣称角色已向用户承诺。迟到投递回执只修订投递事实，不再次触发首写/关系累加。

消费端尚未实现，本包中的 source ledger 与幂等结果是联合输入/输出约束，不能标为后台记忆实际写入成功。

## 7. 配置和模型（I14/I15）

`POST /internal/v1/model-config/snapshot`：model.config_request → config_response；平台是唯一发布者，网关读取已发布 config_version，不把管理草稿当运行配置。轮次首次调度固定 config_version，后续所有请求使用同版。网关校验来自已认证平台及结构后缓存快照；平台离线时仅可在 usable_until 之前且尚未收到撤销信号时使用已验证同版与仍可解析凭据。未知版本、过期、已知撤销、解析凭据失败均拒绝新调用；不得静默套用 latest。有效期是离线撤销传播的上界，平台/网关需共同配置并测试，不能宣称瞬时离线撤销。

credential_ref 是不含密钥的 secret-ref；credential_namespace 标记上游租户/账号边界。网关仅能经部署指定的服务器凭据存储读取已授权引用，平台浏览器仅看脱敏 provider 状态，不得获得 credential_ref、上游授权头或完整配置快照。provider base_url 从已发布且服务端审查的配置读，不能来自普通聊天/模型参数；示例 `.invalid` 永不访问。默认 TLS；V2 明确配置的本地模型可用 HTTP，但必须在服务端发布时核对已登记私网目标及凭据发送边界，不能仅凭 schema 允许 http 就任意访问内网。内部业务服务认证仍要求 TLS。

首轮原生 `POST /v1/chat/completions`；业务调用的关联元数据放 `X-Request-ID`、`X-Tianshu-Config-Version`、`X-Tianshu-Workload: companion.text`、`X-Tianshu-Turn-ID`，不混入原生 body。这些内部头由认证服务产生；外部客户端不能用它们越权选择配置。普通原生客户端的鉴权/路由绑定由网关服务端配置，其显式 model、messages、tools、tool_choice、reasoning 和未知扩展字段原样保留。流式保持上游数据帧、工具分片、finish_reason、usage 与终止标记，不转换成业务事件；中途断流为 unknown，不能拼接另一提供商输出。

model_policy/reasoning_policy 分别明确 preserve_client、default_if_absent 或 force；默认 preserve_client。只有客户端字段缺失才补 default，显式 null/空值不是缺失。force 必须在已发布配置中显式可见，并在独立 route_receipt 记录 requested/resolved 值及 applied_policy；不得悄悄改模型或丢 reasoning。companion.text 无显式模型时由绑定指定模型，不影响外部客户端保留模型的规则。

本候选 fallback=disabled。上游/credential_namespace/协议黏性不能被配置刷新改变；涉及前次响应 ID、provider 状态或会话 ID 的调用不可跨命名空间重放。嵌入接口不在文字首切片；向量空间须由 provider/model/revision/dimensions/normalization/credential_namespace 固定，空间变化需显式新索引与迁移，不允许退回其他模型继续写同一索引。Responses/Anthropic 保留独立协议分支，不压成 Chat Completions。

路由回执通过 `GET /internal/v1/model-requests/{request_id}` 按服务授权独立查询；包含固定 config_version、上游/凭据命名空间、请求/实际模型、reasoning 策略、结果和 usage。未知用量为 null，不是零；部分用量只填已知 input/output，usage_complete=false，native_usage 保留供应商原始统计字段，不猜 total/cost。provider 密钥与内部异常正文不进入回执。原生请求是否真正兼容、各 reasoning 方言/错误帧/取消转发、有状态与嵌入协议均需 TS-040 保真夹具及 TS-041 实现验证；此候选只定义文字边界和禁止静默降级的规则。已只读核对协调检出的 TS-040 `6b2daff` 审查与夹具；其实验函数/枚举不作为第二份 wire 合同。
