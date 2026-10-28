本包由协调者冻结，供TS306/307本地实现与TS308隔离验证；schema通过不表示产品或真实链路通过。旧已部署合同字节和真实数据未修改。

## 当前默认：结合上下文的后台整理（schema5，本地候选）

本次用户授权将普通自动学习改为有来源的经历摘要，画像跨多段经历归纳；下方schema3/4原子事实约束只用于已锁旧任务恢复或明确的精细事实能力，不再作为普通经历的前提。新协议仍走原candidates/process、jobs、write_ledger和后台worker，不新增聊天档案或学习调度器。

Core沿原scope队列逐job整理，在同一SummaryWindows应用入口本地读取并持久化有界对话窗口。首次process携带job_id、只含该job的job_ids、supported_extraction_schemas:[5]、output:null及window。Memory在原事务认领该job的精确窗口；不增加跨任务合并、followers或部分覆盖协调。batch_ref是单任务的持久整理引用，cursor={window_id,job_ids}只随该job真实回执推进。未知结果先查原回执；明确unclaimed时仅重送原冻结窗口，不能猜测未执行或重生成提案。

窗口定义为summary_window及summary_window_manifest：1个新turn/job、最多12个相关turn、32个完整成员、48KiB正文，总请求256KiB。单任务仍包含真实来回上下文，不是逐句提炼。成员保留发言者、顺序、实际source scope、binding、版本、观察时间及new/context角色；BOT只纳入实际sent且持久Memory依赖完整的回复。正文不截句，省略如实写coverage；不是完整聊天档案。window_id为除自身外manifest的canonical JSON摘要，不是授权。

job_ids与manifest.new_turns对应同一原jobs.event的turn_id/input_revision，新source文本完整纳入。容量不足时省略可选context；单个新输入仍超限则明确失败，不能省略new来源后仍宣告saved/推进cursor。window.members与manifest.members的member_id必须唯一、顺序和集合相同；Memory重算实际UTF-8正文hash，Core仅收到manifest的check不能代替这一输入绑定。

Memory复用已配置Core来源服务身份/CA，将其source-facts固定路径替换为POST /internal/v1/memory/consolidation/window/check。请求/响应使用summary_window_check_request/response，不使用模型提供的地址。Core重核精确成员、正文摘要与真实送达，不重新选择窗口或依赖全room增长版本。Memory另核当前人物绑定、全部依赖及suppression。memory_dependencies的三个数组逐项使用已注册memory-context/v2的group_dependency、episode_dependency、source_dependency定义验证，禁止另解释为授权。该检查证明解释材料当前可用，不证明摘要每句话语义为真，不做逐句裁决。

窗口接收/提交使用上述窄check；日常读取将需要的turn与source引用并入现SourceAuthority批量source-facts屏障，不按全部历史窗口逐项增加RPC。assistant只有在同window同时包含其turn的完整human input成员时才可纳入，fresh turn.input_sources/input_revision须精确匹配。窗口check已证明该reply真实sent；Core现owner禁止sent降级、同reply正文或本段channel_message_ids变化，因此后续可用fresh turn.reply_ids及输入版本复核。receipt_digest固定为canonical投影{reply_id,state:'sent',channel_message_ids}的摘要，保留ID顺序，原完整回执另存，不把可变receipt_id/observed_at纳入该稳定投影。assistant.observed_at取该reply固定attempted_at，只表示实际投递尝试时间，不冒称精确首次送达时刻。若将来owner改变这些不变量，应同步更新该批量证明，不能静默继续使用旧假设。

模型输出只有summary、portrait(null或文本)，可同时给importance=low/normal/high，缺省normal。Memory固定绑定整个实际输入窗口；模型不选择人物、权限、target或证据闭包。至少有一个已保存的独立摘要与本次新窗口时，才提供portrait_input={expected_revision,summary_refs,summaries,current_text}，固定其完整依据。画像是可修订的推断，不自动授予公开分享范围；可选画像未更新时回执如实pending/not_requested，已保存经历和摘要不回滚。来源、权限、SQL等实际失败仍失败。

current_text也是实际输入：Memory冻结expected_revision对应旧画像，并继承其全部叶来源/依据闭包，再与本次summary_refs、新窗口取AND。正常替代后的旧画像不需永久current，不能通过反复替代形成循环或丢失旧来源的遗忘依赖。这里跟踪的是实际提供材料，不逐句裁决画像含义。

