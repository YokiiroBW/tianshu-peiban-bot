# TS-050 后续最小接点（提案，未发布接口）

以下只描述已固定产品提交暴露的缺口和必要事实，不是新的共享合同或已实现承诺。协调者已另派 TS-032 处理 HTTPS 接线；TS-050 不修改产品。仅补 TLS 仍无法解除 SourceAuthority gate。

## Memory TLS

固定 `358e4a6` 的 `src/tianshu_memory/auth.py:Authenticator.resolve` 在每次解析时直接构造 `httpx.Client(timeout=5, follow_redirects=False, trust_env=False)`，并强制 `issuer_url` 为 HTTPS；构造器只接受 config_path/contracts/clock。`src/tianshu_memory/app.py:configured_app` 创建它时没有 transport/SSLContext/CA 参数。

最小改变位置：在 Authenticator 的**部署输入/构造依赖**提供受控 CA 文件、SSLContext 或同步 HTTP transport，并由 configured_app 明确传入。不是请求 payload 字段，不能接受 verify=False；保留 HTTPS、固定目标、凭据、issuer/caller/receiver/ref/actor/expiry/revocation 的全部检查。优先不复制 resolve 业务验证逻辑。

TS-050 真实输入形态：issuer=platform；issuer_url 为临时 `https://127.0.0.1:<port>/internal/v1/origins/resolve`；caller service=companion；独立 issuer_token 对应平台 service=memory / resolver={caller:companion,purpose:dialogue}；allowed_actors 包含固定角色。第一次 person/conversation 为 null，分别由 Memory register 和 Core ingest 的真实回执回填。需要证明可信 CA 正向 register/resolve、不可信 CA 拒绝，以及来源/凭据错误仍失败；source_authority=None 的 select（含0预算）/consume/revise 继续503。

## 来源组合的事实、归属与接点

| 必要事实 | 归属方 | 当前真正可用接点 | 最小待补能力 |
| --- | --- | --- | --- |
| 已认证入口、账号/角色/渠道/受众、撤销/到期 | Platform | `Origins.issue/resolve/revoke`；resolve 有 HTTP，其他为受信 Python/CLI | 保持真实签发与接收者用途校验；不能从 payload 补 identity/scope |
| person_id、账号绑定版本 | Memory | HTTP identity resolve/register | 现有真实响应，经平台 prepare_mapping/confirm_mapping 回填即可；不由 Core 生成 |
| conversation_id 与首次渠道关联 | Core | HTTP ingest response；平台 mapping prepare/confirm | 已用方案B接通；正式进程边界需明确受信回执适配部署 |
| 当前消息 revision/墓碑和入口 scope tuple | Platform 当前入口；Core 本地收件 | 平台 `observe_source/verify_current_sources`，仅受信应用端口 | 平台返回仅 partial，不验证 receipt_id/归档/Core turn/Memory版本 |
| 某 source.receipt_id 是否确由 Core 受理、原消息/作者/版本/reality 是否对应 | Core 收件权威；归档后原文另属 Chat Audit | Core ingest 回执公开，但没有供后台核验任意 receipt 当前事实的公开端口；内部 Store 不是跨产品接口 | 提供只读受信来源事实查询/核验应用端口，并返回当前修订/撤销及绑定关系；TS-050 不读内部表冒充它 |
| committed_event 的 turn/input_revision/aggregate_version/delivery/sources 是否与当前事实相符 | Core | Core 产生真实 outbox；`repair_blocked_scope(turn_id,verify_current)` 是消费回调，不能自行证明事实 | 提供 owner 当前 turn/source 核验端口或确定的事件认证与重放协议；现 Memory SourceAuthority.verify 的入参只有 sources/scope，**看不到完整 event**，不能独自解决 owner事实问题 |
| 当前 Memory scope_version 和来源更新导致的失效 | Memory | `_scope_version/_invalidate/...` 为内部方法；HTTP select 正确被来源 gate 阻断 | Memory 自己的受信来源更新/失效应用服务，在自有事务内写 sources/lineage 并提高 scope version、失效组/候选/投影；不跨产品写表，不由平台伪造 scope_version |
| archived locator、归档回执与读取范围 | Chat Audit | 本次四产品固定组合未包含归档权威 | 只在独立发布和双方验收后接入；pending 保持 pending，archived 未核验必须拒绝 |
| 用户确认确实对应某 revise/forget/account/scope/版本/有效期 | 认证入口/确认签发方；Memory消费 | 仅 `LocalWorkflow.confirm_revision`，要求 LocalFixtureSources | 独立受信确认登记/核验应用端口，按精确 command digest 绑定、一次消费；模型输出/服务token不是用户确认 |
| 对候选进行完整组写入、重复来源不重复产生副作用 | Memory | `LocalWorkflow.commit_candidate` 仅 fixture backend | 把真实来源候选的提交应用端口与 fixture-only 来源加载分离，保留本地 write_ledger/source_writes 幂等和版本检查；不能通过继承 fixture 类绕过构造检查 |

`MemoryService._source_rows` 在 SourceAuthority.verify 后仍从 Memory 自己的 sources 表读取精确 payload/revision/scope；`_group_current` 把 lineage/source rows 传给 current。**只实现远端 bool 返回值仍无法接通**：当前 sources 更新、确认和候选写入均由 fixture-only LocalWorkflow 承担，必须由 Memory 所有者提供真正受信的应用服务。

需要双方先定义更新与查询的事务/故障边界：远端撤销、来源更新和发送前复核没有天然跨服务原子性；应明确版本快照、拒绝陈旧、更新不可用时的失败关闭、重启和重放行为。范围版本不明时 Core blocked_scope 不可被修补为固定1；不把平台的 false flags 改成 true。

## 解除阻断后的最小联合重验

按协调者发布/集成顺序更新明确 pin，再重跑受影响路径：真实 Memory HTTPS 正向身份；真实来源/owner版本组合后的零预算与完整组；首账号W0回填；真实 T1/T2 生成与 T3 等待；普通名字续问的实际模型输入；来源修订/权限撤销/遗忘后旧证据拒绝；有序发送与渠道unknown；Core进程恢复；真实committed_event/候选重复来源幂等。成功的无关网关字节测试不因画像或纯文档变动重复运行。
