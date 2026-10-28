本包由协调者冻结，供TS306/307本地实现与TS308隔离验证；schema通过不表示产品或真实链路通过。旧已部署合同字节和真实数据未修改。

## 当前补充：解释性摘要、人物概况及自然淡出

普通自动学习改用memory-extraction/v1的schema5上下文后台整理；旧schema3/4事实与精确目标恢复保持原语义。新evidence.kind=summary或portrait沿既有unit/来源形状，uncertainty固定inferred，valid_from/valid_to固定null，附importance及expires_at；它们不是原文span，也不冒充确定事实。observed_at为来源观察时间，保留期另外从真实recorded_at计算。

复用现subquery.intent，不增加重复的查询开关：current省略自然到期的摘要/画像及被schema5真正new来源覆盖的原话候选，history/mixed可取回自然淡出的材料。context重复使用不续期。完整来源/身份/范围及显式suppression始终生效，不把自然到期当删除。未被schema5覆盖的旧原话暂保原行为，不自动猜旧recorded_at或迁写旧库。

summary/portrait沿原facts槽作为衍生材料选择，与typed事实合计受原facts<=8和完整总budget约束；kinds包含facts才选择。episodes槽仅容纳原话，不新增第三配额，也不让各子查询重复取得完整配额。

summary/portrait是带真实owner标签的解释上下文，从原collect已授权frame内按内容相关性选择：私聊只用原scope及明确批准的直接关联，群只用原同actor/会话授权范围。其可用性不以typed NameView成功解析名称为前提；未知named目标仍保留unresolved/incomplete及省略说明，不能用摘要匹配伪造unique_owner、no_match或已证的名称归属。此条只覆盖旧Q06对这两种解释性上下文的限制，typed事实的精确owner约束不变，权限不扩大。

历史查询方式由调用者明确选择，不新增中文关键词意图裁决或每轮前置模型。完整Chat查询仍需窄适配；不能把本地历史检索或已有Chat读取候选冒称已实现完整archive搜索。显式删除与自然淡出分别报告，旧失败证据不随协议调整改写。

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
- Q07 每个子查询的candidate-domain hash/count/complete绑定其完整实际owner域：self含本次requester在authorized_scopes中已获准的scope（保留既有显式批准的直接关联）；named/multiple含服务端完整解析后各person在同一完整授权frame中实际获准的scope；room含完整授权room；unknown/ambiguous不扩成room。scope身份必须含actor/person/conversation/audience及现授权关联，不能仅按person跨私聊。该域包含全部active group及完整record版本、episode身份/revision/epoch/hash和完整性，不按关键词、predicate、排名或时间另缩窄。latest_values proof仍覆盖同subject/predicate当前后继，不能只查已选旧source。姓名唯一性每次仍在完整fresh授权NameView重算，新增同名、别名失效或unknown变known均使对应resolution失效。hash不含未授权对象，不通过计数泄漏其他私聊。
- Q08 evidence_digest是按canonical JSON(sort_keys,separators=(',',':'),UTF8,ensure_ascii=False)对按返回顺序的完整evidence求SHA256。bundle_id和digest不是签名/访问票；verify必须重新检查真实数据，不能因客户端回显自称server-generated而信任。
- Q09 verify返回valid只在整bundle及所有checks都成立时。checks的check_id/kind精确一一回显；任何失效整体拒绝，无部分成功。checked_at/valid_until来自服务端新clock，且不超过本次真实授权期限。
- Q10 schema4和v2包引用旧合同原规则；v1仍运行，不将旧scope version、history version、profile version错误混为同一个版本。
- Q11 query准备、外部ranking前后二阶段和verify必须共用同一子查询domain计算与完整resolution重算。只有其他无关owner的新增内容不改变self/明确目标域；本owner新增、目标事实更正/退休、room其他人新增、姓名唯一性改变、来源撤回或权限改变仍按完整规则拒绝。不能只在发送后置校验跳过domain，也不能把旧bundle悄悄改写成新域；HMAC、完整来源屏障和所有已用依赖继续校验。

