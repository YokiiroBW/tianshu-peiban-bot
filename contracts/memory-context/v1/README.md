# memory-context/v1 提案（未发布）

Memory C1提供此包供协调者审阅。复用text-dialogue/v1精确query/command/scope/source/account/budget与unit、dependency_group，以及source-sync/v1 draft_unit；不更改已发布包。schema闭合且所有外部$ref仅指向上述本地固定依赖。协调者发布并回传清单hash后才能正式装配。本文没有授予生产权限或宣布验收。

## 服务接口

全部POST、服务Bearer、JSON、no-store；共用现有有界请求和超时。路由前缀`/internal/v1/memory/context/`：query→query_request/query_response、propose→proposal_request/receipt、receipt→receipt_request/receipt_response、batch→batch_request/batch_response、association→association_request/association_response。caller operations分别为context_query/context_propose/context_receipt/context_batch/context_association。

Memory实时通过既有Platform origin resolver核验请求者账号、actor及精确scope，并验证Memory账号binding。query先同步来源屏障，再在有权范围取至多limit+1完整组候选，共享调用方整轮预算；selection不固定为evidence。时间范围按来源owner的sent_at过滤，不把记忆形成时间当事件发生时间。coverage.complete只证明本次候选窗口，不证明历史全部捕获（history_complete始终false）。完整语义组不拆分，条件/否定/不确定性保留。scope_checks记录全部实际有权范围及关联版本，发送前零预算以known_scope_checks和known_association_version复验。普通群永不展开私聊关联。

## 逐项提议及恢复

每请求一个item，batch_ref仅关联独立结果，不伪称跨项事务。upsert要求非空units和当前有权evidence；correct要求目标ID/版本、非空新units和purpose=revision证明；forget要求目标ID/版本、空units和purpose=revision证明；no_op只写分析完成回执、不写事实，允许空units和空evidence，但仍须实时scope授权。no_op不会把所有未列入的材料标已处理。

operation_id等于command.idempotency_key。同service/scope/operation重放同payload返回原回执，同键异内容409。版本冲突、失效来源、目标不可用等业务拒绝保存`state=rejected,error_code,current_version`逐项回执（HTTP200），与committed/corrected/tombstoned/no_op区分；身份错误401/403、schema400、容量429和依赖暂不可用503不冒称成功。提交或ACK丢失用receipt查询原operation，不换新ID重做。batch有界返回已经提交或拒绝的item回执，尚未提交项由Companion原batch/coverage owner保存。

来源依赖仍登记lineage，来源撤回/删除及binding变化继续使读取不可用，迟到提议不得复活失效材料。新target_application账本按(source,revision,scope,target,logical operation)记账，允许同一证据支持不同真实目标及修订，不把摘要转述当独立证据；旧source_writes可证明的目标映射迁入，原始表保留历史。

## 同人关联与纠正证明（C4依赖）

Platform提供`POST /internal/v1/memory-context/proof/verify`，由专用Memory→Platform服务凭据调用，schema为proof_request/proof_response。配置`memory_context_proofs:{url,token,ca_file?}`，HTTPS/证书校验、5秒预算、16KiB响应限制；无配置返回503。

proof_ref由Platform持久化签发，purpose、双方账号、actor/两个精确scope、完整operation_digest、到期及真实principal固定。association由一次绑定挑战产生：已认证源账号发起nonce绑定目标与scope，目标账号实际认证后确认同一nonce及用途；普通QQ入站origin只证明作者，不自动证明此次关联同意。验证器重读当前权限/撤销及purpose/digest，Memory另核两个独立person的当前binding。关联仅把这两个有权私聊scope加入可撤销读关系，不合并people/accounts、不传播管理员资格；不能用昵称、模型声明、service token或任意两条origin建立关联。revoke由任一关联本人当前认证账号以CAS执行，立即推进双方关联epoch；再link生成新对象与新证明，旧关联不复活。