summary_completed_response给出每个实际覆盖job的真实回执，result分别报告experience_state、summary_state、summary_record_refs、portrait、covered_job_ids与cursor。摘要/画像复用原group/unit/lineage及派生检索，标记inferred，不能伪装精确原文span或已裁定事实。自然到期仅退出日常召回：现运行配置memory_consolidation.ttl_days可选，默认low7/normal30/high180天，从真实recorded_at计算；重复作为context不续期。自然到期不删除原文、不写suppression，也不使画像的历史依据变成撤回。显式遗忘/删除仍抑制来源及其派生，不能从旧BOT回复、摘要或Chat自动复活。

BOT自主生活长期记忆遵循actor-self-memory独立领域设计；本协议整理用户经历，不能给BOT造person或声称AL02–04已完成。Chat原文引用可供后续精确历史回查，当前窄搜索尚未接通，不把管理员查询转给模型。

# Q21 本地联合开发合同候选（未部署）

唯一共享合同发布者为 root。此目录仅准备 schema、完全合成正反例和关系约束；不代表产品/HTTP/PG/真实模型验证。依赖直接引用Q20精确已物化合同，未复制 common/scope/query/source/history/profile 规则。v2 事实表示直接引用新 memory-extraction/v1；注册表本地加载，无网络解析。输入目录为 `C:/Users/Administrator/Documents/Codex/2026-10-13/task/upgrade-20261013/quality20-20261023-9893cbb68da84b1ea082d7a8281f1bc7/contracts`，不读取协调root当前contracts混合版本。

## 文件与验证入口

- `memory-extraction-v1.schema.json`：拟放 `contracts/memory-extraction/v1/schema.json`，包 v1 的抽取协议字段为4。
- `memory-context-v2.schema.json`：拟放 `contracts/memory-context/v2/schema.json`。
- `frozen-input-dependencies.json`：当前输入 schema 文件 hash、全部对应manifest原始与LF规范化hash；root发布时继续固定这些依赖。
- `extraction-positive.json`：read/pending/submit/completed和同scope fixture权威。
- `context-positive.json`：query/request/response与verify/request/response；包括名称完整witness、规范位置、原文episode、另一个无匹配问题，及select/history/input_history/profile四类旧物料验证。
- `context-positive-multiple-owners.json`：同一子查询“我的猫和小明的猫分别叫什么”，self+named两个服务端解析目标，各自原owner事实。
- `context-positive-partial-owner-resolution.json`：self可证明但另一名字未知，只返回已证的自己侧，status=incomplete/omissions=unresolved_reference；保留未知target，禁止变成room扩搜。
- 六个 `*-negative-*.json`：两包各有正例，抽取2反、v2读取4反。missing-fact为JSON Schema反例；cross-owner、unproved-no-match、cross-room，以及bundle预算/数量上限与原请求不一致，为合法形状但关系不成立的反例。
- `validation-result.json`：只记录schema和fixture关系实际执行，绝不替代产品集成结果。

本地重生成并验证：在TS306运行父目录`build_q21_contract_drafts.py`，使用已安装jsonschema/referencing的现Memory解释器。精确schema入口为`$defs/read_request`、`pending_response`、`submit_request`、`completed_response`和v2的`query_request/query_response/verify_request/verify_response`。生成器的`valid/rejected`通过本地Registry加载旧五份schema+新两份；`extraction_relations/context_relations`仅验证此处合成例的关键关系。

## 协议协商与持久恢复

现候选路径不变：`POST /internal/v1/memory/candidates/process`。

1. 新读取：`{job_id, output:null, supported_extraction_schemas:[3,4]}`。列表明确声明客户端实际支持；已锁旧job选择3，未绑定新job优先4，不能以400后猜测降级。
2. Memory只在schema4数据结构已显式迁移且客户端声明支持时返回`extraction_schema:4`的pending；不从输出是否有fact推断版本。
3. 新提交：`{job_id, extraction_schema:服务端已选版本, output:{memories:[...]}}`。所有经新协商读取的pending/完成/拒绝/失效结果明确带3或4；旧3提案的原statement/fact结构保持，不按4重解释。
4. 旧 `{job_id,output}` 按既有schema3处理并保持旧响应形状。新Companion不得把400/不支持吞掉后改用3。
5. jobs新增nullable `extraction_schema`，显式迁移把既有jobs锁定3；新jobs首次read协商时在原job事务CAS绑定。已绑定的job不可因升级/重试切协议。新jobs建表插入必须改用显式列名，不能因新增列破坏旧四列VALUES路径。
6. 已持久的schema3提案继续按3恢复；receipt仍是原write_ledger。版本不匹配用明确HTTP错误，不调用模型去反复“修复”已经固定的包。运行时错误沿现transport error，rejected只表示该提案没有提交的确定事实。

