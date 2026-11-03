# NR-08C Growth 短引用和一次输出纠正

基线：`59603da3a45138b6c273f3f7ef7bc0e2a52d708b`。分支：`codex/nr08-growth-output`；复用受管 worktree `memory-protocol/tianshu-peiban-bot`。旧 NR-07 提交保留在 `codex/nr07-memory-protocol`。本卡仅本地实现，未运行测试、构建、真实模型或生产操作。

## 实现和查询字段

- 新模型材料 `model_protocol = growth.reflection.output.v3`，`episodes[*].episode_id` 使用批次内 `e1/e2`，`episode_refs` 严格映射至原真实 ID。映射同时保存在不可变 GenerationRequest 材料及 active/retry batch 的 `episode_refs`、`reflection_output_protocol` 中。新材料 key 包含 output_protocol，避免与旧冻结请求碰撞。
- `frozen_episodes` 从该映射还原原 ID、revision、source_refs、时间和游标；未知协议/重复映射/未知引用拒绝，不模糊匹配 UUID。旧冻结请求无 model_protocol 时继续按原 UUID 协议读取。`validate_batch_request` 在应用和 retry 前核对真实 inputs(id,revision)、模型协议及 request/batch 映射；旧控制账本采用显式新请求时仅允许从该原请求补控制元数据，再做相同核对。
- 正式 ReflectionProposal 与原 `growth.reflection.v2` 身份算法保持。完全相同的 kind、title、content、证据集合、conditions、counterexamples、affect 确定性去重；不同语义仍以 reflection_duplicate_proposal 拒绝。证据数组仅按集合排序，其他语义字段不做猜测归并。
- reflection_support_invalid / reflection_duplicate_proposal 的已知成功输出可纠正一次。`GrowthRetries.defer` 同一让位事务在 batch 保存 `output_repair_count=1`、`output_repair.{rejected_material_key,attempt_id,diagnostic}`，随后通过既有队列处理。诊断含 path、actual、expected、allowed_refs、conflict_with、conflict_fields 等有限字段。
- `GrowthRetries.run` 复用 `GenerationRequests.prepare` 冻结独立 attempt，完整原件、persona、权限场景及原模型参数保持；新候选追加 output_repair 诊断和有限错误片段，要求完整 notes JSON。纠正的暂时传输失败继续原有纯计算退避，保留纠正计数与原诊断，不再追加语义纠正。一次已用后再有语义输出错误保持 blocked，request_review 不能重置该上限。
- 初次材料选择通过实际 assembler.counter 测量有界纠正信封并预留；生成纠正时再用同一 counter.measure 测量完整 system/messages/tools。输入仍为 13,000，输出仍 8,192，执行仍 180 秒。超过输入预算拒绝，不裁原件、不补字段、不扩大路由参数。
- 原有 historical_only 分支继续只记录历史 reflection，去掉迟到 affect，不重投影 interest/style。来源/版本/CAS 复检与完成事务保留。

## 原 blocked 两批恢复

现有 request_review 域命令和相同场景授权继续使用，仍只排队，`Models=None` 的最小控制实例可用。旧已知坏 UUID/重复冲突结果在执行 retry 时重新按原协议验证；真正需要纠正时新建短引用候选并保存同一个一次上限。旧 GenerationRequest 和 Models 回执不修改。已经能够无损去重的旧输出直接复核原结果，不额外调用模型。

根无需新增共享接线、表或 Models 接口。上线后可通过原 Growth view 的 retry_batches 查看 current_material_key、attempt_number、output_repair_count 与状态；精确诊断和映射在既有 growth_retry_batches.batch、不可变 generation_requests.request 中。

## 验证交接

已手工审查本卡 diff 和 `git diff --check`；未执行任何用例。根集中验证：

- `tests/companion/test_growth_short_refs.py`：新/旧冻结协议、真实 ID 与来源闭包严格还原、UUID 错字拒绝；所有语义字段重复去重/冲突；跨批映射拒绝；预算超限不裁证据；自动纠正与重启；纠正耗尽；旧正常 v2 blocked 经 request_review 升级且原请求不变；纠正传输退避和迟到历史；来源撤回前不冻结新 attempt。
- `tests/companion/test_growth_output.py`：现有完整输出、来源/冻结身份、旧 wrapper、有限错误、输出额度和 persona/材料保持；模型替身按新请求提供短引用，原 UUID 解析用例保持；预算断言计入实际修正预留。
- `tests/companion/test_growth_retry.py`、`test_growth_legacy_recovery.py`：原计算失败/取消/授权/历史反思/CAS 场景，替身输出按其实际冻结协议引用。
- `tests/companion/test_growth_budget.py`：既有完整前缀选择、过大/缺原件、行动观察缺项和固定人格保留，复用该文件无需改写断言。

限制：无真实模型、旧两批实际恢复或生产效果声明；这些由根在集中验证、串行部署和原场景恢复后核对。