## verify批量旧物料与完整屏障

verify请求`checks`最多64项，每项携kind/check_id、原闭合v1请求和expected子集。select/profile请求强制零预算；history/input_history保留known_snapshot语义，不能把additive事实更新后的新scope_version与旧值强行比较。

- select expected：effective_scope、scope_version，以及原来存在的association_version/scope_checks；旧物料无后两项时显式null表示不增加旧协议没有的约束。
- history/input_history expected：effective_scope+原snapshot。新scope_version可能合法增加，不作为独立失败条件。
- profile expected：requester_scope、target、原profile scope_version。原target sharing和当前profile权限必须重新证明。

本次主origin、各旧请求origin都按原规则重新解析；旧origin本身不能让本次viewer读其他私聊。service/channel/binding/source_turn_id等Companion本地核验仍由Companion保留。

第一版在同一global operation lock里union所需coverage、做一次fresh完整barrier，再用同一Store快照按各组件规则验证；不是对旧public methods循环调用导致每项重复barrier。对无结果、姓名唯一性、当前无后继，必须保留完整selection_constraints，按Q07完整验证该子查询的实际候选域，并独立重算完整授权姓名域的唯一性。不会删除全局lock/CAS，不会把TTL当跨模型/发送边界授权。任何owner head变化都走现完整验证；更精细增量watermark需另有合同证据。

## 实施前注意

新增表/列、authority tracking、派生索引不影响source_revision，以及旧PG库在迁移前可打开/备份，见上级`q21-memory-design.md`。本草案中的原文、人物、范围和时间全为合成例。当前注册表验证为18个正向文档、1个schema反例、6个关系反例，包含旧ACK缺失后的显式schema3协商、计划/实际独立槽、显式有源模态更新及错误确认/撤回拒绝。root发布前可调整形式；需要双方同时装配精确manifest后才能认为接口已落地。


## 长期原话的完整遗忘生命周期

`POST /internal/v2/memory/context/forget` 使用 `forget_request`，kind固定为forget_episode；目标是Memory已返回的episode_ref/version。操作固定遗忘该原来源的全部span及其派生材料，不能只移除向量或一个片段。仅当前有权source/owner可选，完整来源版本、scope与显式人类遗忘指令必须重新验证。源级suppression、相关groups/jobs失效和两种history版本更新由Memory统一处理；没有事实group也执行，不伪造事实记录。

Platform新增 `POST /internal/v2/memory-context/proof/issue`，使用forget_issue_request；以当前人类source/account/scope/route和完整操作摘要签发，复用原证明账本及v1 proof verify。新target不塞入旧record_id字段，不改变旧v1合同字节或权限。issue响应复用v1 issue_response。

forget响应复用v1 receipt；forget_episode目标为来源，record_ids/group_ids为空。tombstoned对应当前operation_id所绑定的精确原请求，不能凭空宣称写过事实。原context_operations幂等账本及v1 receipt查询用于未知结果恢复；不新建账本、队列或权限层。正常事实forget保持原入口。

示例budget_used按完整evidence canonical UTF8字节计费，tokens使用同一保守字节上界，和当前生产者口径一致。示例只提供schema及关系样例；真实HTTP、proof、PG及撤回行为由联合验证确认。


## 原话的现实性

episode.reality为必需字段，直接引用既有unit.reality的real/fictional域，取当前权威source的现实性。fictional原话可按原权限检索，但必须明确标注，不作为人的现实经历或实际完成状态。不能为避免误用静默删除此类原话，也不能由模型或客户端升级为real。事实继续沿完整unit.reality，不增加第二份事实真值。缺少现实性的episode不满足本v2协议。


## Typed适配、目标证据与来源级回执

