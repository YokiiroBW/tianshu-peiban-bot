# 字段关系、事务与故障边界

## 1. 身份先行，事实分属各自 owner

首入站仍用已发布 origin resolver：已验证账号、渠道、actor/audience；person/conversation 可为空。Memory resolve/register 只校验当前入口和账号绑定，不检查来源、轮次、候选或 scope_version。Core 从真实 Memory 响应取得 person/binding_version，再按已验证渠道建立自己的 conversation 并受理。Platform 的受信转发适配器在调用上游前 prepare_mapping，在收到真实 register/resolve/ingest 响应后 confirm_mapping；不接客户端“已验证”的 ID，不由 Core 生成 person。

该适配器运行于 Platform 的应用边界，转发已有上游 HTTP，调用本进程 prepare/confirm 方法；处理 response/request_id/account/channel/幂等对应，先拿到响应再提交本地映射。Core acknowledge_ingest/response_released 与后续调度的竞态要联合验证：映射未提交时来源屏障失败关闭并重试，不能先伪造回填。如果上游已受理而适配器崩溃，以原幂等键取同一响应并完成映射。此部署不增加映射 RPC，也不让 Core 或 Memory 读 Platform 的数据库。

Core 的新 SourceFacts 应用端口只读本地事务状态；**不能调用 Memory**，也不等待 turn_committed。`turn_ids` 查询可返回尚未封账/blocked_scope 的 owner事实，`committed_event=null`，因此 Memory 后台 scope 检查不会形成“先有带版本事件才能取版本”的环。

## 2. 三个候选 HTTP 读取

全部为部署指定 HTTPS 目标、独立可撤销 Bearer 服务凭据、固定 receiver/operation allowlist；禁止从 payload 取 URL/CA/token，禁止跳转、环境代理、关闭证书验证。继承已发布错误对象与 no-store。request_id 关联响应；request_digest 是规范化完整请求的 SHA256，防串包，不是签名。调用者字段即使出现在正文也不授权。

| 候选路径 | 调用方/接收方 | 请求与结果 |
| --- | --- | --- |
| POST `/internal/v1/source-facts/read` | Memory → Core | `core_request/core_response`；snapshot 模式指定稳定 message key 集合、turn_ids 和 include_content；head 模式只读取同一持久水位。三者均不使用入口 assertion 作后台访问凭据。 |
| POST `/internal/v1/source-access/read` | Memory → Platform | `access_request/access_response`；绑定 Core 来源元数据摘要，逐一返回当前 entry/主体/路由/映射事实；在线模式还重新验证当前 viewer origin，后台模式 viewer=null。 |
| POST `/internal/v1/memory/source-sync/check` | Core → Memory | `check_request/check_response`；后台核验指定 turn 与其完整 sources、scope，经屏障后只返回 Memory 的 text-dialogue scope_version；无用户原文或画像正文。用于 blocked_scope 修复，不作任意后台人物查询。 |

Core 和 Platform 的批量请求最多 256 来源、32 轮次，超过返回 413/budget_exceeded；不能截断、静默漏项或以空集合表示完整。单响应上限 1 MiB；零预算使用 metadata 模式，不拉正文。暂不分页：Memory 实际依赖集合过大则 503，后续只在有证据需要时另设计同快照分片；不得分批拼不同水位后声称完整。

每个已请求 key/turn 必须恰好返回一个事实或显式 missing 结果；重复 key 是 400。missing/未知映射/缺 owner 状态是 503，不能认为已撤回，也不能继续使用旧正文。没有 tracked source 的本人首次版本探针允许空 key 集合，仍校验 viewer 与稳定 owner 水位。

## 3. Core 事实的精确绑定

稳定 key = `{channel,message_id}`，不含 revision；沿用 Memory source_key 的 canonical SHA256。完整 `source` 仍是旧 common.source；必须逐字段比较，包括 channel/thread、revision、receipt_id、archive_state、locator。receipt 非空不是证明：必须来自 Core 事务中的该 revision 的真实收件行且为 latest，不接受旧 stale 收件回执或另一个同文消息的 receipt。