revision证明来自可信聊天入站/用户操作：签发绑定真实作者、当前来源版本与本次完整修订内容。C3提交用户明确纠正/遗忘的实际来源给Platform，Platform核验来源属于该本人及当前可用、该用途确由可信用户操作承载后登记证明；不要求额外网页点击。模型解释可以提出语义修订，不能自己签发证明。没有这条生产签发链不能把模型提议冒称用户批准。

operation_digest：对请求去除query/command、proof_ref后使用canonical JSON(sorted keys, separators comma/colon, UTF8, ensure_ascii=False) SHA256；请求关联号、deadline和proof引用不改变业务语义，同item payload修改必须新operation。proof返回request_id/ref/purpose/digest均须回显，valid=true且未过期。

关联query只展开当前请求精确scope直接连接的活跃关联，不传递式合人、不扩展到其它受众。association_version为当前(actor,person)单调epoch，scope_checks同时绑定各scope版本；已接受的Memory事实仍保留各自原person及来源。

## 原始时间有界读取

time_range 仅按已验证 physical_input.sent_at（原始消息发送时间）筛选，半开 UTC 区间 [from,to)，不使用补传 accepted_at 或 turn.occurred_at。仅有时间要求时复用 source-facts/read include_content=true 临时读取，整轮最多 256 个 selector，沿 source_snapshot/current_access/sync_barrier 比对当前授权与 owner head，不保存正文、不新建源时间账本。范围无法核对的候选保留，coverage.complete=false、missing_source_times>0 且 omissions 包含 source_time_unavailable，调用者不得说这些候选发生于指定范围。普通自然回忆 time_range=null 不受受理时间过滤。候选 limit 截断本就使 complete=false，因此不能据此声称范围内无其他历史。

# Memory 修订证明签发增量

已由协调者发布为实现合同，运行与语义联合验收待完成。唯一新增 POST `/internal/v1/memory-context/proof/issue`；生产者 Platform，消费者 Companion。复用 Companion 既有 `source.input` 服务授权；验证端复用 Memory 既有 `source.current` 身份。不会把源正文、token 或证明回显到浏览器/日志。

issue_request/issue_response 已并入本包 `$defs`。revision_draft 派生既有 proposal_request，只允许 correct/forget、proof_ref:null；签发后替换 proof_ref 提交原 proposal_request。其他业务字段、null、数组顺序和文字不变。schema ID 与本地自引用由协调者并入既有包，不能另外发布旁路包。

operation_digest 唯一算法与 C1 一致：对完整 proposal_request 仅排除顶层 query、command、proof_ref，保留所有其他字段；json.dumps(ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)，UTF-8 SHA256 小写十六进制。issue_response 仅返回 ref、digest、到期及请求编号。

Platform 重读 actor origin、actor_origins.input_digest、input_observations/current revision、source_inputs 与当前源 entry。source 必须与本次已登记入站完整 physical_input 摘要一致，作者为该 origin 实际账号，精确 actor/person/audience/conversation 与 proposal.scope 一致；proposal 的 command.origin 同当前 origin。正文要来自真实用户输入，不能是模型生成/回忆/虚构日记；证据中的该 source revision 必须真实对应。proof 持久绑定此源、范围、完整修订、签发身份与到期；verify 再核当前权限/源版本/撤回与摘要。

自然语言具体修订意图沿当前对话的可信用户操作解释，由 Companion 的当轮表达/工具流程判定，Platform 不重新运行记忆检索或模型裁决。`我今天忘记带钥匙` 不是遗忘长期记忆授权，`不是这个` 的目标不明确时应当轮澄清；不能因为文本含“忘记”或 payload 自称 authorized 就签发。Platform 的证明只把已明确的该用户操作绑定真实入站和具体 proposal，目标存在/归属/版本/合法修订仍由 Memory 同事务核验。C3 解释链须验证明确动作及歧义反例；来源证明不等于自然语言语义正确性证明。

3 正/5 反 schema 样例在正式已发布依赖上通过。运行/语义负例尚待实现：无真实源、作者或 audience 不符、源修订/撤回、过期/撤权、同 key 改摘要、单纯提及/歧义源不得成为已同意修订。不会新增网页确认或第二份目标事实库。