新提案最多32原子项，与既有workflow的32 drafts上限一致；无自由statement，所有条目必须fact。未知有效时间为null。negation/conditions/valid_time仍保留作者表达；模型不给scope、person或权限。

schema4的proposal/stored/known事实共用必需modality：asserted（姓名偏好等自述）、observed（来源报告已发生）、planned（计划，不代表已执行）、conditional、uncertain。实体/事件+predicate+modality仍构成默认属性槽，assert/add不会跨模态自动退休其他槽。显式replace须绑定同scope、主体、predicate、reality与cardinality的精确目标，可按新来源报告改变modality，并仅退休指定目标；confirm/retract须保持目标modality与值。predicate不再重复.planned/.observed后缀；例如event.departure+planned和event.departure+observed可并存。渲染不得从planned写出“尚未出发”，也不得把observed说成客观真理。schema3旧事实仍legacy，不猜modality。

## 抽取关系规则（JSON Schema以外）

- E01 每个quote是对应source_index原文的精确子串，索引存在且源当前可读。subject entity/event mention必须出现在该条自己的quote中。
- E02 author绑定可信事件scope.person_id；实体/事件id只能是Memory已知同scope subject或服务端新建，模型不能发明id选择陌生对象。
- E03 targets全部当前、有权、同owner/subject/predicate且record_version相等；目标完整语义组不能拆写。跨scope target拒绝整项/本job，不复制他人旧值。
- E04 已知目标cardinality不能被候选更改；set add不退休其他成员。replace/retract/confirm要求精确targets：replace比较同一业务属性身份，允许有源的模态变化；confirm/retract不得改变目标模态或值。confirm同条件同有效期；retract非空有效期只能继承目标。确认/撤回的控制时间来自新来源observed_at，不能把继承的旧事实有效期当成操作发生时间。无target的计划/实际槽保持独立，不从计划推断实际发生。
- E05 自然语义retract不等于forget，不签发用户权限/修订证明，不删除来源；forget仍遵循原证明/suppression/tombstone流程。
- E06 新值/时间只由本次有源assertion支持；known_facts只能解析身份与更新目标，不能作为新事实证据。混合legacy标记不可假装原子化。
- E07 原文临时动作可保留episode，但不能只因为实体已知就升为永久画像。身份无法唯一链接时，保留有源episode并明确未决，而非丢失全部信息或猜owner。
- E08 相同job/schema/output重试复用原receipt；不同schema或不同已提交输出不能产生第二次写入。

## v2 路由、预算与数据

`POST /internal/v2/memory/context/query` 与 `POST /internal/v2/memory/context/verify`。认证、origin resolver、bounded admission、当前授权、事务/guard仍共用既有能力。

query最多4子查询，总体默认8个事实单元+8个episode并共享一个tokens/bytes budget。limits可缩小，不能以4子查询各得8+8放大；完整group、owner witness、source依赖预算不满足时整组省略。schema的default只是说明，客户端显式填默认值。

v2 evidence中current/historical/legacy fact与episode明确区分。事实unit由规范fact渲染；episode.text是原文连续UTF-8 span。episode的`source_episode`只是一种Memory返回引用，不伪造既有shareable_projection数据库记录；bundle必须连接它的原source/revision/epoch/hash，客户端仅据它引用文本，不能授权。

observed_at是来源观测时间，valid_from/to是事实有效时间，event_time是有源事件时间；三者不互换。没有明确日期/时区时保留表达，不填合成时间。source_refs、bundle及ID不作为模型指令或身份工具输入，表达层只把证据文字作为数据。

## v2 查询/验证关系规则（必须联合核验）

