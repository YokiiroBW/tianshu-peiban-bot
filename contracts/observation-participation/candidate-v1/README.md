# observation-participation / candidate-v1

版本：1.0.0-candidate.1；日期：2026-10-27；任务：TS-319。此包是根合同 owner 已委托的新命名空间实施候选，未生产发布。平台与适配器须消费完全相同的 manifest LF SHA256；双方真实联合验收完成前不得标生产冻结。基于 runtime 提案修订 2 收敛，未修改 observation-source、Core ingest、BotDelivery 或其他 actor/user/Chat/group/archive 包。

## 四组边界

| 组 | 消息 | 生产者与消费者 |
| --- | --- | --- |
| 候选 | participation_candidate | Platform 从真实普通群消息和当前身份映射产生；Companion AL12 消费 |
| 宿主归属/领取 | host_ownership、participation_claim_request/result | 宿主提供状态和领取回执；Platform 发起领取；宿主持有唯一 CAS 权威 |
| 语义决定 | semantic_participation_decision | Companion AL12 产生；Platform 只接受已配置服务的当前决定 |
| 派发前登记/既有投递绑定 | participation_admission_registration、participation_delivery_binding | Platform 在 Core 派发前持久登记；原回复创建路径同事务接上实际 reply，原投递路径消费 |

共 7 种封闭 DTO。地址、凭据、操作者身份不在模型参数中指定；固定服务认证由传输层完成，字段本身不是授权。只处理 mentioned=false 的普通群文本候选；明确 @、关键词和已有命令沿原路径。不改原消息 mentioned，不制造虚构消息或轮次。

candidate 携带原生消息身份、原作者账号、原 source digest/scope_version/archive_epoch，以及当前角色、回复策略、配置代际和期限。正文来自宿主本次持久捕获的原事件，须与完整原事件 source_digest 对应；Platform 的 sources 行只有证明元数据，不能从该行杜撰正文。author_anchor 按 observed/registered/resolved 明确身份确认阶段；首次旁听不要求提前有 person 或 entry，不为候选调用 Core 造映射。实例、机器人账号、原作者及群必须共同匹配。阶段规则见下文。

source_digest 沿原 observation 来源语义，不改算为候选摘要。candidate_digest、decision_digest、registration_digest 分别覆盖对应完整 DTO 的 UTF-8 规范 JSON SHA256（键排序、无多余空格、保留 Unicode）。消费者读取原持久记录逐项核对，来参摘要自身不是证明。

## 宿主状态、失败和领取回执

正常边：processing → free / handled_elsewhere / ownership_unknown；只有 free → claimed。处理或领取到期可进入 expired。handled_elsewhere、ownership_unknown、claimed、expired 均不得被迟到回调改回 free；expired 不可再领取。同一 native message 只有一个宿主归属槽，不能每 actor 各建槽。owner_revision 随真实变更单调递增。

- NoneBot 固定 2.5.0：在宿主已正常通过原 matcher 筛选、确定进入实际其他 matcher 的入口处，先持久标 handled_elsewhere，再允许处理体运行。不等发送，不重复执行 filter/rule 探测归属。该 matcher 随后失败、取消或无输出也不释放归属。
- free 仅表示受控宿主已登记的该事件处理路径全部正常闭合；all_handlers_completed 只证明这一受控范围。event_postprocessor 自身只证明 matcher 分发结束，其他 postprocessors 可能并行，不能据此置 free。事件级 Depends/AsyncExitStack 收尾可作为更后边界，但 finally 被执行不证明正常完成。
- 任一路径异常、取消、完成信号缺失、结果未知或存在未纳管脱离任务，若尚未 handled_elsewhere/claimed，则为 ownership_unknown；到期后 expired。已 handled_elsewhere/claimed 时保留该事实，清理回调不得覆盖。必须正向收集全部登记路径的正常闭合；超时、某 matcher 未发送、空错误或单个后置 hook 都不充分。
- AstrBot 固定 4.27.3：公开 hook 无全局 pipeline 完成证明。普通候选只能保持 processing、标 ownership_unknown/handled_elsewhere 或到期；schema 禁止 free/claimed。不能因尚未观察到回复而领取。

版本字段是固定实现目标，不表示本次运行过 SDK。未来可靠新证明须另行评审，不悄悄放宽。

领取顺序如下，不能先要求当前 free 再查重：