本人姓名在typed适配边界复用既有identity.name/person.name/author.name/name规范化，canonical.predicate、兼容key、field与渲染使用同一identity.name。仅适用于author主体；其他人、宠物或物品的名称不能转成本人姓名。完整原始来源、逐字引文及正向本人声明证明仍必需，规范化不授予身份或权限。

confirm/retract的控制意图不必重报目标内容。目标必须先通过scope、来源版本、现实性、精确记录版本及同值同模态检查；派生内容继承完整目标lineage/evidence。姓名断言的内部引用只能由已验证目标的正向姓名证据生成，读侧重新校验源文本及引用覆盖；控制意图保留完整依赖，不能冒充正向姓名断言。独立assert/add/replace不可借旧证据制造姓名或有效日期。公开提案没有内部证明字段。

forget_episode是精确episode/source操作，其成功回执record_ids/group_ids为空；全部相关记录、组、经历与索引仍按完整来源闭包失效。当前operation_id及持久请求摘要绑定具体episode目标；空列表不表示没有影响，也不能凭回执猜测已撤回内容。


## revision9 准备阶段的共同来源验证

新增 `POST /internal/v2/memory/context/prepare`；原 query、verify、history、forget 及 v1 语义保持。该入口只解决当前准备阶段的重复验证，不是跨请求缓存，也不替代任何模型或发送前 fresh 复验。

- P01 prepare_request 复用 verify 的闭合 checks catalog，另有 required_check_ids。每包最多64项且实际canonical UTF-8请求体不超过262144字节；IDs唯一，required是本包catalog的唯一子集。ID不是授权。同ID不得绑定不同完整检查语义；Core先建立不可变全局ID→内容和组成员映射，再分包。任何必要依赖引用的check均按required处理。
- P02 成功 prepare_response 的state=valid仅在当前bundle及所有本包required检查有效时成立。checks必须与本包catalog完全同序、同ID、同kind；每项为valid，或仅optional项可为rejected，reason为origin_denied/dependency_changed。缺失、多余、重复、错序、错kind一律协议失败。返回bundle的原selection、查询、限制、证据、完整依赖与证明语义均不可替换；仅既有fresh元数据可更新。request_id/effective_scope必须精确匹配。
- P03 确定的业务拒绝使用 prepare_response 的另一闭合形状state=invalid，不含partial bundle/results。component=current_selection时check_id必须null，failure.code必须scope_changed；component=required_check时check_id须属于本包required集合，code只允许scope_changed/forbidden。failure.request_id与外层及原请求一致，execution_state必须not_started。HTTP200仅表示完成判定；Core不得继续生成。consumer严格校验后映射原Fault，只有current_selection可设置memory_selection_invalid，保留真实HTTP200来源，不伪造409，不额外发空checks请求猜失败归属。
- P04 invalid及optional rejected只能由明确类型化的业务比较或确定origin拒绝产生。不得catch任意Fault403/409并降级。顶层caller/main-origin无权、共同SourceAuthority屏障、coverage/CAS、数据库、共享issuer鉴权、协议完整性、网络超时或未知结果仍为原HTTP Fault，不分配component，不返回部分valid。当前bundle/HMAC/domain的确定失效与required历史依赖失效必须在其实际归属处区分。
- P05 每个origin仍独立认证解析。同请求可按相同认证服务、完整origin与权限语义复用解析结果，不能用主origin代替另一作者，不能跨请求缓存。只有先验证完整响应schema、request_id、issuer/service/audience/assertion_ref等绑定之后的明确revoked/expired/actor拒绝，才可内部类型化为origin_denied。issuer裸401/403/404、错配或未知状态仍整批故障。可选origin拒绝仅排除其独占需求；别的check或mandatory仍需要的source必须保留。
- P06 Memory在现bundle_operation中构造服务端授权的完整union coverage，保留profile/group_read准确标记、全局operation lock、owner heads、local revision/CAS、来源和负证明；同一事务验证当前bundle一次，并去重验证各组件。check内容/客户端ID清单不能直接当授权范围。共同屏障不能建立时没有任何可用结果。关系读取有reconcile/tick副作用，本批不并入；关系来源scope不能扩正文权限域。
- P07 Core仍拥有本地sources_current、原binding、各作者Platform授权、lease续期、外部book/image/relationship等检查，并与Memory结果AND。完整回复和input_only变体的检查集合各自保留；空Memory集合不代表外部检查已通过。所有分包全部返回且全部required有效后，Core按原组/变体的完整ID集合AND，再按原chronological/linked whole-group顺序、原预算采用首个有效变体。未发送、缺失、未知、超时不可当valid或确定rejected。分包不得声称同一数据库快照。
- P08 checked_at/valid_until取本次共同证明及main和所有返回valid、可能采用的origin最小期限；明确拒绝的origin不参与期限。保留现20秒续期规则和(check,lease_deadline) checkpoint键；续期后旧证明不可按新期限延长或复用，响应到达原证明期限已过期不得采用。纯期限延长不推进source/event版本：当前checkpoint可在原captured期限内继续使用原证明，前提是fresh本地完整service/origin/scope/binding/channel、source当前性及原控制均保持，且durable期限不短于captured；不得把durable新期限赋给旧证明。协调是否临期仍按captured与20秒加下一有界读取预算判断，需要采用新期限时及下一checkpoint均须按新完整键重新授权。期限缩短、来源或控制变化、原captured过期仍拒绝。模型/tool/send开始后的既有禁止重放规则、30秒/5次准备恢复、所有后续fresh边界均保持。撤回原话时完整reply与input_only都必须失败；不可通过降级变体保留撤回文本。

