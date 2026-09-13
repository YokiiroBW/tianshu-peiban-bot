# 多角色来源主候选 candidate.2（未发布）

本修订面向多个角色共享世界、群聊与私聊同等可用的正式接线方向。candidate.1的单actor渠道限制仅为旧Core迁移保护，不是最终来源架构。本文件覆盖旧方案中物理key直接对应唯一actor/suppression的部分；[共同基础](semantics.md)里的来源级失效、correct只禁旧值、双owner水位、零预算屏障、容量限制与失败关闭全部保留。没有产品实施或L0成功声明。

## 1. 两层身份，一条共享会话

| 对象/键 | owner与不变量 |
| --- | --- |
| 物理来源 `P={channel,message_id}`，版本`P+revision` | Core；同一真实渠道消息只存一份该修订的完整输入、作者、reality、内容摘要与`physical_receipt_id`。与发给几个actor无关。channel含namespace/binding/channel_conversation/thread，群私不会撞键。 |
| 角色受理选择器 `A={key:P,actor_id}`，受理版本`A+revision` | Core；保存该角色确实获准接收该修订的`common.source.receipt_id`、精确scope、Memory身份绑定与当时授权入口。A/B可有相同P/revision和physical_receipt_id，但必须有不同actor receipt。 |
| 当前角色使用权 `A + receiver/purpose` | Platform；当前入口、主体、路由、账号/渠道映射逐actor核验。共同世界、同群成员、P存在或A已获准，都不自动授权B。 |
| Memory来源行、血缘、source_writes与本地suppression | 按`A`（scope必须吻合）索引；epoch/候选/组/版本按各actor隔离。物理来源修订/墓碑为共享否定事实，见第4节。 |
| 会话 `channel → conversation_id` | Core；仍不包含actor。同渠道A/B共享会话、输入受理序号、封存turn_sequence、两轮并发上限及发送顺序。 |
| collector | Core；`(conversation_id,channel,author,actor_id)`，不再把A/B消息塞进同一collector。同一actor仍按原收集窗口合并完整消息。 |

物理`content_digest`覆盖message_key/author/sent_at/kind/parts/reply_refs/mentioned_accounts，保留完整否定、条件、引用及消息边界；**不含target_actor_ids**，后者是路由意图，不是另一份物理消息。给B分送同一修订不能因为target不同制造内容冲突；相同P/revision而正文/作者等不同仍409。classification继续由Core可信输入情境确认，不能因角色不同把同一来源的fictional改成real。

共同世界/房间状态继续由Core负责，不为每个actor复制一个世界或按actor重建conversation。Memory的私密记录、关系、候选、确认和suppression仍属于精确actor/person/audience/conversation；不以共同world或物理P作为跨actor读取授权。群与私聊使用同一组规则，只是各自channel和audience不同。

## 2. 最少新增wire与兼容

`text-dialogue/v1`、`profile-memory/v1`的已发布文件完全不改；新增形状都在独立`source-sync/candidate.2`候选中，最终发布版本由协调者决定。schema见[multi-actor.schema.json](multi-actor.schema.json)。

candidate.1的无actor-selector来源HTTP形状从未发布，不要求正式服务兼容它；保留的是已发布text/profile wire及旧Core单actor入口的安全迁移路径。新source-sync首次发布应采用本分层，不先冻结单actor架构。

| 入口 | 变化与边界 |
| --- | --- |
| Core新增POST `/internal/v1/conversation/ingest-actors` | `fanout_request/fanout_response`：一次物理输入，明确请求targets；返回物理receipt及逐actor结果。单个旧ingest_response无法表达A/B多个receipt，故另加操作，不能把多结果偷塞旧响应。 |
| Platform候选POST `/internal/v1/source-access/read` | candidate.2的`operation=input`返回已核验物理输入、服务器路由默认值及逐actor授权上下文；`operation=current`验证已受理角色来源的当前使用权。这是两个固定业务操作，不是任意action/租户权限框架。 |
| Core候选POST `/internal/v1/source-facts/read` | candidate.2用`selectors:[{key,actor_id}]`明确取角色受理，另外返回所需去重的当前physical facts；仍支持head/metadata，不以“最新一个scope”冒充全部actor。 |
| Memory候选POST `/internal/v1/memory/source-sync/check` | 外层scope已给actor，sources保持common.source；从scope.actor构造A选择器并核对actor receipt。返回原text版本域，不扩为跨actor版本查询。 |
| 已发布Memory select/revise/turn-commits与profiles/select | 形状不变；每条raw source由外层已授权scope.actor与receipt解析为A。内部ledger、lineage、候选来源幂等键必须升级为A。不得只凭P查一条sources行。 |