- Q01 顶层origin由Platform真实解析；effective_scope等于已授权请求scope。群只读扩展只限原同actor/audience=group/conversation且有权原owner；私聊关联只沿现显式批准的直接关联，不传递合人。
- Q02 query_ids唯一，queries/results/selection_constraints一一对应、顺序一致；evidence_ids唯一且全部能映射到实际完整evidence。bundle中的queries/selection/budget/limits/include_associated与请求一致；budget和limits是bundle的required字段。实际budget_used不可超原请求；完整返回事实（包括姓名witness）和episode分别受总limits约束。
- Q03 每个fact的person、owner_scope、完整unit/group成员、source_refs和版本一致。名称唯一性witness的全部unit必须随返回证据进入上下文；不能只返回目标值却隐藏用于归属的证据。
- Q04 每个episode引用精确source与版本/epoch/hash；span必须位于原UTF-8文本内、在有效字符边界，原字节hash与text一致。观察时间来自源；source撤回/forget/suppression覆盖episode和向量。
- Q05 no_match必须有该query的显式no_match proof、零结果、真实candidate-domain hash/count/complete。索引未覆盖/预算截断/未知指代不能伪装no_match，更不能把no_match解释为“历史从未有过”。history_complete固定false。
- Q06 owner_resolution以targets[]为唯一目标真值；每项绑定target_id、kind=self/named、mention、status=resolved/unknown/ambiguous、person_id及witness_group_ids。mode=multiple可在同一子查询同时包含self+named；无独立单值resolved_person_id或第二份名单。已解析named必须有unique_owner proof、完整witness，且服务端重算完整授权候选域的唯一性。self必须绑定本次requester。未知/歧义目标的person_id为null，不能改成room；部分已证时status=incomplete并标unresolved_reference，返回证据只属于已解析目标。person_id不是客户端授权，候选域hash不能直接当已验证事实。
- Q07 latest_values proof覆盖同subject/predicate当前后继判断；只复查已选旧source不能证明没有新值。所有candidate-domain hash按服务端当前授权域规范数据重算；hash不包含未授权对象，不通过计数泄漏其他私聊。
- Q08 evidence_digest是按canonical JSON(sort_keys,separators=(',',':'),UTF8,ensure_ascii=False)对按返回顺序的完整evidence求SHA256。bundle_id和digest不是签名/访问票；verify必须重新检查真实数据，不能因客户端回显自称server-generated而信任。
- Q09 verify返回valid只在整bundle及所有checks都成立时。checks的check_id/kind精确一一回显；任何失效整体拒绝，无部分成功。checked_at/valid_until来自服务端新clock，且不超过本次真实授权期限。
- Q10 schema4和v2包引用旧合同原规则；v1仍运行，不将旧scope version、history version、profile version错误混为同一个版本。

## verify批量旧物料与完整屏障

verify请求`checks`最多64项，每项携kind/check_id、原闭合v1请求和expected子集。select/profile请求强制零预算；history/input_history保留known_snapshot语义，不能把additive事实更新后的新scope_version与旧值强行比较。

- select expected：effective_scope、scope_version，以及原来存在的association_version/scope_checks；旧物料无后两项时显式null表示不增加旧协议没有的约束。
- history/input_history expected：effective_scope+原snapshot。新scope_version可能合法增加，不作为独立失败条件。
- profile expected：requester_scope、target、原profile scope_version。原target sharing和当前profile权限必须重新证明。

本次主origin、各旧请求origin都按原规则重新解析；旧origin本身不能让本次viewer读其他私聊。service/channel/binding/source_turn_id等Companion本地核验仍由Companion保留。

第一版在同一global operation lock里union所需coverage、做一次fresh完整barrier，再用同一Store快照按各组件规则验证；不是对旧public methods循环调用导致每项重复barrier。对无结果、姓名唯一性、当前无后继，必须保留完整selection_constraints和完整domain验证。不会删除全局lock/CAS，不会把TTL当跨模型/发送边界授权。任何owner head变化都走现完整验证；更精细增量watermark需另有合同证据。

## 实施前注意

新增表/列、authority tracking、派生索引不影响source_revision，以及旧PG库在迁移前可打开/备份，见上级`q21-memory-design.md`。本草案中的原文、人物、范围和时间全为合成例。当前注册表验证为18个正向文档、1个schema反例、6个关系反例，包含旧ACK缺失后的显式schema3协商、计划/实际独立槽、显式有源模态更新及错误确认/撤回拒绝。root发布前可调整形式；需要双方同时装配精确manifest后才能认为接口已落地。


## Typed适配、目标证据与来源级回执

本人姓名在typed适配边界复用既有identity.name/person.name/author.name/name规范化，canonical.predicate、兼容key、field与渲染使用同一identity.name。仅适用于author主体；其他人、宠物或物品的名称不能转成本人姓名。完整原始来源、逐字引文及正向本人声明证明仍必需，规范化不授予身份或权限。

confirm/retract的控制意图不必重报目标内容。目标必须先通过scope、来源版本、现实性、精确记录版本及同值同模态检查；派生内容继承完整目标lineage/evidence。姓名断言的内部引用只能由已验证目标的正向姓名证据生成，读侧重新校验源文本及引用覆盖；控制意图保留完整依赖，不能冒充正向姓名断言。独立assert/add/replace不可借旧证据制造姓名或有效日期。公开提案没有内部证明字段。