### 共享画像初选批次

新增 `POST /internal/v2/memory/context/profiles`，使用profile_batch_request/profile_batch_response。每批1–4个原v1 select_request/select_response，复用原profiles.select领域规则；共享调用身份必须同时具备既有context_query与select_profiles权限，不新增授权。

- P09 每项origin必须与外层query.origin相同，requester_scope与外层requested_scope一致；target和sharing仍按原v1规则。各内层request_id、target及响应顺序严格匹配。使用一次完整profile coverage屏障和同一事务，不能循环调用旧HTTP制造多次屏障。任一项无权/版本失效或共同未知故障使整批失败；没有部分画像成功。
- P10 Core保持原每批4个target、批前min(4096,remaining)预算、原每目标预算与整组采用顺序；上一批实际采用后才决定下一批预算。不得预读全部目标再重新平分预算。响应checked_at/valid_until受本次实际origin和各原select_response期限约束，过期/错绑定/缺项不可采用。所有原始画像校验与发送前依赖复验保持。

### 可检索候选与空来源登记

Q07/Q11中的完整实际候选域不把仅登记source/admission、尚无有效group或episode的额外room owner误当可检索内容变化。授权锚点（requester、现有批准的直接私聊关联及关联版本）继续保留；room正文候选owner来自完整有效groups/episodes，不受关键词、类别、时间、排名或结果预算裁剪。完整来源屏障仍覆盖原授权范围，完整fresh NameView仍独立解析；不能用此规则跳过来源/权限复验。首次capture新增任何episode/fact、候选更新/退休、姓名解析改变、撤回/遗忘或关联撤销仍按实际完整域失效。所有query准备、外部rank两边、assemble与verify共用同一规则。

本次定义不承诺解决全部压力失败：初始query admission、队列等待、模型服务502和发送前fresh等待仍需同负载实测；不提高槽数、超时或重试次数来替代证据。合同样例通过仅代表形状和关系约束，实际双方HTTP/PG、撤回/权限、并发与质量验收单独记录。


## revision10：保留独立可选历史的有限降级

本条精确覆盖P02/P04/P07/P08中“所有optional未知都整批失败”的过宽解释；其余revision9规则及profiles初选的整批语义不变。新增prepare_check_result第三态status=unavailable、reason=origin_unavailable，表示未能取得该可选origin的证明，因而不可使用；它不是valid，也不是明确拒绝事实。