`common.source.receipt_id`继续表示Core实际受理回执，candidate.2把它正确持久到某个actor受理版本；物理receipt是新sidecar字段，不能放进旧source冒充actor receipt。source本身不是独立授权令牌；没有外层scope/selector或receipt归属证明就拒绝。Core owner turn及旧committed_event均只属于一个actor，sources中只能是该actor的receipt；事件全字段owner核验继续执行。

旧v1 retract仍是原物理消息撤回；适配时不能解释为“只撤当前actor”，也不能将其旧形状控制回执作为新的可召回actor来源。群输入的actor admission只证明该actor获准接收渠道输入，不替代Memory既有的群共享/画像精确批准；私密记录不能借本次角色分层变成群投影。

迁移时旧v1 ingest保留单actor入口（旧空targets只取其已验证scope.actor，不扩为所有默认actor），可由新内核单actor适配返回原单个ingest_response。legacy receipt只能从原始可信admission、完整输入与scope建立一条持久`legacy receipt → P版本 + A`映射，原receipt值不改；新物理receipt由Core迁移事务明确关联该历史受理。不得给B复制A receipt、按消息键猜actor或让source查询任意选一条。旧混组/归属不明继续隔离503。旧Memory suppression只迁移到被证明的A；无法证明时不自动开放。新旧都不可用opaque ID前缀推断actor。

## 3. 入站、空target、授权与首次映射

Platform先在受信实际渠道/登录入口登记精确`source_input`事实：原始account/channel/message_key/kind/input_digest及有效期，不依赖person、conversation、turn或Memory来源验证。它证明发送者提交/编辑/撤回自己的物理消息，**不授予任何角色记忆权**。Core仍核对已存在P的原作者，别人不能改同一message_id。平台当前rehearsal还未实现此精确输入事实，需producer任务补齐，不能用请求自报digest当证明。

Core调用`source-access/read operation=input`，Platform依据上述已登记事实和当前简单entry/actor路由返回`input_authority`。其中`default_actor_ids/routing_version`来自服务器登记，actor_contexts是逐actor的真实认证结果；每个context.actor、author、channel、caller/receiver、期限均须匹配。这些context是工具/服务端边界的返回值，不能由浏览器或模型提交。可信入口应用可为本次已验证物理输入签发各获准actor的独立origin引用，沿用已有resolve对单actor的语义；不重新要求用户逐角色点审批。

input_authority与物理fact同时给出已核验的audience；Core还与自己登记的channel binding核对，Memory核对每个admission的audience/conversation与物理事实一致。不能让同一private channel的B scope被改写成group。Core在最终受理事务前重新检查实际当前时钟、deadline及授权有效期，不能使用等待Memory期间已到期的快照。

- 新message的非空targets表示精确路由意图，不授予权限；空targets只取该入口已登记的默认集合，不等于所有角色。默认集合为空则`unrouted`，只报告物理受理，无actor receipt、collector或候选。
- 即使默认值列出A/B，也要逐actor核验。只获准A而要求B时，B结果forbidden且绝不产生B receipt/collector/候选；合法A可明确partial成功，不能回全成功。仅物理receipt不表示任何actor已接收或已回复。
- 所有外部核验/Memory身份调用在Core事务外；Core同一SQLite事务提交P、成功的A行、collector、逐actorreceipt和原命令结果。全局错误（物理来源伪造、同revision异正文、旧revision、容量不足）无新受理；单个actor无权可作为明确拒绝结果，同事务保留其他合法actor结果。网络/DB故障不编造accepted。
- 每个actor受理按`(P,revision,actor)`幂等；同消息分送A/B共享物理receipt而各得自己的receipt，重复A不会抑制B，也不重复A记账。命令级幂等仍绑定服务/操作/key及完整语义输入、requested targets。空targets首次解析出的集合和routing_version写入原结果；重试重验原集合权限并返回原结果，不因默认配置变化扩成新角色。新路由意图用新命令key，且仍受actor受理去重。