forget_episode是精确episode/source操作，其成功回执record_ids/group_ids为空；全部相关记录、组、经历与索引仍按完整来源闭包失效。当前operation_id及持久请求摘要绑定具体episode目标；空列表不表示没有影响，也不能凭回执猜测已撤回内容。
# 首次窗口的可选上下文失效

首次capture结果未知时，Core保留完全相同的原窗口，先以`output:null/window:null`查询真实整理引用。仅当Memory在当前事务确认该job仍pending且没有窗口归属，才返回`unclaimed`、`extraction_schema:5`及原`job_ids`；它只表示尚未认领，不表示学习完成。Core据此重送原冻结窗口，不能重新挑选材料。已有引用继续返回pending/真实终态；任意错误或超时均不能推断为unclaimed。查询之后发生同job并发认领，由原幂等逻辑处理。

仅尚未认领schema5批次的首次read，在完整新输入仍有效且已明确核实可选context来源失效时，可以返回`context_changed`、原`job_ids/window_id`和精确`invalid_context_member_ids`。此状态明确表示尚未创建batch，不是完成回执或游标推进。Core核对这些ID确属原窗口context后，在模型前移除完整旧context轮次及依赖它的BOT材料，再对同批完整新输入重新捕获一次；新轮次内只有BOT context失效时只去掉BOT，不删新human。

身份/权限、配置、SQL、未知结果、无法精确归属的失败及任何new输入失效不得降格为该状态。已经pending或提交结果未知的任务固定原窗口，必须按真实回执恢复，不能更换材料。自然到期不属于来源失效。


## 2026-10-25 显式人物归属

summary_output、pending与summary_receipt必须携带subject_person_id，唯一真值为已持久job和window.scope.person_id。Core在模型材料显式提供subject及subject/context成员标签，模型output、服务端提交与客户端回执均核对该冻结对象；不能由名字、账号别称或文本内容猜测、替换person。

portrait_input携相同subject；每个summaries引用仅提供其原manifest中同subject、真实human的subject_material，含member_id、真实speaker/account、observed_at和完整原文text。原同scope多人摘要不再作为画像正文依据。来源版本/epoch/hash/span或正文无法完整恢复、成员不属于同scope/subject时整basis省略，不截取文本凑主体；每basis原文合计沿原6000字符上限，最多16basis及完整256叶依赖预算保持。所有refs和来源闭包仍被验证。current_text只继承同owner、在新显式归属batch中生成且仍有效的画像，旧未绑定画像从合法原文重建，不删除原档。解释性summary允许记录他人与BOT在此人的经历中的行为，不能把叙述中的每个主语等同于owner。

仅既有memory_batches.portrait_input JSON列保存内部{subject_person_id,input}绑定信封，不新增表或迁移。公开input形状仍是portrait_input|null。已完成旧schema5先查真实ledger，回执的subject只作从真实job.scope派生的RPC投影，不改旧stored result/digest。legacy_summary_output仅容许旧精确提案到达已完成ledger重放校验；新bound batch不接受它。仍pending且未绑定的旧schema5 batch沿既有invalidated终态结束该可选整理任务，保留原batch/window/basis/output与原文episode，不重生成、不换窗口、不把原文标withdrawn、不写旧可能串人的派生文本。旧schema3/4恢复不变。

subject echo是数据绑定检查，不伪称已证明自由文本每句话正确；需要同组多人真实模型样例核正文，不能追加人名或词法规则。


## 2026-10-25 schema6：多人会话片段整理（本地候选）

新默认以封盘片段创建一个job；不先创建逐turn模型job，不建立leader/follower或第二个调度器。Companion现MemoryLearning保存待整理与调度阶段，Memory原jobs/batch/write_ledger拥有真实任务、冻结输入及完成结果。

materials为POST /internal/v1/memory/candidates/materials，接收segment_material_request；前台传真实committed_event，旁听只传真实observation_refs。已归档不等于Core admission。旁听先经Platform原verify端口operation=segment，以actor_id:null发现当前实际actor/channel；后续必须固定非null actor。逐源完整匹配当前read权限、作者、物理会话、digest、scope_version和archive_epoch；authority来自真实catalog版本及完整批digest，首末完整校验，不能从昵称或source_ref猜scope。未配置actor只保留原归档/查询能力。

