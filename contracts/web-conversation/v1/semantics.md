# web-conversation/v1 1.0.0 语义

- 使用当前 viewer actor origin；caller=platform，核验 web/self_private、精确 actor/person/channel/conversation 及账号绑定。请求开始与投影输出前均复核当前origin有效与范围版本。source_input不能替代actor origin。
- schema.json 为唯一发布形状；common query不接受deadline；请求外层deadline过期返回common timeout。其他错误沿common#error，不创造新错误枚举。
- collectors 每组最多256消息，active_turns最多64，history每页1–50轮（默认20）；同一个turn内消息不可截断。完整UTF8响应上限1,048,576字节；超过返回budget_exceeded而非缺字段的成功。
- history按turn_sequence降序，仅取严格小于before_turn_sequence的终态轮；null表示最新页。active_turns独立返回当前非终态，不受history游标限制。history/active按turn_id互斥且各自唯一；next_before_turn_sequence为本页最小sequence，仅确有更旧历史时非null。刷新重新查询本页，按turn_id替换；轮次可能迁移到history，客户端不能保留旧active作权威状态。不承诺无损事件订阅。
- 输入正文必须按Core可证明的当前source状态投影。旧revision已编辑、撤回或明确失效时parts=[]并标edited/retracted/unavailable；不能返回旧正文。source_quarantined期间明确dependency_unavailable，不提供可能失效的payload。
- reply.state保留实际sent/failed/unknown/pending/sending；独立content_state=available|unavailable。只有sent且当前来源可展示时available与非空text；其他情况下unavailable/text=null。发送失败、unknown、ready/draft正文不暴露。来源明确失效时，即使state=sent也保持送达事实而清正文。partial轮次的已送达片段不可仅因turn phase泛化成未发。
- 旧入站origin自然到期不等于聊天撤回；只要当前viewer origin有效、当前映射和source仍合法，历史可读。不因无关记忆更新的全域版本变化隐藏聊天。不把历史展示当模型召回依据；无法证明的来源失效范围须交接明确限制。
- Platform sender复用旧conversation send schema，当前companion服务认证与actor origin route到platform单独核验，不能用网页Cookie授权。持久提交才sent，重复语义幂等；凭据/origin/source proofs永不进浏览器。web sender的原始存储仅内部使用，不作绕过Core来源可展示检查的网页读取入口。
- 关系检查：请求/响应request_id/actor/conversation一致；每页仅精确授权域；collector/turn内message id+revision唯一；reply segment_sequence<=segment_count，同轮段序唯一。验证超限、scope更换、源撤回及过期负例需真实Core实现测试，JSON例子不能替代双方联合验收。

- Core读取路径为POST /internal/v1/conversation/web-snapshot，仅platform服务；sender为已发布POST /internal/v1/conversation/send，平台接收仅companion服务。内部服务认证与网页Cookie会话隔离。
- sending是实际在途状态，content_state=unavailable且text=null。所有字段/数组或1MiB预算超限均明确budget_exceeded，不截断为成功；首版仅文本投影，无法安全表示的附件正文标unavailable，不泄露原始媒体定位符。
- 每次重取active与指定history页，终态集合为sent/failed/cancelled/observed/closed_unknown。列表按实际序号、ID替换，不能拼成一致全历史快照。授权示例仅实现待验场景，不是已经运行的授权证明。
