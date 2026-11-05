# RBG-03 视频证据与解析恢复交接

基线 `5cd309ae67c9896feb787ad03cc65272c4884173`，分支 `codex/rbg-capabilities-20261105`。本卡只改视频感知、Action 恢复入口与视频定向用例；未新增 SQL，未改共享迁移或装配。

## 行为与接口

- 程序按实际提供给模型的静态帧、音频、原生视频/音轨和实际字幕生成稳定证据 ID，模型引用 ID；时间与模态由程序绑定。原生提供方内部采样保持未知；仅帧输入上的旧 `video` 声明降为基于采样帧的推断。`sound`/`acoustic` 只在确有音频输入时映射。结构性证据校验不代表自然语言正文已被事实核验，结果保留 `semantic_validation=model_report_not_verified`。
- 模型无效或无依据的正文不能作为解析成功。旧格式可读取；六位小数边界的半微秒舍入归一到真实来源边界，非有限值、空/反向区间及实质越界仍拒绝。
- 只读分类：`await actions.inspect_video_parse_recovery(action_id, context, expected_attempt_id=...)`，返回 `eligible`、`reason`、`operation=resume_parse`、`mode=local_parse|bounded_correction`、`segment`、`model_attempt_id`、`parser_version`、`action_id`、`attempt_id`、`receipt_id` 和 `result_revision`。实际执行复用 `Actions.resume`，传入期望的 attempt、receipt、revision 和现有 `recovery_guard`。分类是快照，真正恢复还会事务内重验来源、generation、回执和检查点。
- 解析恢复复用原成功 Models 结果与冻结输入，不重新下载或分析已完成段；保留原 Action/模型回执、检查点以及解析版本和关联。提交中断后复用原 consumption，重复恢复不新建消费。无证明的历史记录和未知外发不被推断为成功或未发生。
- 结构或证据仍不合法时，原 Action 的所有片段共享一次纠正机会：Runtime `RecoveryPolicy` 使用 `domain=video_parse`、`owner=capabilities.video.parse`、`max_attempts=2`，原成功模型计一次、纠正计一次。纠正只使用失败片段的冻结输入和证据目录，保留原来源、上下文、业务截止与 generation；新模型尝试和策略预留在 Action 源/所有权锁下持久化，重复观察使用原尝试。最终消费提交前再次检查策略尝试可应用。已有纠正历史或预算身份无法确认时只允许本地重解析，纠正明确阻塞。
- 协调者在产品共享装配中绑定 `caps.perception.recovery_policy = policy`。本窗口未改 `service.py`、`product.py`、全局迁移或共享类型。与 RBG-02 衔接的失败回执为 `phase=parse`、`execution=failed`、`effect=none`、`delivery=not_requested`、`recovery=resume_parse`，含 parser_version、segment、原 model_attempt_id/receipt、source_refs；纠正后的无效输出为 `recovery=none`。

## 已写用例与未验证项

新增 `test_rbg_video_evidence.py`、`test_rbg_video_recovery.py`，并适配既有视频定向夹具，覆盖半微秒及真实越界、非有限/反向、实际音轨、采样帧/原生视频、证据 ID、字幕/推断/未知、无效正文、旧结果零调用重解析、来源/检查点变化、预算身份与跨片段限制、原纠正尝试复用、迟到结果及消费提交中断后的复用。

静态审阅及 `git diff --cached --check` 已完成，修改的 Python 文件已作 AST 语法解析。按本批约定，本窗口不运行测试、构建、烟测、模型、生产或真实数据恢复；协调者集成后集中执行必要验证，并单独判断现场两条 Action 的可恢复分类及业务结果。结构检查与用例代码不构成已通过的测试或线上验收。

## Action 持久回执身份补充

`Actions.save` 先完成来源撤回、取消标记和现有终态保护，再比较不含 `receipt_id` 的语义：内容相同时沿用当前 ID、结果版本和观察时间，不发布新事件；内容变化时，若调用者复用当前或本补充已记录的旧 ID，生成新的后继 ID，以该 ID 发布 `capability.result`，不更换原 Action、attempt、provider handle 或其他副作用身份。已提交语义变更的旧 ID、attempt ID、版本、执行/效果/投递状态、旧回执摘要和后继 ID 写入 `document.receipt_lineage`，不复制正文或来源内容；既有 `prior_unknown_receipts` 的保留和撤回清除逻辑不变。本补充不声称补回此前已不存在的历史原文。

本合同覆盖持久回执、事件和消费身份。`enqueue_control` 在排队后返回的 `reconcile_requested`/`cancel_requested` 临时确认仍沿用原公开响应，未持久化，也未纳入本补充；将控制确认从回执拆出属于后续接口协调。

新增 `test_rbg_action_receipt_identity.py`，使用实际 `Actions.save`、结果事件入队与 `ResultConsumer` 消费路径的隔离 PostgreSQL 夹具，覆盖同 ID 语义变化、事件与消费去重、重复保存、历史 ID 再用、来源撤回、取消标记及旧 unknown 回执；只编写用例，未运行测试。