- P11 仅独立optional origin解析发生可确定的纯网络/传输不可用时，Memory可返回unavailable。必须在实际传输归属使用内部类型区分，不能凭generic dependency_unavailable、retryable_read或字符串错误文本猜测。共享caller/issuer配置、证书/TLS信任、裸401/403/404、schema/JSON/编码/绑定错配、HTTP协议完整性、共同SourceAuthority屏障/coverage/SQL/CAS等仍整批Fault。main/required的同类不可用仍整批Fault，不构造invalid业务判定；同check或origin若被main/required依赖，required优先。取消、续租和带副作用读取的未知不得归入本条。
- P12 unavailable项没有授权证明，不进入valid checkpoint，也不延长任何租约；期限只取main和所有返回valid、可能采用的origin。仅排除不可用项的独占coverage，其他必要或有效项共享的source仍完整覆盖。排除后当前bundle及全部有效/必要依赖仍须同包完整fresh屏障；共同屏障不确定时没有部分成功。所有unavailable引用均不可被采用。
- P13 Core本地optional历史纯只读检查可在其明确传输失败归属标记不可用，但不是整类吞Fault；required、来源/权限完整性、取消/续租、关系reconcile/tick等带副作用未知仍失败。原作者origin不可用时，完整回复与input_only都不可用；只有回复专属依赖不可用而input_only自身完整source/history/权限检查独立有效时，才可依原顺序和整组预算采用input_only。未发送、未返回或整个包失败仍不可当unavailable响应、valid或明确rejected。画像初选profiles batch仍全部成功或整批失败，不变成可选成功。

本次变化保留原optional历史无法使用时的有限降级，不赋予新权限，不复用过期证明，不增加重试/超时或重放任何model/tool/send。原不利测试与revision9初轮组件结果保留。新增状态仍须生产者/消费者及真实PG联合验证，schema样例不是运行验收。


## revision11：按依赖隔离未获证明的可选 origin

P14–P17替代P05/P11/P13中将独立可选origin的裸403/404或不合法成功context一律升级为整轮失败的部分，其余P01–P13继续成立。只调整既有准备阶段的故障归属；schema、状态枚举、原共同来源屏障、当前bundle、分组/预算/槽位/超时/重试及后续fresh验证均不变。

- P14 纯origin读取必须先通过本地caller/service/issuer/URL/凭据/CA配置。仅该单次远端只读解析未能取得可信context时，允许用明确内部出处标记origin_unavailable：包括既有可确定的传输中断、远端403/404，以及有界成功正文的JSON/schema或request_id/issuer/authenticated_service/audience/assertion_ref绑定不成立。它们没有授权证明，不能标origin_denied。common.trusted_context.revoked仍为const:false；revoked:true只是不合法context，不能伪造为合法撤销响应。只有完整合法绑定context之后的过期或本地actor拒绝可作确定拒绝。
- P15 只有完整全局catalog确认(service,完整origin)独立于main及全部required依赖时，才能把该出处转换为unavailable，并排除所有依赖该origin的物料/组变体。相同origin即使先以optional出现在另一个check或包内，只要main/required使用它，整轮必须失败并保留原故障语义。原作者origin不可证明时完整回复和input_only都不得采用；仅回复专属依赖不可用时，仍可依原顺序采用自身全部依赖完整有效的input_only。不可通过删检查制造空集合valid。
- P16 不能捕获整个auth.resolve/_authorize/_verify_context_check的任意Fault来降级。共享配置或凭据故障（含issuer401）、TLS/证书信任、连接池/HTTP帧与正文传输完整性、其他未分类HTTP故障、合同注册表/加载故障、本地role-grants/identity/register/channel/SQL/CAS、共同SourceAuthority/coverage与整包prepare响应协议故障仍全局失败。取消、续租及关系reconcile/tick等副作用未知不在纯origin读取范围。异常类型/标记须在实际故障归属产生，不能凭错误文本或generic retryable_read猜测；不增加重放。
- P17 不可用origin不贡献任何权限、coverage独占域、checkpoint或租约；其他有效或必要依赖共享的source仍完整保留。当前bundle及所有可能采用的有效依赖必须重新通过同包完整fresh共同来源屏障和同事务验证，所有分包闭合后才选择物料。未发送、缺失或整个包失败不等于收到unavailable。画像初选仍整批成功或失败。必需负例包括main/required同origin、optional先required后、跨包required闭包、TLS/401/本地身份写入/共同屏障/续租/副作用失败；独立optional403及非法context的正例须证明未采用其任何依赖数据。

