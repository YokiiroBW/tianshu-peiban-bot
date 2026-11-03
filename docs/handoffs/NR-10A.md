# NR-10A：统一后台输入预算与 Memory 配置

基线 `f1a783b0`，分支 `codex/nr10-memory-budgets`，受管 worktree `C:/Users/Administrator/.codex/worktrees/memory-materials/tianshu-peiban-bot`。开始时工作区干净，NR-09 向量候选分支保留。

公共单一导出为 `from tianshu.models.budgets import BACKGROUND_INPUT_TOKENS`，值 `200000`，供本批 B/C 直接导入。复用现有 Models 预算模块，没有新配置系统；原输出参数解析、媒体字节/数量上限不变。

`ConsolidationPolicy.max_input_tokens` 默认使用该常量，`from_settings` 允许范围从 4,000 到 200,000；runtime schema 的同字段上界同步为 200,000。显式较小配置继续生效。初始装配、完整消息/记录裁减、候选诊断预留、补查、纠正、预算失败配置指纹均读取这份 policy，无另一个需要替换的旧输入 token 上限。`extraction_contract.py` 的 `128000` 是提案 JSON 字节上限，保持其原职责。

输出 4,096 和推理预留 8,192 保持；输入预算内仍计协议、图像估算和必要的纠正空间。300 秒执行预算、真实模型窗口约束、来源/身份/CAS、原图与来源别名、完整单元分批、旧冻结请求/结果/unknown 观察路径没有修改。`max_episodes=32`、`max_subjects=16`、`max_materials=32`、`max_old_records=32`、`context_messages=6`、`max_source_characters=12000` 及其配置上界未扩大。原装配公式继续受既有正文长度上限约束，不另加或提高字符/数量门槛。

有效配置键是 `memory.consolidation.max_input_tokens`。根已只读核对当前生产 `memory.consolidation={}`，没有显式覆盖，因此此轮不需要写生产 settings，新默认自然生效；其他环境若已有旧显式值，仍需由根按授权更新该键。仓库现有部署样例未设置此覆盖，同样继承新默认，无需写入一份会覆盖默认的重复值。新增 Settings.load 用例使用该真实样例，并分别测试空配置和显式值。

建议根集中运行的精确目标：

- `tests/memory/test_input_budget.py`：6 个参数展开场景，覆盖部署样例无覆盖时默认 200,000、输出/材料上限保持、旧较小覆盖和 200,000 可加载、上下边界与 Memory validator 一致。
- `tests/memory/test_generation_policy.py::test_near_cap_input_reserves_a_complete_repair_before_freezing_sources`：初始/补查 × 小配置/200,000 共 4 个场景，实际计数确认近界输入为后续纠正留足空间，纠正不裁原证据。此用例沿用保守 UTF-8 counter 的精确可计算边界，不把其字节计数冒称供应商实际 token。
- `tests/memory/test_material_media.py::test_historical_images_are_bounded_by_whole_messages_with_reply_original_first`
- `tests/memory/test_material_media.py::test_candidate_diagnostic_reserve_protects_limit_sized_original_text_and_image`：以上两条原小预算边界测试显式固定 16,384；继续检验原边界，并避免把其上限误当新默认。
- `tests/core/test_consolidation_pipeline.py::test_budget_configuration_change_keeps_an_unknown_original_attempt`：将已冻结的 64,000 配置升到 200,000，仅观察原 attempt；完成后数据库 `memory.model_steps.request` 与升级前逐值相同，不生成第二个请求。

实际验证只有调用链静态检索、完整 diff 阅读和 `git diff --check`；未运行测试、构建、应用、生产、真实模型或 QQ。旧冻结请求实测和本批联合预算验收由根集中执行，未宣称实际线上效果。
