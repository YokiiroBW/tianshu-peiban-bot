# NR-06A 日记语义材料、独立请求与撤回收尾

## 提交与边界

- 基线：`c218515c21cb4b221dbda390aada8c9141693e46`。
- 分支：`codex/nr-06a-diary-materials-20261103`。
- 日记、Channels、共享 session 首片：`d6139830`，根已集成为 `19e9c1d9`。
- 后续 Models 取消原因：`b8ee1ce5233a0beb7cb1dab8ec0d384980c593ea`。
- 只写本任务新 worktree；未修改 Memory/Growth、任务调度器、前端、生产或配置。根追加授权的共享 Models session/取消合同及 Conversation chat 单处接线包含在上述提交。
- Life 产品所有权在首片收齐后已交回根，并由根分配 C 继续纯计算重试；本交接不宣称规划/日记自动重试已经由 A 实现。

## 日记材料

`life/diary_materials.py` 将当天原始 Episode 投影为有来源的整语义单元：发生时间、做了什么、实际执行/效果/交付、覆盖与原记录中的主观感受。发现只报告找到候选和可续读，不能当成已读正文；成功正文读取保留标题与实际读取状态，正文不重复放进日记提示；失败与未知保持原状态；想象活动保留 fictional_world/software_action=false；没有已记录描述的图片不从生成 prompt 编造画面。

候选列表、next_actions、网页全文、图片计划与供应商技术 payload 留在完整采集 recipe 中。模型材料附各经历的 diary_coverage，预算不足时省略完整语义字段或完整经历，不做字符串硬截取。固定人格、协议和写作指令都计入同一实际输入预算。

按用户后续要求，只有日记使用 `DIARY_INPUT_TOKENS=200000`；其他 Life 的 `INPUT_TOKENS=16000` 保持。装配总额度是 200000 + 当前 route 输出预留，扣除输出预留后独立输入上限恰为 200000。本轮不改已有日记 16384 输出/300 秒执行配置，不做付费满窗口探针。供应商满窗口实测不属于本提交的验证证据。

日记复用 ContextAssembler 的 tokenizer/固定权限规则，但使用无 retrieval/history 的专用实例。每个 actor/day/generation 只有固定人格、当日 observations 和写作指令；不引入聊天、其他日期、Memory 检索、先前失败的 partial 或模型 response。

完整 recipe 与所有原件 refs 继续承担撤回/修订失效依赖。模型请求只声明选中经历的来源。`diary_material_selection` 与冻结请求在同一事务落库，并以原 recipe digest 绑定；保存日记时仍重检原 Episode 版本及完整 audit refs。成功版本保留完整原 recipe、选中材料及模型回执。已有完整 recipe 冻结请求原样解码和观察，不改预算、session、attempt 或内容。

## 请求与上游 session

共享修改：`models/types.py`、`codec.py`、`transport.py`、`protocols.py`、`ledger.py`、`broker.py`、`discovery.py`，以及 `conversation/service.py` 的 chat 构造点。

- 新 ModelRequest 的 `provider_session_scope` 默认 `intent`。日记也明确设置 intent。
- intent 哈希含 principal、actor、conversation、function、intent。不同角色、私聊/群、功能或逻辑生成互相独立；同一个冻结逻辑请求观察恢复不换 session。
- 内部聊天入口明确选择 conversation，保留本会话连续性。原 conversation 哈希格式保持。
- 旧 wire 缺该字段时解码为 conversation；ledger 对 conversation 值省略新增键，保留旧 request_digest。intent 值参与新请求身份，不能把原 attempt 改成另一策略。
- intent 请求在 codec/Broker/Compiler 拒绝非空 previous_response_id、conversation/conversation_id、thread_id、session_id、cachedContent（包括 route 参数），不暗中带入上游续链。
- 原生 HTTP chat 明确选择 conversation，保留外部客户端主动提交、仍由 Broker 授权校验的显式 native 续接；native embedding 默认 intent。该入口无内部 conversation ID，独立请求仍有各自 intent。
- 模型供应商 HTTP 与目录读取临时 session 禁用 CookieJar，消除潜在 cookie 沿共享连接继承的路径；没有证据声称生产发生过 cookie 串话。其他 HTTP/网页引擎不受影响。