前台/旁听去重另以同端口operation=segment_resolve接受真实完整Core source+scope，利用Platform已持久event/message_id、reply连接与已确认admission精确反查；保持请求顺序，回真实observation七字段或明确unmapped=null。已知映射尚未confirm/archive必须503重试，不能提前当unmapped造第二份材料；不能用文本/时间或当前slot猜历史。旧手工adapter缺原native映射证据时保留原Core范围，不伪造相同物理身份。匹配的两入口复用同一个真实material_ref和覆盖结果，actor不同不合并。

Memory复用Core原window/check端口，segment_frame_request由Core分配并精确复核会话映射，返回segment_frame_response。Platform证明稳定只读channel与实际delivery_channel属于同一actor/物理会话，Core在已有Store将它们解析到唯一会话状态owner，保留原始来源和发送channel；不创建两份序号/计数状态，不造turn或授予回复。新鲜映射或仅一方已有可以复用唯一owner；已经有不同真实convid不能暗合。当前catalog策略换代不等于撤销历史可读，真实archive_epoch/actor/read权限变更仍核。旧manifest检查形状保留。原observation-source/v1及text-dialogue/v1发布字节不改；新增proof及unit/source扩展均在本候选包内。

seal为POST /internal/v1/memory/candidates/segments，使用segment_seal_request/response。固定稳定segment_id、actor/audience/conversation、所有targets及新材料；创建丢ACK按同段幂等恢复。材料在等待期间被明确删除/撤回或原修订已失效，只有真实持久来源证明成立时，沿原jobs/memory_segments记录同包invalidated负终态并返回真实job_id；不创建claims、batch、write_ledger或coverage，也不调用模型。任意403、503、未知回执及没有持久来源失效证明的409均不据此转换。已有真实完成账本优先，不降级为失效；负终态丢ACK也按同segment_id和原digest取回。材料受理响应最多256项，沿原Core collector合法事件上限；分段owner在完整材料边界分段，不因单段32成员上限拒收整个合法turn。封段输入预算为32个完整成员、48KiB正文、8个目标，单人最多8条记忆、全段最多32条、8个话题；来源proof仍按原完整边界执行。预算不满足不能截正文后谎报覆盖。seal声明omitted_context，segment_window.coverage.complete_new固定true，只表示新材料完整，不是完成账本。

process沿既有candidates/process，extraction_schema明确6，使用segment_read_request/segment_submit_request。pending冻结完整segment_window；model只返回提供的逐人槽位、消息引用、记忆和可选画像，不提交scope或权限。成员的real/fictional沿真实来源标记，不从词句再判；reply引用分别保留真实Core message_key对象或旁听native ID，引用及@沿原每输入256项界限，不字符串化丢channel/版本。results必须恰好对应targets，covered_member_ids恰好等于该人的新成员；明确零条记忆可完成，漏人不能完成。身份及引用校验不证明正文语义正确；真实多人物正文仍需产品验证。

任一必要来源、作者授权或CAS不符，整个段不写；不能循环旧单人提交或临时裁人。一次有界原子事务按逐人scope复用原记录/索引/lineage，并写一份segment_receipt，含逐人真实覆盖及可选画像状态。unknown保留原segment/window/output及digest，先查唯一ledger；不能更换输出/窗口重放。确定invalidated使用segment_invalidated_response，不将暂时503或unknown当作已失效。

每条记忆的重要度与short/medium/long保留建议独立；自然淡出不等于来源撤回。真实新表达可以形成新版本；BOT复述或重复上下文不能自动续期。约定的考虑、确认、完成、取消由上下文整理，更新仅指向输入提供、同owner且版本仍真的prior_refs；到期不自动标完成。显式记忆工具、删除和suppression继续沿原路径。新segment_portrait_input允许真实首次CAS为0、空历史refs，用本段本人材料形成可选画像；不强制首段写，不捏历史依据，旧portrait_input保持原约束。

真实observation来源使用observation_source，segment_unit复用原unit形状并列真实来源；不伪造Core raw_message。仅本地memory-context/v2的派生summary/portrait evidence接受该候选扩展，typed事实及旧发布的unit/source形状不改。每个实际输入材料与全部继承叶来源的删除闭包保留，模型声称未引用不解除依赖。

旧schema3/4/5冻结任务仍按其原协议恢复；schema6不重解释旧提案、旧manifest或旧回执。segment-examples.json只含合成的AB/CD交错话题、显式空结果、真实来源形状及结构/关系反例；schema通过不代替产品、TLS、PG、压力或真实模型验收。
