# role-relationship 候选 v1（2026-10-01 本地收敛）

**candidate_only / 未正式发布 / 显式隔离本地输入。** TS-114/115/116 已实现下述接口并完成固定版本本机验收，不再是“未实现端点”。本包没有正式运行 manifest、生产发布或启用授权；产品仅按明确配置读取候选，不能默认发现或加载它。正式发布须协调者确认生产者/消费者、完整信封及权限/迁移边界，另固定正式版本和字节哈希。

本轮保留 schema.json 和 examples.json 原字节，不新增功能或修改五类业务 DTO。LF 归一化 schema SHA256：`f3b588591411f1ed4b8aa7c9003d201530644d4dfc02294bdd9e9d7f847214a3`；当前 CRLF 工作文件原始 SHA256：`481a610729539005c42553c20336580acc9068b3729e43bdf575340698627bb1`。运行验证使用前者；原字节记录不是可跨行尾使用的加载器 pin。样例是合成结构样例，业务结果以 acceptance.md 及固定交付回执为准。

## 权威、身份和权限

Memory 唯一拥有 `(actor_id, person_id)` 关系事实、分数、策略、冻结时间、事件账本及历史。Companion 只读有界投影，使用已有 outbox 保存版本 pin、可信候选意图和回执；Platform 只认证后台会话与选择并转发操作，没有关系/分数权威表。跨产品不读对方数据库。

可信 person 来自现有身份/来源链；昵称、聊天正文、模型、静态人格、浏览器目标字段均不能证明身份或授权。关系类型和好感不授予管理员、工具、角色、机器人回复或数据权限，也不按阈值自动建立或解除伴侣关系。后台管理要求当前真实 principal、既有角色与查询权限和 Memory 明确 service operation；不默认加 grant。普通 read/check 的 pair 须与当前 origin 一致，跨人物后台私密读另要求 `managed=true`、管理授权和真实 Platform 操作人。

## 实际本地端口及信封

Memory 内部端口均为经过服务认证及当前来源核验的 HTTPS POST，JSON 上限 16 KiB。公共信封是 `schema_version:1, request_id, origin:{assertion_ref}` 加下面字段；正常响应是 `schema_version:1, request_id` 加一个结果字段。浏览器 Cookie/Origin 不可直进 Memory，错误走现有 Fault 闭集，不回显正文或凭据。

| Memory 后缀 | 请求业务字段 | 结果字段 / 既有权限 |
| --- | --- | --- |
| /internal/v1/relationships/read | pair；可选 managed | projection / relationships.read |
| /internal/v1/relationships/check | pair、expected_version；可选 managed | check={version,current:true} / relationships.check |
| /internal/v1/relationships/manage | command，内外 request_id 一致 | projection / relationships.manage |
| /internal/v1/relationships/settle | candidate | settlement / relationships.settle |
| /internal/v1/relationships/history | pair、managed=true | history / relationships.read，同时要求管理授权和 self_private 操作人来源 |

最后一项是 TS-116 接线中已实现的有界审计回读，未增加权威表或自动迁移。`history` 恰含 `{projection,items,has_more}`，最近最多 20 条，按 pair 现有索引读取 20+1 判断 has_more；每项恰含 `{id,kind,delta,outcome,at,valid,operation,reason}`。id 是摘要，无源原文、source_ref 或操作人 ID；旧未保存原因为 null，不编造。投影和历史在同一当前来源屏障中读取。HTTP 信封、managed/check/history 形状不是现有五 DTO schema 的一部分，正式发布需明确纳入；本轮只记录实际约定，不擅自改变已固定 schema。

Platform 同源 POST `/api/web/relationships/{catalog,people,view,manage}`，用真实 Cookie/Host/Origin/CSRF，拒绝浏览器 Bearer。catalog/people 复用当前角色与已确认人物目录，每人物页 100；view/manage 接受 `role_id,role_version,person_id,people_after`。manage 另接受 client_id 和不含 pair/request_id 的 command；真实 pair、管理 ID、principal 与 origin 由服务器生成。结果返回前再次核验会话、角色范围、来源、配置与凭据。接口没有 /apply、批量写入、后台自动重试或新的授权入口。

