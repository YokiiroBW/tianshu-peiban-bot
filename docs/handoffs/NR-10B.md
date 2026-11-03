# NR-10B Growth 与自主对话输入预算

基线 `f1a783b03df5abce58d441957776b5298d4658ee`；分支 `codex/nr10-growth-budgets`。开始时 tracked clean，原 NR-09 分支与提交保留。公共 `tianshu.models.budgets.BACKGROUND_INPUT_TOKENS=200000` 由 A 单写，本块只直接导入，不增加第二个预算来源。

## 实现与边界

ContextAssembler 的 `max_input_tokens` 包含 `reserved_output_tokens`，实际输入上限是两者之差，因此装配额度如下：

| 调用 | 装配总额 | 独立保留 | 实际输入 |
| --- | --- | --- | --- |
| Growth 初次反思 | 200000 + 8192 | 输出 8192 + 既有纠正 envelope | 200000 减纠正空间 |
| Growth 纠正 | 计数器检查完整新候选 | 原输出额度保持 | 完整输入至多 200000 |
| 群参与判断 | 200000 + 256 | 输出 256 | 200000 |
| 自主 chat / ELASTIC | 200000 + 2048 | 原输出预留 2048 | 200000 |
| 普通人工 chat | 原默认 16000 | 原预留 2048 | 原默认 13952 |

删去 Growth 原 13000 的独立输入别名，materials 与 repair 直接使用公共导出。输出 JSON/推理共用的 8192、一次有界纠正、完整证据、人格与协议保留。群参与未知请求在 `frozen is not None` 时继续读取原请求；Growth 已冻结 batch/request 继续原读取分支。纠正只生成 successor 候选，不覆盖原请求、原结果、身份或来源闭包。没有修改 GenerationRequests、重试策略、冻结身份、Sources、CAS、模型绑定、超时、外部投递或数据库结构。

自主 chat 只在 `row.autonomous` 为真时显式传后台预算；人工路径不传新预算，继续现有装配默认。共享 ContextAssembler 及 Companion port 的 16000 默认保留，避免人工聊天随之扩容。本域其他调用点未发现剩余显式后台输入上限；没有把无输入上限的视觉/感知、字节数或输出上限当成本次替换对象。

## 原 Growth 阻塞分区的正常恢复

根提供的现状是 `reflection_material_budget_exceeded`、14 条 pending、`active_batch=None`。本代理没有读取或修改生产；实际 actor、scope_key 和 revision 由根操作前通过原分区当前视图确认。

1. 使用原角色、原 person/conversation 场景及相同授权 scope 集合，调用 `POST /api/v1/companion/growth/view`。确认 `review.last_error` 为上述预算码、`review.batch` / `material_key` 为空、`pending_inputs=14`、`legacy_state=complete` 且 `can_request_review=true`，读取当次 `review.revision`。不要用管理员的更宽 scope 集合替换原分区；scope_key 是排序后 scope_ids 的 signature。
2. 调用现有 `POST /api/v1/companion/growth/review`，沿用同一 `actor_id`、`person_id`、`conversation_id`，请求体加入当次整数 `expected_revision` 和全新的 `client_request_id`。本例不传 `retry_key`，没有需要重读的已冻结批次。JSON 形状：

   ```json
   {
     "actor_id": "原角色ID",
     "person_id": "原人物ID或null",
     "conversation_id": "原会话ID或null",
     "expected_revision": 123,
     "client_request_id": "本次新的幂等请求ID"
   }
   ```

   上述 ID/revision 只是形状示例；人物或会话原来为空时使用 JSON null，不填写字符串 null。权限沿用该写入口要求的 `companion.manage`，不新增授权。
3. `GrowthService.request_review` 在事务内校验来源/上下文并锁原 organize_state，`revision(row, expected_revision)` 进行 CAS，CommandStore 负责该 client_request_id 的幂等。正常分支只递增 generation_revision 与 revision、清原预算阻塞并排入 `companion.organize`；不会把 14 条 pending 直接改 reviewed。409 时重新读当前视图，不强改版本。
4. 原队列按新预算重新取原 pending Episode 版本，完整复核 Sources/Identity，冻结新 material key/request；实际提交仍执行 `_validate_material` 的精确 Episode 版本复查及 `current_material` / material key / cursor 事务保护。只有模型结果通过现有协议并成功应用后，`_finish` 才推进这些原版本的 reviewed_revision/cursor。未调用模型的原预算错误没有可被覆写的 unknown 请求。

启动 `growth.recover()` 会避开此类 REVIEW_ERRORS，单独重启不会解除预算阻塞；应使用上述既有 review 入口。若当次视图已经有 active batch 或 unknown，则停止按本模板新建批次，沿现有原请求观察/重试分支处理，不改旧回执。

## 精确验证与局限

只做 `git diff --check`；未运行测试、编译、应用导入、构建、生产、真实模型或 QQ。依赖 A 的公共常量收齐后由根集中验证。

- `tests/companion/test_growth_budget.py::test_reflection_input_reaches_200000_with_correction_and_output_reserved_separately`：使用真实 ContextAssembler/TokenCounter 计数到 200000 减纠正空间，越界一个 ASCII token 拒绝，保留完整材料。原前缀缩批用例增大夹具以继续覆盖新阈值。
- `tests/companion/test_growth_short_refs.py::test_correction_never_trims_evidence_to_fit_its_budget`：真实计数器校准纠正完整输入恰好 200000 可用，200001 拒绝，原冻结对象、证据与输出额度不变。复用同文件原 unknown、纠正次数、重启/身份覆盖。
- `tests/companion/test_growth_output.py::test_reasoning_and_complete_small_batch_share_the_reserved_output_budget` 适配共享输入常量；新增 `test_existing_budget_blocker_resumes_fourteen_pending_versions_through_normal_review_cas`：14 条原 pending、batch=None，旧 revision 拒绝；正常 review 后仍 pending，执行新批复核后才 reviewed，输出仍 8192、执行仍 180 秒。
- `tests/conversation/test_group_participation.py::test_group_participation_has_200000_input_but_keeps_256_output_and_frozen_unknown`：真实装配器填到 200000 输入，输出仍 256；再次处理原 unknown 不重新装配或调用生成。
- `tests/conversation/test_turn_rejections.py::test_only_autonomous_chat_gets_200000_input_and_keeps_its_original_output_budget` 两参数：自主输入 200000，人工总额仍 16000，输出仍 2048；本地超预算拒绝发生在任何模型调用前。复用同文件失效权限、业务截止和旧 RPC 回执场景。

这些是隔离计数/行为用例，不是供应商实际 token 或生产恢复成功证据。生产路由实际上下文能力及新预算恢复结果由根集中核实；不把输入 200000 解释为输出 max_tokens 200000。
