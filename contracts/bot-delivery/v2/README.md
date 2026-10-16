# bot-delivery/v2 实现合同

主协调审核发布；生产者/消费者联合与实机验收待完成。

POST Platform /internal/v2/bot-delivery/send、/query、/cancel、/finalize。Companion Delivery实际调用者为普通Core增量回复、Direct已授权主动指令、Proactive有据动机；全部进入Platform现有bot_send_jobs队列和真实QQ adapter，不另建发送账本。主动不借turn_sequence；response引用真实turn_id和origin，direct引用真实direct_request_id和origin；Platform用既有resolver/绑定校验Actor、接收者、渠道和实际操作授权，自填actor_id不构成权限。

同expression_id固定origin/scope/channel。每段segment_id、reply_id、sequence稳定：首次从1连续增长；已存在segment完全同内容重放回既有回执，ID/顺序/内容冲突409。request_id是一次append操作ID；重放同摘要，冲突409。send至少一段非空text或refs；final=True在接纳本次段后关闭；没有新段时调用finalize（仅关闭，不发送空text），已final后仅接受完全相同重放，不再接纳新段。最多64段。平台实际出站序号由现有队列发放，不由Companion构造。

send_receipt汇总该expression已接纳全部段。final=False且已接纳段送达时仍为sending，不称整个表达sent；final=True且所有段真实sent才整体sent。queued/sending是非终态；一部分sent另有failed/cancelled为partial；无法确认某段为unknown，retry_safe=false。流中断保留已发片段，Companion调用finalize或cancel其尚未执行部分并保留partial/unknown；不得重发整个表达。query返回receipt:null是尚无记录，与failed不同。cancel不等于撤销已远端发出段，无可靠未产生效果证明不得重发。媒体引用由Platform向owner按授权读原件并实际QQ上传/引用，无法读取或发送须真实失败，不返回queued冒称完成。

兼容迁移：保留既有/v1/conversation/send和reply-status及其真实inbound turn_sequence，旧非Platform bridge继续走其原v1协议；Platform绑定的普通/Direct/主动改由v2封套进入同一现有队列实现，v1也调用同一发送/回执归属，不建平行ledger。现有发送记录不重写、不推导假sequence；旧未结算记录按旧ID查回执；v2新表达使用独立ID。

segment 可选 media 由 Companion 按原 scope/actor 权威实读物化，Platform 同队列持久化并校验 base64 解码、sha/type 和同段 content_refs。最多 4 个媒体、decoded 合计 32MiB 均按 expression 累计，幂等重放不重计；单 POST JSON 至多 45MiB。不得把原图暗中缩小以绕限制；插件只读物化 bytes、不自行联网读取私有 URL，真实 SDK 回执后才 sent。内容正文不得进入普通日志或状态回执。

# Existing proactive recipient's current read context

POST `/internal/v2/bot-delivery/context`, existing authenticated Companion `dialogue.send` caller only. No input, collection, turn, expression, or recipient registry is created. Only `kind=proactive` is accepted: this is current recipient scope authorization, not new user intent.

Platform reuses Delivery._entry/_connection under the existing managed-actor guard. The exact account/actor/audience/conversation/channel registration, live connection, current QQ policy, principal and entry revocations must still hold. Companion→Memory/dialogue must be an existing route. Origins.issue_registered shares the issuer's normal durable origin record, entry digest, TTL, and revocation semantics. A response or Direct origin cannot be renewed into a new user's source.

Companion is the sole subscription/candidate owner; its runtime must check active subscription, enabled role/life epoch, and still-valid candidate before asking for this context and again before any send. No model-facing context or permission tool is exposed. A paused/revoked subscription stops this production consumer; Platform does not mirror the subscription database.

The response origin is used only for the candidate's actual scope-bound Memory queries and live source checks. Historical source identifiers remain unchanged and do not turn into a newly admitted message.
