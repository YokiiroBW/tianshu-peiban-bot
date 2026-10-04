# life-runtime/v2 实现合同

主协调审核发布；生产者/消费者联合与实机验收待完成。

POST /internal/v2/life/manage、/internal/v2/life/read、/internal/v2/life/media/read（原图二进制）、/internal/v2/life/content/read（精确原文/原媒体引用）。管理只接受可信 Platform 服务；operator由认证服务决定，value不能授予权限。读沿现有 life_readers/life_access，query.origin经既有Origins resolver和Memory身份查证；scope=null代表角色自有生活，仍须现有生活读授权和真实origin，绝不是自动公开。原件必须核Actor、相册scope和当前源权限。跨person私有记录404。state缺角色404；未配置能力503；空列表仅表示已存在Actor该资源确无记录。expected_version用于目标CAS（创建0），request_id与摘要同事务记账。outfit覆盖只影响本次image，不更新当前穿搭。认识片段期望版本分别核对；来源无效不采用。manage仅返回闭合id/version/state/operation_ref，read按resource投影闭合字段；总量超限返回429不截断正文。

content.acquire的URL/upload由现有正文owner纳管，owner为Memory knowledge时使用memory；Companion只保存稳定引用、scope、阅读会话和已读取coverage，不复制正文权威。reading.open solo只允许空participants；together须实际授权参与者，不因列出person ID授予访问。reading.read调用owner实读成功后推进版本/进度；暂停/重启保留coverage，失效原文标unavailable，不能伪造complete。content/read是实际读取，目录/search不推进进度。0<=start<=end<=total（total可未知），单位必须对应owner覆盖；时间、coverage和共同读者关系是语义校验。

image.backend.configure只配置ComfyUI现有服务，不安装GPU。base_url为固定origin，无userinfo/query/fragment/额外路径、无重定向；部署可信私有HTTP或HTTPS可用。profile standard_sd由Companion内建t2i/img2img工作流，checkpoint来自真实object_info模型清单；staging固定部署目录，不由网页选择。credential_ref=null代表确无认证的后端；非空通过下面可信服务resolve取值，不是把ref当token。读投影永不含token/API graph/本机路径。config保存与backend连通状态分别表达；不可用不能报告ready或生成完成。edit_source_id须同Actor现有相册原件及scope授权，不是任意路径/URL；缺工作流/原件拒绝。

content_read_response 必须保留真实 representations、帧时刻及 gaps；Knowledge 复用本包通用表示定义，依赖单向 Knowledge → Life → Common，不反向引用 Knowledge。表示中的 sha256 对应表示 bytes，source_sha256 对应原件，截取/缩放不冒称完整阅读。

POST /internal/v2/life/proactive/control 仅既有 Platform 管理身份调用；平台先核真实 QQ slot/person/channel 与 qq.admin.view/manage。闭合 control_read_request/response 仅订阅配置和投递状态，无私人正文、动机、输入或内容引用。proactive.subscription.state 复用 manage CAS，active/paused/revoked 不通过 quota=0 伪装。

POST /internal/v2/life/conversation/ensure resolves the actual registered actor, authorized origin, Memory person and channel into the existing Core conversation. It creates no input or turn and invokes no model. The persisted scope/channel/binding_version receipt is idempotent across restart; Platform applies it to existing identity/channel mappings and rejects conflicts before signing scope. First-use uploads and account association must not require a preceding chat. Life reader authorization does not enable dialogue capability.

read resource image_jobs exposes the existing image execution receipt, with scoped artifacts and actual state. unknown after interrupted work is not automatic replay; completed means original artifacts exist. No backend token, workflow graph or local path is exposed.

Image references use exact image content_ref plus original scope and current authority. image.request edit_source_ref and non-null album edit_source_id are mutually exclusive. outfit.put object references require explicit source_scope/query; actor.image-reference.configure persists the existing actor identity reference and supports clearing. Originals stay with Knowledge and are authorized/hash-checked before actual Comfy upload. No fabricated generation jobs. Private references are filtered from unauthorized readers, never downgraded to actor scope. Identity and clothing are distinct reference purposes; actual participation does not claim unverified output quality.