## DTO、CAS、幂等及未知结果

五类业务 DTO 为 RelationshipCommand、AffinityEventCandidate、PrivateProjection、PublicProjection、SettlementResult，Pair 为其共享定义。人工 command 仅 set_binding、set_freeze、adjust_affinity；人工 delta 为整数 -100..100，非空原因最多 200 字符，称呼最多 40。自动 candidate 没有 delta、operator 或管理员标记。

人工操作按真实 principal/request_id 幂等并审计；同 ID 不同内容 409。CAS expected_version 来自 Memory 当前事实，冲突必须重读后由调用者明确再审阅，不静默覆盖。重复回执只证明历史结果，仍需 read/check 当前权限及版本。candidate 的 event_id 与内容摘要去重。

Companion 仅在真实私聊成功发送、所有回复有渠道消息 ID、当前可信直接用户文字及来源/身份/角色仍有效时提交 conversation_completed。引用、媒体、群聊、失败/部分/未知发送、模型自述和历史材料不是证据。Memory 核对真实持久轮次及 admission key、来源版本和时间。negative/repair 枚举尚需可信同产品 behavior_verifier，默认拒绝，Companion 本批不生成这两类候选。

管理和结算不会在可能已执行的超时、断连、取消或非法回执后自动重放。Platform 页面禁止继续写，显式刷新后允许新的人工意图；取消等待不表示撤回。Companion 保存 unknown 及同一候选，其公共未知恢复工具未实现。授权/来源错误可能是 HTTP 403/409，不要求候选 outcome 枚举全部变成 200 回执。

## 策略、冻结、来源纠正与旧增量

已采用可配置默认 -1200..1200，八阶段起点 -1200/-800/-400/0/200/600/900/1200，迟滞 20 且最高进入阈值截到最大分，1200 可达。自动单次 +4/-12、每 pair 每策略日正向总额 12，UTC 默认，其他 IANA 时区需已有 zoneinfo。策略在 pair 首建时固化，配置变化不自动改写旧 pair。

正分活动后宽限三个完整日，第 4–7 日 2/天、第 8–14 日 5/天、第 15 日起 8/天趋于 0；负分不自动向正分增长。冻结和结算在同一 Memory 事务线性化；逐 actor/person 暂停自动正负变化与衰减，冻结内迟到事件仍拒绝。解冻从当下重启宽限，不追补、不回放；重复解冻不推迟游标，持久 watermark 不回退。短期情绪属于 Companion，独立变化。

冻结不阻止来源/授权撤销、遗忘和显式纠错。来源失效按已实际贡献重建，不让已衰减正来源撤销制造负分。旧 relationship_entries 原值/provenance 保留，确定的 actor/person 单 scope 一次性接管，新旧同源不双算；多 scope、超范围或归属不明为 pending，不猜、合并或清零。只验证合成库的显式 DB+source-guard 一致备份和迁移，不启动生产迁移。

## 投影隐私与固定交付

获准私密投影含分数、阶段、显式类型/称呼、冻结与版本；Companion 私聊表达只使用类型/称呼/阶段，不把分数、冻结游标、管理原因或权限放模型。群聊只接受 PublicProjection 的固定低信息提示，没有私聊类型、称呼、分数、阶段、冻结或私密版本。群自动计分默认关闭。

prepare、生成及每段发送仍复核原来源/身份/角色链及关系版本；旧 pin、缓存、历史回执和冻结不能豁免检查。配置/授权/来源变化时拒绝使用已失效私密背景。

固定本机三产品 SHA、实际测试及基线失败见根 `docs/development/role-relationship-affinity-delivery-2026-10-01.json`。合入顺序 Memory → Companion → Platform，尚未集成、NAS 部署或真实 QQ/模型验收；橙汐原登录/个人 QQ 绑定阻塞另线保留。现有产品 main 含未部署身份功能，不能直接整体更新线上。