`source_fact` 还包含 author、精确 scope、binding_version、accepted_origin、accepted_at、ingest_sequence、kind、content_digest、classification 与 state。`content` 是原 ingest 的完整语义 payload（去 command）：原 message_key/author/sent_at/kind/parts/reply_refs/mentioned_accounts/target_actor_ids。digest 以该对象计算；不只 hash 最后一段 text，不删除否定、主语、引用、消息边界或附件占位。metadata 模式 content=null，但 Core 必须从已持久的实际输入算出同一 digest；正文拉取须保持相同 head/fact，否则重试屏障。

accepted_origin 仅保留已认证 admission 的历史链路，不赋予当前访问权。Platform 保留签发历史与当时 entry 快照/摘要、签发/到期/撤销时间，核对该 Core 接受时间及 account/channel/actor/scope。然后检查**当前** entry、principal、route、账号绑定和 channel→conversation 映射。当前映射缺失、冲突或绑定版本不相符不能从查询 payload 回填。现有 Platform 还缺部分历史字段，必须由其后续任务补齐；仅凭传来的 accepted_at 不能证明渠道行为，信任来自已认证 Core 的实际收件事实及其原入站验证。

来源已授权用于后台整理且 entry/owner/映射仍有效时，历史 origin 到期或事后单独 revoke origin 不撤销已受理来源；在线 viewer 使用该 origin 仍被拒绝。撤销 entry/principal/后台整理路由或明确底层来源使用权使 access.state=denied，并同步失效。不是让过期 ref 重新 resolve 成有效用户来源。Platform 不再充当消息 revision/retract owner；现有 observe_source 只能作 rehearsal 对照，不能拿其 revision 覆盖 Core。

classification.value 为 real/fictional/mixed/unclassified；basis 是已登记输入模式或受信精确来源复核，附 policy_ref/version。不能从服务token、模型输出、缺省 real 或文字里自称“真实”获得分类。real 表示现实情境中的陈述，**不表示陈述已核实为真**，记忆仍保留 uncertainty。Core 当前 `_finish` 常量 real 必须修正为实际来源分类汇总。本候选中 mixed/unclassified 单条来源只保存不可提炼元数据，不产生现实/虚构单位；若需拆单消息内混合证据，须另定义可验证段范围后发布新能力。多个分别已分类来源可以组成旧 event.reality=mixed，但每个 unit 只能引用与自己 reality 相同的来源。

`turn_fact` 给出当前输入 revision、context_revision、当前对象版本、完整有序 input_sources、输入状态、真实当前 delivery_state/reply_ids，以及持久原始 committed_event（可空）。封账事件核验比较原 event 除 event_id 外的**全部字段**与 owner 保存值，event_id 换名只能得到相同输入的 duplicate；不同 scope_version/delivery/reply_ids/时间/causation/aggregate_version 等都拒绝。随后核对输入集合、当前输入状态和全部 source 事实，不拿历史事件覆盖当前撤回。input_revision 不等于 message revision，aggregate_version 是对象修订号，可能跳号；owner 快照证明当前事实后，不再用“每次事件 +1”假设堵住合法事件缺口。

迟到 sent 回执只能更新投递投影；不能重开轮次或重复候选/关系记账。unknown/partial 的输入仍可整理，但不得以角色已发送承诺作为记忆事实；候选提炼此阶段仅以真实输入为依据。owner turn 被取消/输入撤回时，新候选拒绝，旧候选失效。对传入错误 event 不能用 owner 正确值静默改写成 accepted。

## 4. 读取屏障与 Memory 原子失效

水位为 owner 自己的持久 `{generation,sequence}`：sequence 在所有影响其事实的事务中递增；Core 覆盖受理/编辑/撤回/分类/轮次输入与投递变化，Platform 覆盖主体/入口/路由/映射及来源授权变化。generation 只在数据库重建/恢复无法保证序号延续时更换，普通重启保持不变。禁止用 process 启动次数或墙钟当水位。

一次操作按下列顺序；远端调用全部在 Memory SQLite 写事务之外：