1. 认证同一 producer，定位原观察行（包括 acked=1 的保留行），按 producer 与原 request_id 查询不可变回执。request_digest 覆盖完整 claim_request；全部字段参与同参判定。
2. 同 request_id 同参：先返回原 result/reason/claim/recorded_at，receipt_status=replayed；不再 free/CAS，不延长期限、不造新 claim。再 fresh 读取当前 ownership/current_claim_state。原 grant 过期后仍是历史 granted，当前却为 inactive；历史事实不是现时权限。
3. 同 request_id 异参：conflict/idempotency_conflict，result=unavailable，不透露原 claim、不改原回执。原行已删除：unavailable/row_unavailable，ownership/claim/recorded_at 均为 null；不按复制候选重建，不把无法恢复说成从未领取。
4. 仅无原回执且原行仍在时，核对当前 free、expected_owner_revision、host_epoch、host_generation、候选/决定及期限，在宿主同一原行事务内 CAS 并保存回执；提交后才响应。成功/拒绝回执均不可变，初次 receipt_status=recorded。

current_claim_state=active 只表示宿主当前认可领取，不代表角色、来源、群回复或投递许可。配置变化、过期或当前校验失败为 inactive，即使原 ownership 仍记录 claimed。checked_at 是本次检查时间，recorded_at 是原回执时间。重放不能用旧期限拒掉原事实查询，也不能用原事实跳过发送前当前检查。ACK 后查询复用同 claim_request，不新增查询 DTO。

归档 ACK 与参与状态独立。复用原观察行附加状态、代际和回执，不新增观察队列；候选可在 processing 时记录。不能为等待 AL12 模型阻塞 poll、归档或 ACK。当前 ACK 仅标 acked，历史行达到保留上限可被清理；领取/重放 fresh 读同一原行，缺失即 unavailable。

## 派发前登记与回复原子关联

本次只读核对 TS-311 物理 worktree 的 Platform HEAD 为 3e2cc412c676450a90dc0cbd3c3387d1c945e5e2，不据此宣称发布。可复用的实际标识和接点（路径均为 services/platform 下）：

| 源码位置 | 已有事实与提案接点 |
| --- | --- |
| bots.py:700–744 | events 原受理键 connection_id/event_id/account_id，保存 semantic；message_id 由 _admit_event 生成 |
| bots.py:595–643 | 用真实 message_id 构建 physical input，register_input 后按原 bot 幂等键 dispatch |
| sources.py:97–153、220–339、631–646 | register_input 产生 source-input assertion；prepare_mapping 保存 fanout ticket/actor origin；core_post 在准备之后 |
| bot_delivery.py:434–468；bots.py:882–898 | 两条回复创建路径都把真实 reply/attempt 直接插为 pending |
| bots.py:979–999 | claim 选择 pending；同连接已有 claimed 挡住后续批次，不能先领取再等关联 |

participation_admission_registration 是 Platform 已持久完成登记的内部记录，不是 AL12 受理请求。它绑定 candidate/decision/host claim 与真实原 admission/source，禁止预填 turn_id/reply_id。具体顺序：