TrustedContext 的人物、场景、权限、scope、audience 与来源校验均未改动。

## 已撤回 Channels 输入

实证竞态为 channel_recall 已将事件设为 withdrawn 并清空 payload/outcome，但在途 Conversation._prepare_input → Sources.record_event 仍拒绝旧媒体 source_media_not_authorized，且尚未进入 collector.accept。

`channels/ingress.py` 继续执行原 Sources 保护。遇已知来源拒绝时重新读取同一事件；仅数据库已确认 withdrawn 才正常返回。checkpoint、进入 Conversation 前、最终落库前也检查当前撤回状态。未撤回的同类授权错误、其他业务错误继续抛出；不重发读取或消息、不改空 payload/outcome、不登记新 turn。

## Models 取消原因补片与 DDL

根追加授权处理 Client cleanup 把网络/消费者中断混同用户取消的同根因。

DDL：`src/tianshu/models/cancellation_reasons.sql`，由根在 foundation 注册 `0006_models_cancellation_reasons`。仅给现有 models.cancellations 增加非空 reason（explicit_cancel/consumer_interrupted）；旧行默认 explicit_cancel，保持保守含义。没有给 models.attempts 增列。

`ModelReceipt.cancellation_reason` 可空，status/result、正常及重复 create/finish、external 观察、管理列表和目录回执均读取当前取消原因。显式取消优先的 UPSERT 防止迟到 cleanup 降级；后来的 explicit 仅更新取消元数据，不重写原 status/error/request/result。取消先于 attempt 创建的竞态也保留该原因。

- Client 内部 cleanup 传 consumer_interrupted；普通 cancel 默认 explicit_cancel。
- submitted 消费者/流关闭保持 unknown，新增错误码 consumer_interrupted；submitted 显式取消仍 unknown/cancelled_unknown，并带 explicit_cancel 原因；owner 关闭/丢失使用 broker_stopping。
- 纯计算消费者应在重试前观察最新原回执，并优先排除 cancellation_reason=explicit_cancel。B/C 已收到精确字段与 SHA。
- 原未知占用保护、真实业务到期、Sources 校验保持。external 同样可以记录原因，但不会因此自动重发任何外部效果。

## 根统一运行的必要检查

A 首片：

- `tests/life/test_diary_materials.py`
- `tests/life/test_diary_automation.py`
- `tests/channels/test_ingress_withdrawal.py`
- `tests/channels/test_ingress_reads.py`
- `tests/models/test_provider_transport.py`
- `tests/models/test_native_protocols.py`
- `tests/models/test_execution_timing.py`
- `tests/conversation/test_provider_session.py`

Models 补片新增：`tests/models/test_cancellation_reasons.py`。受影响既有消费者可纳入同一轮：`tests/models/test_native_settlement.py`、`test_result_recovery.py`、`test_configuration_lifecycle.py`；无须按两个提交逐层重跑相同检查。

实际仅完成 18 个首片 Python 文件与 9 个补片 Python 文件的 AST 静态解析，以及两片 git diff --check。没有运行 tests/typecheck/build、模型、SSH、生产或真实 QQ；不宣称上述用例通过。

## 昨日失败日记的一次性恢复建议

部署并由根完成受影响验证后，使用原有管理授权场景读取 `/api/v1/life/diaries` 中目标 actor 的 `2026-11-02` 条目，核对状态 failed、原因 context_mandatory_budget_exceeded、当前元数据 revision，以及原来源仍合法。这里使用条目的 `revision`，不是正文 `current_revision=0`。

通过已有 `POST /api/v1/life/diaries/request` 提交 actor_id、原合法 person_id/conversation_id（若该场景需要）、day="2026-11-02"、expected_revision=刚读取的 revision、一个新的 client_request_id。该命令创建独立 generation，正常 collect/generate，保留旧失败记录；不直接改 SQL、不重放旧 model attempt、不注入伪造结果。重复传同一 client_request_id 用于观察同一命令，不创建额外生成。

如果状态已变为 collecting/generating/unknown/cancelling，不强建新请求；先沿已有 diaries/reconcile/原 generation 的恢复入口或 C 的纯计算重试实现观察收口。A 未操作生产恢复。
