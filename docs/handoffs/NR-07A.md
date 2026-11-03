# NR-07A：Memory 多模态材料装配

基线：`59b9b37a68fae619517cca0862d0848cdf5633e6`。独立受管 worktree：`C:/Users/Administrator/.codex/worktrees/memory-materials/tianshu-peiban-bot`，分支 `codex/nr07-memory-materials`。

本任务只改 `materials.py`、专属 `material_media.py`、`tests/memory/test_material_media.py` 和本交接。未改整理协议、持久化、恢复、Sources、前端或共享记录；未推送、部署、访问生产、调用真实模型或 QQ。

材料装配现在直接使用已授权 `Sources.read` 的图片 `MediaRef`，不要求预先 caption。新批次固定 `model_protocol=memory.proposal.v2`；旧 frozen 无此键继续原纯文字模型视图。首个 `ModelMessage` 的首个 part 仍为 `model_material()` 的 JSON，后接标有 `mN` 的图片；同一 source/revision 只发送一份图像，不按字节散列跨来源复用。不同 episode 的重复来源保留各自 `reporting_speaker_id`、目标覆盖与引用语义。

导出接口：

- `model_messages(value, policy, *, context_round=0, requests=()) -> tuple[ModelMessage, ...]`
- `has_new_material(value) -> bool`：新增文本或可用原图才算有效新材料；纯引用/提及/缺图元数据不算。
- `material_capabilities(value) -> frozenset[str]`：包含图片时为 `text, vision`。
- `append_context_material(...) -> int`：返回真正添加的材料数，补图和补文字均不重编号。

后端 `media` 为可序列化 `MediaRef`；`evidence_kind` 为 `text/image/text_image/reference`。来源版本、locator、原作者/上传者、报告者、原始来源时间和实际消息时间保留在材料中。`scope_ids` 优先使用 Sources 返回的真实来源范围；`source_person_id` 只补 human 图片的上传者，不能证明图中人物是谁。原图上传时间另存 `source_occurred_at`，原会话时间使用可信 episode/message 的 `occurred_at`；重复报告的时间以 `reported_at` 与原件时间区分。

v2 可见视图隐藏原始 reply/mention JSON 和可误用的 QQ/event ID，只给出实际可见 `mN` 的 `reply_to`、`related_material_ids`、已验证 `mention_subject_ids` 和 `media.ref`。每次序列化重新计算这些关系，历史上下文被移除后不会保留悬空别名。缺图/不支持的 MIME 有显式 `coverage_missing` 与 episode 缺项，不能当作已分析。历史图片按整条消息有界选择，显式引用原图优先；省略计数进入 `selection_coverage`。

集中验证入口（本代理未运行）：`python -m pytest tests/memory/test_material_media.py tests/memory/test_consolidation_contract.py`。新增用例覆盖纯图片多人输入、同源图片去重和重复报告归属、权威上传者/消息时间、引用原图、技术元数据隔离、缺图及其分页、不支持图片格式、背景图预算、补查恢复与旧文字协议。已做静态 diff 审阅和 `git diff --check`；没有执行测试、构建或真实模型。

集成依赖：根为 `Sources.read/read_many` 增加 `source_person_id`、真实 `scope_ids`，并从 Broker 原有 32 MiB 限额提取 `models.budgets.MAX_INLINE_MEDIA_BYTES`（本任务直接导入）；单张大小未知、负值或超限的图片明确记缺项，不阻塞同批可用文字。NR-07B 消费上述视觉类型与 v2 模型协议；NR-07C 用上述消息/能力/判空接口接线、保留 16,384 输入与 300 秒预算、以完整 TokenCounter 作最终收敛，并按 append 返回值处理补查。C 的整消息删裁以 `context_message_id` 分组、优先保留 `context_required`；必要省略引用原件时须保留覆盖缺项。素材文件实际可读性、供应商视觉路由和真实整理质量由主协调者集中验证，本任务不把材料引用存在等同真实图像分析成功。