本次收口用于恢复旧独立可选历史失效时的正常对话能力；不把旧四项失败改名为兼容通过。组件、真实双方ASGI和原生PG证据完成前保持未验收，既有rev8/rev9/rev10的失败和原始产物全部保留。


## 原始输入投影的精确证明归属（客户端实现修正）

本条显式覆盖P07/P13/P17对新精确raw-input投影的检查归属；此前旧文字没有定义此例外。没有新增Memory wire kind，也不改history/input_history的known_snapshot语义。现闭合四类Memory检查仍全量进入原prepare/verify，保持同包共同来源屏障及同事务验证。

- P18 仅实际只含原始人类输入、不含生成回复或其继承物料的投影，允许使用原source-sync/check精确证明。内部检查须不可变地捕获source_turn_id、input_revision及完整exact input sources，并保留原service/origin/scope/binding/channel。Core先验证当前请求者和原作者权限及同群/渠道边界，实际await现check_sources，证明Core当前turn/input revision/exact sources和Memory当前active/revision/epoch/来源负证明；publisher身份本身不能替代viewer权限。RPC、原fullkey去重及captured期限归属在显式异步检查内，不藏进本地guard。没有精确捕获payload的旧持久检查、完整回复及其history/profile/继承依赖继续原路径，不能裁旧snapshot或改known=None制造valid。
- P19 新raw检查不伪造input_history wire，也不把空Memory checks当作来源已通过。Core按完整组ID将每个实际raw RPC成功、外部权限/来源检查和原完整Memory bundle结果AND；linked组不得拆开，main/required优先，未发送/缺失/未知/协议/共同故障不得视为valid或确定拒绝。raw RPC和Memory bundle各有自己的fresh读点，是多RPC组合而非单快照；保留既有分布式检查后远端变化的竞态边界，不声称本地guard能观测所有远端变更。
- P20 每个模型/tool/send等原fresh checkpoint重新执行raw RPC；其前后fresh本地完整身份、current sources及捕获input_revision/exact sources必须仍相同，沿用原captured截止、原20秒/后0、原有界协调与副作用禁止重放。raw本身撤回/遗忘、Memory已知negative、权限/binding/channel/input变化或原截止已过期都不能通过input_only保留；RPC后Core发生raw变化须在本地closing拒绝。明确已发生在fresh RPC前的Memory失效须实际读到；后续仅远端变化按既有source-sync竞态边界处理，下一checkpoint仍须重新证明。不能用无据增加预算/超时/槽或提前物化候选改变查询域来替代该归属修复。


## 普通上下文与严格事实读取

`query_request`与`bundle`中的可选`retrieval_mode`为`context|factual`，省略等同原`factual`。`intent=current|history|mixed`仍只控制时间范围。普通Core与Memory回忆工具使用context；严格事实调用保留原latest/unique/no_match集合证明。

context只保证所选材料当前有来源且允许使用，不保证整个域的最新值、名字唯一或穷尽。返回原有`incomplete`状态、`context_only`遗漏，`candidate_complete`及`candidate_domain_complete`为false，`proof_kinds`仅`positive_sources`；`owner_resolution={mode:unresolved,targets:[]}`明确未执行文本人物消歧。每条材料的真实owner_scope继续标明作者，不能用名字猜测授予额外范围。空结果也不能声称no_match。