1. 当前 engage、宿主有效领取和当前发送许可均成立后，以原作者、消息及 account/群/实例进入原 _admit_event 全部身份和许可校验，复用原事件键与真实 message_id。同一事件已有普通受理或另一参与登记时不转换身份，不生成新 event_id 绕过。
2. 经原 register_input 和 _prepare_dispatch 取得真实 source-input assertion、input_digest、fanout ticket 与该 actor 的 actor assertion。仅选 candidate.actor_id 作为 target_actor_ids，且验证它在连接当前允许集合；不扇出其他角色。原 Core ingest wire 不变。
3. 在任何 core_post 前，来源/准备 owner 将该原事件及 prepared route 标记为参与受理，队列 owner 持久保存完整 registration。登记至少是 registered 阶段，含真实 connection/event/account/semantic、message_id/revision、input_entry_id、input_digest、两种 assertion、fanout_ticket 和原 Core idempotency_key。此时真实 actor entry 已安装，但首次作者的人物/会话可以尚未确认；不能为满足 schema 提前派发 Core。source-input 是输入来源，actor assertion 是该 actor 的来源；历史 observation source_ref 不能替代。
4. 只有两侧真实记录已写成并重新核对一致，且 claim/来源/配置仍有效，才进入原 core_post。来源权威库、宿主库与 Bot 队列不是跨库事务；阶段失败即不派发。崩溃后先查同一 event/command 的真实准备记录和登记；同参复用、异参冲突。找不到唯一原准备或已过期即停止，不换 event_id/candidate/claim 补发。HTTP 结果 unknown 沿原受理幂等查询恢复，不重造轮次。
5. Core 真正受理后，turn/reply/expression 仍由原执行 owner 生成。BotDelivery origin.kind=response 的 actor assertion（旧 send 为 command.origin.assertion_ref）必须解析回真实 prepared source/admission，据此查登记。不能按字符串猜测，也不能因登记缺失把已标为参与的来源当普通回复。来源别名/换签只能由来源 owner 证明仍指向同一受理，不能仅凭相同 actor/群串接。
6. 在两条回复创建路径各自同一个 Bot 队列本地事务内，核对登记、当前有效性及原 send 已验证的 resolved actor/person/entry/scope，插入真实 reply/attempt，并同时持久写 delivery_binding；binding 引用 registration_id/digest。本次 fanout 的来源回执可能尚未确认，必须显式写 source_confirmation=pending；身份已知不等于本次来源确认。缺失关联或身份未解析时不能创建可领取回复，不能先让 reply 可见再异步补 binding。
7. claim 的 eligible 集合在 ORDER BY/LIMIT 前排除缺失/失效关联及 source_confirmation=pending 的参与回复；pending 不得先置 claimed，也不占连接槽。只有原 Core admission 回执及 Sources._confirm/current source 验证本次 fanout、同原作者和同实际身份后才能改为 confirmed，仍须当前许可/期限。确认只更新该状态，不换 registration、reply、attempt 或原绑定时间；网络等待不持事务，也不声称跨库原子确认。正常回复沿原路径，不为参与候选预留连接槽，正常 claimed 原排序约束保持。

这确定了 AL11 的内部接线要求；现有源码尚未实现这些新接点。delivery_binding 是实际 reply 创建事务的记录和读取投影，不是事后补丁或额外发送许可，仅引用真实 connection/turn/reply/attempt。相同 reply 重放只取原绑定，不能换 registration。原 BotDelivery 仍是唯一执行/回执账本，没有第二个 sender。

## 语义决定与发送许可

AL12 未接入时静默：无 engage、不领取、不登记、不创建回复/绑定；可返回 abstain/not_integrated。defer 不占宿主槽，后续决定受原期限约束。engage 只是愿意参与；contribution 是短表达意图，不是直接发送正文或内部推理。

Platform 在 claim 前核对真实原生作者/群、原 source、当前角色、回复许可、宿主状态、代际和期限；只能使用该阶段已有的真实映射，不强迫 observed 候选提前有 person。登记/派发前再核真实 input/actor entry 与来源准备；reply 创建必须已有 owner 验证的 resolved 身份，实际领取/投递还要求本次 source_confirmation=confirmed 及所有当前条件。observe_only 永不发送。历史 proof、模型决定、旧 grant、登记和 binding 均不能独自授权。可读过去消息不等于允许事后补发；配置变更使旧候选不可用。

观察事件本身不生成 conversation.turn_committed；只有真实原消息按上述登记后经原 Core 受理链执行才产生真实轮次。claimed 后模型失败、回复 unknown 或发送未知均不得回 free；沿原 BotDelivery 账本核对副作用，不能再次参与重发。

## 运行时约束与验证范围

Schema 仅校验单 DTO 形状，不能证明认证、原行存在、同一 owner CAS、全部路径正常闭合、摘要对应原文、跨 DTO 相等、当前权限、期限先后或真实账本本地事务。各 owner 验证自身当前字段，不能只回显来参。current_claim_state 不是跨服务事务承诺。

sent_at 不由模型补写；created_at < expires_at；决定/claim/登记/绑定的期限不晚于候选和全部上游许可。网络延迟/重试不延长期限，网络/模型等待不持数据库事务。正文 ≤8000 字符、单 DTO ≤64KiB UTF-8；固定本地 schema，不截断身份字段。沿既有调度，不新增模型队列。

examples.json 包含结构正反例及有意通过 schema 的运行时反例，后者不算权限/CAS 验证。对应 unittest 用真实 jsonschema Draft2020-12、本地 Registry 与标准库 datetime 严格日期检查，不运行状态模拟器、产品、SDK、QQ、数据库或模型。

## 候选校验与继承证据

本包不携带旧 runtime 自检器、状态转换模拟器或通过收据。原修订 2 在原文件摘要下为 24 正例、29 反例通过；包命名空间、版本、宿主完成收据及案例已变化，旧结果不能替代本包验证。新验证入口是 `tests/contracts/test_observation_participation_contracts.py`，沿既有 unittest/jsonschema，使用本地 Registry、标准库严格日期检查及真实 LF 文件摘要。