1. 在线验证身份，Memory 读取本地 revision `m0` 与完整 dependency coverage。text 查询覆盖该精确 scope 已登记来源、候选及活动投影来源；profile 查询覆盖该 actor 全部活动 public_preference 和当前群 group_only 投影血缘，**不依赖本次 target/query/预算**；额外并入 consume/check/commit/revise 的输入和证据。coverage 只读私有元数据，画像响应不包含它。
2. Core 同一只读事务返回该集合 C1 及 head；Platform 再返回对应 P1 的当前 source access，在线同时复核 viewer 与当前 scope。Platform 响应 request_digest 必须绑定该批 Core admissions，记录集合须完全相等。
3. Core 再读 C2 head。C1.head != C2.head（包括 generation）就丢弃本轮 observation 并重试。失败、超时或响应不完整都不能返回旧版本；持续变更超出请求 deadline/有界重试上限，503。此稳定区间中 Core 状态不变，P1 的事务点构成两远端事实的共同读点；不要求 Platform 锁住 Core。
4. Memory `BEGIN IMMEDIATE` 后比较本地 revision/coverage 仍为 m0。变了则 rollback，重取集合重试。还须用 Memory 自己当前的 account→person/binding_version 核对来源和viewer，Platform 的映射副本不覆盖 Memory 身份权威；本地绑定变化同样失效依赖并改变本地revision。未变时，先应用事实，再同事务失效所有关联 groups/records/lineage/projections/pending jobs，提高 source epoch 和受影响版本，写 Memory outbox 与屏障记录。缺条/不明状态整体失败；不得提交“已同步水位”但未失效。
5. **失效事务先提交**。随后独立业务事务读取屏障对应本地 revision；不匹配则重跑屏障。授权/版本比较、零预算提前返回、select/consume/候选提交均在这一事务完成。若 known_scope_version 陈旧，返回409，但不能回滚已提交失效。业务拒绝不回滚同步进度。并发 Memory revise 必须改变本地 revision，避免插入两个事务间却继续使用旧屏障。

本地 revision/屏障水位不对客户端公开。事务内 source_authority.verify/current 只检查此次已证明的本地 rows/lineage；完整 event 验证由有 event 入参的 consume 应用服务做，不能塞进仅见 sources/scope 的旧 verify。

Core `17eba4f` 的 `context.inherited_checks/merge_checks` 已合并原轮次 text 检查、profile_checks、context_checks；`_verify_checks/_preflight` 对生成/发送的跨作者群依赖分别用其原 origin、scope、binding_version 及版本域调用旧 select/profiles/select。Memory 必须对**每一次**这些调用执行上述屏障，不能只同步本轮发言人的 scope。群内 B 的历史依赖仍以 B 的已验证入口核验，不能改写成 A；profile target 与 requester 分开。过期继承 origin 导致整段历史省略/发送拒绝，不能用新的后台 check 绕过在线入口授权。空/零预算画像覆盖 actor 的活动共享血缘，不能只按 target 当前命中项同步。

后台 source-sync/check 与候选仅证明 owner 本轮原始输入。它不取代这些已持久的派生上下文检查，也不使包含旧画像生成结果的回复重新可用。后续 Core source/read 任务须保留 17eba4f 的生成前/发送前检查和去重上限，不能以“本轮 source 全 current”替换它们。

保证范围：在上述共同读点之前已提交的来源/授权变化必须被本次探针反映；进入业务事务前的本地变更必须重试。共同读点之后的远端撤回与返回/外部发送仍可竞争。每次生成和发送前重新执行屏障；30秒 valid_until 不是离线使用许可证。本包不承诺与渠道发送线性化、即时清除已经展示的数据或跨服务原子提交；若业务要求撤回完成后绝无并发发送，必须另审发送协调机制，不暗藏租约。

## 5. ledger、幂等、墓碑和版本域

Memory 持久区分 remote_current、access_current 与 local_suppression。有效来源需三者均允许。本地 correct/forget 的 suppression 按稳定 source key 保存，单调、跨 revision 生效；远端更高 revision/archived 回执不能清除。首切片没有 restore，重新允许此旧来源须另经明确新能力，不自动实施。Core retract 同样是终止使用标记；候选要求 Core 禁止更高 edit 将已撤回稳定 key 复活，现有 fixed Core 仅 latest-retract 比较不足以证明这一点。