context的selection HMAC固定mode、调用范围/查询/预算、证据内容和完整选中依赖；每次重算的scope_version、association_version、scope_checks、history_snapshot及input_history_snapshot是当前观测，不是不可变选中集合身份。verify仍重新核验原签名、当前权限/私聊关联、所选owner的可用范围、全部实际来源与派生叶版本、suppression和有效时间，比较完整依赖及evidence_digest。无关新经历或其他作者追加消息不要求原正向材料重新选择。factual的既有HMAC与全域集合核验保持。`validation_mode=full_barrier`仍表示完整SourceAuthority屏障，本批不另造选中来源同步协议，也不延长旧proof期限。


## Exact raw input checks in the existing v2 boundary

`input_sources` is an additional component of the existing prepare/verify checks.
This section supersedes P18-P20 only where they describe no new wire kind or an
independent Memory RPC per raw input; their authority, captured-lease and fresh
checkpoint requirements remain in force. Standalone source-sync/check remains.
Its request reuses the full source-sync/v1 check request and adds the original
author's query/origin. `requested_scope` must exactly equal `source_check.scope`.
The nested turn id, input revision and ordered complete sources must equal that
actual Core turn. `expected` is empty: this proves current exact sources, not an
old history snapshot or a substituted scope version.

The caller must retain the original check_sources operation/service/role/scope
admission, in addition to the existing per-origin context authorization and lease.
These checks do not grant a new capability to context-query-only callers.
Every explicit raw source selector and raw turn id participates in the complete
union source barrier, including sources not yet admitted into local Memory.
All original source/turn relationships, suppression, currentness, remote head
double collection and local CAS are checked. There is no independent nested
source operation per component, cross-checkpoint cache, prefix acceptance or
implicit success for missing components. The original four component kinds and
the standalone source-sync/check operation keep their existing semantics.

Only the exact `_source_rows` inactive/revision/payload negative maps to a raw
component dependency_changed rejection. Prepare retains optional whole-linked
exclusion and required-check failure; verify retains the complete adopted AND.
Admission/role/configuration failure, unavailable/unknown transport, incomplete
coverage, malformed relationships and unclassified errors remain global failures.
Captured author leases and exact local material are rechecked after the response.
The existing 64-component packet bound is unchanged; multiple packets are complete
AND boundaries, not a claim of a shared snapshot across packets.


## schema6派生材料来源

summary/portrait evidence的source_refs与unit并列接受memory-extraction/v1的segment_recall_source/segment_unit，用真实observation引用保存旁听来源；旧variant不变，typed事实约束和原完整SourceAuthority屏障不放宽。summary evidence可选携memory_kind、state、valid_until，直接来自有版本的真实记录，不靠文字推导状态。重要度与逐条保留时间由schema6提交执行；自然到期与显式删除仍分别处理。旧published v1读取仅返回原schema可表示的unit，私域observation新摘要从旧接口省略，v2可完整读取；不伪造raw_message，也不把旧接口空结果解释为完整历史不存在。本地候选尚待产品联合验证。


## Adopted reply context provenance (local candidate rev6)

The existing batch boundary additionally accepts context_bundle: request contains
query, requested_scope and the original signed context-mode bundle; expected is
empty. This is a stable selection identity, never a cached permission. Every use
authenticates its HMAC and checks the current complete selected dependency closure,
origin, authorization, associations, source revisions and the final CAS barrier.
The old verified_at/valid_until are observations; only the newly returned proof and
the current origin lease authorize the present checkpoint. Factual selections and
all existing check kinds retain their complete-domain meaning. A failed optional
child is classified as that child's dependency failure; required children and
strict verify retain AND semantics. No nested RPC or second verification queue.

Companion records exact human input and every context actually sent to a model,
including all native continuation rounds, for future reply/summary provenance.
This causal history does not replace the refreshed active prompt after a tool.
Unknown or old replies without complete provenance keep their original proof path.
