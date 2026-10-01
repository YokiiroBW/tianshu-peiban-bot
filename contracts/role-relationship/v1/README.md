# role-relationship v1 / 1.0.0

2026-10-01 发布为跨产品实现基线。Memory 是生产者及唯一关系账本权威，Companion 是表达/可信事件消费者，Platform 是经过真实后台会话认证的管理消费者。正式包由协调仓库单写者维护。发布合同不等于生产迁移、上线或真实 QQ/模型验收。

`schema.json` 固定五类业务 DTO，`http-schema.json` 固定外层内部 HTTPS 信封、managed read/check、有界 history 和现有 Fault 信封。`examples.json` / `http-examples.json` 仅含合成正反例。manifest 记录 LF 字节哈希、生产者/消费者交付来源及运行验证边界。

## 权威与安全语义

关系键严格为 `(actor_id, person_id)`，同人不同角色及同角色不同人物分别独立。Memory 拥有类型、称呼、分数、策略、冻结、事件和历史；Companion 仅表达并保留可信候选/outbox；Platform 无关系权威表。跨产品禁止直接读写对方数据库。

人物来自既有已确认身份链。正文、昵称、模型、浏览器字段和人格都不能证明身份。关系/好感不授予任何实际权限，也不自动建立/解除 partner。后台操作人由 Platform 真实 principal 派生；须有既有 role.manage、memory.read、qq.admin.view 及当前角色/来源授权。Memory 管理服务需 relationships.read/manage、role_admin 与实时 Platform origin；不自动创建 grant。

## 内部 HTTPS

五个 POST `/internal/v1/relationships/{read,check,manage,settle,history}` 均使用现有服务认证、当前来源核验、独立读体/执行时限和 no-store；请求上限16KiB。请求必须含 schema_version=1、稳定 request_id、origin={assertion_ref}。Origin/Cookie 不能进入内部端口。read/check 的 pair 必须与当前 origin 相同；跨人物后台读取须 managed=true 及管理权限。history 强制 managed=true 和 self_private 的真实操作人来源。

| operation | 业务字段 | 结果/服务能力 |
| --- | --- | --- |
| read | pair；可选managed | projection / relationships.read |
| check | pair、expected_version；可选managed | check={version,current:true} / relationships.check |
| manage | command | projection / relationships.manage；role_admin |
| settle | candidate | settlement / relationships.settle |
| history | pair、managed=true | history / relationships.read；同时管理权限 |

manage 的 command.request_id 必须等于外层 request_id；这是跨字段语义检查，不能只靠 JSON Schema。返回 request_id、pair/event_id、版本、新鲜度及当前授权均由消费者复核。history 恰含 projection/items/has_more，最近20条，单项无原文、source_ref、操作人ID或凭据；旧原因缺失为null，不编造。

Fault 沿用 Memory 现有封闭字段 schema_version/request_id/code/execution_state/retryable 及可选current_version。错误码和状态来自产品现有边界；未知错误不能解释为成功。**现有 execution_state=not_started/retryable 不能证明已发送写入未执行**：超时、断连、取消、非法回执和未知服务故障均按未知结果处理，禁止自动重放 manage/settle。调用方保留原意图并明确核对；取消等待不是撤回。

## 幂等、冻结与表达

人工 command 仅 set_binding/set_freeze/adjust_affinity；delta 为整数-100..100且需非空原因≤200字，称呼≤40。稳定真实principal/request_id幂等，同ID不同内容冲突；CAS冲突须重读后明确新意图。candidate没有delta/管理员字段，仅真实私聊成功发送并有渠道ID、当前直接用户文字及来源/身份/角色仍有效时允许 conversation_completed。negative/repair须同产品可信behavior_verifier，默认拒绝。

策略默认-1200..1200、八阶段起点-1200/-800/-400/0/200/600/900/1200、迟滞20、单次+4/-12、每pair每日正向12，UTC默认；初建pair固化策略，不自动重写旧pair。正分活动宽限3日，4–7日2/天、8–14日5/天、15日起8/天趋零；负分不自动转正。逐pair冻结暂停自动正负及衰减，解冻不追补/回放，短期情绪独立。撤权、失效来源、遗忘与显式纠错不受冻结阻挡。

私密投影含管理事实，但Companion模型只收到类型/称呼/阶段，不含分数、冻结游标、管理原因或权限。群聊只用PublicProjection固定低信息提示。prepare/generate/每段send复核关系版本和原身份/来源授权；旧pin、缓存和回执不能豁免。

## 部署与迁移

本包是首次正式版本；旧candidate保持历史原字节，不能作为本版本生产输入。绝对schema路径指向本包；产品保留历史配置键candidate_schema_path作为现有配置字段名称，但其值与加载器哈希必须绑定本正式schema，不会自动发现候选或增加权限。Companion版本域为role-relationship/v1，旧候选pin失效并按现有来源检查重新准备。

Memory关系表安装必须停其他写者，显式调用Store.migrate_relationships并独占完整DB及同次source-guard备份，不能启动自动迁移。既有relationship_entries保留原值/provenance；明确actor/person且单scope一次接管；多scope、超范围或不明映射为pending，不猜、不清零、不双算。部署恢复须一致保存Memory DB/checkpoint、Platform alias/角色sidecar、Companion DB/owner事实及Knowledge数据/日志持久缓冲；回滚必须恢复同次完整状态，不能仅换镜像或仅回退一个DB。

验证使用`python -B contracts/role-relationship/v1/validate.py`。它验证字节和结构/跨字段语义；权限、事务及真实三产品HTTPS由固定版本联合验证证明，生产恢复和真实账号体验另验。详细原本地证据见role-relationship-affinity-delivery-2026-10-01.json及TS-114/115/116 handoff；其中失败/skip保留，不宣称全量全绿。