原始事实更新与 Memory 更正不是一个版本：

| 版本 | 所有者/用途 |
| --- | --- |
| message_key.revision / receipt_id | Core；渠道消息当前修订与该次真实受理 |
| input_revision / aggregate_version / context_revision | Core；封存输入、轮次对象、短期上下文；互不替代 |
| binding_version | Memory 身份绑定；Platform 核验真实响应映射 |
| source epoch / record_version | Memory；含权限、修订、classification 与本地 suppression 的失效，以及记录历史 |
| text-dialogue/v1 scope_version | Memory 精确 actor/person/audience/conversation；缺行初始1，变化在 Memory 事务单调推进 |
| profile-memory/v1 scope_version | Memory actor公开epoch，群读加当前群epoch减1；与查询目标是否存在无关 |
| owner head / Memory local revision | 仅内部屏障与恢复，不是上述任何公开版本 |

首次来源插入只建立 ledger/初始版本；现有来源实质变化推进其 text scope（即使尚无组）；完整重复不加 epoch。只给**仍 active** 的共享投影做 active→invalidated 转换时推进相应 profile epoch；一次事务同域只增一次。仅私密来源变化、私密 suppression、已经失效的公开投影后续来源修订都不改变公开或其他群 epoch。不得把 Core/Platform 全局水位加进画像 scope_version。

重复 event_id 必须 digest 一致；同 turn/input_revision 换 event_id 仍去重；重放旧 accepted 可以回历史 duplicate，但不产生新 job、重新激活旧 job 或绕过 commit 时的屏障。缺口先取 owner 快照，无法证明就503；乱序旧来源/旧 turn 不覆盖新行。水位回退或 generation 变化停止使用、重同步所有覆盖集合；如果 Memory 也从旧备份恢复，必须先恢复独立保留的 suppression/消费ledger/outbox 记录，不能把 source owner 快照当完整恢复数据。恢复完整性未确认前不提供任何记忆版本探针。

候选提交复用 jobs.source_snapshot、write_ledger、source_writes 与完整组写入事务：检查 job 当前状态、event owner facts、scope_version、全部 source revision/epoch、草稿来源确切子集及 reality、整组语义字段；任一源已记账则整个 job duplicate_source，不部分累加关系。相同 job/drafts 重试返回原结果；异 drafts 409。记录消费幂等键、来源账本、关系增量和候选状态同一事务；崩溃恢复无半组。

## 6. 确认与共享批准仍是精确操作

Memory 新内部 TrustedWorkflow 与 fixture-only LocalWorkflow 分离，复用验证/事务函数，不继承 fixture 类以绕过检查。内部 `confirm_revision(request, verified_context, decision)` 由部署登记的本人操作适配器调用：真实用户在受信 UI/本地操作入口对完整更正/遗忘操作确认后，Memory 签发 confirmation_ref，保存当前账号、精确 scope、binding_version、目标版本、完整 semantic_request digest、有效期与未消费状态。schema 中 confirmation_record 是该**内部持久记录**，不是客户端可提交的授权字段。

沿用现有 semantic_request 定义：去 command/query，保留 record_id/expected_version/revision_kind/confirmation_ref/evidence_refs/replacement_statement；规范化为 UTF-8、键排序、紧凑 JSON、无 NaN/重复键，数组顺序不变。proof 同时绑定签发时的主体和当前绑定版本。revise 原子消费 proof、写 suppression、失效、写幂等结果；原幂等重试可返回结果，其他键不能复用已消费 proof。账号/版本/语义变了必须重新核对具体操作，普通查询不新增提示。

群画像共享继续使用已发布 profile-memory 的独立精确类别/主体/范围批准，允许已登记的本人共享设置或群整理策略；dialogue 路由、自动提炼、服务token和普通 revise 确认均不授予共享权。候选内部首写默认只允许 event.scope 内的私有整理；显式群投影通过已有批准应用服务，禁止复用 LocalWorkflow._write_group 的“传 group scope 即投影”路径绕过真实批准。

本包不定义跨产品原文物理删除。Memory forget 返回其派生语义墓碑；Audit/Core 原文保留策略需各自另行执行和回执，不能合并成“全系统已删除”。