首次入口的actor_context.allowed_scope.person_id/conversation_id都可null。对有成功actor授权的输入，Core先凭其中一个真实actor origin调用现有Memory resolve/register取得唯一person/binding_version；其余actor的非null映射必须与这个真实响应相符。再在Core按channel建立/取得单一conversation，写入各actor独立受理。Platform受信转发适配器验证批量响应中所有成功receipt的account输入、同person、同conversation与逐actor关联，再通过本地prepare/confirm映射流程回填一次；响应丢失按原key重取。若只做物理受理且无合法actor，不需要虚构person；后续第一次actor受理再走身份。身份流程从不调用来源select/consume/check，也不等committed_event。新的Core来源读取仍只读Core，不回调Memory。

## 4. 编辑、撤回、角色遗忘与同步

物理edit/retract是原作者/可信渠道动作，不要求他同时持有所有旧收件actor的当前授权。retract请求不带角色targets，含targets则拒绝含混意图；“只让A忘记”应走Memory精确scope的forget，不能伪装物理retract。

Core先将已验证edit/retract写入P当前revision及全局水位；任何已有A/B受理的旧revision都因此过期。edit的新正文只有在某actor的**当前**授权通过后才成为该actor的新receipt/新输入；正常转发可对原收件集合逐一取得本次编辑的当前授权，失权者只失效，不重新收件。若此轮只成功A，B保留旧receipt作为历史，但当前性检查因P版本不符而拒绝；B后续经可信当前授权重投同一编辑版本才产生自己的新receipt。不能拿B过期入口ref或单凭A授权自动签发B的新receipt。物理retract不产生新actor受理，旧的所有actor来源均不可再用，同P后续高revision也不能复活墓碑。

Memory屏障仍是Core C1 → Platform P1 → Core C2 → 本地失效事务提交 → 业务事务。不同之处：

1. coverage中的每条角色依赖使用A，查询必须带actor选择器；物理keys由这些A去重得到。Core必须为每个selector返回精确受理或missing；missing不能用另一个actor的事实补。
2. Memory事务内保存共享物理current/tombstone和私有A行。可信P更新触发**本地所有已登记actor受理的反向血缘**失效，包括这次查询没请求的B；不需要读取B正文或取得B的正向授权来使其过期。各actor的pending jobs、已commit完整组、投影与版本在同一事务失效，双owner水位随之提交。不能只更新本次A让旧B缓存继续current。
3. 正向激活/新建A必须同时匹配P当前revision/physical_receipt、该actor receipt、author/scope/Memory binding与Platform针对A的当前grant。新P存在、另一个actor已授权或共同世界都不能激活B。对P的否定广播不授予任何读取权。
4. Memory correct/forget的suppression按A持续跨revision，A的确认只禁用A及其派生血缘；B的同P记忆不因此被删。源级scope仍包含person/受众/会话，确认不能借actor选择器扩大范围。候选写入幂等/关系累计也按A+revision+scope，不跨角色相互吞写或重复。
5. P墓碑全actor不可恢复；A suppression独立不可被新P/新actor receipt/旧event清除。权限仅撤A也只失效A；物理撤回则失效所有已登记actor。普通reply cancel不影响P或A已受理输入。
6. 私有epoch与公开画像epoch仍隔离：A私密变化不推动B版本或任何无活动投影的公开epoch。P撤回可以使A/B各自仍活动的投影失效，各自在自身公开/群域推进一次；响应不返回其他actor的数量、存在性、血缘或版本。