`manifest.json` 只列 schema.json、examples.json、README.md 的 UTF-8 LF 规范化 SHA256；消费者另计算 manifest 本身的同口径 SHA256 作为 pin。文件行尾规范化只用于文件 hash，不能改写 JSON 字段内的换行、Unicode 或原消息内容。消息摘要为键排序、紧凑分隔符、保留 Unicode 的 UTF-8 JSON SHA256，禁止 NaN/Infinity。传输解码前限制每 DTO 为 65536 字节；schema 的字符上限不代替字节上限。测试仅核合成案例及这些规格，不证明 owner/CAS/事务。

## NoneBot 首版宿主完成证明

普通 free/claimed 快照必须带 `completion_evidence`：`event_record_id` 引用真实宿主原事件行，`handler_set_digest` 绑定该事件实际运行的受控处理器/前后 hook/任务集合；capture_receipt_id、explicit_reply_receipt_id、scope_receipt_id、closure_receipt_id 分别引用以下实际执行的持久结果。processing/handled_elsewhere/ownership_unknown 不得带完整成功证明；expired 可保留历史证明，绝不恢复许可。DTO 引用只是可查的键，不是调用方自签的权力。

1. `_observe`：本次原消息确实持久捕获成功。不能用 observer 返回 false、忽略 capture 返回值或只出现 hook 记录代替。
2. `_claim_observation_reply`：本次明确触发判断正常完成且没有接管。未运行、抛错、超时、取消或已有 owner 均不满足。
3. `_scope`：本次旧精确路由正常确认无 owner。必须区分正常无路由与多路由、容量、存储或其他失败；原 capture()==False 混合多种结果，不能当作成功空闲。
4. 正常闭合：所有自有前后 hook、依赖及登记任务正常结束，前三项同事件成功收据齐全，集合自捕获起未变，且没有其他 matcher 入场。finally 到达、event_postprocessor 返回或空错误不等于成功。

只支持由 `integrations/nonebot/tianshu_nonebot/onebot.py` 构造、只装天枢且处理器集合受控的独立宿主。adapter_kind 字符串和插件列表本身不是宿主图证明。任何未知处理器、动态图变更、命令 trie 依赖、不能出具正常结果的新处理器、脱离任务都保持 ownership_unknown。permission/rule 异常可被框架吞成 false；记录必须在原函数实际执行内部产生，不重跑过滤，不猴子补丁框架。各 matcher 得到 state.copy，不能用它充当共享收据；同原事件的持久 owner 才能核验所有引用及未变化集合。其他 matcher 真正进入即 handled_elsewhere，此后即使失败或无输出也不返 free。

AstrBot 4.27.3 无公开全 pipeline 完成证明，普通观察候选仍为 unknown，schema 禁止 free/claimed；原明确触发能力保留。不得借 NoneBot 证明规则放宽 AstrBot。

