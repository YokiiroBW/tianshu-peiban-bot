# NR-07C Memory 整理独立计算重试与装配

基线：`59b9b37a68fae619517cca0862d0848cdf5633e6`。独立受管树：`memory-recovery/tianshu-peiban-bot`。本交接与实现同提交；精确最终 SHA 随交付回执提供。

## 变更

- `consolidation_retry.py` 归属 Memory 的模型计算重试；复用 `models.retry.transient_model_failure`、`retry_delay_seconds` 和既有 `memory.consolidate` 队列，不增加通用队列或修改全局 Broker。
- 同一 `batch_id` 保留逻辑工作、原 episodes/来源/版本、frozen、原 request/receipt；`consolidation_attempts` 分别持久化新 step/attempt 的前驱、序号、到期时间、首个失败快照和语义拒绝原因。批次最终以 `selected_step_id` 选择一次结果。
- 暂时失败释放活动位，默认 30/60/120…秒退避、上限 900 秒，让后续新材料推进。重启按原 `retry_due_at` 重新装配原生队列，普通排队不消耗模型执行时长。
- 已排队/运行的原请求仅观察。已知原成功先消费；确认为未知或暂时失败后才复制原冻结请求创建新纯计算身份，保留原型号/参数/图像/上下文，分配新的 300 秒执行窗口。旧请求不会重发或改写。
- 原结果迟到仍只是候选；最终来源、Identity、episode revision/sequence/cursor、读集及 batch/selected-step CAS 与全部语义操作在原事务中提交，重复消费不能应用两次。
- 明确取消、撤回/权限失败及永久配置错误不自动重试。旧提案发生读集或路由绑定版本冲突时保留旧批次审计，只有仍匹配且未消费的原 episodes 回到正常 pending，由新 batch 重取材。
- 历史旧协议 known-output failed 通过既有 extraction resume 正常重新取材成 v2。新 v2 异常语义最多一次持久输出修正及一次显式独立恢复；不以创建新 batch 逃逸限制。输入/输出预算配置实际变化仍可正常刷新材料。
- `_request` 接入 A 的图文消息/有效新材料/能力接口和 B 的按 frozen 分型解析。完整原图参与 TokenCounter 与 Broker 相同的 inline 字节/part 预算。超预算先裁整条非必要历史消息，再省略可补读的旧记录正文，必要引用消息最后取舍并明确缺项；新材料仅缩小真实前缀，不以图片说明代替原图。
- 旧记录 stub 的 `record_context_required.details.requests` 先做一次确定性补查。补查无法完整放入预算时返回明确 unresolved、保留所需引用，不删除依赖后强行应用。补查恢复原文本时保留 media、作者、消息时间和既有 mN/rN 编号。
- grouped proposal 同一主体部分 changed、部分 unresolved 时，仍保留 `completed_with_missing` 的覆盖语义。

## 根集成依赖

1. 注册 `0024_memory_consolidation_retry.sql` 到 foundation migrations 与 Memory module migrations；根已告知完成。迁移仅增加 Memory 域列、表和索引，不改写历史批次为成功。
2. A：`materials.model_messages`、`has_new_material`、`material_capabilities`；新的 `append_context_material` 图片/作者/范围语义与 `model_protocol='memory.proposal.v2'`。
3. B：`extraction_contract.parse_model_proposal`、`generation.repair_diagnostic`；record stub 诊断携带最多四条完整正式 `details.requests`。
4. 根：`models.budgets.MAX_INLINE_MEDIA_BYTES`、`MAX_INLINE_MEDIA_PARTS`，保持 Broker 原 32 MiB / 128 parts 的既有行为。
5. 不需要新 queue handler 或公开路由。保留既有 `extraction/status` 和幂等 `extraction/resume`；resume 仍经 CommandStore、Sources/Identity 和原 episode CAS，不能手工把旧失败/未知数据改成功。

## 回归用例

更新并补充 `tests/core/test_consolidation_pipeline.py`：新 frozen 的 fixture 输出严格使用 v2；专门旧 frozen 用例仍输出并消费正式 v1。涵盖未知独立计算、原成功优先、迟到结果竞争与只应用一次、queued/running 不重复、失败让位、重启 due、显式取消/永久配置、来源撤回、历史 quarantine 接管、路由/CAS 重取材、已知旧坏输出升级、显式语义恢复上限、图像 inline 边界、stub 补查/预算未决及部分成功覆盖。

## 已做静态检查与局限

- 对 5 个修改的 Memory Python 文件与 pipeline 测试文件执行 `compile(source, path, 'exec')`；无导入、不生成 pyc，6 个文件语法通过。
- `git diff --check` 通过。
- 按本轮约定，没有运行 pytest、构建、应用导入、真实模型/QQ 或生产操作。A/B/根共享依赖只在根集成后才齐备；正式隔离 PG 回归由根集中执行，不能把上述静态检查称为业务通过。

没有推送、生产迁移、部署或发送消息。原 Models unknown/failed 回执保持；新计算与旧结果能否在当前真实供应商配置下完成，须由根使用正常恢复入口验证。