后台Memory consume/commit使用认证Core完整owner event + 当前P/A/grant，原短期entry ref可作为历史admission定位，但不是当前授权。本候选不放松此既有规则。旧version域和30秒valid_until都不能当离线授权。Core/Platform任一水位回退或换代仍失败关闭，双owner恢复与墓碑/各A suppression必须一起核验；不自动接纳恢复快照里的allowed。

## 5. 同一会话两轮与发送顺序

P共享不意味着把A/B放进同一语义collector。Core为每次actor实际受理分配会话全局ingest_sequence（一次fanout按固定actor_id排序分配，命令重试保留原序号），collector含actor且保留自己的消息边界。到期封存按deadline/首次受理序号确定顺序，在同一Core事务中为整个会话分配唯一递增turn_sequence。

同会话最多两个活跃turn，计数跨所有actor；A的T1与B的T2可并行生成，第三轮无论哪个actor都排队。发送仍全会话按(turn_sequence,segment_sequence)；B先生成完也不能越过未封账的A。取消A回复可按旧终态规则释放槽/顺序，但不撤回其用户输入。未知回执沿用closed_unknown/核对语义，不能因另一个actor要发送就假定前一轮已达。

各轮发送前重新验证自己的P/A和text/profile两域依赖；17eba4f已保存的同actor跨作者context_checks继续保留。不能把A的私密画像检查移给B以共享生成结果；跨actor可共享的群消息/世界事实必须有B自己的可见来源或明确共享投影。共同会话顺序不赋予跨角色私密记忆访问权。这里未新增分布式发送事务或锁租约。

## 6. 最小后续实施及验收

Core任务按本分层拆分物理inbox与actor admission键、collector键，保留共享conversation与发送队列；实现新fanout操作、精确facts selector和P修订广播，迁移/隔离旧混组。Platform任务补精确物理输入签发与两种source-access操作、服务器默认路由及逐actor权限，不把default或source_input证明当actor授权。Memory任务将来源/血缘/幂等/suppression键升级为A，并在本地事务将P变化传播给所有已知actor；不让旧global source_key覆盖新架构。

联合合成验收必须覆盖群与私两种channel：A/B不同消息分collector、同一P分送两角色receipt不混、空target默认非通配、只授权A不能冒领B、edit只重授当前授权角色但让所有旧版本失效、P retract跨actor/跨重启失效、A forget不删B、两轮跨actor并发但全会话有序、首人/会话映射不循环及错误回填拒绝。schema/参考模型只证明离线关系；实际产品实现、真实入口和并发/迁移验收另做，不据此宣布已支持多角色L0。

可执行正反联合例见`tests/contracts/source-sync/test_multi_actor.py`，每个适用场景同时验证group/self_private：

| 场景 | 正例 | 反例/边界 |
| --- | --- | --- |
| 相同P分送 | 同physical receipt，不同actor receipt/collector，同conversation | 改targets复用同命令key冲突；A receipt不能冒领B |
| A/B不同消息 | 按actor分collector，仍同会话 | 不能以物理键覆盖另一个actor受理 |
| 空target | 固定服务器默认集合；空默认为unrouted | 客户端default字段拒绝；重试不能扩大角色集合 |
| 只授权A | A成功、B明确forbidden | B无receipt/候选，缺B事实不能用A补 |
| 物理edit | A新受理使所有旧角色来源过期；B获新授权后才重收 | 仅同步A也必须让本地旧B缓存冲突 |
| 物理retract | 无B新授权仍广播失效pending/committed并跨重启保留 | 高revision不复活；回滚不能留下半次跨actor失效 |
| A forget/撤权 | 只影响A，B仍可用；新edit也不解除A suppression | actor私密活动不推动B epoch；回退平台allowed不得复活A |
| 两轮 | A/B并行生成，共同全会话序号/段序 | 第三轮不能按actor另开槽；B不得越过A发送 |
| 初始映射 | null逻辑ID先由Memory/Core实际owner补齐，各actor共享person/channel映射 | 不读SourceAuthority；错person/conversation或receipt别名拒绝 |