这些前提来自父级 `docs/development/autonomous-life-wave2-execution-2026-10-26.md` 的固定源码审查：[NoneBot 2.5.0 message.py](https://github.com/nonebot/nonebot2/blob/v2.5.0/nonebot/message.py#L392)（permission/rule 异常转 false；命令 trie 错误仅日志；state.copy）、[插件接口](https://github.com/nonebot/nonebot2/blob/v2.5.0/nonebot/plugin/__init__.py#L151)、[AstrBot RespondStage](https://github.com/AstrBotDevs/AstrBot/blob/v4.27.3/astrbot/core/pipeline/respond/stage.py)、[PipelineScheduler](https://github.com/AstrBotDevs/AstrBot/blob/v4.27.3/astrbot/core/pipeline/scheduler.py)。是固定源码依据，不是本候选 SDK 运行通过。

## 读取入口与消费者责任

七个 schema 定义为 candidate、host_ownership、claim_request、claim_result、semantic_decision、admission_registration、delivery_binding，统一入口 `#/$defs/wire`。宿主 claim 组可用 `#/$defs/host_claim`。全部 `$ref` 均为本文件内部引用，禁止校验时联网获取 schema。

本包只定义业务 DTO；原查询目录、RPC/HTTP framing、认证、错误和重试语义继续由各已有入口承载，不新增管理员 token、跨库 RPC 或自行推定端点。`request_id` 的幂等命名空间包含经认证 producer；同键异参为原 framing 内冲突。原行被清理的回执为本包 unavailable，不代表可创建新行。候选受众仅是 native 的原群与物理原作者，resolved 后的人物/entry 必须由 owner 证明属于同一作者；不能改为 BOT person、扩大到其他群/私聊或拿可读历史证明发送许可。

平台消费者核对候选、当前来源/角色/受众/配置、语义决定、真实原 admission/source/fanout/actor assertion、身份阶段与投递 source_confirmation；适配器消费者核对原生事件身份、封闭宿主正常收据、全宿主唯一 owner、原回执优先与当前领取。两边都须验证期限和各自权威当前值。AL12 未接入时只有静默/abstain，没有自动 engage；候选不对其他路径预留连接槽。实现必须在保存/实际发送时重新使用原权威端口，不把 schema.validate 的通过作为许可。

## 原作者锚点的阶段与本次来源确认

author_anchor 始终携带物理 account_id/group_id，分别与 native.author_account_id/native.group_id、原 source 持久行核对。三阶段使用同一封闭结构：

| stage | entry_id | person_id / conversation_id / binding_version | 允许位置 |
| --- | --- | --- | --- |
| observed | null | 全部 null | 首次候选，已有 native/source 实锚 |
| registered | 原 owner 已安装的真实 actor entry | 全部 null，表示本阶段未确认完整映射 | 候选或派发前登记 |
| resolved | 同一真实 actor entry | 原 owner 已证实的非空人物、内部会话和绑定版本 | 已有映射的候选/登记，及所有 delivery_binding |

null 只表示此 DTO 阶段尚未确认，不声称全局不存在人物/会话。不能只填一半 resolved 映射，不能把物理 group:123、观测帧或 actor_id 拼成内部人物/会话。内部 ID 沿已发布 common.id 语法；person:/conv: 是现实现产物，不增设前缀门槛。跨阶段可增加经实际 owner 证实的信息，不能改原 native/account/group/source/actor；已存在的 entry 也不能换成另一作者的 entry。候选记录及其 digest 不因后来解析而改写，后续登记/绑定各自携带新增事实并核原引用。

author_anchor.entry_id 是 actor assertion 对应的 origins.entry_id；admission.input_entry_id 是 source_inputs 的真实输入 entry。两者通过 actor_origins.input_entry_id 与真实 input_digest/account/channel 关联，不能拼字符串推导或互换。Platform BotObservation.record（827–976）只记录物理来源，不产生 person；动态 entry 在 `_install_reply`（167–211）由真实 account/channel 安装。Origins.scope（49–73）只读取已有 identities/channels，缺失为 null。

首次作者的 person/binding_version 在 Core ingest 内由原 Memory identity owner resolve/register 产生，不能通过候选侧虚构。Core 的 learning-frame 可先分配真实会话，但它不证明 person、admission 或 turn；Platform 映射仍须真实 Core admission 回执确认。Sources._confirm（449–544）检查 accepted_origin、actor_origins、input entry/digest/account/channel 后回写 identities/channels/admission_history。registered 到 resolved 必须据此链及 current scope 证明是原作者。

还必须区分**身份已解析**与**本次来源已确认**：首次未映射时 BotDelivery.context 的 allow_pending 分支随后仍要求 current_scope 等于 request.scope，否则可恢复 503 且不续期；send 不允许未映射身份，旧 Bots.send 同样要求 person 非空。此包不放宽它们。老作者/旧会话已有映射时，Core JSONResponse 的 BackgroundTask 可在 Platform 完成本次 Sources._confirm 前启动回复；原 send 能验证已有非空 scope，因此合法 reply 可先创建，但其 binding 必须原子固定 registration/reply/attempt 并保留 source_confirmation=pending，直到本次 fanout 的真实 admission 确认后才 eligible。

该竞态的固定源码依据为 Companion `671deead` 的 source_sync.py（identity 调用 719–726、allocate_channel、真实接受事务）及 app.py:839–846，Platform `3e2cc41` 的 sources.py:643–645、bot_delivery.py:59–69/106–141/311 与 bots.py:811–826。首次未映射回调的原回归为 test_c4_delivery.py::test_pending_ingest_ack_context_waits_without_renewing_then_duplicate_confirms。本卡只读核链，不冒称重新运行这些产品用例。两个消费者应新增实际用例覆盖：首次 observed 作者正常解析；老作者早 reply 已绑定但 pending 时不能领取，真实当前 fanout 确认后同一绑定才进入 eligible。
